"""外网数据项：治理/风险硬信号、估值分位、资金流、研报。

每个取数函数**失败时返回空**，不抛异常：外网是辅助来源，不该让评分链路因为
一个取不到的字段整体失败。取不到就是取不到，如实缺失比编造强。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .external import Subdomain, datacenter, fetch_json, fetch_text, ExternalError

logger = logging.getLogger(__name__)

# 估值分位的四类指标。东财的 INDICATOR_TYPE 没有文档，靠**与本地快照对账**
# 确认的映射：本地 v_valuation_latest 的 pe_ttm / pb_mrq / ps_ttm / pcf_ttm
# 与 type 1/2/3/4 的 INDEX_VALUE 逐位一致。
VALUATION_TYPES = {
    "pe_ttm": "1",
    "pb": "2",
    "ps": "3",
    "pcf": "4",
}

# 研报评级 → 归一化。评级是第三方观点，不能当事实，但作为定性锚点有价值。
# 目标价字段不接入：实测东财 reportapi 返回的「目标价」实为未来两年 EPS 预测
# （茅台 71~83 对应的是 EPS 量级而非 1680 元的股价），语义不符，接了会误导。
_RATING_KEYWORDS = (
    ("买入", ("买入", "强烈推荐", "推荐", "增持", "优于大市", "跑赢行业", "outperform", "buy")),
    ("中性", ("中性", "持有", "同步大市", "观望", "neutral", "hold")),
    ("减持", ("减持", "卖出", "跑输行业", "underperform", "sell")),
)


def _bare(code: str) -> str:
    """取**纯 6 位**代码：东财报表的 SECURITY_CODE 与同花顺 F10 的 URL 路径都要它。

    与本项目其它地方「thscode 统一存带后缀」的约定相反：本地 DuckDB 要带后缀，
    这些外网接口不要。传错**不报错**，只是安静地查不到任何行 —— 所以归一化必须
    在这里做，而不是指望每个调用方都记得。

    支持三种写法：``600519`` / ``600519.SH``（后缀）/ ``SH600519``（前缀）。
    原先只做 ``split(".")[0]``，于是前缀写法会原样传下去并查空。
    """
    text = code or ""
    match = re.search(r"\d{6}", text)
    if match:
        return match.group(0)
    return text.split(".")[0]


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN 过滤


# ---------------------------------------------------------------------------
# 估值分位（datacenter-web，稳定档）
# ---------------------------------------------------------------------------


def valuation_percentiles(code: str) -> Dict[str, Dict[str, Any]]:
    """PE/PB/PS/PCF 的历史分位。"""
    out: Dict[str, Dict[str, Any]] = {}
    for name, typ in VALUATION_TYPES.items():
        rows = datacenter(
            "RPT_VALUATIONSTATUS",
            f'(SECURITY_CODE="{_bare(code)}")(INDICATOR_TYPE="{typ}")',
            size=1, sort=("TRADE_DATE", "-1"),
        )
        if not rows:
            continue
        r = rows[0]
        out[name] = {
            "value": _f(r.get("INDEX_VALUE")),
            "percentile": _f(r.get("INDEX_PERCENTILE")),
            "status": r.get("VALATION_STATUS"),
            "as_of": r.get("TRADE_DATE"),
        }
    return out


# ---------------------------------------------------------------------------
# 股东户数（datacenter-web）
# ---------------------------------------------------------------------------


def holder_count(code: str) -> Dict[str, Any]:
    rows = datacenter(
        "RPT_HOLDERNUMLATEST", f'(SECURITY_CODE="{_bare(code)}")',
        size=1, sort=("END_DATE", "-1"),
    )
    if not rows:
        return {}
    r = rows[0]
    return {
        "holder_num": _f(r.get("HOLDER_NUM")),
        "avg_market_cap": _f(r.get("AVG_MARKET_CAP")),
        "as_of": r.get("END_DATE"),
    }


# ---------------------------------------------------------------------------
# 治理与风险硬信号（datacenter-web）
# ---------------------------------------------------------------------------


def equity_pledge(code: str) -> Dict[str, Any]:
    """股权质押比例。**高质押是强风险信号**，可复现且不依赖判断。"""
    rows = datacenter(
        "RPT_CSDC_LIST", f'(SECURITY_CODE="{_bare(code)}")',
        size=1, sort=("TRADE_DATE", "-1"),
    )
    if not rows:
        return {}
    r = rows[0]
    return {
        "pledge_ratio": _f(r.get("PLEDGE_RATIO")),
        "repurchase_balance": _f(r.get("REPURCHASE_BALANCE")),
        "as_of": r.get("TRADE_DATE"),
    }


def holder_trades(code: str, limit: int = 5) -> List[Dict[str, Any]]:
    """股东增减持记录。"""
    rows = datacenter(
        "RPT_SHARE_HOLDER_INCREASE", f'(SECURITY_CODE="{_bare(code)}")',
        size=limit, sort=("NOTICE_DATE", "-1"),
    )
    return [
        {
            "date": r.get("NOTICE_DATE"),
            "holder": r.get("HOLDER_NAME"),
            "change_num": _f(r.get("CHANGE_NUM")),
            "change_rate": _f(r.get("CHANGE_RATE")),
            "after_rate": _f(r.get("AFTER_CHANGE_RATE")),
        }
        for r in rows
    ]


def earnings_forecast(code: str, limit: int = 3) -> List[Dict[str, Any]]:
    rows = datacenter(
        "RPT_PUBLIC_OP_NEWPREDICT", f'(SECURITY_CODE="{_bare(code)}")',
        size=limit, sort=("NOTICE_DATE", "-1"),
    )
    return [
        {
            "date": r.get("NOTICE_DATE"),
            "report_period": r.get("REPORT_DATE"),
            "kind": r.get("PREDICT_FINANCE_CODE"),
            "amount": r.get("PREDICT_FINANCE"),
            "reason": (r.get("PREDICT_REASON") or "")[:200],
        }
        for r in rows
    ]


def institution_surveys(code: str, limit: int = 3) -> List[Dict[str, Any]]:
    rows = datacenter(
        "RPT_ORG_SURVEYNEW", f'(SECURITY_CODE="{_bare(code)}")',
        size=limit, sort=("NOTICE_DATE", "-1"),
    )
    return [
        {
            "date": r.get("NOTICE_DATE"),
            "org": r.get("OPERATEDEPT_NAME"),
            "person": r.get("RECEIVE_PERSON"),
            "type": r.get("RECEPTION_TYPE"),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 资金流（push2his，易封档）
# ---------------------------------------------------------------------------


def fund_flow(code: str, days: int = 60) -> List[Dict[str, Any]]:
    """近 N 日资金流。

    字段对应东财 daykline 的 f51~f55：日期 / 主力净额 / 小单 / 中单 / 大单。
    主力 = 大单 + 超大单，本接口未取 f56（超大单），所以**只返回各档原值**，
    不自行合并出「主力」—— 少一个档位就合并，等于用错误的口径做加法。
    """
    bare = _bare(code)
    secid = ("1." if bare[:1] in "56" else "0.") + bare
    try:
        resp = fetch_json(
            Subdomain.PUSH2HIS,
            "/api/qt/stock/fflow/daykline/get",
            {
                "lmt": str(days), "klt": "101", "secid": secid,
                "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55",
            },
        )
    except ExternalError as exc:
        logger.warning("资金流取数失败 %s: %s", code, exc)
        return []
    lines = ((resp or {}).get("data") or {}).get("klines") or []
    out = []
    for ln in lines:
        parts = (ln or "").split(",")
        if len(parts) < 5:
            continue
        out.append({
            "date": parts[0],
            "main_net": _f(parts[1]),
            "small_net": _f(parts[2]),
            "medium_net": _f(parts[3]),
            "large_net": _f(parts[4]),
        })
    return out


def margin_trading(code: str, limit: int = 10) -> List[Dict[str, Any]]:
    """融资融券明细（日级，datacenter-web 稳定档）。

    字段是东财 RPTA_WEB_RZRQ_GGMX 的原值，单位**元**，这里不做亿元换算 ——
    换算是展示层的事，取数层多一次换算就多一个口径。

    与 ``fund_flow`` 一样只返回原值、不自行派生「净买入」之类的合成指标：
    接口给了 rzmre（买入）与 rzche（偿还），相减是调用方的选择，不是取数层的。
    """
    rows = datacenter(
        "RPTA_WEB_RZRQ_GGMX", f'(SCODE="{_bare(code)}")',
        size=limit, sort=("DATE", "-1"),
    )
    out = []
    for r in rows:
        out.append({
            "date": str(r.get("DATE") or "")[:10],
            "rzye": _f(r.get("RZYE")),      # 融资余额
            "rzmre": _f(r.get("RZMRE")),    # 融资买入额
            "rzche": _f(r.get("RZCHE")),    # 融资偿还额
            "rqye": _f(r.get("RQYE")),      # 融券余额
            "rzrqye": _f(r.get("RZRQYE")),  # 融资融券余额合计
        })
    return out


# ---------------------------------------------------------------------------
# 北向资金（同花顺 data.hexin.cn）
# ---------------------------------------------------------------------------

#: 序列完整性的下限。实测深股通只回传 35/262 个点，而沪股通是满的 —— 用
#: **覆盖率**而不是硬编码「sgt 不可信」，这样上游恢复时判断会自动跟上，
#: 也不会把某个档位永久钉死。
_NORTHBOUND_MIN_COVERAGE = 0.8


def hsgt_realtime() -> Dict[str, Any]:
    """沪深股通当日实时分钟流向。单位**亿元**，原样返回，不做加工。

    返回 ``{"time": [...], "hgt": [...], "sgt": [...]}``。

    这里**不判断完整性**：「多少个点算完整」是解读问题而不是取数问题，交给
    :func:`northbound_summary`。上游背景（a-stock-data 的实测记录，2026-07）：
    北向自 2024-08 起收紧盘中实时披露，深股通常只回传零星几个点且末值量级异常。
    本函数如实返回收到的内容，由调用方决定能不能用。
    """
    try:
        data = fetch_json(Subdomain.THS_HSGT)
    except ExternalError as exc:
        logger.warning("北向资金取数失败: %s", exc)
        return {"time": [], "hgt": [], "sgt": []}
    if not isinstance(data, dict):
        return {"time": [], "hgt": [], "sgt": []}
    return {
        "time": list(data.get("time") or []),
        "hgt": list(data.get("hgt") or []),
        "sgt": list(data.get("sgt") or []),
    }


def northbound_summary() -> Dict[str, Any]:
    """当日北向资金读数：**只把序列完整的那一档当作可用**。

    为什么不直接取末值：实测（2026-10）深股通只回传 35/262 个点、末值 379.75
    亿元 —— 那个量级对单日深股通净买入是异常的。把这种数字当读数渲染出去比不渲染
    更糟：读者无从判断它是真的还是残缺序列的残端。

    所以每一档都带 ``usable`` 与 ``reason``，由渲染层决定怎么说。
    """
    raw = hsgt_realtime()
    times = raw.get("time") or []
    total = len(times)
    lanes: Dict[str, Any] = {}
    for key, label in (("hgt", "沪股通"), ("sgt", "深股通")):
        series = raw.get(key) or []
        vals = [v for v in series if isinstance(v, (int, float))]
        coverage = (len(series) / total) if total else 0.0
        usable = total > 0 and coverage >= _NORTHBOUND_MIN_COVERAGE and bool(vals)
        lanes[key] = {
            "label": label,
            "latest": vals[-1] if vals else None,
            "coverage": round(coverage, 3),
            "points": len(series),
            "usable": usable,
            "reason": "" if usable else (
                f"仅回传 {len(series)}/{total} 个点，序列不完整，末值不可采信"
                if total else "未取到数据"
            ),
        }
    return {"as_of": times[-1] if times else None, "points": total, "lanes": lanes}


# ---------------------------------------------------------------------------
# 个股新闻（东财搜索，JSONP）
# ---------------------------------------------------------------------------


def _strip_tags(text: Any) -> str:
    """去掉新闻标题/摘要里的高亮标签。

    东财在命中关键词处插 ``<em>``，直接渲染会把标签当正文显示出来。
    """
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def stock_news(code: str, limit: int = 20) -> Dict[str, Any]:
    """东财个股新闻。

    返回 ``{"items": [...], "degraded": bool, "reason": str}``。

    ``degraded`` 是实测过的**间歇风控**：部分住宅 IP 调本接口只拿到 passportWeb
    （股民资料）而无 cmsArticleWebOld（文章列表）。那不是「这只票没有新闻」，而是
    「这次没搜到」—— 两者必须能区分，否则报告会把风控说成「消息面平静」。

    接口是 JSONP（``cb=jQuery_news``）；括号包装的解析由 fetch_json 统一处理，
    这里不必自己剥。
    """
    inner = json.dumps({
        "uid": "", "keyword": _bare(code), "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": limit, "preTag": "", "postTag": "",
        }},
    }, separators=(",", ":"))

    try:
        resp = fetch_json(Subdomain.EM_SEARCH,
                          params={"cb": "jQuery_news", "param": inner})
    except ExternalError as exc:
        logger.warning("个股新闻取数失败 %s: %s", code, exc)
        return {"items": [], "degraded": True, "reason": f"取数失败：{exc}"}

    result = (resp or {}).get("result") or {}
    articles = result.get("cmsArticleWebOld") or []
    if not articles:
        keys = sorted(result.keys())
        if "cmsArticleWebOld" not in keys:
            # 风控指纹：回了别的板块（通常 passportWeb）却没有文章列表。
            return {
                "items": [], "degraded": True,
                "reason": f"接口只返回 {keys or '空结果'}，未含文章列表"
                          f"（东财对部分 IP 的间歇风控，非「没有新闻」）",
            }
        return {"items": [], "degraded": False, "reason": ""}

    items = [{
        "title": _strip_tags(a.get("title")),
        "summary": _strip_tags(a.get("content"))[:200],
        "date": str(a.get("date") or "")[:16],
        "source": str(a.get("mediaName") or ""),
        "url": str(a.get("url") or ""),
    } for a in articles]
    return {"items": items, "degraded": False, "reason": ""}


# ---------------------------------------------------------------------------
# 宏观（LPR / PMI）
# ---------------------------------------------------------------------------


def lpr_latest(limit: int = 12) -> Dict[str, Any]:
    """贷款市场报价利率（LPR）最近若干期。单位 %。

    走 datacenter-web 的 ``RPTA_WEB_RATE`` 报表 —— 结构化接口，比抓页面稳得多，
    而且直接复用既有的稳定档限速。

    同一报表里混着 2019-08 改革前的**旧贷款基准利率**调整行（LPR 字段为空），
    按 LPR1Y 非空过滤掉。5 年期品种 2019-08-20 才设立，更早期次的 ``lpr_5y``
    为 None —— 那是「当时没有这个品种」，不是「没取到」。
    """
    rows = datacenter("RPTA_WEB_RATE", "", size=limit,
                      sort=("TRADE_DATE", "-1"),
                      columns="TRADE_DATE,LPR1Y,LPR5Y")
    items = []
    for r in rows:
        if r.get("LPR1Y") is None:
            continue          # 旧基准利率行，不是 LPR
        items.append({
            "date": str(r.get("TRADE_DATE") or "")[:10],
            "lpr_1y": _f(r.get("LPR1Y")),
            "lpr_5y": _f(r.get("LPR5Y")),
        })
    return {"items": items, "source": "eastmoney RPTA_WEB_RATE"}


#: 统计局正文里的空白必须**整个删掉**再匹配：正文用全角括号且括号内带空格
#: （`（ PMI ）为 49.2%`）。只压成单个空格会一条都匹配不到。
_NBS_SPACE = re.compile(r"[\s\u3000\xa0]+")


def nbs_pmi() -> Dict[str, Any]:
    """国家统计局最新一期采购经理指数（PMI）。单位 %，50 是荣枯线。

    返回 ``{"month", "manufacturing", "non_manufacturing", "composite",
    "large", "medium", "small", "degraded", "reason"}``。

    **为什么是抓页面**：统计局没有结构化接口。这里只取三个主指标（制造业 /
    非制造业商务活动 / 综合产出）—— 实测它们版式稳定；企业规模分档统计局用过
    三种措辞，解析不到就留 None（可选字段，不为了凑数去猜）。

    月频数据，月末发布，比上市公司财报早一个季度反映景气。
    """
    try:
        index_html = fetch_text(Subdomain.NBS)
    except ExternalError as exc:
        logger.warning("统计局索引页取数失败：%s", exc)
        return {"degraded": True, "reason": f"统计局索引页取数失败：{exc}"}

    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>\s*([^<]{6,80}?)\s*</a>', index_html)
    hit = next(((u, t) for u, t in links if "采购经理指数" in t), None)
    if not hit:
        return {"degraded": True, "reason": "统计局最新发布页未找到「采购经理指数」条目"}
    href, title = hit

    try:
        html = fetch_text(Subdomain.NBS, href.lstrip("./"))
    except ExternalError as exc:
        logger.warning("统计局 PMI 正文取数失败：%s", exc)
        return {"degraded": True, "reason": f"PMI 正文取数失败：{exc}"}

    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S)
    text = _NBS_SPACE.sub("", re.sub(r"<[^>]+>", "", text))

    def grab(pattern: str) -> Optional[float]:
        m = re.search(pattern, text)
        return float(m.group(1)) if m else None

    out: Dict[str, Any] = {
        "manufacturing": grab(r"制造业采购经理指数[（(]PMI[)）]为([\d.]+)%"),
        "non_manufacturing": grab(r"非制造业商务活动指数为([\d.]+)%"),
        "composite": grab(r"综合PMI产出指数为([\d.]+)%"),
        "large": grab(r"大型企业PMI为([\d.]+)%"),
        "medium": grab(r"中型企业PMI为([\d.]+)%"),
        "small": grab(r"小型企业PMI为([\d.]+)%"),
    }
    # 统计局用过三种版式，逐层回退。注意「半拆」那条必须带「分别为」—— 否则
    # 「中、小型企业PMI分别为」会被误当成单条「中型企业PMI为」。
    if out["medium"] is None or out["small"] is None:
        m = re.search(r"大、中、小型企业PMI分别为([\d.]+)%、([\d.]+)%和([\d.]+)%", text)
        if m:                       # ① 全合并
            out["large"], out["medium"], out["small"] = (float(x) for x in m.groups())
        else:
            m = re.search(r"中、小型企业PMI分别为([\d.]+)%和([\d.]+)%", text)
            if m:                   # ② 半拆（大型单独给）
                out["medium"], out["small"] = (float(x) for x in m.groups())

    ym = re.search(r"(\d{4})年(\d{1,2})月", title)
    out["month"] = f"{ym.group(1)}-{int(ym.group(2)):02d}" if ym else None
    out["degraded"] = out["manufacturing"] is None
    out["reason"] = "" if not out["degraded"] else "正文里没有解析出制造业 PMI"
    return out


# ---------------------------------------------------------------------------
# 机构一致预期 EPS（同花顺 F10）
# ---------------------------------------------------------------------------


def consensus_eps(code: str, max_years: int = 4) -> Dict[str, Any]:
    """机构一致预期 EPS（同花顺 F10 的 ``worth.html``）。

    返回 ``{"items": [{"year", "analysts", "low", "mean", "high", "industry_avg"}],
    "degraded": bool, "reason": str}``。

    三个坑，都实测过：
    * URL 路径只认**纯 6 位**代码 —— 带 SH/SZ 前缀不报错，安静地 404 到空表；
    * 页面是 **GBK**，必须指定编码：按 UTF-8 解会把表头（「年度」「预测机构数」）
      整片吞掉而数字还在，表现为「表能读到但列名对不上」，比整页失败更难查；
    * 用正则解析表格而**不引入 lxml/bs4** —— ``pd.read_html`` 会把「零依赖服务」
      这条性质破坏掉，而这个表的结构足够简单（一列一个值）。

    ``analysts`` 必须一起给出来：1-2 家机构的「一致预期」不是一致预期。渲染层据此
    标注可信度，而不是把均值当权威数字。
    """
    try:
        html = fetch_text(Subdomain.THS_BASIC, f"{_bare(code)}/worth.html", encoding="gbk")
    except ExternalError as exc:
        logger.warning("一致预期取数失败 %s: %s", code, exc)
        return {"items": [], "degraded": True, "reason": f"取数失败：{exc}"}

    target = None
    for table in re.findall(r"<table[^>]*>.*?</table>", html, re.S):
        if "预测机构数" in table or "每股收益" in table:
            target = table
            break
    if target is None:
        return {"items": [], "degraded": True,
                "reason": "页面里没有找到一致预期表（可能已改版）"}

    items = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", target, re.S):
        cells = [_strip_tags(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        # 表头行第一格是「年度」，用四位年份把它与数据行分开。
        if len(cells) < 5 or not re.fullmatch(r"\d{4}", cells[0]):
            continue
        analysts = None
        try:
            analysts = int(re.sub(r"[^\d]", "", cells[1]))
        except (TypeError, ValueError):
            pass
        items.append({
            "year": cells[0],
            "analysts": analysts,
            "low": _f(cells[2]),
            "mean": _f(cells[3]),
            "high": _f(cells[4]),
            "industry_avg": _f(cells[5]) if len(cells) > 5 else None,
        })

    if not items:
        return {"items": [], "degraded": True,
                "reason": "一致预期表里没有解析出任何年份行"}
    return {"items": items[:max_years], "degraded": False, "reason": ""}


# ---------------------------------------------------------------------------
# 沪深股通日频（港交所官方每日统计）
# ---------------------------------------------------------------------------

#: 港交所对「不可用」用的哨兵值。每日额度余额（DQB）未披露时填 999,999,999 ——
#: 那不是「额度还剩 999,999,999 百万」，而是「没有」。naive 解析会把它当读数
#: 渲染出去，所以必须在取数层就换成 None。
_HKEX_SENTINEL = 999_999_999


def _hkex_number(raw: Any) -> Optional[float]:
    """港交所单元格 → 数字；哨兵值与畸形值都返回 None。"""
    text = str(raw if raw is not None else "").replace(",", "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return None if value == _HKEX_SENTINEL else value


def _parse_hkex_js(text: str) -> List[Dict[str, Any]]:
    """把 ``tabData = [...]`` 解成 Python 结构。文件带 BOM，且是 JS 字面量不是 JSON。"""
    body = (text or "").lstrip("\ufeff").strip()
    start, end = body.find("["), body.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(body[start:end + 1])
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def hkex_northbound(max_lookback: int = 7) -> Dict[str, Any]:
    """沪深股通**日频**数据（港交所官方每日统计）—— 北向的权威源。

    北向自 2024-08 收紧盘中披露后，同花顺的分钟序列只剩「当日情绪」；权威日频在
    港交所，这就是那个源。

    文件是 ``data_tab_daily_YYYYMMDDc.js``，**按日期逐个回退**找最近一个有数据的
    交易日（周末与假期当天没有文件）。周末直接跳过，不浪费一次请求。

    两个实测过的坑：
    * **同一文件里单位不统一**：``tradingTable`` 的成交额是**百万元**，而
      ``top10Table`` 是**元**。这里统一换算成**元**再交给调用方 —— 换算只在取数层
      做一次，别让每个调用方各换一次。
    * **哨兵值 999,999,999**（见 ``_HKEX_SENTINEL``）。

    取不到时返回 ``{"degraded": True, "reason": ...}``，不返回半份数据。
    """
    today = date.today()
    for back in range(max_lookback):
        day = today - timedelta(days=back)
        if day.weekday() >= 5:            # 周六日没有文件，别浪费请求
            continue
        try:
            text = fetch_text(Subdomain.HKEX, f"data_tab_daily_{day.strftime('%Y%m%d')}c.js")
        except ExternalError:
            continue
        blocks = [b for b in _parse_hkex_js(text) if b.get("tradingDay") == 1]
        if _has_northbound_turnover(blocks):
            return _summarize_hkex(blocks)
    return {"degraded": True,
            "reason": f"最近 {max_lookback} 天没有港交所北向成交额"
                      f"（假期文件存在但北向 tradingDay=0、数值为「-」，已跳过）",
            "as_of": None, "markets": {}}


def _has_northbound_turnover(blocks: List[Dict[str, Any]]) -> bool:
    """至少要有一个**北向**市场给出了成交额。

    为什么不能只看「有没有 tradingDay=1 的块」：A 股休市而港股开市的日子（如
    2026-10-02 国庆假期），同一个文件里**北向块 tradingDay=0、数值是「-」，
    而南向块 tradingDay=1、数值是 0.00**。只按 tradingDay 过滤会停在这种日子上，
    产出「as_of 是假期、北向全空」的假读数 —— 比取不到更糟。
    """
    for blk in blocks:
        if "Northbound" not in str(blk.get("market") or ""):
            continue
        for content in blk.get("content") or []:
            table = content.get("table") or {}
            if str(table.get("classname")) != "tradingTable":
                continue
            rows = table.get("tr") or []
            cells = rows[0].get("td") if rows else None
            if cells and _hkex_number(cells[0][0] if cells[0] else None) is not None:
                return True
    return False


def _summarize_hkex(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把港交所的四张市场表压成报告要用的形状。单位统一为**元**。"""
    markets: Dict[str, Any] = {}
    as_of = None
    for blk in blocks:
        market = str(blk.get("market") or "").strip()
        if not market:
            continue
        as_of = as_of or blk.get("date")
        tables = {}
        for content in blk.get("content") or []:
            table = content.get("table") or {}
            tables[str(table.get("classname") or "")] = table

        trading = tables.get("tradingTable") or {}
        # tradingTable 是**纵向**键值：schema 的列名与 tr 的行一一对应。
        schema = (trading.get("schema") or [[]])[0]
        totals: Dict[str, Any] = {}
        for name, row in zip(schema, trading.get("tr") or []):
            cells = row.get("td") or []
            raw = cells[0][0] if cells and cells[0] else None
            value = _hkex_number(raw)
            # 百万元 → 元（见 docstring 的单位说明）。
            totals[str(name)] = None if value is None else value * 1_000_000

        top10 = tables.get("top10Table") or {}
        actives = []
        for row in top10.get("tr") or []:
            # **td 是「一个含多格的列表」**（`td: [["1","603259","藥明康德","…"]]`），
            # 不是「多个单格列表」。按后者解析每行只取到 1 格，长度判断直接跳过 ——
            # 表现是「十大活跃股永远是 0 只」而没有任何报错。
            td = row.get("td") or []
            cells = [str(x) for x in ((td[0] if td else []) or [])]
            if len(cells) < 4:
                continue
            actives.append({
                "rank": str(cells[0]).strip(),
                "code": str(cells[1]).strip(),
                # 港交所的股票名后面拖着一串全角空格，必须去掉。
                "name": str(cells[2]).replace("\u3000", " ").strip(),
                "turnover": _hkex_number(cells[-1]),   # 已是元
            })

        markets[market] = {"totals": totals, "top10": actives}
    return {"degraded": not markets, "reason": "" if markets else "文件解析后没有市场数据",
            "as_of": as_of, "markets": markets}


