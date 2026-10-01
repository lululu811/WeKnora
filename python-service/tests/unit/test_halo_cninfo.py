"""``halo.cninfo_source`` 单元测试。

全部用例**不触网**：``urlrequest.urlopen`` 被替换成假响应，payload 与 headers
都从假响应侧反向断言（即「我们确实按巨潮要求发了 category 和 Referer」）。
真实联网验证是另一回事，见验收时的手工跑法。

假响应用一个类而不是 ``httpx``/``responses`` 之类的 mock 库：本模块只用标准库
urllib，再为它引入一层 mock 依赖不划算。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import parse as urlparse

import pytest

from halo import cninfo_source as cs
from halo.cninfo_source import (
    CATEGORY_ANNUAL,
    CATEGORY_BY_REPORT_TYPE,
    CATEGORY_H1,
    CATEGORY_Q1,
    CATEGORY_Q3,
    CninfoError,
    CninfoSource,
    LocalFiling,
    NoFilingFoundError,
    build_pdf_url,
    fallback_orgid,
    filing_year,
    report_type_of,
)
from halo.store import REPORT_ANNUAL, REPORT_H1, REPORT_Q1, REPORT_Q3


# ----------------------------------------------------------------------
# 假 HTTP 层
# ----------------------------------------------------------------------


class FakeResponse:
    """够用的 urlopen 返回值：上下文管理器 + read + headers + status。"""

    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: Optional[Dict[str, str]] = None,
        chunk_size_ignorable: bool = True,
    ) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            out, self._pos = self._body[self._pos:], len(self._body)
            return out
        out = self._body[self._pos: self._pos + n]
        self._pos += len(out)
        return out

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeHttp:
    """记录所有出站请求，按 URL 前缀分发响应或异常。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.get_json: Dict[str, Any] = {}
        self.post_json: Any = None
        # 按 pageNum 分页返回，模拟「往回找旧年份需要翻页」。
        self.post_json_by_page: Dict[str, Any] = {}
        self.post_raises: List[Optional[BaseException]] = []
        self.pdf_body: bytes = b"%PDF-1.4 fake"
        self.pdf_headers: Dict[str, str] = {}
        self.pdf_raises: Optional[BaseException] = None

    def __call__(self, req, timeout=None):  # noqa: ANN001 — 签名对齐 urlopen
        full_url = req.full_url
        if full_url == cs._STOCK_MAP_URL:
            self.calls.append({"method": "GET", "url": full_url, "headers": dict(req.headers)})
            return FakeResponse(json.dumps(self.get_json).encode("utf-8"))
        if full_url == cs._QUERY_URL:
            body = dict(urlparse.parse_qsl(req.data.decode("utf-8")))
            self.calls.append(
                {
                    "method": "POST",
                    "url": full_url,
                    "headers": {k.lower(): v for k, v in req.headers.items()},
                    "payload": body,
                }
            )
            if self.post_raises:
                # 列表里的 None 是「本次不抛异常」的哨兵，模拟重试后成功。
                pending = self.post_raises.pop(0)
                if pending is not None:
                    raise pending
            if self.post_json_by_page:
                data = self.post_json_by_page.get(body.get("pageNum", "1"), {"announcements": []})
            else:
                data = self.post_json
            return FakeResponse(json.dumps(data).encode("utf-8"))
        # PDF 静态资源
        self.calls.append({"method": "GET", "url": full_url, "headers": dict(req.headers)})
        if self.pdf_raises is not None:
            raise self.pdf_raises
        return FakeResponse(self.pdf_body, headers=self.pdf_headers)

    def last_post(self) -> Dict[str, Any]:
        posts = [c for c in self.calls if c["method"] == "POST"]
        assert posts, "没有发出 POST 公告检索请求"
        return posts[-1]


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr(cs.urlrequest, "urlopen", fake)
    # 关掉真实 sleep，否则退避用例每次白等 0.8+1.6 秒。
    monkeypatch.setattr(cs.time, "sleep", lambda _s: None)
    return fake


@pytest.fixture(autouse=True)
def clean_caches():
    """orgId 映射是模块级全局，必须逐用例隔离，否则用例之间互相污染。"""
    cs._ORGID_MAP.clear()
    yield
    cs._ORGID_MAP.clear()


