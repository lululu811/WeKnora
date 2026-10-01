"""外网数据客户端 —— 按**子域**分级的限速与降级。

为什么要分子域而不是分数据源
----------------------------
东财不同子域走**不同的 WAF**。这是实测出来的，不是推测：

* ``datacenter-web.eastmoney.com`` —— 稳定档。股东户数、估值分位、增减持、
  业绩预告、回购、分红都在这里。
* ``push2his.eastmoney.com`` —— 易封档。资金流在这里。
* ``reportapi.eastmoney.com`` —— 第三档。研报列表。

a-stock-data 记录的一次实测封禁（2026-06-30）里，``push2``/``push2his``
全系列被封 **20+ 小时**，而 ``datacenter-web`` **完全不受影响**。所以
「东财被封」这句话是错的 —— 只有部分子域被封。halo-skill 的
``meta.missing`` 恒为 ``['company_info','concepts','fund_flow']``，正好全
是 push2/push2his 系；它用 datacenter-web 取的股东户数和估值分位从来没进过
missing 列表。按子域分级，正好把这个规律固化成代码。

限速铁律（社区实测，2026-05）
----------------------------
===============  ==============  ==========
行为             封禁阈值          风险
===============  ==============  ==========
每秒请求数       > 5               高
单 IP 并发        >= 10             高
1 分钟请求数      >= 200            中高
5 分钟请求数      >= 300            触发封禁
===============  ==============  ==========

本模块对所有子域**串行**、带最小间隔与随机抖动；并发调用会在锁上排队。
本项目的负载是「同步一只股票」这种单次调用，串行的代价可以接受，换来的是
不会成为封 IP 的贡献者。
"""

from __future__ import annotations

import gzip
import http.client
import json
import logging
import os
import random
import threading
import time
import urllib.error
from http.client import RemoteDisconnected
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class Subdomain:
    """东财子域与它们的封禁风险档位。"""

    DATACENTER = "datacenter-web"   # 稳定：治理/风险/估值分位/股东户数
    PUSH2HIS = "push2his"           # 易封：资金流
    REPORTAPI = "reportapi"         # 第三档：研报


#: (base_url, 最小间隔秒, 备注)
_TIERS: Dict[str, tuple] = {
    Subdomain.DATACENTER: (
        "https://datacenter-web.eastmoney.com/api/data/v1/get", 0.5,
        "稳定档，WAF 与 push2 系独立",
    ),
    Subdomain.PUSH2HIS: ("https://push2his.eastmoney.com", 1.0, "易封档，务必串行"),
    Subdomain.REPORTAPI: ("https://reportapi.eastmoney.com", 0.5, "研报"),
}

_REFERERS = {
    Subdomain.DATACENTER: "https://data.eastmoney.com/",
    Subdomain.PUSH2HIS: "https://quote.eastmoney.com/",
    Subdomain.REPORTAPI: "https://data.eastmoney.com/report/",
}


class ExternalError(RuntimeError):
    """外网请求失败。``banned`` 为真表示是 403 封禁信号，不应重试。"""

    def __init__(self, message: str, *, banned: bool = False) -> None:
        super().__init__(message)
        self.banned = banned


