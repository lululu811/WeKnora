"""公告流接入报告：渲染、降级、以及「不默认取」。

三件事必须能区分开
------------------
1. 这只票近期**确实没有**公告；
2. 取公告**失败**了（降级成空）；
3. **压根没取**（include_announcements 未开启）。

三者都表现为「公告列表为空」，但含义完全不同。所以 render_markdown 恒渲染这一节，
未取时写明原因，而不是留白让人猜 —— 与「缺就标缺失，不估算」是同一条规矩。
"""

import asyncio

import pytest

from halo import analyze as az


class FakeAnnouncement:
    """CninfoSource.recent_announcements 返回的元素形状。"""

    def __init__(self, title, date="2026-09-30", doc_type="PDF"):
        self.title = title
        self.date = date
        self.doc_type = doc_type
        self.detail_url = f"https://www.cninfo.com.cn/new/disclosure/detail?{title}"
        self.pdf_url = f"http://static.cninfo.com.cn/{title}.PDF"


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_render_markdown_renders_announcement_table():
    md = az.render_markdown({
        "thscode": "600519.SH", "period": "2025-12-31",
        "announcements": [
            {"title": "关于回购公司股份的公告", "date": "2026-09-30",
             "doc_type": "PDF", "detail_url": "https://example.com/1"},
            {"title": "2026年半年度报告", "date": "2026-08-20",
             "doc_type": "PDF", "detail_url": "https://example.com/2"},
        ],
    })
    assert "## 四、近期公告" in md
    assert "关于回购公司股份的公告" in md
    assert "2026年半年度报告" in md
    assert "https://example.com/1" in md
    assert "共 2 条" in md


def test_render_markdown_states_why_announcements_are_absent():
    """空列表必须说明原因，不能只留一节空白。"""
    md = az.render_markdown({"thscode": "600519.SH", "period": "2025-12-31"})
    assert "## 四、近期公告" in md
    assert "未取公告" in md
    assert "include_announcements" in md


def test_render_markdown_survives_missing_announcements_key():
    """缺键与空列表同义 —— 早返回路径不带 announcements，不能因此崩。"""
    md = az.render_markdown({"thscode": "600519.SH"})
    assert "## 四、近期公告" in md


def test_render_markdown_escapes_pipe_in_title():
    """标题里的 | 会破坏 markdown 表格。"""
    md = az.render_markdown({
        "thscode": "600519.SH",
        "announcements": [{"title": "关于A|B事项的公告", "date": "2026-09-30",
                           "doc_type": "PDF", "detail_url": ""}],
    })
    row = [ln for ln in md.splitlines() if "关于A" in ln][0]
    assert row.count("|") == 4, f"表格列数应保持 3 列，实际: {row}"


def test_render_markdown_announcement_without_url_is_plain_text():
    md = az.render_markdown({
        "thscode": "600519.SH",
        "announcements": [{"title": "无链接公告", "date": "2026-09-30",
                           "doc_type": "PDF", "detail_url": ""}],
    })
    assert "无链接公告" in md
    assert "[](" not in md


def test_section_numbering_stays_contiguous():
    """插入公告一节后编号必须连续，否则读者会以为中间缺了一节。

    注意二（成长性）与三（治理诚信事实）是**条件渲染**的 —— 没有财务数据源或没有
    事实时它们整节不出现。所以这里只断言恒渲染的几节，并断言公告那一节确实在
    定性维度之前。
    """
    md = az.render_markdown({"thscode": "600519.SH", "period": "2025-12-31"})
    for num in ("一、", "四、", "五、", "六、", "七、"):
        assert f"## {num}" in md, f"缺章节 {num}"
    assert md.index("## 四、") < md.index("## 五、") < md.index("## 六、") < md.index("## 七、")


# ---------------------------------------------------------------------------
# 取数：降级与入参
# ---------------------------------------------------------------------------


def test_fetch_announcements_degrades_to_empty_on_failure(monkeypatch):
    """巨潮失败只该少一节，不该让整份报告失败。"""
    from halo import cninfo_source as cs

    def boom(*_a, **_k):
        raise cs.CninfoError("巨潮挂了")

    monkeypatch.setattr(cs.CninfoSource, "recent_announcements", boom)
    assert run(az._fetch_announcements("600519.SH")) == []


def test_fetch_announcements_uses_bare_code(monkeypatch):
    """巨潮只认 6 位代码，带后缀会被判为查无此股。"""
    from halo import cninfo_source as cs

    seen = {}

    def fake(self, code, **kwargs):
        seen["code"] = code
        return [FakeAnnouncement("测试公告")]

    monkeypatch.setattr(cs.CninfoSource, "recent_announcements", fake)
    rows = run(az._fetch_announcements("600519.SH"))
    assert seen["code"] == "600519"
    assert rows[0]["title"] == "测试公告"
    assert rows[0]["detail_url"]


def test_fetch_announcements_takes_one_page(monkeypatch):
    """只取一页：巨潮限速，公告不值得为它多翻页。"""
    from halo import cninfo_source as cs

    seen = {}

    def fake(self, code, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(cs.CninfoSource, "recent_announcements", fake)
    run(az._fetch_announcements("600519.SH"))
    assert seen["max_items"] == cs.CNINFO_MAX_PAGE_SIZE


def test_analyze_does_not_fetch_announcements_by_default(monkeypatch):
    """默认不取：每次分析都多打一次巨潮是不可接受的。"""
    called = []

    async def fake(code):
        called.append(code)
        return []

    monkeypatch.setattr(az, "_fetch_announcements", fake)

    class FakeStore:
        def latest_period(self, *a, **k):
            return None

        def query(self, *a, **k):
            return []

    result = run(az.analyze("600519", store=FakeStore()))
    assert called == [], "默认路径不该碰巨潮"
    assert result["announcements"] == []


def test_analyze_fetches_announcements_when_asked(monkeypatch):
    called = []

    async def fake(code):
        called.append(code)
        return [{"title": "T", "date": "D", "doc_type": "PDF",
                 "detail_url": "", "pdf_url": ""}]

    monkeypatch.setattr(az, "_fetch_announcements", fake)

    class FakeStore:
        def latest_period(self, *a, **k):
            return "2025-12-31"

        def query(self, *a, **k):
            return []

    result = run(az.analyze("600519", store=FakeStore(), include_announcements=True))
    assert called, "开启后必须真的去取"
    assert result["announcements"][0]["title"] == "T"