def make_source(**kwargs: Any) -> CninfoSource:
    """默认关限速（0 间隔）且不重试的 source，只有个别用例显式覆盖。"""
    params: Dict[str, Any] = {"min_interval": 0.0, "max_retries": 1}
    params.update(kwargs)
    return CninfoSource(**params)


def announcement_item(
    title: str,
    *,
    anno_id: str = "1225114741",
    ts: int = 1776355200000,
    adjunct: str = "finalpage/2026-04-17/1225114741.PDF",
) -> Dict[str, Any]:
    """一条巨潮公告的原始 JSON。字段名与线上实测一致。"""
    return {
        "announcementTitle": title,
        "announcementId": anno_id,
        "announcementTime": ts,
        "adjunctUrl": adjunct,
        "adjunctType": "PDF",
        "announcementTypeName": None,
    }


# ----------------------------------------------------------------------
# 分类映射：本次改造的核心
# ----------------------------------------------------------------------


def test_category_codes_match_cninfo_spec():
    assert (CATEGORY_ANNUAL, CATEGORY_H1, CATEGORY_Q1, CATEGORY_Q3) == (
        "category_ndbg_szsh",
        "category_bndbg_szsh",
        "category_yjdbg_szsh",
        "category_sjdbg_szsh",
    )


def test_category_is_keyed_by_store_constants():
    """category 的键必须是 store 的 report_type，否则检索与落库口径会错位。"""
    assert CATEGORY_BY_REPORT_TYPE == {
        REPORT_ANNUAL: CATEGORY_ANNUAL,
        REPORT_H1: CATEGORY_H1,
        REPORT_Q1: CATEGORY_Q1,
        REPORT_Q3: CATEGORY_Q3,
    }
    # 键必须是 store 的常量本身（值相等但不是同一对象会在别处埋雷）
    for report_type in (REPORT_ANNUAL, REPORT_H1, REPORT_Q1, REPORT_Q3):
        assert any(k is report_type for k in CATEGORY_BY_REPORT_TYPE)


def test_query_sends_category_not_empty_string(http: FakeHttp):
    """回归测试：原实现把 category 写死成空串，年报会被挤出最近 30 条。"""
    http.post_json = {"totalAnnouncement": 1, "announcements": [announcement_item("贵州茅台2025年年度报告")]}
    make_source().query_announcements("600519", report_type=REPORT_ANNUAL)
    assert http.last_post()["payload"]["category"] == CATEGORY_ANNUAL
    assert http.last_post()["payload"]["category"] != ""


@pytest.mark.parametrize(
    "report_type,expected",
    [
        (REPORT_ANNUAL, CATEGORY_ANNUAL),
        (REPORT_H1, CATEGORY_H1),
        (REPORT_Q1, CATEGORY_Q1),
        (REPORT_Q3, CATEGORY_Q3),
    ],
)
def test_each_report_type_maps_to_its_own_category(http: FakeHttp, report_type, expected):
    http.post_json = {"announcements": []}
    make_source().query_announcements("600519", report_type=report_type)
    assert http.last_post()["payload"]["category"] == expected


def test_query_sends_required_anti_scrape_headers(http: FakeHttp):
    """Referer/Origin 缺任一头，巨潮不报错、直接返空结果。"""
    http.post_json = {"announcements": [announcement_item("贵州茅台2025年年度报告")]}
    make_source().query_announcements("600519")
    headers = http.last_post()["headers"]  # 假 HTTP 侧统一转小写
    assert headers["referer"] == "https://www.cninfo.com.cn/new/disclosure"
    assert headers["origin"] == "https://www.cninfo.com.cn"
    assert headers["content-type"] == "application/x-www-form-urlencoded"


def test_unknown_report_type_rejected(http: FakeHttp):
    http.post_json = {"announcements": []}
    with pytest.raises(CninfoError, match="未知 report_type"):
        make_source().query_announcements("600519", report_type="quarterly")


# ----------------------------------------------------------------------
# orgId 解析
# ----------------------------------------------------------------------


def test_orgid_prefers_official_map(http: FakeHttp):
    """601318 的 orgId 是 9900002221，前缀硬拼 gssx0{code} 一定查不到。"""
    http.get_json = {"stockList": [{"code": "601318", "orgId": "9900002221"}]}
    http.post_json = {"announcements": []}
    make_source().query_announcements("601318")
    assert http.last_post()["payload"]["stock"] == "601318,9900002221"


