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
    assert "## 二、消息面（近期公告）" in md
    assert "关于回购公司股份的公告" in md
    assert "2026年半年度报告" in md
    assert "https://example.com/1" in md
    assert "共 2 条" in md


def test_render_markdown_states_why_announcements_are_absent():
    """空列表必须说明原因，不能只留一节空白。"""
    md = az.render_markdown({"thscode": "600519.SH", "period": "2025-12-31"})
    assert "## 二、消息面（近期公告）" in md
    assert "未取公告" in md
    assert "include_announcements" in md


def test_render_markdown_survives_missing_announcements_key():
    """缺键与空列表同义 —— 早返回路径不带 announcements，不能因此崩。"""
    md = az.render_markdown({"thscode": "600519.SH"})
    assert "## 二、消息面（近期公告）" in md


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


def test_chapters_follow_the_v5_template_shape():
    """章节形状必须与 halo-skill 的 V5.0 模板一致。

    从模板抄下来的阅读习惯与批注位置要能直接复用，所以顺序与标题都得对得上；
    这也让「模板里有、报告里没有」这件事一眼可见（那些章节在第十一章列了清单）。

    注意第三章之后的 HALO/成长性各章是**恒渲染**的（拿不到就写不可计算），
    而治理诚信事实是条件渲染的（没有事实就没有那一节）。
    """
    md = az.render_markdown({"thscode": "600519.SH", "period": "2025-12-31"})
    chapters = [
        "## 第零章 执行摘要",
        "## 一、公司概况",
        "## 二、消息面（近期公告）",
        "## 三、HALO 六维（Python 计算）",
        "## 四、成长性（Python 计算）",
        "## 五、低淘汰率",
        "## 六、滞胀防御",
        "## 七、ESG",
        "## 八、管理层质量",
        "## 九、股东与资金面",
        "## 十、风险评估",
        "## 十一、综合评估与投资建议",
        "## 附录：数据来源与缺失项汇总",
    ]
    positions = []
    for c in chapters:
        assert c in md, f"缺章节 {c}"
        positions.append(md.index(c))
    assert positions == sorted(positions), "章节顺序必须与模板一致"


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
