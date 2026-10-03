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

import asyncio
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


def _fund_flow_anchors(external: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把已抓回来的 push2his 资金流序列换成「股东资金面」能用的锚点。

    ``extdata.fund_flow`` 的 docstring 明确**不自行合并「主力」**（该接口没取
    f56 超大单，少一个档位就做加法等于用错口径），所以这里只用它返回的
    ``main_net`` 原值，不做任何跨档合并。

    给三个尺度而不是一个：单日噪声大，5/20 日才看得出方向。``main_fund_flow``
    是 AI_DIMENSIONS 声明的锚点名，取最新一日；5d/20d 以**附加键**给出 ——
    附加键不进 ``wanted``，因此不会把 ``has_anchor`` 拖成 false。

    单位跟其它 amount 锚点一致（元），不做亿元换算：换算是展示层的事，锚点这层
    多一次换算就多一个口径。
    """
    series = ((external or {}).get("push2his") or {}).get("fund_flow") or []
    rows = [r for r in series if isinstance(r.get("main_net"), (int, float))]
    if not rows:
        return {}
    rows.sort(key=lambda r: str(r.get("date") or ""))
    out: Dict[str, Any] = {"main_fund_flow": rows[-1]["main_net"]}
    for label, window in (("5d", 5), ("20d", 20)):
        tail = rows[-window:]
        if tail:
            out[f"main_fund_flow_{label}"] = sum(r["main_net"] for r in tail)
    return out


def build_ai_slots(
    facts: Dict[str, Dict[str, Any]],
    financial: Dict[str, Any],
    industry: Optional[Dict[str, Any]] = None,
    external: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """构造 7 个定性维度的待判槽位，附上各自能拿到的量化锚点。

    锚点缺失时显式标注 —— 区分「有锚点却没给分」和「没锚点却给了分」这两种
    不同的失败，前者是判断问题，后者是数据问题。

    锚点解析顺序：本地库（financial）→ 年报事实（facts）→ 外网（external）。
    外网放在**最后**是刻意的：``holder_count`` 同时存在于年报事实和东财快照，
    而年报是权威原文、东财只是更快的近似；让外网抢先会静默换掉既有锚点的口径。
    外网只补本地两处都没有的项（当前就是 ``main_fund_flow``）。
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

    external_anchors = _fund_flow_anchors(external)

    def anchor(name: str) -> Optional[Any]:
        if financial.get(name) is not None:
            return financial[name]
        value = _field_value(facts, name)
        if value is not None:
            return value
        return external_anchors.get(name)

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
        if key == "shareholder" and external_anchors:
            # 5d/20d 只作附加键。锚点名 main_fund_flow 已由 anchor() 解析过，
            # 这里补上多尺度是为了让判分看得到方向；它们不进 wanted，所以
            # missing / has_anchor 的语义完全不变。
            anchors.update(
                {k: v for k, v in external_anchors.items() if k != "main_fund_flow"}
            )
        slots.append({
            "dimension": key,
            "label": label,
            "anchors": anchors,
            "missing_anchors": missing,
            "has_anchor": not missing,
            "score": None,
        })
    return slots


def _md_header(result: Dict[str, Any]) -> List[str]:
    """报告头。资产类型与判定依据放这里 —— 后面每一章的解读都依赖它。"""
    ths = result.get("thscode", "?")
    period = result.get("period", "?")
    asset = result.get("asset_type") or "未知"
    basis = result.get("asset_type_basis") or "-"
    return [
        f"# {ths} HALO 滞胀复合分析报告",
        "",
        f"> 报告期：{period} ｜ 资产类型：{asset}（判定依据：{basis}）",
        ">",
        "> 本报告的数据层由 Python 锁定，分析层待填。**所有缺失项在正文如实标注，"
        "不做外推或补值。**",
        "",
    ]


def _md_score_card(result: Dict[str, Any], slots: List[Dict[str, Any]]) -> List[str]:
    """第零章：核心评分卡片。

    与模板一致是 8 行：HALO 六维 + 成长性 + 6 个定性维度（低淘汰率/滞胀防御/
    ESG/管理层/股东资金面/风险）。估值那一维不在这张表里 —— 模板把它放在
    第十一章的估值分析，所以这里也不放，免得同一件事有两个位置。

    Python 能算的两行直接填；定性维度**待判分**就如实写「待判分」，不留空也不填 0：
    空白会被读成「没有这一项」，0 会被读成「很差」。
    """
    halo = result.get("halo") or {}
    growth = result.get("growth") or {}
    L = ["## 第零章 执行摘要", "", "### 核心评分卡片", "",
         "| 评估维度 | 评分 | 评级 | 来源 |", "|:--|:--:|:--|:--|"]

    if halo.get("ok"):
        L.append(f"| **HALO 六维** | **{halo['score']:.2f}/5.0** | {halo.get('rating') or '—'} | Python 计算 |")
    else:
        L.append("| **HALO 六维** | ⚠️ 不可计算 | — | 缺输入，见第三章 |")

    if growth:
        L.append(f"| **成长性** | **{growth['score']:.2f}/10** | {growth.get('rating') or '—'} | Python 计算 |")
    else:
        L.append("| **成长性** | ⚠️ 不可计算 | — | 缺财务数据源 |")

    for s in slots:
        if s["dimension"] == "valuation":
            continue
        mark = "" if s["has_anchor"] else " ⚠️ 无锚点"
        L.append(f"| {s['label']}{mark} | 待判分 | — | 需人工/模型给分 |")

    L += [
        "",
        "> 标 ⚠️ 的维度量化锚点不全，判分时只做定性判断。综合分要等 7 个定性维度"
        "（含估值）都给分后才能复算，见第十一章。",
        "",
    ]
    return L


def _md_company_profile(result: Dict[str, Any]) -> List[str]:
    """第一章：公司概况。

    素材由 Python 备好，**判定由 AI 做**。不移植 halo-skill 的 Serenity：
    它靠「经营范围里有没有某个词」查表拼装，一半字段是全常量（所有公司逐字相同），
    还会把医用敷料公司判成「半导体」。这里给的是真实营收结构与毛利率 + 行业归属，
    AI 读这些比查表可靠得多。
    """
    L = ["## 一、公司概况", ""]

    ind_map = (result.get("narratives", {}).get("industry_map") or {})
    if ind_map.get("level1") or ind_map.get("level2"):
        L += [
            f"**行业归属**：一级 {ind_map.get('level1') or '—'} ／ "
            f"二级 {ind_map.get('level2') or '—'}",
            "",
        ]

    ext = result.get("external") or {}
    vault = ((ext.get("datacenter") or {}).get("valuation_percentiles") or {})
    if vault:
        L += [
            "**估值历史分位**：" + "、".join(
                f"{k.upper()} {v['value']:.1f}（{v['percentile']:.0f}%）"
                for k, v in vault.items() if v.get("value") is not None
            ),
            "",
        ]

    segs = (result.get("narratives", {}).get("business_segments") or {})
    if segs:
        L += ["### 主营业务构成", ""]
        for dim, blk in segs.items():
            L.append(f"**分{dim}**")
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
        L += [
            "> 分行业/分产品/分地区/分销售模式是同一收入的四种切法，各维度内占比之和"
            "为 100%，**不可跨维度相加**。",
            "",
        ]
    else:
        L += ["- （年报里没有可用的分部数据。）", ""]

    L += [
        "**产业链定位判定**（基于上表真实营收结构与毛利率推断，不要套模板）：",
        "",
        "- 产业链位置：`{{chain_position}}`",
        "- 关键瓶颈：`{{bottleneck}}`",
        "- 稀缺性评级：`{{scarcity_rating}}`",
        "- 判定依据：`{{chain_evidence}}`",
        "",
    ]

    reports = (result.get("narratives", {}).get("research_reports") or [])
    if reports:
        L += ["### 附：研报观点（第三方观点，不是事实）", ""]
        for r in reports[:6]:
            L.append(f"- [{r.get('rating_bucket') or '未分类'}] {r.get('org')} "
                     f"{r.get('date')}：{r.get('title')}")
        L.append("")
    return L


def _md_announcements(result: Dict[str, Any]) -> List[str]:
    """第二章：消息面（近期公告）。

    恒渲染这一节，即使没取到：报告里「没有公告」和「忘了取」必须看得出区别。
    未开启 include_announcements 时说明原因，而不是留一段空白让人猜。

    模板这一章还要「利好/利空因素（带日期与来源）」，那需要新闻源 —— 目前没有
    接入，所以显式写出来，而不是留个空章节。
    """
    announcements = result.get("announcements") or []
    L = ["## 二、消息面（近期公告）", ""]
    if announcements:
        L += [f"> 共 {len(announcements)} 条（按披露时间倒序，取自巨潮全类型公告流）。", ""]
        L += ["| 披露日 | 类型 | 标题 |", "|:--|:--|:--|"]
        for a in announcements:
            # 标题里的 | 会破坏表格，替换成全角；链接用详情页（PDF 直链对未登录
            # 用户会 302，详情页才是稳的入口）。
            title = (a.get("title") or "").replace("|", "／")
            doc_type = (a.get("doc_type") or "-").replace("|", "／")
            date = a.get("date") or "-"
            url = a.get("detail_url") or ""
            cell = f"[{title}]({url})" if url else title
            L.append(f"| {date} | {doc_type} | {cell} |")
        L.append("")
    else:
        L += ["- （未取公告。生成报告时需显式开启 include_announcements；"
              "它走巨潮，是限速的按需请求。）", ""]
    L += [
        "> 本节只列**公告原文**，不做情绪判断 —— 用公告标题猜利好利空是不靠谱的。"
        "消息面的分类见下面的个股新闻与判定槽位。",
        "",
    ]
    L += _md_news(result.get("external") or {})
    return L


def _md_halo(result: Dict[str, Any]) -> List[str]:
    """第三章：HALO 六维（Python 计算，确定值）。"""
    halo = result.get("halo")
    L = ["## 三、HALO 六维（Python 计算）", ""]
    if halo and halo.get("ok"):
        L += [f"**HALO 总分：{halo['score']:.2f} / 5.0 —— {halo['rating']}**", ""]
        L += ["| 维度 | 原始值 | 得分 | 权重 |", "|:--|--:|--:|--:|"]
        for name, d in halo["dimensions"].items():
            raw = "不可计算" if d["raw"] is None else f"{d['raw']:.2f}{d['unit']}"
            L.append(f"| {name} | {raw} | {d['score']} | {d['weight']} |")
        L.append("")
    else:
        L += [
            f"**⚠️ {halo.get('reason', 'HALO 不可计算') if halo else 'HALO 不可计算'}**",
            "",
            "按数据铁律不做外推或补值。缺 HALO 总分时，综合评分里的 HALO 一项也无法计算。",
            "",
        ]
    return L


def _md_growth(result: Dict[str, Any]) -> List[str]:
    """第四章：成长性（Python 计算，确定值）。"""
    growth = result.get("growth")
    if not growth:
        return [
            "## 四、成长性（Python 计算）", "",
            "**⚠️ 不可计算**：需要本地 financials 数据源提供营收/净利同比等子项。", "",
        ]
    state = "" if growth["complete"] else f"（缺 {', '.join(growth['missing'])}，已按实际权重归一）"
    L = ["## 四、成长性（Python 计算）", "",
         f"**成长性：{growth['score']:.2f} / 10 —— {growth['rating']}**{state}", ""]
    L += ["| 子项 | 得分 | 依据 |", "|:--|--:|:--|"]
    for name, s in growth["sub_scores"].items():
        applied = "；".join(s.get("applied", [])) or "—"
        L.append(f"| {name} | {s['score']} | {applied} |")
    L.append("")
    return L


def _md_dimension(slot: Dict[str, Any], heading: str, intro: str = "") -> List[str]:
    """一个定性维度 = 一章。

    模板给这 6 个维度各留了一章（五~十），所以这里一章一个槽位，而不是把它们
    挤在一节里 —— 挤在一起时判分的人要在同一屏里来回对照锚点。
    """
    mark = "" if slot["has_anchor"] else " ⚠️ 无量化锚点"
    L = [f"## {heading}", ""]
    if intro:
        L += [intro, ""]
    L += [f"`{{{{{slot['dimension']}_score}}}}`{mark}", ""]
    if slot["anchors"]:
        L += ["量化锚点：", ""]
        for k, v in slot["anchors"].items():
            L.append(f"- `{k}` = {v}")
    else:
        L.append("- （无）")
    L += ["", f"分析：{{{{{slot['dimension']}_analysis}}}}", ""]
    return L


def _md_governance_facts(result: Dict[str, Any]) -> List[str]:
    """治理诚信事实（年报原文抽取）。放在风险评估章里 —— 它是治理风险的事实依据。"""
    facts = result.get("facts") or []
    if not facts:
        return []
    L = ["### 治理诚信事实（年报原文抽取）", "",
         "| 事实 | 值 | 来源页 | 原文 |", "|:--|:--|--:|:--|"]
    for f in facts:
        v = f.get("value_text") or (
            f"{f['value']:g} {f.get('unit') or ''}" if f.get("value") is not None else "-"
        )
        raw = (f.get("raw_text") or "").replace("|", "／")[:60]
        L.append(f"| {f['field']} | {v} | p{f.get('source_page', '-')} | {raw} |")
    L.append("")
    return L


def _md_margin(ext: Dict[str, Any]) -> List[str]:
    """两融数据（第九章的补充）。

    **只在真取到时渲染**：两融不是判分的必需锚点，缺了不该在报告里占一行
    「无数据」—— 它与「必须有的锚点缺失」不是一回事，只有后者需要显式标注
    （那些已经进了 missing_anchors 与附录）。到处都写「无数据」会把真正重要的
    缺失淹没掉。
    """
    rows = [r for r in ((ext.get("datacenter") or {}).get("margin_trading") or [])
            if r.get("date")]
    if not rows:
        return []
    rows.sort(key=lambda r: r["date"])
    latest = rows[-1]
    L = ["### 融资融券（两融）", "",
         f"> 数据日期：{latest['date']}（东财 datacenter-web，日级）", "",
         "| 指标 | 数值 |", "|:--|--:|"]
    for key, label in (("rzye", "融资余额"), ("rqye", "融券余额"), ("rzrqye", "两融合计")):
        v = latest.get(key)
        if isinstance(v, (int, float)):
            L.append(f"| {label} | {v / 1e8:,.2f} 亿 |")
    L.append("")

    # 窗口内的变化：单点余额看不出资金是在进还是在退，而判分要的正是方向。
    first = rows[0]
    if (len(rows) >= 2
            and isinstance(first.get("rzye"), (int, float))
            and isinstance(latest.get("rzye"), (int, float))):
        delta = latest["rzye"] - first["rzye"]
        L += [f"> 近 {len(rows)} 个交易日融资余额变化：**{delta / 1e8:+,.2f} 亿**"
              f"（{first['date']} → {latest['date']}）", ""]
    return L


def _md_northbound(ext: Dict[str, Any]) -> List[str]:
    """北向资金（第九章的补充）。

    只渲染**序列完整**的那一档读数；不完整的如实说明它不完整，而不是把残端当数字
    写出去。实测深股通只回传 35/262 个点、末值 379.75 亿 —— 那个量级对单日深股通
    净买入是异常的，而读者无从判断它是真的还是残缺序列的残端。

    与两融一样：完全取不到就**不渲染这一节**（它不是必需锚点）。但「取到了却不可信」
    必须说出来 —— 这两件事不同：前者是缺数据，后者是数据在骗人。
    """
    nb = ((ext.get("ths") or {}).get("northbound") or {})
    lanes = list((nb.get("lanes") or {}).values())
    if not lanes:
        return []

    usable = [l for l in lanes if l.get("usable")]
    unusable = [l for l in lanes if not l.get("usable")]
    L = ["### 北向资金（当日）", ""]

    if usable:
        L += [f"> 数据时点：{nb.get('as_of') or '—'}（同花顺，单位亿元）", ""]
        L += ["| 通道 | 最新累计净买入 | 序列覆盖 |", "|:--|--:|--:|"]
        for l in usable:
            cov = (l.get("coverage") or 0) * 100
            L.append(f"| {l['label']} | {l['latest']:+.2f} 亿 | {cov:.0f}% |")
        L.append("")
    else:
        L += ["**⚠️ 当日北向读数不可用**（没有一档的序列是完整的），故不给数字。", ""]

    for l in unusable:
        L += [f"- **{l['label']}**：{l.get('reason')} —— 不作为读数。", ""]

    L += [
        "> 上游背景：北向自 2024-08 起收紧盘中实时披露，同花顺这一路的数据完整性随之"
        "变差；东财系北向净买额同期也已断供。**权威日频数据在港交所（HKEX）官方日统计**，"
        "尚未接入。所以本节的数字只能当「当日情绪」，不能当持仓依据。",
        "",
    ]
    return L


def _md_news(ext: Dict[str, Any]) -> List[str]:
    """第二章的个股新闻 + 利好/利空判定槽位。

    Python 只提供新闻原文（标题/摘要/日期/来源），**分类由 AI 做** ——
    「利好还是利空」「影响多大」「可信度几星」都是判断而不是事实，用标题正则猜
    必然出错。这与七维定性维度是同一条分工：Python 锁数据，AI 做判断。

    四种状态分开说，因为它们的下一步动作完全不同：
    ① 有新闻 → 列表 + 判定槽位；② 被风控（只回 passportWeb）→ 说明是「没搜到」
    而不是「没有新闻」；③ 真没搜到 → 明说；④ 压根没取 external → 也说清，
    否则读者会以为这只票消息面平静。
    """
    if ext.get("news") is None:
        return [
            "### 个股新闻",
            "",
            "- （未取。需要 include_external；新闻源在东财搜索，是限速的按需请求。）",
            "",
        ]

    news = (ext.get("news") or {}).get("stock_news") or {}
    items = news.get("items") or []
    degraded = bool(news.get("degraded"))
    reason = news.get("reason") or ""

    L = ["### 个股新闻（近 20 条）", ""]
    if items:
        L += [f"> 共 {len(items)} 条，按东财搜索返回顺序。", ""]
        L += ["| 日期 | 来源 | 标题 |", "|:--|:--|:--|"]
        for n in items:
            title = (n.get("title") or "").replace("|", "／")
            url = n.get("url") or ""
            cell = f"[{title}]({url})" if url else title
            L.append(f"| {n.get('date') or '-'} | {(n.get('source') or '-')} | {cell} |")
        L.append("")
    elif degraded:
        L += [f"**⚠️ 本次未搜到新闻**：{reason}", ""]
    else:
        L += ["- （本次没有搜到该标的的新闻。）", ""]

    L += [
        "### 利好/利空判定（待填）",
        "",
        "**分类由 AI 做**：Python 只给上面的原文。「利好还是利空」「影响多大」"
        "「可信度几星」都是判断而不是事实，用标题正则猜必然出错。",
        "",
        "| # | 类型 | 事件 | 日期 | 影响 | 来源 | 可信度 |",
        "|:-:|:--|:--|:--|:--|:--|:--|",
        "{{positive_factors}}",
        "{{negative_factors}}",
        "",
        "> 可信度按来源给：公司公告 > 主流财经媒体 > 自媒体转载。只有单一来源的事件"
        "不要写成「已确认」；没有事件时留空，不要为了填满表格把旧闻当新闻。",
        "",
    ]
    return L


def _md_macro(ext: Dict[str, Any]) -> List[str]:
    """宏观环境锚点（第十章的补充）。

    PMI 与 LPR 是**全市场级**读数（不随标的变），给「宏观与市场风险」这一维提供
    量化依据。与两融/北向同一条规矩：完全取不到就不渲染这一节。

    PMI 必须给**荣枯线对比** —— 「50.1」这个数本身不说明任何事，50 以上还是
    以下是它的全部信息。LPR 必须给**变化方向** —— 单点利率看不出在宽松还是收紧。
    """
    macro = ((ext.get("macro") or {}).get("pmi") or {})
    lpr = ((ext.get("datacenter") or {}).get("lpr") or {})
    pmi_rows = [l for l in ("manufacturing", "non_manufacturing", "composite")
                if isinstance(macro.get(l), (int, float))]
    lpr_items = [i for i in (lpr.get("items") or []) if i.get("date")]
    # 取数失败（degraded）也要渲染：那正是降级标志存在的意义 —— 静默跳过会让
    # 读者以为「这一节本来就没有」，而实际是「取了但没解析出来」。
    if not pmi_rows and not lpr_items and not macro.get("degraded"):
        return []

    L = ["### 宏观环境（锚点）", ""]

    if pmi_rows:
        month = macro.get("month") or "—"
        L += [f"**采购经理指数（{month}）**　50 为荣枯线：", "",
              "| 指标 | 读数 | 相对荣枯线 |", "|:--|--:|:--|"]
        for key, label in (("manufacturing", "制造业"), ("non_manufacturing", "非制造业商务活动"),
                           ("composite", "综合产出")):
            v = macro.get(key)
            if not isinstance(v, (int, float)):
                continue
            L.append(f"| {label} | {v:.1f} | {'扩张' if v >= 50 else '收缩'}"
                     f"（{v - 50:+.1f}）|")
        L.append("")
        sizes = [(k, macro.get(k)) for k in ("large", "medium", "small")]
        sizes = [(k, v) for k, v in sizes if isinstance(v, (int, float))]
        if sizes:
            L += ["企业规模分档：" + "、".join(
                f"{ {'large': '大型', 'medium': '中型', 'small': '小型'}[k] } {v:.1f}"
                for k, v in sizes), ""]

    # 降级说明独立渲染，不挂在 pmi_rows 分支上：解析失败时恰恰没有 pmi_rows，
    # 挂在里面就等于「失败时反而什么都不说」。
    if macro.get("degraded"):
        L += [f"> ⚠️ PMI 未取到：{macro.get('reason') or '原因未知'}", ""]

    if lpr_items:
        lpr_items.sort(key=lambda i: i["date"])
        latest = lpr_items[-1]
        prev = lpr_items[-2] if len(lpr_items) >= 2 else None
        bits = [f"1 年期 {latest['lpr_1y']:.2f}%" if latest.get("lpr_1y") is not None else "1 年期 —"]
        if latest.get("lpr_5y") is not None:
            bits.append(f"5 年期 {latest['lpr_5y']:.2f}%")
        L += [f"**贷款市场报价利率（{latest['date']}）**：" + "、".join(bits), ""]
        if prev and latest.get("lpr_1y") is not None and prev.get("lpr_1y") is not None:
            delta = latest["lpr_1y"] - prev["lpr_1y"]
            trend = "持平" if abs(delta) < 1e-9 else ("下行（宽松）" if delta < 0 else "上行（收紧）")
            L += [f"> 较上一期（{prev['date']}）{delta:+.2f} 个百分点 —— {trend}。", ""]

    return L


def _md_valuation(ext: Dict[str, Any]) -> List[str]:
    """第十一章的估值分析（11.2）。

    一致预期 EPS 是**输入**，不是估值结论 —— 所以这里只给机构预期与区间，并把
    **机构数一起列出来**：1-2 家机构的「一致预期」不是一致预期，均值单独看会
    被当成权威数字。

    目标价 / DCF 仍未接入：那需要一个折现率假设与估值模型，属于判定而不是取数。
    这里不猜一个目标价出来 —— 缺什么就说什么。
    """
    eps = ((ext.get("ths-basic") or {}).get("consensus_eps") or {})
    items = eps.get("items") or []
    if not items and not eps.get("degraded"):
        return []

    L = ["### 11.2 估值分析（机构一致预期 EPS）", ""]
    if items:
        L += ["| 年度 | 预测机构数 | 一致预期 EPS | 区间（最小 ~ 最大） |",
              "|:--|--:|--:|:--|"]
        for i in items:
            mean = i.get("mean")
            lo, hi = i.get("low"), i.get("high")
            rng = f"{lo} ~ {hi}" if lo is not None and hi is not None else "—"
            L.append(f"| {i.get('year') or '—'} | {i.get('analysts') or '—'} | "
                     f"{mean if mean is not None else '—'} | {rng} |")
        L.append("")
        thin = [i for i in items
                if isinstance(i.get("analysts"), int) and i["analysts"] < 3]
        if thin:
            years = "、".join(str(i.get("year")) for i in thin)
            L += [f"> ⚠️ {years} 的预测机构数不足 3 家，那不是一致预期，只是个别机构"
                  f"的观点，不要当权威数字用。", ""]
    else:
        L += [f"- （未取到一致预期：{eps.get('reason') or '原因未知'}）", ""]

    L += [
        "> **目标价 / DCF 尚未接入**：一致预期 EPS 只是输入，估值还需要折现率假设"
        "与估值模型 —— 那是判定而不是取数，本报告不替你猜一个目标价。",
        "",
    ]
    return L


def _md_northbound_daily(result: Dict[str, Any]) -> List[str]:
    """沪深股通**日频**数据（港交所官方），第九章的补充。

    与 _md_northbound（同花顺当日分钟）互补：那个是「当日情绪」，这个是**权威日频**
    —— 北向自 2024-08 收紧盘中披露后，日频只剩港交所这一路。

    最有用的一条是**本标的是否在北向十大活跃股里**：外资当天大额买卖这只票是实打实
    的信号，而且该列表由港交所按成交额排，不含任何主观判断。
    """
    daily = ((result.get("external") or {}).get("hkex") or {}).get("northbound_daily") or {}
    markets = daily.get("markets") or {}
    if not markets and not daily.get("degraded"):
        return []

    L = ["### 沪深股通日频（港交所官方）", ""]
    if not markets:
        L += [f"- （未取到：{daily.get('reason') or '原因未知'}）", ""]
        return L

    north = {m: d for m, d in markets.items() if "Northbound" in m}
    if not north:
        L += ["- （当日没有北向数据。）", ""]
        return L

    as_of = daily.get("as_of") or "—"
    L += [f"> 数据日期：{as_of}（港交所每日统计，单位亿元）", "",
          "| 通道 | 成交额 |", "|:--|--:|"]
    total = 0.0
    for market, label in (("SSE Northbound", "沪股通"), ("SZSE Northbound", "深股通")):
        block = north.get(market) or {}
        value = (block.get("totals") or {}).get("Total Turnover")
        if isinstance(value, (int, float)):
            total += value
            L.append(f"| {label} | {value / 1e8:,.2f} |")
    if total:
        L.append(f"| **北向合计** | **{total / 1e8:,.2f}** |")
    L.append("")

    # 本标的是否在北向十大活跃股里 —— 这是本节最该被看到的信息。
    bare = str(result.get("thscode") or "").split(".")[0]
    hit = None
    rows = []
    for market, label in (("SSE Northbound", "沪股通"), ("SZSE Northbound", "深股通")):
        for a in (north.get(market) or {}).get("top10") or []:
            if a.get("code") == bare:
                hit = (label, a)
            rows.append((label, a))

    if hit:
        label, a = hit
        L += [f"**⚠️ 本标的出现在北向十大活跃股：{label}第 {a.get('rank')} 名，"
              f"成交 {((a.get('turnover') or 0) / 1e8):,.2f} 亿元。**", ""]

    if rows:
        L += ["**北向十大活跃股**（按成交额，★ 为本标的）：", "",
              "| 通道 | 排名 | 代码 | 名称 | 成交额(亿) |", "|:--|--:|:--|:--|--:|"]
        for label, a in rows:
            star = "★ " if a.get("code") == bare else ""
            turnover = a.get("turnover")
            cell = f"{turnover / 1e8:,.2f}" if isinstance(turnover, (int, float)) else "—"
            L.append(f"| {label} | {a.get('rank')} | {star}{a.get('code')} | "
                     f"{a.get('name')} | {cell} |")
        L.append("")
    return L


def _md_comprehensive(result: Dict[str, Any], slots: List[Dict[str, Any]]) -> List[str]:
    """第十一章：综合评估与投资建议。

    综合分不由本报告给出：它是 7 个定性维度收齐后由 Python 复算校验的
    （容差 0.05 且评级必须同档）。所以这里只列槽位与口径，不猜一个数字出来。
    """
    halo = result.get("halo") or {}
    growth = result.get("growth") or {}
    L = ["## 十一、综合评估与投资建议", "", "### 11.1 综合评分", "",
         "先给出下列维度的分数，再由 Python 按权重复算校验：", "",
         "| 维度 | 分数 | 来源 |", "|:--|--:|:--|"]
    for s in slots:
        L.append(f"| {s['label']} | {{{{{s['dimension']}_score}}}} | 待判 |")
    if halo.get("ok"):
        L.append(f"| HALO 六维 | {halo['score']:.2f} | Python 计算 |")
    if growth:
        L.append(f"| 成长性 | {growth['score']:.2f} | Python 计算 |")
    L += [
        "",
        "声明综合分：`{{comprehensive_score}}` 声明评级：`{{comprehensive_rating}}`",
        "",
        "> 复算容差 0.05，且声明评级必须与复算分同档。**用 halo.verify 提交，"
        "不要自己心算。**",
        "",
    ]
    L += _md_valuation(result.get("external") or {})
    L += [
        "### 11.3 尚未接入的章节",
        "",
        "下列模板章节需要本地没有的数据源，**本报告不生成它们**（宁缺勿造）：",
        "",
        "| 模板章节 | 缺什么 |",
        "|:--|:--|",
        "| 二、利好/利空**分类** | 分类已留给 AI；缺的是财联社等第二新闻源（当前只有东财） |",

        "| 十、政策与板块舆情风险 | 政策与舆情数据 |",
        "| 十一、目标价 / DCF | 一致预期 EPS + 估值模型 |",
        "",
    ]
    return L


def _md_missing_appendix(result: Dict[str, Any], slots: List[Dict[str, Any]]) -> List[str]:
    """附录：把缺失项汇总到一处。

    散在正文各处的 `⚠️` 读起来要来回翻；汇总一次，读者才能一眼判断「这份报告
    的可信边界在哪」。这也是「缺就标缺失」这条规矩的落地形式。
    """
    rows: List[str] = []
    halo = result.get("halo") or {}
    if not halo.get("ok"):
        rows.append(f"| HALO 六维 | {halo.get('reason') or '不可计算'} |")
    growth = result.get("growth")
    if not growth:
        rows.append("| 成长性 | 本地 financials 数据源不可用 |")
    elif not growth.get("complete"):
        rows.append(f"| 成长性子项 | 缺 {', '.join(growth['missing'])} |")
    for s in slots:
        if s["missing_anchors"]:
            rows.append(f"| {s['label']} 锚点 | 缺 {', '.join(s['missing_anchors'])} |")
    if not (result.get("announcements") or []):
        rows.append("| 近期公告 | 未取（include_announcements 未开启）或巨潮无数据 |")
    if result.get("external") is None:
        # 两融与北向的素材挂在 external 上。不取 external 时它们整节不渲染 ——
        # 但**必须说一句**，否则读者会以为这只票没有两融数据。
        rows.append("| 两融 / 北向资金 | 未取（include_external 未开启）|")

    L = ["## 附录：数据来源与缺失项汇总", "",
         "- **事实层**：巨潮年报 PDF 抽取（带来源页与原文）",
         "- **数值层**：本地 hithink 库（三表 / 估值 / 行情）",
         "- **公告层**：巨潮全类型公告流",
         "- **判定层**：定性维度由人工/模型判分，Python 只负责复算校验", ""]
    if rows:
        L += ["### 本次缺失", "", "| 项 | 说明 |", "|:--|:--|"] + rows + [""]
    else:
        L += ["### 本次缺失", "", "- （无：所有必需输入都已取到。）", ""]
    L += [
        "---",
        "",
        "*本报告由 Python 锁定数据层，分析层待填。数据缺失项已在正文标注，不作估算。*",
    ]
    return L


def render_markdown(result: Dict[str, Any]) -> str:
    """预渲染报告骨架，章节形状对齐 halo-skill 的 V5.0 模板。

    分三档，写法上刻意区分开：
    * **确定值**（HALO 六维、成长性）直接填；
    * **待判分**的定性维度各占一章，锚点写在旁边，分数留 `{{xxx_score}}` 槽位；
    * **拿不到**的写清缺什么，并汇总进附录 —— 不留空、不填 0、不外推。

    章节顺序与模板一致（第零章执行摘要 → 一公司概况 → 二消息面 → 三 HALO →
    四成长性 → 五~十 六个定性维度 → 十一综合评估 → 附录），这样从模板抄下来的
    阅读习惯与批注位置能直接复用。
    """
    slots = result.get("ai_slots") or []
    by_dim = {s["dimension"]: s for s in slots}

    L: List[str] = []
    L += _md_header(result)
    L += _md_score_card(result, slots)
    L += _md_company_profile(result)
    L += _md_announcements(result)
    L += _md_halo(result)
    L += _md_growth(result)

    # 五~十：六个定性维度各一章。估值那一维不在这里 —— 模板把它放在第十一章。
    chapters = [
        ("moat", "五、低淘汰率", "壁垒能否穿越技术迭代与竞争，决定长期持有价值。"),
        ("stag", "六、滞胀防御", "实物资产与转嫁能力决定通胀环境下的相对表现。"),
        ("esg", "七、ESG", "环境披露与人均产出，衡量可持续性与合规风险。"),
        ("management", "八、管理层质量", "资本配置与回报水平反映管理层质量。"),
        ("shareholder", "九、股东与资金面", "股东结构与资金流向反映筹码稳定性。"),
        ("risk", "十、风险评估", "硬风险事实优先于财务比率 —— 处罚与内控非标是"
                                 "已经发生的事，比率只是征兆。"),
    ]
    for dim, heading, intro in chapters:
        slot = by_dim.get(dim)
        if slot is None:
            L += [f"## {heading}", "", "- （该维度未在本次分析中生成槽位。）", ""]
            continue
        L += _md_dimension(slot, heading, intro)
        if dim == "shareholder":
            # 两融与北向都是股东资金面这一章的事实素材，但**都不是**必需锚点 ——
            # 取不到就不渲染（见 _md_margin / _md_northbound 的说明）。
            ext = result.get("external") or {}
            L += _md_margin(ext)
            L += _md_northbound(ext)
            L += _md_northbound_daily(result)
        if dim == "risk":
            # 宏观锚点放在风险章里：它服务的是「宏观与市场风险」那一维。
            L += _md_macro(result.get("external") or {})
            L += _md_governance_facts(result)

    L += _md_comprehensive(result, slots)
    L += _md_missing_appendix(result, slots)
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
            # 两融在**稳定档**（datacenter-web），不是易封的 push2 系 ——
            # 所以它跟股东户数一起放在这一桶，不受资金流那档封禁影响。
            "margin_trading": extdata.margin_trading(code, limit=10),
            # LPR 也在这张报表体系里（同一稳定档），所以并进这一桶 ——
            # 它是月频数据，跟股东户数一样属于「慢变量」。
            "lpr": extdata.lpr_latest(limit=12),
        }),
        ("reportapi", "third", lambda: {
            "research_reports": extdata.research_reports(code, limit=8),
        }),
    ]
    # 北向资金单独一档：它是**另一个厂商**（同花顺），反爬与东财的 WAF 无关，
    # 混进东财任一档都会让两边的限速互相牵制。
    buckets.append(
        ("ths", "independent", lambda: {"northbound": extdata.northbound_summary()})
    )
    # 个股新闻也单独一档：主机不同（search-api-web），而且它**实测过间歇风控** ——
    # 并进 datacenter 会让风控期间的限速拖慢稳定的股东户数/两融。
    buckets.append(
        ("news", "independent", lambda: {"stock_news": extdata.stock_news(code, limit=20)})
    )
    # 统计局单独一档：政府站点、月频数据，限速给到 3s。它是**全市场级**的宏观
    # 读数（不随 code 变），但每份报告都要用到，所以跟着一起取。
    buckets.append(
        ("macro", "independent", lambda: {"pmi": extdata.nbs_pmi()})
    )
    # 一致预期 EPS 也在同花顺（basic 主机，与北向的 data 主机不同档）。
    # 它是模板 11.3 估值分析的输入，但**不随 code 之外的参数变化**。
    buckets.append(
        ("ths-basic", "independent", lambda: {"consensus_eps": extdata.consensus_eps(code)})
    )
    # 港交所北向日频。**不随标的变**（全市场级），但每份报告都要用到。
    buckets.append(
        ("hkex", "independent", lambda: {"northbound_daily": extdata.hkex_northbound()})
    )
    if with_fund_flow:
        buckets.append(
            ("push2his", "volatile", lambda: {"fund_flow": extdata.fund_flow(code, days=60)})
        )
    for name, tier, fn in buckets:
        try:
            out[name] = fn()
            # ok 必须反映「取到了没有」，而不是「这次调用有没有抛」。
            #
            # 子域失败时 fetch 是**降级成空值**而不是抛异常的（见 extdata 各函数），
            # 而 bool({"fund_flow": []}) 是 True —— 只看容器非空会把「被封禁」
            # 报成 ok。这是实测出来的：push2his 被判 IP 级封禁的那次，
            # subdomains.push2his.ok 报的是 True，与这一栏存在的意义正好相反。
            out["subdomains"][name] = {
                "tier": tier,
                "ok": any(bool(v) for v in (out[name] or {}).values()),
            }
        except Exception as exc:  # noqa: BLE001 —— 单档失败不牵连其它档
            out["errors"][name] = str(exc)[:200]
            out["subdomains"][name] = {"tier": tier, "ok": False}
    return out