def test_orgid_map_is_cached_across_calls(http: FakeHttp):
    http.get_json = {"stockList": [{"code": "600519", "orgId": "gssh0600519"}]}
    http.post_json = {"announcements": []}
    src = make_source()
    src.query_announcements("600519")
    src.query_announcements("600519")
    stock_map_calls = [c for c in http.calls if c["url"] == cs._STOCK_MAP_URL]
    assert len(stock_map_calls) == 1, "映射表应当只拉一次"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("600519", "gssh0600519"),   # 6 → 上交所主板（实测恰好成立）
        ("601318", "gssh0601318"),   # 同段但实际是 9900002221 → 兜底会查不到
        ("688017", "gssh0688017"),   # 688 科创板也以 6 开头，落进沪市分支（见下方说明）
        ("430047", "gsbj0430047"),   # 4 → 北交所
        ("830799", "gsbj0830799"),   # 8 → 北交所
        ("000001", "gssz0000001"),
        ("300750", "gssz0300750"),
    ],
)
def test_fallback_orgid_prefix_rules(code, expected):
    assert fallback_orgid(code) == expected


def test_prefix_rule_cannot_cover_star_market():
    """前缀规则对科创板无解：688017 的真实 orgId 是 9900041602，前缀拼不出来。

    688xxx 虽然是「沪市科创板」，但代码以 6 开头，所以会落进 6→gssh0 分支。这不是
    可修的分类错误——科创板的 orgId 是 99000xxxx 形式，任何前缀规则都拼不出来。
    唯一可靠来源是官方映射表，本函数只是「映射表拉不到时别直接崩」的兜底。
    """
    assert fallback_orgid("688017") == "gssh0688017"  # 不会是 9900041602


def test_orgid_falls_back_when_map_fetch_fails(http: FakeHttp, caplog):
    """映射表拉不到不该中断链路，退到前缀规则并留日志。"""
    http.get_json = {}
    http.post_json = {"announcements": []}
    with caplog.at_level("WARNING"):
        make_source().query_announcements("600519")
    assert http.last_post()["payload"]["stock"] == "600519,gssh0600519"
    assert any("回退" in r.message or "回退" in r.getMessage() for r in caplog.records)


def test_empty_orgid_map_is_not_cached(http: FakeHttp):
    """空映射表不能写进缓存，否则一次网络抖动会永久毒化整个进程。"""
    http.get_json = {"stockList": []}
    http.post_json = {"announcements": []}
    src = make_source()
    src.query_announcements("600519")
    src.query_announcements("600519")
    stock_map_calls = [c for c in http.calls if c["url"] == cs._STOCK_MAP_URL]
    assert len(stock_map_calls) == 2


# ----------------------------------------------------------------------
# 标题解析
# ----------------------------------------------------------------------


def test_report_type_of_h1_is_not_misread_as_annual():
    """「2025年半年度报告」里含「年度报告」三个字，匹配顺序错了就会误判。"""
    assert report_type_of("贵州茅台2025年半年度报告") == REPORT_H1
    assert report_type_of("贵州茅台2025年年度报告") == REPORT_ANNUAL
    assert report_type_of("贵州茅台2025年第一季度报告") == REPORT_Q1
    assert report_type_of("贵州茅台2025年第三季度报告") == REPORT_Q3
    assert report_type_of("贵州茅台关于回购股份的公告") is None


def test_filing_year_comes_from_title_not_disclosure_date():
    """2025 年报在 2026-04-17 披露，period 必须取标题年份，否则整期错一年。"""
    assert filing_year("贵州茅台2025年年度报告") == 2025
    assert filing_year("关于召开2025年第一次临时股东大会的通知") == 2025
    assert filing_year("临时公告") is None


def test_html_highlight_tags_are_stripped(http: FakeHttp):
    """isHLtitle=true 会把命中的年份包上 <em>，不剥则所有匹配失效。"""
    http.post_json = {
        "announcements": [announcement_item("<em>2025</em>年年度报告")]
    }
    got = make_source().query_announcements("600519")
    assert got[0].title == "2025年年度报告"


# ----------------------------------------------------------------------
# 定位正本年报
# ----------------------------------------------------------------------


