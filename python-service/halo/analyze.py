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
from .industry_map import (
    get_anomaly_narratives,
    get_concept_tags,
    get_industry_map,
)
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

#: 纯数值输入字段：它们服务于六维计算与对账，不是「事实」也不是叙事。
_NUMERIC_INPUT_FIELDS = frozenset({
    "fixed_assets", "construction_in_progress", "inventory",
    "intangible_assets", "goodwill", "total_assets", "employees_total",
})

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

    # --- 第十二章：产业链定位 ---
    # 素材由 Python 备好，**判定由 AI 做**。不移植 halo-skill 的 Serenity：
    # 它靠「经营范围里有没有某个词」查表拼装，一半字段是全常量（所有公司
    # 逐字相同），还会把医用敷料公司判成「半导体」。这里给的是真实营收结构
    # 与毛利率 + 行业归属，AI 读这些比查表可靠得多。
    segs = result.get("narratives", {}).get("business_segments") or {}
    L.append("## 五、产业链定位")
    L.append("")
    if segs:
        for dim, blk in segs.items():
            L.append(f"### 主营业务构成 · 分{dim}")
            L.append("")
            L.append("| 业务 | 收入 | 占比 | 毛利率 |")
            L.append("|:--|--:|--:|--:|")
            for x in blk.get("rows", []):
                L.append(
                    f"| {x['segment']} | {x['revenue'] / 1e8:,.2f} 亿 | "
                    f"{(x.get('share') or 0) * 100:.1f}% | "
                    f"{x['gross_margin'] * 100:.1f}% |"
                    if x.get("revenue") else f"| {x['segment']} | - | - | - |"
                )
            L.append("")
        L.append("> 分行业/分产品/分地区/分销售模式是同一收入的四种切法，各维度内占比"
                 "之和为 100%，**不可跨维度相加**。")
        L.append("")
    ind_map = (result.get("narratives", {}).get("industry_map") or {})
    if ind_map.get("level1") or ind_map.get("level2"):
        L.append(f"**行业归属**：一级 {ind_map.get('level1') or '—'} ／ "
                 f"二级 {ind_map.get('level2') or '—'}")
        L.append("")
    ext = result.get("external") or {}
    vault = ((ext.get("datacenter") or {}).get("valuation_percentiles") or {})
    if vault:
        L.append("**估值历史分位**：" + "、".join(
            f"{k.upper()} {v['value']:.1f}（{v['percentile']:.0f}%）"
            for k, v in vault.items() if v.get("value") is not None
        ))
        L.append("")

    L.append("**判定**（基于上表真实营收结构与毛利率推断，不要套模板）：")
    L.append("")
    L.append("- 产业链位置：`{{chain_position}}`")
    L.append("- 关键瓶颈：`{{bottleneck}}`")
    L.append("- 稀缺性评级：`{{scarcity_rating}}`")
    L.append("- 判定依据：`{{chain_evidence}}`")
    L.append("")

    ext_section = (result.get("narratives", {}).get("research_reports") or [])
    if ext_section:
        L.append("### 附：研报观点（第三方观点，不是事实）")
        L.append("")
        for r in ext_section[:6]:
            L.append(f"- [{r.get('rating_bucket') or '未分类'}] {r.get('org')} "
                     f"{r.get('date')}：{r.get('title')}")
        L.append("")

    L.append("## 六、综合评分")
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
    L.append("> 复算容差 0.05，且声明评级必须与复算分同档。**用 halo.verify 提交，"
             "不要自己心算。**")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*本报告由 Python 锁定数据层，分析层待填。数据缺失项已在正文标注，不作估算。*")
    return "\n".join(L)


def _local_source(name: str) -> Any:
    """取本地 DuckDB 库的 executor；未就绪返回 None（由调用方按缺失处理）。"""
    try:
        from datasources import registry
    except Exception:  # noqa: BLE001 —— 单测环境没有 datasources
        return None
    return registry.get(name)