class _Throttle:
    """串行 + 最小间隔 + 随机抖动。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self, min_interval: float) -> None:
        with self._lock:
            gap = min_interval + random.uniform(0, 0.3)
            delta = self._last + gap - time.monotonic()
            if delta > 0:
                time.sleep(delta)
            self._last = time.monotonic()


_THROTTLES = {name: _Throttle() for name in _TIERS}


def _min_interval(name: str) -> float:
    override = os.getenv(f"HALO_EM_MIN_INTERVAL_{name.split('.')[-1].upper()}")
    if override:
        try:
            return max(0.0, float(override))
        except ValueError:
            pass
    return _TIERS[name][1]


def fetch_json(
    subdomain: str,
    path: str = "",
    params: Optional[Dict[str, Any]] = None,
    *,
    timeout: float = 20.0,
    retries: int = 3,
    method: str = "GET",
) -> Any:
    """带限速与退避地取一个 JSON。

    重试策略：5xx / 429 / 网络错误退避重试；**403 不重试** —— 那是 IP 级
    封禁信号，继续打只会让封禁更久，重试毫无意义。
    """
    base, _, tier_note = _TIERS[subdomain]
    url = base + path
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)

    throttle = _THROTTLES[subdomain]
    # 头要**像浏览器**。`push2his` 这类易封档对 `Accept: */*`（curl 风格的
    # 通配）敏感，会直接 `Remote end closed connection without response` ——
    # 服务端主动断开、不返回状态码，urllib 只能报连接错误，看起来像网络故障，
    # 实际是反爬。补齐 Accept-Language / Accept-Encoding 后同一请求正常返回。
    headers = {
        "User-Agent": _UA,
        "Referer": _REFERERS.get(subdomain, "https://www.eastmoney.com/"),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    data = None
    if method == "POST":
        data = urllib.parse.urlencode(params or {}).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        url = base + path

    last_err: Optional[str] = None
    disconnects = 0
    for attempt in range(retries):
        throttle.wait(_min_interval(subdomain))
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                raw = body.decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                # 封禁信号：明确抛出，让调用方走降级，而不是重试加重封禁
                raise ExternalError(
                    f"{subdomain} 返回 403（IP 级封禁信号，tier={tier_note}）：{exc.code}",
                    banned=True,
                ) from exc
            if exc.code == 429 or exc.code >= 500:
                last_err = f"HTTP {exc.code}"
                backoff = (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "%s 第 %d 次失败（%s），%.1fs 后退避重试",
                    subdomain, attempt + 1, last_err, backoff,
                )
                time.sleep(backoff)
                continue
            raise ExternalError(f"{subdomain} HTTP {exc.code}") from exc
        except (RemoteDisconnected, http.client.IncompleteRead) as exc:
            # **实测到的封禁形态不是 403，而是「服务端主动断开、不返回任何状态码」**。
            # 2026-10-01 复现：push2his 在少量请求后开始对所有请求头组合
            # （含裸 UA+Referer）返回 Remote end closed connection without
            # response，同时 datacenter-web 与 reportapi 完全正常 —— 这正是
            # 子域独立 WAF 的现场证据。
            #
            # 这类断开**不是**网络抖动，继续重试只会浪费配额并可能延长封禁。
            # 第一次可以重试（确实存在偶发断连），连续出现即判定封禁。
            disconnects += 1
            last_err = f"服务端主动断开（{type(exc).__name__}）"
            if disconnects >= 2:
                raise ExternalError(
                    f"{subdomain} 连续 {disconnects} 次被服务端主动断开，"
                    f"判定为 IP 级封禁（tier={tier_note}）；"
                    f"同 WAF 的其它子域不受影响，请走降级而非重试",
                    banned=True,
                ) from exc
            logger.warning(
                "%s 被服务端断开（第 %d 次），%.1fs 后重试一次",
                subdomain, disconnects, 2.0,
            )
            time.sleep(2.0)
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = str(exc)
            backoff = (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning(
                "%s 第 %d 次网络失败（%s），%.1fs 后退避重试",
                subdomain, attempt + 1, last_err, backoff,
            )
            time.sleep(backoff)
            continue

        raw = raw.strip()
        if raw.startswith("{") or raw.startswith("["):
            return json.loads(raw)
        if "(" in raw and raw.endswith(")"):     # JSONP 包装
            return json.loads(raw[raw.index("(") + 1: raw.rindex(")")])
        return json.loads(raw)

    raise ExternalError(f"{subdomain} 重试 {retries} 次仍失败：{last_err}")


def datacenter(report_name: str, filter_: str, *, size: int = 2,
               sort: Optional[tuple] = None, **extra: Any) -> list:
    """查 datacenter-web 报表，返回 data 行列表。

    ``report_name`` 是东财的报表 ID（如 ``RPT_VALUATIONSTATUS``），
    ``filter_`` 是它的过滤表达式（如 ``(SECURITY_CODE="600519")``）。
    接口出错时返回空列表而不是抛异常：外网数据是锦上添花，不该让整条
    评分链路因为一个取不到的辅助字段而失败。
    """
    payload: Dict[str, Any] = {
        "reportName": report_name,
        "columns": extra.pop("columns", "ALL"),
        "pageNumber": "1",
        "pageSize": str(size),
        "filter": filter_,
        "source": "WEB",
        "client": "WEB",
    }
    if sort:
        payload["sortColumns"], payload["sortTypes"] = sort[0], sort[1]
    payload.update(extra)
    try:
        resp = fetch_json(Subdomain.DATACENTER, params=payload)
    except ExternalError as exc:
        logger.warning("datacenter %s 取数失败：%s", report_name, exc)
        return []
    return ((resp or {}).get("result") or {}).get("data") or []