def _annual_batch() -> Dict[str, Any]:
    """模拟实测顺序：摘要、英文版排在正本之前。"""
    return {
        "totalAnnouncement": 74,
        "hasMore": True,
        "announcements": [
            announcement_item("贵州茅台2025年年度报告（英文版）", anno_id="1225114733"),
            announcement_item("贵州茅台2025年年度报告摘要", anno_id="1225114731"),
            announcement_item("贵州茅台2025年年度报告", anno_id="1225114741"),
            announcement_item("贵州茅台2024年年度报告", anno_id="1222993920", ts=1743609600000),
        ],
    }


def test_find_filing_skips_summary_and_english_version(http: FakeHttp):
    """「取第一条」是错的：摘要只有十来页，英文版文本层不是中文。"""
    http.post_json = _annual_batch()
    got = make_source().find_filing("600519", year=2025)
    assert got is not None
    assert got.title == "贵州茅台2025年年度报告"
    assert got.announcement_id == "1225114741"


def test_find_filing_picks_requested_year(http: FakeHttp):
    http.post_json = _annual_batch()
    got = make_source().find_filing("600519", year=2024)
    assert got is not None and got.announcement_time == 1743609600000


def test_find_filing_defaults_to_latest(http: FakeHttp):
    http.post_json = _annual_batch()
    got = make_source().find_filing("600519")
    assert got is not None and filing_year(got.title) == 2025


def test_find_filing_paginates_for_older_year(http: FakeHttp):
    """2020 年报在第 2 页，必须翻页才能找到。"""
    http.post_json_by_page = {
        "1": {"announcements": [announcement_item("贵州茅台2025年年度报告")], "hasMore": True},
        "2": {"announcements": [announcement_item("贵州茅台2020年年度报告")], "hasMore": False},
    }
    got = make_source().find_filing("600519", year=2020, max_scan_pages=3)
    assert got is not None and filing_year(got.title) == 2020
    pages = [c["payload"]["pageNum"] for c in http.calls if c["method"] == "POST"]
    assert pages == ["1", "2"]


def test_find_filing_stops_when_page_is_empty(http: FakeHttp):
    """翻到空页就该停，不要把 max_scan_pages 次请求全打出去。"""
    http.post_json_by_page = {"1": {"announcements": [], "hasMore": False}}
    make_source().find_filing("600519", year=2020, max_scan_pages=5)
    posts = [c for c in http.calls if c["method"] == "POST"]
    assert len(posts) == 1


def test_find_filing_returns_none_when_absent(http: FakeHttp, caplog):
    http.post_json = {"announcements": None}
    with caplog.at_level("WARNING"):
        got = make_source().find_filing("600519", year=2019)
    assert got is None


def test_find_filing_required_raises_domain_error(http: FakeHttp):
    """退市股无年报是正常业务结果，但要能被单独识别，不能和网络故障混在一起。"""
    http.post_json = {"announcements": []}
    with pytest.raises(NoFilingFoundError) as exc:
        make_source().find_filing("600519", required=True)
    assert issubclass(NoFilingFoundError, CninfoError)
    assert "600519" in str(exc.value)


# ----------------------------------------------------------------------
# 限速与退避
# ----------------------------------------------------------------------


def test_min_interval_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HALO_CNINFO_MIN_INTERVAL", "1.5")
    assert CninfoSource().min_interval == 1.5
    monkeypatch.setenv("HALO_CNINFO_MIN_INTERVAL", "not-a-number")
    assert CninfoSource().min_interval == 0.5  # 非法值回退默认，不崩


def test_rate_limiter_sleeps_between_requests(http: FakeHttp, monkeypatch: pytest.MonkeyPatch):
    slept: List[float] = []
    monkeypatch.setattr(cs.time, "sleep", slept.append)
    http.post_json = {"announcements": []}
    src = CninfoSource(min_interval=0.5, max_retries=1)
    src.query_announcements("600519")
    src.query_announcements("600519")
    # 第一次无等待（next_allowed 在过去），第二次必须等到间隔满足
    assert any(s >= 0.4 for s in slept), f"第二次请求未被限速，实际 sleep: {slept}"


def test_zero_interval_disables_throttle(http: FakeHttp, monkeypatch: pytest.MonkeyPatch):
    slept: List[float] = []
    monkeypatch.setattr(cs.time, "sleep", slept.append)
    http.post_json = {"announcements": []}
    src = CninfoSource(min_interval=0.0, max_retries=1)
    src.query_announcements("600519")
    src.query_announcements("600519")
    assert slept == []


