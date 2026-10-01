"""外网数据项：治理/风险硬信号、估值分位、资金流、研报。

每个取数函数**失败时返回空**，不抛异常：外网是辅助来源，不该让评分链路因为
一个取不到的字段整体失败。取不到就是取不到，如实缺失比编造强。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .external import Subdomain, datacenter, fetch_json, ExternalError

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
    """东财报表的 SECURITY_CODE 是 **6 位**（600519），不是带后缀的 600519.SH。

    与本项目其它地方「thscode 统一存带后缀」的约定相反：本地 DuckDB 要带
    后缀，东财 filter 不带。传错不报错，只是安静地查不到任何行。
    """
    return (code or "").split(".")[0]


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
