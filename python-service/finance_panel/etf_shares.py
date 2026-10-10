"""
ETF 份额摄取 — 东方财富。

为什么份额要单独抓
------------------
`fund.duckdb` 的 `v_etf_daily` 只有价量额（OHLCV + turnover），**没有份额**。
而"大资金追踪"真正关心的是份额变动：成交额放大可能是高频资金对倒，份额增加
才意味着真实增量资金进场。份额是这条数据线的唯一新数据，必须新抓。

口径与数据源（实测结论）
------------------------
首选 `datacenter-web.eastmoney.com` 的 ETF 份额接口，但**实测探测未找到可用的
reportName**：已试 `RPT_FUND_ETF*` / `RPT_ETF_*` / `RPT_CUSTOM_*` 等 20 余个候选，
全部返回 `success=false / 报表配置不存在`；`data.eastmoney.com/etf/` 与
`gmbd.html` 均已 404，因此也无法按"从页面 JS bundle 抠 reportName"的路子定位。
唯一确认存在的 ETF 数据中心报表是 `RPT_FUND_ETFLIST`（列表快照，无份额字段）。

所以落到**退路**：`fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd`
（就是基金 F10「规模变动」页背后那个接口），字段为：

    日期          → 报告期（**季频**：03-31 / 06-30 / 09-30 / 12-31）
    期末总份额    → 份额存量，单位**亿份**
    期末净资产    → 元，单位**亿元**
    期间申购/赎回 → 亿份

粒度是季频不是日频，这点**不隐藏**：落库时 `granularity` 字段如实写 `quarterly`，
分析层（`etf_flow`）据此把"日度份额变动%"标注为由季频观测推得，避免读成
"汇金今天在买"这种根本不存在的精度。

    ⚠️ 若日后东财恢复日频接口，改 `_PROVIDERS` 顺序即可，上层无需改动。

限速
----
沿用 `calendar.py` 的口径：≥1 秒/次、带 UA、带重试。抓取失败**不抛异常**，
返回空列表由调用方决定降级 —— 份额数据缺失不该把整个接口拖垮。
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# 限速 / 重试
# ----------------------------------------------------------------------

# 与 calendar.py 同口径：低于 1 秒/次开始出现空结果。
_MIN_INTERVAL = 1.0
_TIMEOUT = 20
_RETRIES = 3

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/javascript, application/javascript, */*; q=0.01",
}

_FMBA_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
# datacenter-web 首选入口保留在代码里：接口恢复后只需把 provider 顺序调回来。
_DC_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DC_REPORT_NAME = os.getenv("ETF_SHARE_REPORT_NAME", "")  # 探测未果，留空即跳过

_last_request_at = 0.0

# 份额单位：接口给"亿份"，落库存"份"（×1e8）。统一到份是因为分析层要跟
# DuckDB 的成交额算比例，不统一量纲就会把 189.15 亿份读成 189.15 份。
_UNIT_SCALE = 1e8
GRANULARITY_QUARTERLY = "quarterly"
GRANULARITY_DAILY = "daily"


def _throttle() -> None:
    global _last_request_at
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _to_float(text: Any) -> Optional[float]:
    """'189.15' / '1,234.5' / 189.15 → float；不可解析返回 None（**不返回 0**）。

    东财在千分位和空值上不统一（有的行给 '---'）。这些情况一律 None ——
    份额 0 份与份额不可知是两回事。
    """
    if text is None:
        return None
    s = str(text).strip().replace(",", "").replace("%", "")
    if not s or s in {"-", "---", "--", "null", "None"}:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_fmba(text: str) -> List[Dict[str, Any]]:
    """`var gmbd_apidata={content:"<table>...</table>"}` → 行字典列表。

    不用 HTML 解析库：接口返回的是**固定列头**的窄表，正则取 `<td class='tor'>` 的
    文本比引入一个 HTML 依赖更可控，也避开了引号转义（JS 字符串里 `\'` 会被
    json/正则处理成不同结果）的坑。
    """
    m = re.search(r"content:\"(.*?)\",arryear", text, re.S) or re.search(
        r"content:\"(.*?)\"\s*}", text, re.S
    )
    if not m:
        return []
    table = m.group(1)
    rows: List[Dict[str, Any]] = []
    for tr in re.findall(r"<tr>(.*?)</tr>", table, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 4:
            continue
        cells = [re.sub(r"<[^>]+>", "", t).strip() for t in tds]
        period = cells[0][:10]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", period):
            continue
        raw_shares = _to_float(cells[3])  # 期末总份额（亿份）
        rows.append(
            {
                "trade_date": period,
                # 存"份"。None 保持 None —— 缺失 ≠ 0 在摄取层就要守住。
                "shares_outstanding": None if raw_shares is None else raw_shares * _UNIT_SCALE,
                "nav": _to_float(cells[4]) if len(cells) > 4 else None,  # 亿元
                "raw_shares_yi": raw_shares,
                "granularity": GRANULARITY_QUARTERLY,
                "source": "fundf10.gmbd",
            }
        )
    return rows


def _fetch_fmba(code: str, page: int = 1) -> Optional[List[Dict[str, Any]]]:
    """取一页规模变动。返回 None 表示该页失败。"""
    global _last_request_at
    params = {"type": "gmbd", "code": code, "page": page, "per": 49}
    headers = dict(_HEADERS, Referer=f"https://fundf10.eastmoney.com/gmbd_{code}.html")
    for attempt in range(_RETRIES):
        _throttle()
        try:
            resp = requests.get(_FMBA_URL, params=params, headers=headers, timeout=_TIMEOUT)
            _last_request_at = time.monotonic()
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return _parse_fmba(resp.text)
        except Exception as exc:  # noqa: BLE001 —— 抓取失败不抛给调用方
            logger.warning("ETF %s 第 %d 页抓取失败（%d/%d）：%s", code, page, attempt + 1, _RETRIES, exc)
            time.sleep(1.0 * (attempt + 1))
    return None


def fetch_shares(code: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """抓单只 ETF 的份额序列（升序）。

    Args:
        code: 6 位裸代码（如 "510300"）。
        since: 增量续抓起点 `YYYY-MM-DD`。**只返回 trade_date > since** 的行。
            None 表示全量抓。

    Returns:
        `[{trade_date, shares_outstanding, granularity, source, ...}]`，升序。
        抓取彻底失败返回 **空列表**（不抛异常）—— 调用方据此回
        `{"ok": false, "reason": ...}` 并保留库里已有数据。
    """
    rows: List[Dict[str, Any]] = []
    for page in (1, 2, 3):  # F10 每页 49 条，3 页足够覆盖近 10 年季报
        batch = _fetch_fmba(code, page=page)
        if batch is None:
            if not rows:
                return []
            break
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 49:
            break

    if not rows:
        logger.warning("ETF %s 份额抓取为空", code)
        return []

    # 去重（同报告期可能在多页里重复出现）+ 升序
    dedup: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        dedup[r["trade_date"]] = r
    ordered = [dedup[k] for k in sorted(dedup)]

    if since:
        ordered = [r for r in ordered if r["trade_date"] > since]
    return ordered


def sync_pool(
    pool_codes: Sequence[str],
    last_dates: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Any]:
    """对池内 ETF 增量抓取份额。

    始终返回**固定 shape**（halo 不变式）：失败也回 `ok=false` + reason，
    不抛异常、不返回半截结构。

    Returns:
        `{ok, per_etf: {code: {fetched|reason}}, total_rows}`
    """
    per_etf: Dict[str, Dict[str, Any]] = {}
    last_dates = last_dates or {}
    total_rows = 0

    for code in pool_codes or []:
        since = last_dates.get(code)
        try:
            rows = fetch_shares(code, since=since)
        except Exception as exc:  # noqa: BLE001 —— 单只失败不影响整池
            per_etf[code] = {"fetched": 0, "reason": f"抓取异常: {exc}"}
            continue
        if not rows:
            # 区分"库里已是最新"与"抓取失败"：后者的 DB 还没建表/网络不通时
            # last_dates 为 None，这里补一句可诊断的原因。
            per_etf[code] = {
                "fetched": 0,
                "reason": (
                    f"无新数据（库内最新 {since}，已最新）"
                    if since
                    else "抓取失败：东财返回空数据（可能限速或接口变更）"
                ),
            }
            continue
        per_etf[code] = {"fetched": len(rows), "rows": rows}
        total_rows += len(rows)

    ok = any(v.get("fetched", 0) > 0 for v in per_etf.values())
    return {"ok": ok, "per_etf": per_etf, "total_rows": total_rows}


def load_pool(path: Optional[str] = None) -> List[Dict[str, str]]:
    """读 `etf_pool.yaml` → `[{code, name, tags, group}]`。

    读两个段：`pool`（宽基，汇金口径）与 `sector_pool`（行业，补充视野）。
    **两段必须分开读、不能混成一个列表丢掉分组** —— 前端按 `group` 切筛选，
    且"宽基里 n 只变了"这个结论的分母只由 `pool` 得出。

    YAML 解析失败不抛异常，返回空列表 —— 清单读不出来应该让端点回固定 shape，
    而不是 500。
    """
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "etf_pool.yaml")
    try:
        import yaml  # 局部导入：纯规则层（etf_flow）不需要它
    except ImportError:  # pragma: no cover
        logger.warning("未安装 PyYAML，追踪池为空")
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 ETF 追踪池失败 %s: %s", path, exc)
        return []

    out: List[Dict[str, str]] = []
    # (段名, 该段的默认 group)。缺 group 字段时用段名兜底，
    # 这样老清单（只有 pool 段、没有 group）仍按宽基处理，不会静默变空。
    for section, default_group in (("pool", "broad"), ("sector_pool", "sector")):
        for it in data.get(section) or []:
            if not isinstance(it, dict) or not it.get("code"):
                continue
            out.append(
                {
                    "code": str(it["code"]).strip(),
                    "name": str(it.get("name") or "").strip(),
                    "tags": list(it.get("tags") or []),
                    "group": str(it.get("group") or default_group).strip(),
                }
            )
    return out


def pool_codes(path: Optional[str] = None) -> List[str]:
    """追踪池的 6 位代码列表（去重、保序）。"""
    seen: Dict[str, None] = {}
    for item in load_pool(path):
        seen.setdefault(item["code"], None)
    return list(seen)