def test_retry_on_5xx_then_success(http: FakeHttp, monkeypatch: pytest.MonkeyPatch):
    """5xx 是服务端临时状态，退避后重试有意义。"""
    slept: List[float] = []
    monkeypatch.setattr(cs.time, "sleep", slept.append)
    http.post_raises = [urlerror.HTTPError(cs._QUERY_URL, 503, "busy", {}, None), None]
    http.post_json = {"announcements": [announcement_item("贵州茅台2025年年度报告")]}

    got = make_source(max_retries=3).query_announcements("600519")
    assert len(got) == 1
    assert slept and slept[0] > 0, "5xx 之后应当有退避"
    # 指数退避：第二次重试的等待应是第一次的 2 倍
    if len(slept) > 1:
        assert slept[1] > slept[0]


def test_retry_on_timeout(http: FakeHttp):
    http.post_raises = [urlerror.URLError("timed out"), None]
    http.post_json = {"announcements": [announcement_item("贵州茅台2025年年度报告")]}
    assert len(make_source(max_retries=3).query_announcements("600519")) == 1


def test_no_retry_on_4xx(http: FakeHttp):
    """4xx = 请求本身不对，重试只会拖慢全量预热。"""
    http.post_raises = [urlerror.HTTPError(cs._QUERY_URL, 400, "bad", {}, None)]
    with pytest.raises(CninfoError, match="HTTP 400"):
        make_source(max_retries=3).query_announcements("600519")
    posts = [c for c in http.calls if c["method"] == "POST"]
    assert len(posts) == 1, "4xx 不应重试"


def test_retry_exhausted_raises_cninfo_error(http: FakeHttp):
    http.post_raises = [urlerror.HTTPError(cs._QUERY_URL, 500, "err", {}, None) for _ in range(3)]
    with pytest.raises(CninfoError, match="已重试 3 次"):
        make_source(max_retries=3).query_announcements("600519")


def test_429_is_retried(http: FakeHttp):
    """429 虽然是 4xx，但语义是「等会儿再来」，应当重试。"""
    http.post_raises = [urlerror.HTTPError(cs._QUERY_URL, 429, "slow down", {}, None), None]
    http.post_json = {"announcements": [announcement_item("贵州茅台2025年年度报告")]}
    assert len(make_source(max_retries=2).query_announcements("600519")) == 1


# ----------------------------------------------------------------------
# PDF 下载
# ----------------------------------------------------------------------


def test_build_pdf_url():
    assert build_pdf_url("finalpage/2026-04-17/1225114741.PDF") == (
        "http://static.cninfo.com.cn/finalpage/2026-04-17/1225114741.PDF"
    )
    assert build_pdf_url("") == ""


def test_download_pdf_streams_to_disk(http: FakeHttp, tmp_path):
    http.pdf_body = b"%PDF-1.4 " + b"x" * 5000
    http.post_json = _annual_batch()
    src = make_source()
    ann = src.find_filing("600519", year=2025)
    assert ann is not None

    target_dir = tmp_path / "nested" / "600519"  # 目录不存在，应自动创建
    path = src.download_pdf(ann, target_dir, filename="a.PDF")
    assert os.path.isfile(path)
    assert open(path, "rb").read() == http.pdf_body


def test_default_filename_uses_title_year(http: FakeHttp, tmp_path):
    """按披露日期命名会得到 600519_2026_annual.PDF，看不出是哪一期。"""
    http.post_json = _annual_batch()
    src = make_source()
    ann = src.find_filing("600519", year=2025)
    assert ann is not None
    path = src.download_pdf(ann, tmp_path)
    assert os.path.basename(path) == "600519_2025_annual.PDF"


def test_download_pdf_enforces_size_cap(http: FakeHttp, tmp_path):
    http.pdf_body = b"%PDF-1.4 " + b"y" * 100_000
    http.post_json = _annual_batch()
    src = make_source()
    ann = src.find_filing("600519", year=2025)
    assert ann is not None
    with pytest.raises(CninfoError, match="超过上限"):
        src.download_pdf(ann, tmp_path, max_bytes=1000)
    # 半截文件必须清掉：留半截 PDF 会让下游报一个与真实原因无关的解析错误
    assert not os.path.exists(os.path.join(os.fspath(tmp_path), "600519_2025_annual.PDF"))