async def _fetch_announcements(thscode: str) -> List[Dict[str, Any]]:
    """取近期全类型公告（公告流）。

    只取**一页**（``CNINFO_MAX_PAGE_SIZE`` 条）。这是刻意的：巨潮会封 IP，而公告
    在报告里只是「消息面」的背景，不值得为它多翻几页。``recent_announcements``
    的循环条件在取数**之前**判断，所以 max_items 正好等于单页上限时只发一次请求。

    失败一律降级成空列表：拿不到公告只是少一节，不该让整份报告失败 —— 与
    ``fetch_external`` 按子域降级是同一条规矩。

    这里不缓存。公告是每日新增的，缓存要处理失效；而报告生成本就是低频动作
    （且归档路径已经由「先同步年报」把关），为它引入一层缓存得不偿失。
    """
    from .cninfo_source import CNINFO_MAX_PAGE_SIZE, CninfoSource

    bare = thscode.split(".")[0]
    try:
        source = CninfoSource()
        rows = await asyncio.to_thread(
            source.recent_announcements, bare, max_items=CNINFO_MAX_PAGE_SIZE
        )
    except Exception as exc:  # noqa: BLE001 —— 降级优先于报错
        logger.warning("取公告失败 %s: %s", thscode, exc)
        return []
    return [
        {
            "title": a.title,
            "date": a.date,
            "doc_type": a.doc_type,
            "detail_url": a.detail_url,
            "pdf_url": a.pdf_url,
        }
        for a in rows
    ]


