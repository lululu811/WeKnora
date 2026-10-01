"""巨潮资讯网（cninfo）年报 PDF 数据入口。

HALO 的数据铁律是「数字只能来自权威源，LLM 不许编」。本地 hithink DuckDB 里有利润表
和资产负债表主干科目，但**没有**固定资产、在建工程、存货、无形资产、商誉、员工数——
这些只在年报 PDF 原文里。本模块负责整条链路的第一段：把「某只股票某一期年报」在巨潮
上定位准，并把 PDF 落到本地。解析在 :mod:`halo.pdf_extract`，对账不在这里。

为什么是巨潮而不是交易所官网
----------------------------
巨潮是证监会指定的信息披露平台，沪深两市法定公告的**唯一权威原文入口**；交易所官网
只是转载，格式随时会变。选它等于选「人工复核时能翻到同一页」。

移植说明
--------
移植自 easy_tdx 的 ``easy_tdx.cninfo.client``（stdlib urllib），并修了三个已知问题：

1. **category 被写死成空字符串**（原实现 ``_query_announcements`` 里 ``"category": ""``）。
   不带 category 时接口返回的是**该股票全部类型**公告按时间倒序的前 30 条，一只活跃
   股票一个季度的公告就足以把年报挤出这 30 条，于是永远「查无年报」。按报告类型传
   ``category_ndbg_szsh`` 等代码后，贵州茅台 600519 实测 ``totalAnnouncement=74``（年报
   类），第一条即「贵州茅台2025年年度报告」。
2. **限速与退避缺失**。原实现是裸 ``urlopen``。HALO 预热全市场要发上万次请求，巨潮
   有反爬，连续打必然被限流。这里加了模块级共享限速器 + 指数退避重试。
3. **pageSize 上限 30 未做保护**。调用方传 100 会被巨潮静默截断成 30，分页逻辑会漏数据
   却毫无察觉——这种「静默丢数据」比报错更危险，所以在这里夹断。

原实现对、但极易漏掉的一点：orgId 必须走官方映射表。巨潮的 orgId 没有统一格式
（601318→9900002221、688017→9900041602、600519→gssh0600519），按 ``gssx0{code}`` 硬拼
会让 601xxx 一整段返回 ``totalAnnouncement=0``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import parse
from urllib import request as urlrequest

from .store import REPORT_ANNUAL, REPORT_H1, REPORT_Q1, REPORT_Q3

logger = logging.getLogger(__name__)

# Referer/Origin 必填：巨潮的反爬会校验来源，缺任一头直接返回空结果（不是报错，
# 是「查无此项」），排查起来极其浪费时间。这里写死是因为它没有可配置的价值。
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_STOCK_MAP_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
_PDF_BASE = "http://static.cninfo.com.cn/"

# 巨潮的公告分类代码。category 是这次改造的核心：不传就只能拿到该股最近 30 条公告。
CATEGORY_ANNUAL = "category_ndbg_szsh"
CATEGORY_H1 = "category_bndbg_szsh"
CATEGORY_Q1 = "category_yjdbg_szsh"
CATEGORY_Q3 = "category_sjdbg_szsh"

# 报告类型 → 巨潮 category。用 store 的常量做键，保证下游落库的 report_type 与
# 检索分类永远来自同一份定义，不会出现「检索的是年报、落库写成 h1」这种错位。
CATEGORY_BY_REPORT_TYPE: Dict[str, str] = {
    REPORT_ANNUAL: CATEGORY_ANNUAL,
    REPORT_H1: CATEGORY_H1,
    REPORT_Q1: CATEGORY_Q1,
    REPORT_Q3: CATEGORY_Q3,
}

# 标题中用于二次确认报告类型的关键词。category 只保证「大类」对，标题仍可能同时
# 命中多个关键词（见 report_type_of 的顺序陷阱说明）。
REPORT_KEYWORD_BY_TYPE: Dict[str, str] = {
    REPORT_ANNUAL: "年度报告",
    REPORT_H1: "半年度报告",
    REPORT_Q1: "第一季度报告",
    REPORT_Q3: "第三季度报告",
}

# 年报 category 的检索结果里混着这两种，它们**不是**年报正文：
#   - 摘要（贵州茅台2025年年度报告摘要）：只有十来页，字段表根本不完整；
#   - 英文版（……（英文版））：文本层是英文，规则抽取全部失效。
# 实测这两条都排在真正的年报之前（2026-04-17 同批披露），所以「取第一条」是错的。
_NON_PRIMARY_MARKERS = ("摘要", "英文", "English", "已取消")

# pageSize 硬上限。巨潮对超出部分静默截断，调用方以为拿到了 100 条、分页继续往下走，
# 结果中间的 70 条永久丢失。
CNINFO_MAX_PAGE_SIZE = 30

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")

# 官方 code→orgId 映射。6259 条，全市场预热只需拉一次，因此放模块级全局共享
# （CPython dict 读写原子，并发下最多多发一次请求，可接受）。
_ORGID_MAP: Dict[str, str] = {}
_ORGID_LOCK = threading.Lock()


# ----------------------------------------------------------------------
# 领域异常
# ----------------------------------------------------------------------


class CninfoError(Exception):
    """巨潮数据请求或解析失败。"""


class NoFilingFoundError(CninfoError):
    """该股票在该报告期/年份下没有可用的法定披露文件。

    单独一个类型而不是复用 ``CninfoError``：批量预热时「601234 已退市无年报」是
    正常业务结果，不该和网络故障混在一起被重试。
    """


# ----------------------------------------------------------------------
# 限速
# ----------------------------------------------------------------------


class _RateLimiter:
    """按「两次请求之间的最小间隔」节流。

    锁在 sleep 期间不释放：这里要的就是把并发调用者**串行**到节流节奏上，放开锁
    等于让 N 个线程同时醒来同时发请求，节流形同虚设。
    """

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            delay = self._next_allowed - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            # 用 sleep 后的真实时刻推进，而不是预估值：sleep 可能被信号打断而
            # 提前返回，用预估值会让下一次请求紧贴着发出去，反而更危险。
            self._next_allowed = time.monotonic() + self.min_interval


# 限速器按间隔共享：全市场预热会 new 出成百上千个 CninfoSource，若每个实例各限各的，
# 总并发请求数就是「实例数 × 1/间隔」，等于没限。
_LIMITERS: Dict[float, _RateLimiter] = {}
_LIMITER_LOCK = threading.Lock()


def _shared_limiter(min_interval: float) -> _RateLimiter:
    with _LIMITER_LOCK:
        limiter = _LIMITERS.get(min_interval)
        if limiter is None:
            limiter = _RateLimiter(min_interval)
            _LIMITERS[min_interval] = limiter
        return limiter


# ----------------------------------------------------------------------
# 记录
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Announcement:
    """巨潮的一条公告（标准化后的视图）。

    只保留 HALO 链路真正要用的字段，**不引入 pandas**：easy_tdx 原实现返回
    DataFrame 是为了喂它的 ``get_*`` 约定，HALO 这边只逐条消费，多一层 DataFrame
    只是多一份内存拷贝和一个 dtype 陷阱。

    Attributes:
        title: 公告标题（已剥离 ``<em>`` 高亮标签）。
        doc_type: 公告类型。巨潮的 ``announcementTypeName`` 对多数公告为 null，
            实际拿到的几乎总是 ``adjunctType``（"PDF"），所以这个字段基本不参与
            判断，保留它只是为了如实记录服务端返回了什么。
        date: 披露日期 ``YYYY-MM-DD``（由 announcement_time 换算）。
        detail_url: 公告详情页 URL（4 个参数缺一不可，否则 404）。
        code: 6 位股票代码。
        org_id: 巨潮 orgId。
        announcement_id: 巨潮公告 ID。
        announcement_time: 原始 Unix 毫秒时间戳。
        adjunct_url: 巨潮返回的附件相对路径，如 ``finalpage/2026-04-17/1225114741.PDF``。
        pdf_url: PDF 直链（``adjunctUrl`` 拼 static.cninfo.com.cn），无附件时为空串。
    """

    title: str
    doc_type: str
    date: str
    detail_url: str
    code: str
    org_id: str
    announcement_id: str
    announcement_time: int
    adjunct_url: str
    pdf_url: str


@dataclass(frozen=True)
class LocalFiling:
    """一份已落盘的年报 PDF 及其定位信息。

    ``year`` 单列出来，是因为下游要往 ``store`` 的 ``period`` 字段写「报告期年份」，
    而它来自**标题里的年份**（2025 年报在 2026 年披露），不是披露日期的年份——
    用披露年份会把 2025 年报记成 2026，评分内核取数直接错一年。
    """

    code: str
    report_type: str
    year: Optional[int]
    path: str
    announcement: Announcement


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("环境变量 %s 不是合法数字，回退默认值 %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        logger.warning("环境变量 %s 不是合法数字，回退默认值 %s", name, default)
        return default


def fallback_orgid(code: str) -> str:
    """按板块前缀猜 orgId —— **只在**官方映射表不可用时兜底。

    正确性有限，这一点必须写清楚，否则会有人误以为它能覆盖全市场：

    - 它只对少数老股票成立（600519 恰好成立，601318/688017 不成立）；
    - 688xxx 科创板虽然挂在沪市，却以 ``6`` 开头，所以会落进 6→gssh0 分支；
      而科创板真实 orgId 是 ``9900xxxx`` 形式，前缀规则**原理上**就拼不出来。
    - 601xxx 整段（中国平安等）真实 orgId 多为 9900 形式，同样拼不出来。

    之所以还留着，是因为映射表偶尔会因为网络问题拉不到，而完全放弃比猜错更容易
    让整条链路停摆。猜错的表现是「查无公告」，会在下面的日志里明确暴露。
    """
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def build_pdf_url(adjunct_url: str) -> str:
    """``adjunctUrl`` 拼成 PDF 直链；无附件返回空串。"""
    if not adjunct_url:
        return ""
    return f"{_PDF_BASE}{adjunct_url}"


def build_detail_url(code: str, announcement_id: str, org_id: str, announcement_time: int) -> str:
    """公告详情页 URL。四个参数缺一不可，否则 404。"""
    return (
        "https://www.cninfo.com.cn/new/disclosure/detail?"
        f"stockCode={code}&announcementId={announcement_id}"
        f"&orgId={org_id}&announcementTime={announcement_time}"
    )


def report_type_of(title: str) -> Optional[str]:
    """从标题判断报告类型，判断不出返回 None。

    **顺序陷阱**：巨潮的标题形如「2025年半年度报告」，它**含有**「年度报告」三个字。
    若按「年报 → 半年报」的顺序匹配，半年报会被误判成年报。所以必须按
    「更长/更具体 → 更宽泛」的顺序：先半年报、一季度、三季度，最后才是年报。
    """
    for report_type in (REPORT_H1, REPORT_Q1, REPORT_Q3, REPORT_ANNUAL):
        if REPORT_KEYWORD_BY_TYPE[report_type] in title:
            return report_type
    return None


def filing_year(title: str) -> Optional[int]:
    """从标题取报告期年份（如「2025年年度报告」→ 2025），取不到返回 None。"""
    m = _YEAR_RE.search(title)
    return int(m.group(0)) if m else None


def _is_primary_filing(title: str) -> bool:
    """是否是「正本」文件（排除摘要、英文版、已取消）。"""
    return not any(marker in title for marker in _NON_PRIMARY_MARKERS)


def _strip_html(text: str) -> str:
    """剥掉 cninfo 的高亮标签。

    开了 ``isHLtitle=true`` 后命中关键词的标题会被包上 ``<em>``，不剥的话
    「2025年年度报告」会变成「<em>2025</em>年年度报告」，年份正则和关键词匹配
    全部落空。
    """
    return re.sub(r"<[^>]+>", "", text)


def _ts_to_date(ts: Any) -> str:
    """Unix 毫秒 → ``YYYY-MM-DD``；畸形值给空串而不是抛异常。

    巨潮偶尔返回 null 或字符串，硬转 int 会让整批公告解析失败——而一条坏记录
    不该拖垮另外 29 条。
    """
    try:
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            return str(ts)[:10] if ts else ""
        return datetime.fromtimestamp(float(ts) / 1000).strftime("%Y-%m-%d")
    except (OSError, ValueError, OverflowError, TypeError):
        return ""


# ----------------------------------------------------------------------
# 客户端
# ----------------------------------------------------------------------


class CninfoSource:
    """巨潮公告检索 + PDF 下载（无状态 HTTP，无需 connect/close）。

    用法::

        src = CninfoSource()
        filing = src.fetch_filing_pdf("600519", dest_dir="/tmp/halo_cninfo_test",
                                      year=2025)
        # → LocalFiling(path=".../600519_2025_annual.PDF", year=2025, ...)
    """

    def __init__(
        self,
        *,
        timeout: Optional[float] = None,
        min_interval: Optional[float] = None,
        max_retries: Optional[int] = None,
        backoff_base: float = 0.8,
        max_pdf_bytes: Optional[int] = None,
        chunk_size: int = 64 * 1024,
        limiter: Optional[_RateLimiter] = None,
    ) -> None:
        # 配置在构造时读 env 而不是 import 时：批量预热场景下测试与调用方需要在
        # 运行期调参，模块级常量做不到。
        self.timeout = timeout if timeout is not None else _env_float("HALO_CNINFO_TIMEOUT", 15.0)
        self.min_interval = (
            min_interval
            if min_interval is not None
            else _env_float("HALO_CNINFO_MIN_INTERVAL", 0.5)
        )
        self.max_retries = (
            max_retries
            if max_retries is not None
            else _env_int("HALO_CNINFO_MAX_RETRIES", 3)
        )
        self.backoff_base = backoff_base
        self.max_pdf_bytes = (
            max_pdf_bytes
            if max_pdf_bytes is not None
            else _env_int("HALO_CNINFO_MAX_PDF_MB", 100) * 1024 * 1024
        )
        self.chunk_size = chunk_size
        self.limiter = limiter if limiter is not None else _shared_limiter(self.min_interval)

    # ------------------------------------------------------------------
    # HTTP 底座
    # ------------------------------------------------------------------

    def _sleep_backoff(self, attempt: int, reason: str) -> None:
        """指数退避：0.8s → 1.6s → 3.2s。

        起点不用 0，是为了和限速器节奏错开——退避只解决「服务端刚被我们打疼了」，
        常态节流仍由限速器负责，两者职责不同。
        """
        delay = self.backoff_base * (2 ** (attempt - 1))
        logger.warning("巨潮请求失败（%s），第 %d 次退避 %.1fs 后重试", reason, attempt, delay)
        time.sleep(delay)

    def _urlopen(self, req: urlrequest.Request) -> Any:
        """带限速与退避重试的 ``urlopen``（测试的 monkeypatch 点）。

        重试策略刻意区分 4xx / 5xx：

        - 4xx（429 除外）= 请求本身不对（URL 过期、orgId 错、参数非法），重试一万次
          也是同样的结果，只会白白拖慢全量预热，所以立刻转领域异常。
        - 5xx 与超时 = 服务端临时状态，退避后重试有意义。
        - 429 = 限流。虽然是 4xx，但恰恰是「等一会儿再来」的意思，单独放行重试。
        """
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_retries + 1):
            self.limiter.wait()
            try:
                return urlrequest.urlopen(req, timeout=self.timeout)
            except urlerror.HTTPError as exc:
                if exc.code < 500 and exc.code != 429:
                    raise CninfoError(
                        f"巨潮拒绝请求（HTTP {exc.code}）: {req.full_url}"
                    ) from exc
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                self._sleep_backoff(attempt, reason=f"HTTP {exc.code}")
            except (urlerror.URLError, TimeoutError, OSError) as exc:
                # HTTPError 是 URLError 的子类，必须排在前面单独处理，否则 4xx/5xx
                # 的区分会被这里吞掉。
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                self._sleep_backoff(attempt, reason=type(exc).__name__)
        raise CninfoError(
            f"巨潮请求失败（已重试 {self.max_retries} 次）: {req.full_url}: {last_exc}"
        ) from last_exc

    def _get_json(self, url: str) -> Any:
        req = urlrequest.Request(url, headers={"User-Agent": _UA})
        try:
            with self._urlopen(req) as resp:
                payload = resp.read()
        except CninfoError:
            raise
        except Exception as exc:  # noqa: BLE001 — 解析失败也转领域异常
            raise CninfoError(f"巨潮 JSON 拉取失败: {url}: {exc}") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CninfoError(f"巨潮返回的不是合法 JSON: {url}") from exc

    def _post_form_json(self, url: str, payload: Dict[str, str]) -> Any:
        data = parse.urlencode(payload).encode("utf-8")
        req = urlrequest.Request(
            url,
            data=data,
            headers={
                "User-Agent": _UA,
                "Content-Type": "application/x-www-form-urlencoded",
                # 必填。巨潮反爬校验来源，缺 Referer/Origin 不报错、直接返回空结果。
                "Referer": "https://www.cninfo.com.cn/new/disclosure",
                "Origin": "https://www.cninfo.com.cn",
            },
            method="POST",
        )
        try:
            with self._urlopen(req) as resp:
                raw = resp.read()
        except CninfoError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CninfoError(f"巨潮公告检索失败: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CninfoError("巨潮公告检索返回的不是合法 JSON（可能被反爬拦截）") from exc

    # ------------------------------------------------------------------
    # orgId 解析
    # ------------------------------------------------------------------

    def _fetch_stock_map(self) -> Dict[str, str]:
        """拉官方 ``szse_stock.json`` 建 code→orgId 映射（实测 6259 条）。"""
        data = self._get_json(_STOCK_MAP_URL)
        stock_list = data.get("stockList", []) if isinstance(data, dict) else []
        result: Dict[str, str] = {}
        for item in stock_list:
            if not isinstance(item, dict):
                continue
            code, org = item.get("code"), item.get("orgId")
            if code and org:
                result[str(code)] = str(org)
        if not result:
            logger.warning("巨潮 orgId 映射表为空（结构可能已变），将走前缀兜底")
        return result

    def resolve_orgid(self, code: str) -> str:
        """查该股票的真实 orgId：官方映射表优先，失败再按前缀猜。

        映射表失败**不抛异常**——它只是优化项，猜错的后果是「查无公告」，会在
        检索处给出明确日志；为了一次网络抖动把整条链路打断不划算。
        """
        with _ORGID_LOCK:
            if not _ORGID_MAP:
                try:
                    fetched = self._fetch_stock_map()
                except CninfoError as exc:
                    logger.warning("orgId 映射表拉取失败，回退前缀规则: %s", exc)
                    fetched = {}
                # 只在确实拿到数据时写缓存；空结果保留，让下次调用还能重试。
                if fetched:
                    _ORGID_MAP.update(fetched)
            org = _ORGID_MAP.get(code)
        if org:
            return org
        guessed = fallback_orgid(code)
        logger.warning("未在巨潮映射表命中 %s，回退前缀 orgId=%s（可能查无公告）", code, guessed)
        return guessed

    def clear_orgid_cache(self) -> None:
        """清空进程内 orgId 缓存。映射表每日更新，跨天长跑需手动刷新。"""
        with _ORGID_LOCK:
            _ORGID_MAP.clear()

    # ------------------------------------------------------------------
    # 公告检索
    # ------------------------------------------------------------------

    def query_announcements(
        self,
        code: str,
        *,
        report_type: str = REPORT_ANNUAL,
        page: int = 1,
        page_size: int = CNINFO_MAX_PAGE_SIZE,
        se_date: str = "",
    ) -> List[Announcement]:
        """按报告类型检索该股票的公告。

        Args:
            code: 6 位股票代码。
            report_type: ``store`` 的报告类型常量，决定巨潮 category。
            page: 页码（1 起始）。
            page_size: 每页条数，超过 30 会被**静默截断**，这里先夹断并告警。
            se_date: 巨潮的日期区间，``"2025-01-01~2026-12-31"``，空为不限。

        Returns:
            按服务端返回顺序（通常最新在前）的公告列表，无结果返回空列表。
        """
        category = CATEGORY_BY_REPORT_TYPE.get(report_type)
        if category is None:
            raise CninfoError(f"未知 report_type: {report_type!r}")
        if page < 1:
            raise CninfoError(f"page 必须 ≥ 1，收到 {page}")
        if page_size > CNINFO_MAX_PAGE_SIZE:
            # 静默截断会让分页逻辑漏数据，所以要么告警要么夹断——这里两者都做。
            logger.warning(
                "page_size=%d 超过巨潮上限 %d，已夹断（巨潮本身也会静默截断，"
                "不夹断会导致分页漏数据）",
                page_size,
                CNINFO_MAX_PAGE_SIZE,
            )
            page_size = CNINFO_MAX_PAGE_SIZE

        org_id = self.resolve_orgid(code)
        payload = {
            "stock": f"{code},{org_id}",
            "tabName": "fulltext",
            "pageSize": str(page_size),
            "pageNum": str(page),
            "column": "",
            # 本次改造的关键：按报告类型过滤。不传就只能拿到该股最近 30 条
            # 全类型公告，年报会被挤出去。
            "category": category,
            "plate": "",
            "seDate": se_date,
            "searchkey": "",
            "secid": "",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        data = self._post_form_json(_QUERY_URL, payload)
        items = data.get("announcements") if isinstance(data, dict) else None
        if not items:
            logger.info(
                "巨潮无公告: code=%s orgId=%s category=%s —— 若确定该股有年报，"
                "先怀疑 orgId 兜底猜错",
                code,
                org_id,
                category,
            )
            return []
        return [a for a in (self._to_announcement(code, org_id, it) for it in items) if a]

    @staticmethod
    def _to_announcement(code: str, org_id: str, item: Any) -> Optional[Announcement]:
        """单条 JSON → Announcement；字段缺失/类型不对返回 None。"""
        if not isinstance(item, dict):
            return None
        try:
            title = _strip_html(str(item.get("announcementTitle", "") or ""))
            if not title:
                return None
            anno_id = str(item.get("announcementId", "") or "")
            raw_time = item.get("announcementTime", 0) or 0
            anno_time = int(raw_time) if isinstance(raw_time, (int, float)) else 0
            adjunct = str(item.get("adjunctUrl", "") or "")
            # announcementTypeName 对多数公告为 null，回退 adjunctType（"PDF"）。
            doc_type = str(item.get("announcementTypeName") or item.get("adjunctType") or "")
            return Announcement(
                title=title,
                doc_type=doc_type,
                date=_ts_to_date(raw_time),
                detail_url=build_detail_url(code, anno_id, org_id, raw_time),
                code=code,
                org_id=org_id,
                announcement_id=anno_id,
                announcement_time=anno_time,
                adjunct_url=adjunct,
                pdf_url=build_pdf_url(adjunct),
            )
        except (TypeError, ValueError) as exc:
            # 单条畸形不应让整页 30 条一起失败——那会把「一条脏数据」放大成
            # 「这只股票查无年报」。
            logger.warning("巨潮公告记录解析失败，已跳过: %s", exc)
            return None

    def find_filing(
        self,
        code: str,
        *,
        report_type: str = REPORT_ANNUAL,
        year: Optional[int] = None,
        max_scan_pages: int = 3,
        required: bool = False,
    ) -> Optional[Announcement]:
        """定位该股票该报告期/该年份的**正本**公告。

        Args:
            year: 报告期年份（标题里的年份），None 表示取最新一期。
            max_scan_pages: 最多翻几页。年报一只股票十年就有十份，往回找
                2020 年需要翻页；但翻页有成本，所以封顶。
            required: True 时查不到抛 :class:`NoFilingFoundError`，
                False 时返回 None。默认 False——「某股就是没有 Q3 报告」在批量
                预热里是正常结果，调用方自己决定要不要当错误。

        Returns:
            命中的公告；查不到返回 None（``required=True`` 时抛异常）。

        Note:
            命中的候选里会剔除摘要 / 英文版 / 已取消——实测这三类与正本同日披露
            且排在正本前面，「取第一条」会拿到只有十来页的摘要。
        """
        seen_years: List[int] = []
        for page in range(1, max(1, max_scan_pages) + 1):
            announcements = self.query_announcements(
                code, report_type=report_type, page=page
            )
            if not announcements:
                break
            candidates: List[Announcement] = []
            for ann in announcements:
                if report_type_of(ann.title) != report_type:
                    continue
                if not _is_primary_filing(ann.title):
                    logger.debug("剔除非正本公告: %s", ann.title)
                    continue
                y = filing_year(ann.title)
                if y is not None:
                    seen_years.append(y)
                if year is not None and y != year:
                    continue
                candidates.append(ann)
            if candidates:
                # 同一年内可能有原始版与修订版，按披露时间倒序取最新的那份。
                best = max(candidates, key=lambda a: a.announcement_time)
                logger.info(
                    "巨潮定位成功: %s %s%s → %s",
                    code,
                    report_type,
                    f"/{year}" if year else "",
                    best.title,
                )
                return best
        msg = (
            f"{code} 未找到 {report_type}"
            f"{f'/{year}' if year else ''} 年报（已翻 {max_scan_pages} 页，"
            f"见到年份: {sorted(set(seen_years), reverse=True) or '无'}）"
        )
        if required:
            raise NoFilingFoundError(msg)
        logger.warning(msg)
        return None

    def download_pdf(
        self,
        announcement: Announcement,
        dest_dir: str | os.PathLike[str],
        *,
        filename: Optional[str] = None,
        max_bytes: Optional[int] = None,
    ) -> str:
        """流式下载 PDF 附件到本地。

        流式而不是 ``resp.read()`` 一次读完：年报动辄上百 MB，全量预热时
        一次性读进内存很容易被 OOM  killer 直接带走（进程消失、无任何 Python
        级报错），这类故障事后极难定位。

        Args:
            max_bytes: 体积上限，默认构造时的 ``max_pdf_bytes``。超限会删掉已
                写出的半截文件——留半截 PDF 比报错更坏：下次流程会拿它去解析，
                然后报一个和真实原因毫不相干的解析错误。

        Returns:
            落盘文件的绝对路径。
        """
        if not announcement.pdf_url:
            raise CninfoError(
                f"该公告无 PDF 附件（adjunctUrl 为空）: {announcement.title!r}"
            )
        limit = max_bytes if max_bytes is not None else self.max_pdf_bytes
        dest = os.fspath(dest_dir)
        os.makedirs(dest, exist_ok=True)
        target = os.path.join(dest, filename or self._default_filename(announcement))

        req = urlrequest.Request(announcement.pdf_url, headers={"User-Agent": _UA})
        written = 0
        try:
            with self._urlopen(req) as resp:
                # 有 Content-Length 就能在写盘前就拒绝，不必先占磁盘。
                declared = resp.headers.get("Content-Length") if resp.headers else None
                if declared and declared.isdigit() and int(declared) > limit:
                    raise CninfoError(
                        f"PDF 体积 {int(declared)} 字节超过上限 {limit} 字节: {target}"
                    )
                with open(target, "wb") as fh:
                    while True:
                        chunk = resp.read(self.chunk_size)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > limit:
                            raise CninfoError(
                                f"PDF 体积超过上限 {limit} 字节: {announcement.pdf_url}"
                            )
                        fh.write(chunk)
        except CninfoError as exc:
            self._discard_partial(target, written)
            raise
        except urlerror.HTTPError as exc:
            self._discard_partial(target, written)
            if exc.code == 404:
                raise CninfoError(
                    f"PDF 不存在（HTTP 404），巨潮可能已下架该附件: {announcement.pdf_url}"
                ) from exc
            raise CninfoError(f"PDF 下载失败（HTTP {exc.code}）: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — 下载失败统一转领域异常
            self._discard_partial(target, written)
            raise CninfoError(f"PDF 下载失败: {announcement.pdf_url}: {exc}") from exc

        if written == 0:
            self._discard_partial(target, written)
            raise CninfoError(f"下载到空文件，巨潮附件可能已损坏: {announcement.pdf_url}")
        return os.path.abspath(target)

    @staticmethod
    def _discard_partial(path: str, written: int) -> None:
        """删掉写了一半的文件。失败只记日志——此时真正的异常更要报。"""
        if not written:
            return
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("清理半截文件失败 %s: %s", path, exc)

    @staticmethod
    def _default_filename(announcement: Announcement) -> str:
        """默认文件名 ``{code}_{年份}_{report_type}.PDF``。

        用**标题里的年份**而非披露日期：2025 年报在 2026 年披露，按披露日期命名
        会得到 ``600519_2026_annual.PDF``，看文件名根本判断不出是哪一期。
        """
        year = filing_year(announcement.title) or announcement.date[:4] or "unknown"
        rtype = report_type_of(announcement.title) or "other"
        return f"{announcement.code}_{year}_{rtype}.PDF"

    def fetch_filing_pdf(
        self,
        code: str,
        dest_dir: str | os.PathLike[str],
        *,
        report_type: str = REPORT_ANNUAL,
        year: Optional[int] = None,
        filename: Optional[str] = None,
        max_scan_pages: int = 3,
    ) -> LocalFiling:
        """一步到位：定位年报 + 下载 PDF。取不到正本抛 :class:`NoFilingFoundError`。

        这是本模块对外的**唯一推荐入口**：调用方不该关心 orgId、category、分页这些
        巨潮的实现细节。但它只做「定位 + 下载」这一件事，不编排抽取/对账流程。
        """
        announcement = self.find_filing(
            code, report_type=report_type, year=year, max_scan_pages=max_scan_pages,
            required=True,
        )
        assert announcement is not None  # required=True 保证非 None
        path = self.download_pdf(announcement, dest_dir, filename=filename)
        return LocalFiling(
            code=code,
            report_type=report_type,
            year=filing_year(announcement.title),
            path=path,
            announcement=announcement,
        )
