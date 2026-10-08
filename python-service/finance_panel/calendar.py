"""
财经日历抓取 — 东方财富 datacenter-web。

数据源与字段
------------
接口：`https://datacenter-web.eastmoney.com/api/data/v1/get`
reportName：`RPT_CPH_FECALENDAR`（东财「财经日历」页 `/cjrl/` 实际在用的
那张表；`RPT_ECONOMIC_*` 系列全部返回 success=false，是死路）。

返回字段里对应关系：
    START_DATE → 事件日期（注意是 `'YYYY-MM-DD HH:MM:SS'` 字符串，要截前 10 位）
    FE_NAME    → 标题
    FE_TYPE    → 分类（**可能为 null**，落库时补 "其他"，不补空串）
    FE_CODE    → 稳定 ID（同一条事件多次抓取时用它确认是同一条）

限速
----
东财对高频请求会返回空 result。这里固定 ≥1 秒/次（`_MIN_INTERVAL`），并带
指数退避重试。抓取失败由调用方决定降级，**本模块不抛异常** —— 日历是面板
上的辅助块，不该把整个接口拖垮。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_REPORT_NAME = "RPT_CPH_FECALENDAR"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/cjrl/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

# 东财没有公开限速阈值，但实测低于 1 秒/次会开始返回空 result。
_MIN_INTERVAL = 1.0
_TIMEOUT = 20
_MAX_PAGES = 5
_PAGE_SIZE = 100
_RETRIES = 3

_last_request_at = 0.0


def _throttle() -> None:
    """全局串行限速，保证任意两次请求间隔 ≥ `_MIN_INTERVAL` 秒。"""
    global _last_request_at
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _fetch_page(start: str, end: str, page: int) -> Optional[Dict[str, Any]]:
    """取一页。返回 None 表示该页失败（调用方重试）。"""
    global _last_request_at
    params = {
        "reportName": _REPORT_NAME,
        "columns": "ALL",
        "pageNumber": page,
        "pageSize": _PAGE_SIZE,
        "sortColumns": "START_DATE",
        "sortTypes": "1",
        "source": "WEB",
        "client": "WEB",
        "filter": f"(START_DATE>='{start}')(START_DATE<='{end}')",
    }
    for attempt in range(_RETRIES):
        _throttle()
        try:
            resp = requests.get(_API_URL, params=params, headers=_HEADERS, timeout=_TIMEOUT)
            _last_request_at = time.monotonic()
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:  # noqa: BLE001 —— 抓日历失败不该抛给调用方
            logger.warning("财经日历第 %d 页请求失败（%s/%d）：%s", page, attempt + 1, _RETRIES, exc)
            time.sleep(1.0 * (attempt + 1))
            continue

        if body.get("success") is False:
            logger.warning("财经日历第 %d 页 success=false：%s", page, body.get("message"))
            time.sleep(1.0 * (attempt + 1))
            continue

        result = body.get("result") or {}
        return {"count": result.get("count") or 0, "data": result.get("data") or []}

    return None


def fetch_calendar(
    start: Optional[str] = None,
    end: Optional[str] = None,
    past_days: int = 3,
    future_days: int = 14,
) -> List[Dict[str, Any]]:
    """抓取区间内的财经日历事件。

    默认区间：过去 3 天 → 未来 14 天。往回留 3 天是因为当天补录 / 改期
    的事件还可能落在这个窗口里。

    Returns:
        `[{date, title, category, payload}]`，date 为 `YYYY-MM-DD`。
        抓取彻底失败时返回 **空列表**（不抛异常），调用方应据此返回
        `{"ok": false, "reason": ...}` 并保留库里已有数据。
    """
    today = date.today()
    start_date = start or (today - timedelta(days=past_days)).isoformat()
    # END_DATE 补到当天 23:59:59，否则 START_DATE <= '2026-10-08' 这种
    # 字符串比较会把当天 00:00:00 的事件漏掉（实测会少一天）。
    end_ts = f"{end or (today + timedelta(days=future_days)).isoformat()} 23:59:59"

    rows: List[Dict[str, Any]] = []
    for page in range(1, _MAX_PAGES + 1):
        page_result = _fetch_page(start_date, end_ts, page)
        if page_result is None:
            # 第一页就失败 = 整个抓取失败，不能返回部分数据假装成功。
            if not rows:
                return []
            break
        batch = page_result["data"]
        if not batch:
            break
        for row in batch:
            event_date = str(row.get("START_DATE") or "")[:10]
            title = str(row.get("FE_NAME") or "").strip()
            if not event_date or not title:
                continue
            rows.append({
                "date": event_date,
                "title": title,
                # FE_TYPE 经常是 null，补 "其他" 而不是空串 ——
                # 空串在前端会被渲染成一个没有文字的分类标签。
                "category": row.get("FE_TYPE") or "其他",
                "payload": {
                    "fe_code": row.get("FE_CODE"),
                    "city": row.get("CITY"),
                    "content": row.get("CONTENT"),
                },
            })
        if page * _PAGE_SIZE >= (page_result["count"] or 0):
            break

    logger.info("财经日历抓取完成：区间 %s ~ %s，%d 条", start_date, end_ts[:10], len(rows))
    return rows