# ---------------------------------------------------------------------------
# 舆情（互动易问答 / 市场热度）
# ---------------------------------------------------------------------------

#: 互动易只覆盖深市。实测（2026-10）：沪市代码在第一步能命中公司，但第二步问答
#: 列表固定返回 0 条 —— 那是**平台不提供**，不是「近期没有问答」。
_IRM_SHANGHAI_PREFIXES = ("60", "68", "900")


def cninfo_irm(code: str, limit: int = 10) -> Dict[str, Any]:
    """互动易投资者问答（巨潮）。返回 ``{"items", "covered", "reason"}``。

    ``covered`` 区分**「平台不覆盖这个市场」与「近期确实没有问答」**。把前者说成
    后者，报告会把「查不到」读成「投资者没有关切」—— 而这两件事的结论正好相反。

    两步都是 POST，且**第二步的参数必须在 query string 上、body 为空**，放 body
    会 HTTP 400（a-stock-data 记了同一条，我这边实测复现）。
    """
    bare = _bare(code)
    try:
        found = fetch_json(Subdomain.IRM, "index/queryKeyboardInfo",
                           {"keyWord": bare}, method="POST")
    except ExternalError as exc:
        logger.warning("互动易公司检索失败 %s: %s", code, exc)
        return {"items": [], "covered": False, "reason": f"取数失败：{exc}"}

    hits = (found or {}).get("data") or []
    if not hits:
        return {"items": [], "covered": False, "reason": "互动易里没有这家公司的条目"}

    try:
        page = fetch_json(
            Subdomain.IRM, "company/question",
            {"_t": 1, "stockcode": bare, "orgId": hits[0].get("secid"),
             "pageSize": limit, "pageNum": 1, "keyWord": "", "startDay": "", "endDay": ""},
            method="POST", params_in="query",
        )
    except ExternalError as exc:
        logger.warning("互动易问答取数失败 %s: %s", code, exc)
        return {"items": [], "covered": False, "reason": f"问答取数失败：{exc}"}

    rows = (page or {}).get("rows") or []
    if not rows and bare.startswith(_IRM_SHANGHAI_PREFIXES):
        return {"items": [], "covered": False,
                "reason": "互动易只覆盖深市（实测沪市问答固定返回 0 条）；"
                          "沪市需用上证 e 互动，尚未接入"}

    items = []
    for it in rows:
        pub = it.get("pubDate")
        items.append({
            "question": _strip_tags(it.get("mainContent"))[:200],
            "answer": _strip_tags(it.get("attachedContent"))[:300],
            "answerer": str(it.get("attachedAuthor") or ""),
            "time": (datetime.fromtimestamp(pub / 1000).strftime("%Y-%m-%d")
                     if isinstance(pub, (int, float)) else ""),
        })
    return {"items": items, "covered": True, "reason": ""}