def build_narratives(segment_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """文本/表格类素材，独立于数值锚点。

    为什么要分开：``anchors`` 里装的是「ROE=0.3253」这种**数**，AI 按数值
    参与推理；这里装的是「哪个业务占多少收入、毛利多少」这种**结构化描述**，
    AI 按叙述与表格来读。混在一起 AI 没法区分哪个能算、哪个只能读。

    主营构成（分部收入 + 毛利率）是产业链定位最可靠的输入 —— 真实营收结构
    带上毛利率，比「经营范围里有没有『制造』两个字」这种关键词匹配强得多。
    """
    # 按切分维度分组：分行业 / 分产品 / 分地区 / 分销售模式 是**同一总量的
    # 四种切法**，平铺会让读者以为它们是四个独立业务（茅台：酒类 1688 亿、
    # 茅台酒 1465 亿、国内 1639 亿、直销 845 亿，误加会得到 5600 亿的幻觉）。
    by_dim: Dict[str, Dict[str, Dict[str, float]]] = {}
    for row in segment_rows:
        name = row.get("value_text")
        field = row.get("field", "")
        if not name or not field.startswith("segment_"):
            continue
        rest = field[len("segment_"):]
        metric, _, dimension = rest.partition("__")
        if not dimension:
            dimension = "unspecified"
        by_dim.setdefault(dimension, {}).setdefault(name, {})[metric] = row.get("value")

    out_dims = {}
    for dim, items in by_dim.items():
        total = sum((v.get("revenue") or 0) for v in items.values()) or 0
        ordered = sorted(items.items(), key=lambda kv: -(kv[1].get("revenue") or 0))
        rows = []
        for name, vals in ordered:
            share = (vals["revenue"] / total) if (total and vals.get("revenue")) else None
            rows.append({"segment": name, "share": round(share, 4) if share else None, **vals})
        out_dims[dim] = {"rows": rows, "total_revenue": total or None}

    return {
        "business_segments": out_dims,
        "segment_dimensions": list(out_dims.keys()),
        "segment_count": sum(len(v["rows"]) for v in out_dims.values()),
        "has_business_breakdown": bool(out_dims),
        "segment_note": (
            "分行业/分产品/分地区/分销售模式是同一总收入的四种切法，"
            "各维度内 share 之和为 1；**不可跨维度相加**。"
        ),
    }


async def fetch_external(code: str, *, with_fund_flow: bool = True) -> Dict[str, Any]:
    """拉外网数据（治理/风险/估值分位/研报/资金流）。

    **默认不随 analyze 一起跑**。理由：分析是按需行为，而外网慢、会封 IP，
    稳定档的数据（估值分位、股东户数）又是慢变量。把它做成显式可选，
    agent 可以在需要「最新治理动态 / 研报观点」时再拉。

    分档降级：稳定档（datacenter-web）与第三档（reportapi）独立可用；易封档
    （push2his）失败只影响资金流这一项，不牵连其它。实测 push2his 封禁时
    另两个子域完全正常 —— 这就是按子域分级而非按「东财」整体的回报。
    """
    from . import extdata

    out: Dict[str, Any] = {
        "enabled": True, "subdomains": {}, "errors": {},
    }
    buckets = [
        ("datacenter", "stable", lambda: {
            "valuation_percentiles": extdata.valuation_percentiles(code),
            "holder_count_latest": extdata.holder_count(code),
            "equity_pledge": extdata.equity_pledge(code),
            "holder_trades": extdata.holder_trades(code, limit=5),
            "earnings_forecast": extdata.earnings_forecast(code, limit=3),
            "institution_surveys": extdata.institution_surveys(code, limit=3),
        }),
        ("reportapi", "third", lambda: {
            "research_reports": extdata.research_reports(code, limit=8),
        }),
    ]
    if with_fund_flow:
        buckets.append(
            ("push2his", "volatile", lambda: {"fund_flow": extdata.fund_flow(code, days=60)})
        )
    for name, tier, fn in buckets:
        try:
            out[name] = fn()
            out["subdomains"][name] = {"tier": tier, "ok": bool(out[name])}
        except Exception as exc:  # noqa: BLE001 —— 单档失败不牵连其它档
            out["errors"][name] = str(exc)[:200]
            out["subdomains"][name] = {"tier": tier, "ok": False}
    return out


async def analyze(
    thscode: str,
    *,
    store: FactStore,
    period: Optional[str] = None,
    report_type: str = "annual",
    scope: str = SCOPE_CONSOLIDATED,
    pages: Optional[ExtractResult] = None,
    include_external: bool = False,
) -> Dict[str, Any]:
    """对一只股票出评分结果。

    Args:
        include_external: 额外拉外网数据（治理/估值分位/研报）。默认关闭 ——
            分析是按需行为，外网慢且会封 IP，需要时再显式开启。
    """
    thscode = normalize_thscode(thscode)
    if period is None:
        period = store.latest_period(thscode, report_type)
    if period is None:
        # 早返回也要带**完整结构**。缺键会让调用方（agent / 前端）在
        # KeyError 上崩掉，而不是拿到一个「暂无数据」的正常响应 ——
        # 「这股票没同步过年报」是最常见的正常情况之一，不该是异常路径。
        return {
            "thscode": thscode,
            "period": None,
            "report_type": report_type,
            "scope": scope,
            "ok": False,
            "reason": f"{thscode} 没有已落库的年报事实。先调用 halo.filing.sync。",
            "missing": ["filing_facts"],
            "asset_type": None,
            "asset_type_basis": "no_filing_facts",
            "asset_type_signals": {},
            "asset_type_disagreement": False,
            "halo": {"ok": False, "reason": "无年报事实，HALO 不可计算"},
            "growth": None,
            "facts": [],
            "environment_disclosure": False,
            "narratives": {
                "business_segments": [],
                "segment_count": 0,
                "has_business_breakdown": False,
                "industry_map": {"level1": None, "level2": None, "memberships": [],
                                "basis": "not_found", "as_of_note": "无年报事实"},
                "concepts": [],
                "anomalies": [],
            },
            "ai_slots": [],
            "markdown": f"# {thscode} HALO 分析\n\n"
                        f"**⚠️ 无数据**：{thscode} 尚无已落库的年报事实，"
                        f"请先调用 halo.filing.sync。\n",
        }

    rows = store.query(
        thscode, period=period, report_type=report_type, scope=scope, only_verified=True
    )
    # 按 field 去重只适用于**标量**字段（估值锚点取一条即可）。
    # 分部数据是 (field, 业务名) 组合，同一 field 下有多行，去重会把
    # 茅台的 7 个业务压成 1 个 —— 所以单独收集。
    facts: Dict[str, Dict[str, Any]] = {}
    segment_rows: List[Dict[str, Any]] = []
    for r in rows:
        if r["field"].startswith("segment_"):
            segment_rows.append(r)
        else:
            facts.setdefault(r["field"], r)

    financial = await _fetch_financials(thscode, period)

    # --- 行业归属（本地 index 库）---
    # 与财务比例分类互为交叉验证：财务比例说「重不重」，行业库说「属于哪行」。
    # 两者不一致本身就是信号（比如一家做实业的公司被归进软件行业）。
    ind_map = await get_industry_map(_local_source("index"), thscode)
    concept_tags = await get_concept_tags(_local_source("index"), thscode)
    anomalies = await get_anomaly_narratives(_local_source("special"), thscode, limit=5)

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
            for k, v in facts.items() if k not in _NUMERIC_INPUT_FIELDS
            and not k.startswith("segment_")
        ],
        "environment_disclosure": env_disclosure,
        "narratives": {
            **build_narratives(segment_rows),
            "industry_map": ind_map,
            "concepts": concept_tags,
            "anomalies": anomalies,
            "anomaly_coverage_note": (
                "异动归因为事件驱动，实测仅覆盖 1484/5571 只股票（约 27%）；"
                "无记录表示近期无异动事件，不表示基本面信息缺失。"
            ),
        },
        "ai_slots": build_ai_slots(facts, financial, ind),
    }
    if fact_records:
        result["fresh_facts"] = [f["field"] for f in fact_records]
    if include_external:
        result["external"] = await fetch_external(thscode)
        result["narratives"]["research_reports"] = [
            r for r in (result["external"].get("reportapi") or {}).get("research_reports", [])
        ]
    result["markdown"] = render_markdown(result)
    return result