async def analyze(
    thscode: str,
    *,
    store: FactStore,
    period: Optional[str] = None,
    report_type: str = "annual",
    scope: str = SCOPE_CONSOLIDATED,
    pages: Optional[ExtractResult] = None,
    include_external: bool = False,
    include_announcements: bool = False,
) -> Dict[str, Any]:
    """对一只股票出评分结果。

    Args:
        include_external: 额外拉外网数据（治理/估值分位/研报）。默认关闭 ——
            分析是按需行为，外网慢且会封 IP，需要时再显式开启。
        include_announcements: 额外取近期全类型公告（巨潮，一次请求）。默认关闭，
            理由同上：巨潮同样限速，公告是归档/出报告时才需要的东西，不该让每次
            分析都多打一次外网。
    """
    thscode = normalize_thscode(thscode)
    # 空串必须当成「没指定」，不能只判 None。
    #
    # 这是实测过的线上失败：调用方（Go 工具、curl、任何客户端）把可选参数序列化
    # 成 "" 而不是省略时，period 就不是 None，于是跳过「取最新期次」的解析，一路
    # 带到 _fetch_financials 的 `int(period[:4])` —— 在那里抛
    # `ValueError: invalid literal for int() with base 10: ''`，整个端点 500。
    # 而且它**只在 financials 数据源就绪时才炸**：数据源缺失时 _fetch_financials
    # 提前返回，于是本地无库的环境测不出来。
    if not period:
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
            # 恒存在：结构完整是早返回路径的既有承诺（缺键会让调用方在 KeyError
            # 上崩，而不是读到「暂无数据」）。这里不真去取公告 —— 没有年报事实时
            # 报告本来就不会被归档，为它多打一次巨潮不值得。
            "announcements": [],
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

    # 外网数据必须在构造槽位**之前**拿到：main_fund_flow 是 AI_DIMENSIONS 声明的
    # 锚点名，而它只有 push2his 一条来源（本地库和年报事实都没有）。挪到前面不增加
    # 任何请求 —— 就是原来那个调用，只是提前。
    external: Optional[Dict[str, Any]] = None
    if include_external:
        external = await fetch_external(thscode)

    # 公告与外部数据一样是**按需**取的：巨潮限速，公告只在出报告/归档时需要。
    announcements: List[Dict[str, Any]] = []
    if include_announcements:
        announcements = await _fetch_announcements(thscode)

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
        "ai_slots": build_ai_slots(facts, financial, ind, external),
        # 恒存在（未取时为空列表）：缺键会让 render_markdown 与下游调用方在
        # KeyError 上崩，而不是渲染出「没有公告」这一正常情况。
        "announcements": announcements,
    }
    if fact_records:
        result["fresh_facts"] = [f["field"] for f in fact_records]
    if external is not None:
        result["external"] = external
        result["narratives"]["research_reports"] = [
            r for r in (external.get("reportapi") or {}).get("research_reports", [])
        ]
    result["markdown"] = render_markdown(result)
    return result