def market_heat(code: str, top: int = 100) -> Dict[str, Any]:
    """市场热度：同花顺热榜 + 东财人气榜，并标出**本标的是否在榜**。

    「在不在榜」比「榜单内容」更有用：一只票突然进热榜前列本身就是舆情风险的信号，
    而热榜的概念标签能告诉你市场把它归到哪个题材。

    两个榜都取不到时返回 ``degraded=True``；只取到一个也照常返回（各榜独立）。
    """
    bare = _bare(code)
    out: Dict[str, Any] = {"ths": None, "em": None, "in_ths": None, "in_em": None,
                           "degraded": False, "reason": ""}

    try:
        resp = fetch_json(Subdomain.THS_HOT, "stock",
                          {"stock_type": "a", "type": "hour", "list_type": "normal"})
        stock_list = (resp or {}).get("data", {}).get("stock_list") or []
        hit = next((it for it in stock_list if str(it.get("code")) == bare), None)
        if hit:
            tag = hit.get("tag") or {}
            out["in_ths"] = {
                "rank": hit.get("order"),
                "heat": hit.get("rate"),
                "pct": hit.get("rise_and_fall"),
                "rank_chg": hit.get("hot_rank_chg"),
                "concepts": list(tag.get("concept_tag") or []),
                "tag": str(tag.get("popularity_tag") or ""),
            }
        out["ths"] = {"total": len(stock_list)}
    except (ExternalError, AttributeError, TypeError) as exc:
        logger.warning("同花顺热榜取数失败: %s", exc)
        out["reason"] = f"同花顺热榜失败：{exc}"

    try:
        resp = fetch_json(Subdomain.EM_HOT, "getAllCurrentList",
                          {"appId": "appId01",
                           "globalId": "786e4c21-70dc-435a-93bb-38",
                           "marketType": "", "pageNo": 1, "pageSize": top},
                          method="POST")
        data = (resp or {}).get("data") or []
        # 人气榜只给带前缀代码（SH601127），比对要剥前缀。
        hit = next((it for it in data if str(it.get("sc") or "")[2:] == bare), None)
        if hit:
            out["in_em"] = {"rank": hit.get("rk")}
        out["em"] = {"total": len(data)}
    except (ExternalError, AttributeError, TypeError) as exc:
        logger.warning("东财人气榜取数失败: %s", exc)
        out["reason"] = (out["reason"] + "；" if out["reason"] else "") + f"东财人气榜失败：{exc}"

    out["degraded"] = out["ths"] is None and out["em"] is None
    return out


