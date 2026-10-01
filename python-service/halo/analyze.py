"""评分编排：读事实库 + 本地 DuckDB → 输出评分结果与 AI 待判槽位。

边界（与 grilling 定的一致）
--------------------------
本模块只做**确定性计算**。七个定性维度（护城河/滞胀/ESG/管理层/资金面/
估值/风险）由调用方给分，本模块负责把每项的**量化锚点**算好一并返回 ——
不让 AI 凭空判分，也不让 AI 做算术（综合分由 :func:`recalc_comprehensive`
复算，调用方填的分会被校验）。

缺数据的处理一律是「标缺失」，不做外推、不用别的口径顶替。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import scoring
from .facts import extract_facts
from .industry import classify
from .pdf_extract import ExtractResult
from .reconcile import get_financials_source
from .pipeline import normalize_thscode
from .scoring import MissingInput
from .store import SCOPE_CONSOLIDATED, FactStore

logger = logging.getLogger(__name__)

#: 七个定性维度。``anchors`` 列出该维度能拿到哪些量化依据。
#:
#: 每个锚点**显式**声明单位，不靠启发式猜。本地库里百分数与倍数混存：
#: roe/gross_margin/assets_debt_ratio/ocf_to_profit 是百分数（32.53 表示
#: 32.53%），current_ratio 是**倍数**，ocf 是金额。
#:
#: 曾用「值大于 3 就当百分数」的启发式，结果把茅台的 current_ratio=5.09
#: （流动比率 5 倍，真实值）除成了 0.0509 —— 流动比率 > 3 很常见，启发式
#: 必然在某些值上失效。单位必须逐字段声明。
#:
#:   percent → 库内存百分数，统一折算成小数（32.53 → 0.3253）
#:   multiple→ 倍数，原样（5.09 就是 5.09 倍）
#:   amount  → 金额（元），原样
#:   count   → 计数，原样

AI_DIMENSIONS = (
    ("moat", "护城河", (("gross_margin", "percent"), ("roe", "percent"),
                       ("net_margin", "percent"))),
    ("stag", "滞胀防御", (("tangible_pct", "percent"), ("assets_debt_ratio", "percent"),
                          ("current_ratio", "multiple"), ("ocf", "amount"))),
    ("esg", "ESG", (("employees_total", "count"), ("revenue_per_employee", "amount"),
                    ("emissions", "amount"))),
    ("management", "管理层", (("roe", "percent"), ("assets_debt_ratio", "percent"),
                              ("dividend_payout", "percent"))),
    ("shareholder", "股东资金面", (("main_fund_flow", "amount"), ("holder_count", "count"))),
    ("valuation", "估值", (("pe_ttm", "multiple"), ("pb", "multiple"),
                           ("ps", "multiple"), ("pcf", "multiple"))),
    ("risk", "风险", (("assets_debt_ratio", "percent"), ("current_ratio", "multiple"),
                      ("ocf_to_profit", "percent"), ("pe_percentile", "percent"))),
)

#: 事实字段里天然属于「风险」语义的两个：内控非标、董监高被罚。
_RISK_FACT_FIELDS = ("internal_control_nonstandard", "executive_penalty",
                     "regulatory_penalty_3y")


async def _fetch_financials(thscode: str, period: str) -> Dict[str, Any]:
    """从本地 DuckDB 取评分需要的财务项。取不到就返回空 dict，由调用方按缺失处理。"""
    src = get_financials_source()
    if src is None:
        logger.warning("financials 数据源未就绪，财务锚点将缺失")
        return {}
    year = int(period[:4])
    sql = """
    SELECT i.operating_income AS revenue,
           i.operating_costs  AS operating_costs,
           i.net_profit,
           c.pay_fixed_assets_etc_cash AS capex,
           c.act_cash_flow_net AS ocf,
           d.assets_debt_ratio,
           d.current_ratio,
           d.weighted_avg_roe AS roe,
           d.sale_gross_margin AS gross_margin,
           d.sale_net_interest_ratio AS net_margin,
           d.net_profit_cash_content AS ocf_to_profit,
           d.inventory_turnover_ratio
    FROM v_income_statement i
    JOIN v_cash_flow_statement c
      ON c.thscode = i.thscode AND c.period = i.period
     AND c.fiscal_year = i.fiscal_year AND c.fiscal_period = i.fiscal_period
    LEFT JOIN v_financial_indicators_detail d
      ON d.thscode = i.thscode
     AND d.report = (
           CASE WHEN i.fiscal_period = 'FY'
                THEN CAST(i.fiscal_year AS VARCHAR) || '-4'
                ELSE CAST(i.fiscal_year AS VARCHAR) || '-' || replace(i.fiscal_period, 'Q', '')
           END
         )
    WHERE i.thscode = ?
      AND (i.fiscal_year * 10 + CAST(replace(i.fiscal_period, 'FY', '4') AS INTEGER)) = ?
      AND i.period = 'annual'
    LIMIT 1
    """
    quarter = 4 if period.endswith("12-31") else int(period[5:7]) // 3
    try:
        rows = await src.execute(sql, [thscode, year * 10 + quarter])
    except Exception as exc:  # noqa: BLE001
        logger.warning("取财务锚点失败 %s: %s", thscode, exc)
        return {}
    if not rows:
        return {}
    fin = dict(rows[0])

    # 估值是**快照**（每日更新），不是报告期数据 —— 它回答的是「现在贵不贵」，
    # 与年报口径无关，所以单独取最新一条，不参与报告期 join。
    try:
        v = await src.execute(
            "SELECT pe_ttm, pe_mrq, pb_mrq, ps_ttm, pcf_ttm "
            "FROM v_valuation_latest WHERE thscode = ? LIMIT 1",
            [thscode],
        )
        if v:
            fin.update({
                "pe_ttm": v[0].get("pe_ttm"),
                "pb": v[0].get("pb_mrq"),
                "ps": v[0].get("ps_ttm"),
                "pcf": v[0].get("pcf_ttm"),
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("取估值锚点失败 %s: %s", thscode, exc)
    return fin


def _field_value(
    facts: Dict[str, Dict[str, Any]], field: str
) -> Optional[float]:
    row = facts.get(field)
    if not row:
        return None
    return row.get("value")


def build_ai_slots(
    facts: Dict[str, Dict[str, Any]],
    financial: Dict[str, Any],
    industry: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """构造 7 个定性维度的待判槽位，附上各自能拿到的量化锚点。

    锚点缺失时显式标注 —— 区分「有锚点却没给分」和「没锚点却给了分」这两种
    不同的失败，前者是判断问题，后者是数据问题。
    """
    derived: Dict[str, Any] = {}
    emp = _field_value(facts, "employees_total")
    rev = financial.get("revenue")
    if emp and rev:
        derived["revenue_per_employee"] = rev / emp
    for k in ("emission_particulate", "emission_so2", "emission_nox"):
        v = _field_value(facts, k)
        if v is not None:
            derived.setdefault("emissions", {})[k] = v
    if industry:
        # 有形资产占比是行业分类时算出来的，滞胀防御维度直接复用，
        # 不必为了给 AI 看再算一遍（两个地方算同一个数迟早会漂）。
        tp = (industry.get("signals") or {}).get("tangible_pct")
        if tp is not None:
            derived["tangible_pct"] = tp

    def anchor(name: str) -> Optional[Any]:
        if financial.get(name) is not None:
            return financial[name]
        return _field_value(facts, name)

    risk_flags = {
        k: _field_value(facts, k)
        for k in _RISK_FACT_FIELDS
        if _field_value(facts, k) is not None
    }

    slots: List[Dict[str, Any]] = []
    for key, label, wanted in AI_DIMENSIONS:
        anchors, missing = {}, []
        for name, unit in wanted:
            val = anchor(name)
            if val is None:
                val = derived.get(name)
            if val is None:
                missing.append(name)
                continue
            # 百分数 → 小数。32.53(%) 变成 0.3253，AI 才不会把它当倍数。
            # 倍数/金额/计数原样保留。
            if unit == "percent" and isinstance(val, (int, float)):
                val = val / 100.0
            anchors[name] = val
        if key == "esg" and derived.get("emissions"):
            anchors["emissions"] = derived["emissions"]
        if key == "risk" and risk_flags:
            anchors["hard_risk_facts"] = risk_flags
        slots.append({
            "dimension": key,
            "label": label,
            "anchors": anchors,
            "missing_anchors": missing,
            "has_anchor": not missing,
            "score": None,
        })
    return slots


def render_markdown(result: Dict[str, Any]) -> str:
    """预渲染报告骨架。

    已算分��分是确定的，直接填；AI 待判分处留槽位标记，并把量化锚点一并
    写进槽位下方 —— 让人（或 agent）判分时看到依据，而不是凭印象。
    """
    L: List[str] = []
    ths = result.get("thscode", "?")
    period = result.get("period", "?")
    L.append(f"# {ths} HALO 分析骨架")
    L.append("")
    L.append(f"> 报告期：{period} ｜ 行业类型：{result.get('asset_type', '未知')} "
             f"（判定依据：{result.get('asset_type_basis', '-')}）")
    L.append("")

    halo = result.get("halo")
    L.append("## 一、HALO 六维（Python 计算）")
    L.append("")
    if halo and halo.get("ok"):
        L.append(f"**HALO 总分：{halo['score']:.2f} / 5.0 —— {halo['rating']}**")
        L.append("")
        L.append("| 维度 | 原始值 | 得分 | 权重 |")
        L.append("|:--|--:|--:|--:|")
        for name, d in halo["dimensions"].items():
            raw = "不可计算" if d["raw"] is None else f"{d['raw']:.2f}{d['unit']}"
            L.append(f"| {name} | {raw} | {d['score']} | {d['weight']} |")
    else:
        L.append(f"**⚠️ {halo.get('reason', 'HALO 不可计算') if halo else 'HALO 不可计算'}**")
        L.append("")
        L.append("按数据铁律不做外推或补值。缺 HALO 总分时，综合评分里的 HALO 一项也无法计算。")
    L.append("")

    growth = result.get("growth")
    if growth:
        L.append("## 二、成长性（Python 计算）")
        L.append("")
        state = "" if growth["complete"] else f"（缺 {', '.join(growth['missing'])}，已按实际权重归一）"
        L.append(f"**成长性：{growth['score']:.2f} / 10 —— {growth['rating']}**{state}")
        L.append("")
        L.append("| 子项 | 得分 | 依据 |")
        L.append("|:--|--:|:--|")
        for name, s in growth["sub_scores"].items():
            applied = "；".join(s.get("applied", [])) or "—"
            L.append(f"| {name} | {s['score']} | {applied} |")
        L.append("")

    facts = result.get("facts") or []
    if facts:
        L.append("## 三、治理诚信事实（年报原文抽取）")
        L.append("")
        L.append("| 事实 | 值 | 来源页 | 原文 |")
        L.append("|:--|:--|--:|:--|")
        for f in facts:
            v = f.get("value_text") or (
                f"{f['value']:g} {f.get('unit') or ''}" if f.get("value") is not None else "-"
            )
            raw = (f.get("raw_text") or "").replace("|", "／")[:60]
            L.append(f"| {f['field']} | {v} | p{f.get('source_page', '-')} | {raw} |")
        L.append("")

    slots = result.get("ai_slots") or []
    L.append("## 四、定性维度（待判分）")
    L.append("")
    L.append("下列维度的分数需要人工/AI 判断。**量化锚点已算好**，请依据锚点与"
             "「评分要点」判断，不要凭印象给分；标 `无量化锚点` 的子项只做定性判断。")
    L.append("")
    for s in slots:
        mark = "" if s["has_anchor"] else " ⚠️ 无量化锚点"
        L.append(f"### {s['label']}　`{{{s['dimension']}_score}}`{mark}")
        L.append("")
        if s["anchors"]:
            L.append("量化锚点：")
            L.append("")
            for k, v in s["anchors"].items():
                L.append(f"- `{k}` = {v}")
        else:
            L.append("- （无）")
        L.append("")
        L.append(f"分析：{{{s['dimension']}_analysis}}")
        L.append("")

    L.append("## 五、综合评分")
    L.append("")
    L.append("先给出九个维度的分数，再由 Python 按权重复算校验：")
    L.append("")
    L.append("| 维度 | 分数 | 来源 |")
    L.append("|:--|--:|:--|")
    for s in slots:
        L.append(f"| {s['label']} | {{{s['dimension']}_score}} | 待判 |")
    if halo and halo.get("ok"):
        L.append(f"| HALO 六维 | {halo['score']:.2f} | Python 计算 |")
    if growth:
        L.append(f"| 成长性 | {growth['score']:.2f} | Python 计算 |")
    L.append("")
    L.append("声明综合分：`{{comprehensive_score}}` 声明评级：`{{comprehensive_rating}}`")
    L.append("")
    L.append("> 复算容差 0.05，且声明评级必须与复算分同档。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*本报告由 Python 锁定数据层，分析层待填。数据缺失项已在正文标注，不作估算。*")
    return "\n".join(L)


async def analyze(
    thscode: str,
    *,
    store: FactStore,
    period: Optional[str] = None,
    report_type: str = "annual",
    scope: str = SCOPE_CONSOLIDATED,
    pages: Optional[ExtractResult] = None,
) -> Dict[str, Any]:
    """对一只股票出评分结果。"""
    thscode = normalize_thscode(thscode)
    if period is None:
        period = store.latest_period(thscode, report_type)
    if period is None:
        return {
            "thscode": thscode,
            "ok": False,
            "reason": f"{thscode} 没有已落库的年报事实。先调用 halo.filing.sync。",
            "missing": ["filing_facts"],
        }

    rows = store.query(
        thscode, period=period, report_type=report_type, scope=scope, only_verified=True
    )
    facts: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        facts.setdefault(r["field"], r)

    financial = await _fetch_financials(thscode, period)

    # --- 行业分类 ---
    ind = classify(
        fixed_assets=_field_value(facts, "fixed_assets"),
        construction_in_progress=_field_value(facts, "construction_in_progress"),
        inventory=_field_value(facts, "inventory"),
        total_assets=_field_value(facts, "total_assets"),
    )

    # --- HALO 六维 ---
    try:
        h = scoring.score_halo(
            asset_type=ind["asset_type"] if ind["asset_type"] != "unknown" else "mixed",
            fixed_assets=_field_value(facts, "fixed_assets"),
            construction_in_progress=_field_value(facts, "construction_in_progress"),
            inventory=_field_value(facts, "inventory"),
            total_assets=_field_value(facts, "total_assets"),
            employees=_field_value(facts, "employees_total"),
            revenue=financial.get("revenue"),
            capex=financial.get("capex"),
            ocf=financial.get("ocf"),
        )
        # score_halo 返回 total（与内部加权一致），对外统一用 score，避免
        # 下游同时见到 total/score 两个名字而取错。
        h["score"] = h.pop("total")
        h["ok"] = True
    except MissingInput as exc:
        h = {"ok": False, "reason": str(exc)}

    # --- 成长性 ---
    growth = None
    rev_yoy = prof_yoy = None
    try:
        src = get_financials_source()
        if src is not None:
            yoy = await src.execute(
                """
                SELECT
                  (SELECT operating_income FROM v_income_statement
                    WHERE thscode=? AND period='annual' AND fiscal_period='FY'
                      AND fiscal_year=? LIMIT 1) AS cur_rev,
                  (SELECT operating_income FROM v_income_statement
                    WHERE thscode=? AND period='annual' AND fiscal_period='FY'
                      AND fiscal_year=? LIMIT 1) AS prev_rev,
                  (SELECT net_profit FROM v_income_statement
                    WHERE thscode=? AND period='annual' AND fiscal_period='FY'
                      AND fiscal_year=? LIMIT 1) AS cur_np,
                  (SELECT net_profit FROM v_income_statement
                    WHERE thscode=? AND period='annual' AND fiscal_period='FY'
                      AND fiscal_year=? LIMIT 1) AS prev_np
                """,
                [thscode, int(period[:4])] * 4,
            )
            if yoy and yoy[0].get("prev_rev"):
                rev_yoy = (yoy[0]["cur_rev"] / yoy[0]["prev_rev"] - 1) * 100
            if yoy and yoy[0].get("prev_np"):
                prof_yoy = (yoy[0]["cur_np"] / yoy[0]["prev_np"] - 1) * 100
    except Exception as exc:  # noqa: BLE001
        logger.warning("取成长性同比失败 %s: %s", thscode, exc)

    ocf_to_profit = financial.get("ocf_to_profit")
    if ocf_to_profit is not None and ocf_to_profit > 1:
        ocf_to_profit = ocf_to_profit / 100.0
    growth = scoring.score_growth(
        revenue_yoy=rev_yoy,
        net_profit_yoy=prof_yoy,
        cf_to_profit=ocf_to_profit,
        debt_ratio=financial.get("assets_debt_ratio"),
    )

    # --- 事实字段（治理诚信 + ESG）---
    fact_records: List[Dict[str, Any]] = []
    if pages is not None:
        ex = extract_facts(pages)
        fact_records = ex["facts"]
        env_disclosure = ex["has_environment_disclosure"]
    else:
        env_disclosure = any(k.startswith("emission_") for k in facts)

    result: Dict[str, Any] = {
        "thscode": thscode,
        "period": period,
        "report_type": report_type,
        "scope": scope,
        "ok": True,
        "asset_type": ind["asset_type"],
        "asset_type_basis": ind["basis"],
        "asset_type_signals": ind["signals"],
        "asset_type_disagreement": ind["disagreement"],
        "halo": h,
        "growth": {
            "score": growth["total"], "rating": growth["rating"],
            "sub_scores": growth["sub_scores"],
            "missing": growth["missing"], "complete": growth["complete"],
        },
        "facts": [
            {"field": k, "value": v.get("value"), "value_text": v.get("value_text"),
             "unit": v.get("unit"), "source_page": v.get("source_page"),
             "raw_text": v.get("raw_text")}
            for k, v in facts.items() if k not in (
                "fixed_assets", "construction_in_progress", "inventory",
                "intangible_assets", "goodwill", "total_assets", "employees_total",
            )
        ],
        "environment_disclosure": env_disclosure,
        "ai_slots": build_ai_slots(facts, financial, ind),
    }
    if fact_records:
        result["fresh_facts"] = [f["field"] for f in fact_records]
    result["markdown"] = render_markdown(result)
    return result