def test_download_pdf_rejects_declared_oversize_before_writing(http: FakeHttp, tmp_path):
    http.pdf_headers = {"Content-Length": "999999999"}
    http.post_json = _annual_batch()
    src = make_source()
    ann = src.find_filing("600519", year=2025)
    assert ann is not None
    with pytest.raises(CninfoError, match="超过上限"):
        src.download_pdf(ann, tmp_path, max_bytes=1000)
    assert list(tmp_path.iterdir()) == []


def test_download_pdf_404_gives_clear_message(http: FakeHttp, tmp_path):
    http.pdf_raises = urlerror.HTTPError("http://static.cninfo.com.cn/x.PDF", 404, "nf", {}, None)
    http.post_json = _annual_batch()
    src = make_source()
    ann = src.find_filing("600519", year=2025)
    assert ann is not None
    with pytest.raises(CninfoError, match="404"):
        src.download_pdf(ann, tmp_path)


def test_download_pdf_without_attachment(http: FakeHttp, tmp_path):
    http.post_json = {"announcements": [announcement_item("贵州茅台2025年年度报告", adjunct="")]}
    src = make_source()
    got = src.query_announcements("600519")
    with pytest.raises(CninfoError, match="无 PDF 附件"):
        src.download_pdf(got[0], tmp_path)


def test_download_pdf_rejects_empty_body(http: FakeHttp, tmp_path):
    http.pdf_body = b""
    http.post_json = _annual_batch()
    src = make_source()
    ann = src.find_filing("600519", year=2025)
    assert ann is not None
    with pytest.raises(CninfoError, match="空文件"):
        src.download_pdf(ann, tmp_path)


# ----------------------------------------------------------------------
# 端到端（不触网）
# ----------------------------------------------------------------------


def test_fetch_filing_pdf_returns_local_filing(http: FakeHttp, tmp_path):
    http.pdf_body = b"%PDF-1.4 x" * 100
    http.post_json = _annual_batch()
    filing = make_source().fetch_filing_pdf("600519", tmp_path, year=2025)
    assert isinstance(filing, LocalFiling)
    assert (filing.code, filing.report_type, filing.year) == ("600519", REPORT_ANNUAL, 2025)
    assert filing.path == str(tmp_path / "600519_2025_annual.PDF")
    assert filing.announcement.pdf_url.endswith("1225114741.PDF")


def test_query_parses_announcement_fields(http: FakeHttp):
    http.post_json = _annual_batch()
    got = make_source().query_announcements("600519")
    ann = got[0]
    assert ann.code == "600519"
    assert ann.doc_type == "PDF"  # announcementTypeName 为 null 时回退 adjunctType
    assert ann.date == "2026-04-17"
    assert ann.adjunct_url.startswith("finalpage/2026-04-17/")
    assert ann.pdf_url == "http://static.cninfo.com.cn/" + ann.adjunct_url
    assert "announcementId=1225114733" in ann.detail_url
    assert "orgId=" in ann.detail_url


def test_malformed_announcement_is_skipped_not_fatal(http: FakeHttp):
    """一条脏记录不该被放大成「这只股票查无年报」。"""
    http.post_json = {
        "announcements": [
            "not-a-dict",
            {"announcementTitle": "", "announcementId": "x"},
            announcement_item("贵州茅台2025年年度报告"),
        ]
    }
    got = make_source().query_announcements("600519")
    assert len(got) == 1 and got[0].announcement_id == "1225114741"


def test_page_size_is_clamped_to_30(http: FakeHttp, caplog):
    """巨潮静默截断 pageSize，不夹断会让分页逻辑漏数据却毫无察觉。"""
    http.post_json = {"announcements": []}
    with caplog.at_level("WARNING"):
        make_source().query_announcements("600519", page_size=100)
    assert http.last_post()["payload"]["pageSize"] == "30"
    assert any("page_size" in r.getMessage() for r in caplog.records)


def test_invalid_page_number_rejected(http: FakeHttp):
    http.post_json = {"announcements": []}
    with pytest.raises(CninfoError, match="page"):
        make_source().query_announcements("600519", page=0)