# ---------------------------------------------------------------------------
# 研报（reportapi）
# ---------------------------------------------------------------------------


def research_reports(code: str, limit: int = 8) -> List[Dict[str, Any]]:
    """研报列表：机构、评级、EPS 预测。**不接目标价**（见模块说明）。"""
    c = _bare(code)
    try:
        resp = fetch_json(
            Subdomain.REPORTAPI, "/report/list",
            {
                "cb": "cb", "industryCode": "*", "pageSize": str(limit),
                "beginTime": "2024-01-01", "endTime": "2030-01-01",
                "pageNo": 1, "qType": 0, "code": c, "pageNum": 1,
                "pageNumber": 1, "p": 1,
            },
        )
    except ExternalError as exc:
        logger.warning("研报取数失败 %s: %s", code, exc)
        return []
    if isinstance(resp, dict) and "data" in resp:
        rows = resp["data"]
    else:
        rows = (resp or {}).get("data") or []
    out = []
    for r in rows:
        rating = (r.get("emRatingName") or "").strip()
        out.append({
            "title": (r.get("title") or "")[:120],
            "org": r.get("orgSName"),
            "date": (r.get("publishDate") or "")[:10],
            "rating": rating,
            "rating_bucket": _bucket_rating(rating),
        })
    return out


def _bucket_rating(rating: str) -> Optional[str]:
    low = (rating or "").lower()
    for label, words in _RATING_KEYWORDS:
        for w in words:
            if w.lower() in low:
                return label
    return None
