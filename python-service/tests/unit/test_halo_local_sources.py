"""PR-2 本地库接入测试：行业归属 / 概念标签 / 异动归因 / analyze 契约。"""

import asyncio
import os

import pytest

from halo import analyze as az
from halo.industry_map import (
    get_anomaly_narratives,
    get_concept_tags,
    get_industry_map,
)
from halo.store import FactStore

INDEX_PDF_DUCKDB = os.path.expanduser("~/.hithink-finance/index.duckdb")
SPECIAL_DUCKDB = os.path.expanduser("~/.hithink-finance/special.duckdb")

requires_index = pytest.mark.skipif(
    not os.path.exists(INDEX_PDF_DUCKDB), reason="index.duckdb 不在本机"
)
requires_special = pytest.mark.skipif(
    not os.path.exists(SPECIAL_DUCKDB), reason="special.duckdb 不在本机"
)


class FakeSrc:
    """按 SQL 关键字返回预置行，不碰真库。"""

    def __init__(self, mapping):
        self._mapping = mapping

    async def execute(self, query, params=None):
        for key, rows in self._mapping.items():
            if key in query:
                return rows
        return []


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# 行业归属
# ---------------------------------------------------------------------------


def test_level_from_code_prefix_not_members():
    """一级/二级由**指数编码前缀**判定，成分数只是交叉印证。

    成分数会随指数调样变动，拿它当判据等于埋雷：某天半导体从 189 调成 150，
    分类就会静默变一级。
    """
    src = FakeSrc({
        "u.tag = 'industry'": [
            {"index_code": "881112.TI", "industry_name": "钢铁", "members": 44},
            {"index_code": "884052.TI", "industry_name": "普钢", "members": 22},
        ]
    })
    m = run(get_industry_map(src, "600019.SH"))
    assert m["level1"] == "钢铁"
    assert m["level2"] == "普钢"
    assert all(x["level_source"] == "code_prefix" for x in m["memberships"])


def test_falls_back_to_members_when_prefix_unknown():
    src = FakeSrc({
        "u.tag = 'industry'": [
            {"index_code": "999001.TI", "industry_name": "某未知行业", "members": 8},
        ]
    })
    m = run(get_industry_map(src, "000001.SZ"))
    assert m["memberships"][0]["level_source"] == "members_heuristic"
    assert m["level1"] is None and m["level2"] == "某未知行业"


def test_single_level_membership_exposed_as_fine():
    """茅台只有「白酒」一个归属，level2 为空但细分信息不该丢。"""
    src = FakeSrc({
        "u.tag = 'industry'": [
            {"index_code": "881273.TI", "industry_name": "白酒", "members": 19},
        ]
    })
    m = run(get_industry_map(src, "600519.SH"))
    assert m["level1"] == "白酒" and m["level2"] is None
    assert m["fine"] == "白酒"


def test_missing_source_returns_full_shape():
    m = run(get_industry_map(None, "600519.SH"))
    assert m["level1"] is None
    assert m["basis"] == "not_found"
    assert "未就绪" in m["as_of_note"]
    assert m["memberships"] == []


# ---------------------------------------------------------------------------
# 概念 / 异动
# ---------------------------------------------------------------------------


def test_concept_tags_failure_is_swallowed():
    class Boom:
        async def execute(self, q, p=None):
            raise RuntimeError("index 库不可用")

    assert run(get_concept_tags(Boom(), "600519.SH")) == []
    assert run(get_anomaly_narratives(Boom(), "600519.SH")) == []


def test_anomaly_drops_empty_content():
    src = FakeSrc({
        "v_anomaly_list": [
            {"capture_date": "2026-09-30", "tag_name": "大涨",
             "analysis_content": "  行业原因：政策利好  "},
            {"capture_date": "2026-09-29", "tag_name": "跌", "analysis_content": "   "},
        ]
    })
    got = run(get_anomaly_narratives(src, "000002.SZ"))
    assert len(got) == 1
    assert got[0]["content"] == "行业原因：政策利好"


# ---------------------------------------------------------------------------
# 真实库回归
# ---------------------------------------------------------------------------


@requires_index
def test_real_industry_coverage():
    import duckdb
    con = duckdb.connect(INDEX_PDF_DUCKDB, read_only=True)

    class Real:
        async def execute(self, q, p=None):
            cur = con.execute(q, list(p) if p else [])
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    src = Real()
    for code, l1, l2 in [("600519.SH", "白酒", None), ("600019.SH", "钢铁", "普钢"),
                         ("688981.SH", "半导体", "集成电路制造"),
                         ("000001.SZ", "银行", "股份制银行")]:
        m = run(get_industry_map(src, code))
        assert m["level1"] == l1, f"{code} 一级行业错：{m}"
        assert m["level2"] == l2, f"{code} 二级行业错：{m}"


@requires_special
def test_real_anomaly_coverage_is_event_driven():
    """异动归因只覆盖 1484/5571 只股票，非异动股为 0 条 —— 这是正常的。

    茅台/宝钢这类票一条都没有。若将来这里变成「有数据」，反而要查是不是把
    别的表接错了。
    """
    import duckdb
    con = duckdb.connect(SPECIAL_DUCKDB, read_only=True)

    class Real:
        async def execute(self, q, p=None):
            cur = con.execute(q, list(p) if p else [])
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    src = Real()
    assert run(get_anomaly_narratives(src, "600519.SH")) == []
    hot = run(get_anomaly_narratives(src, "000002.SZ"))
    assert hot, "000002.SZ 是近期异动股，应当有归因文本"
    assert "原因" in hot[0]["content"]


# ---------------------------------------------------------------------------
# analyze 的返回契约
# ---------------------------------------------------------------------------


def test_analyze_without_filing_returns_full_structure(tmp_path, monkeypatch):
    """无年报事实是**最常见的正常情况之一**，返回结构必须完整。

    早返回缺键会让 agent / 前端在 KeyError 上崩，而不是拿到「暂无数据」。
    """
    monkeypatch.setattr(az, "_local_source", lambda name: None)
    monkeypatch.setattr(az, "get_financials_source", lambda *a, **k: None)

    store = FactStore(str(tmp_path / "f.sqlite"))
    res = run(az.analyze("600519.SH", store=store))

    assert res["ok"] is False
    for key in ("narratives", "ai_slots", "facts", "halo", "growth",
                "environment_disclosure", "asset_type_basis", "markdown"):
        assert key in res, f"缺字段 {key}"
    for sub in ("business_segments", "industry_map", "concepts", "anomalies"):
        assert sub in res["narratives"], f"narratives 缺 {sub}"
    assert res["markdown"], "应给出可读说明而不是空串"
    assert isinstance(res["ai_slots"], list)


def test_analyze_segments_not_collapsed_by_field(tmp_path, monkeypatch):
    """分部数据是「一个 field 多条」，不能被按 field 去重压成一个。"""
    monkeypatch.setattr(az, "_local_source", lambda name: None)
    monkeypatch.setattr(az, "get_financials_source", lambda *a, **k: None)

    store = FactStore(str(tmp_path / "s.sqlite"))
    store.upsert([
        {"thscode": "600519.SH", "period": "2025-12-31", "report_type": "annual",
         "field": "fixed_assets", "value": 1.0, "scope": "consolidated", "status": "verified"},
        # 同 field、不同 value_text：应各自成行
        {"thscode": "600519.SH", "period": "2025-12-31", "report_type": "annual",
         "field": "segment_revenue__产品", "value": 168_774_585_187.65, "value_text": "酒类",
         "scope": "consolidated", "status": "verified"},
        {"thscode": "600519.SH", "period": "2025-12-31", "report_type": "annual",
         "field": "segment_revenue__产品", "value": 146_499_906_480.49, "value_text": "茅台酒",
         "scope": "consolidated", "status": "verified"},
        {"thscode": "600519.SH", "period": "2025-12-31", "report_type": "annual",
         "field": "segment_gross_margin__产品", "value": 0.912, "value_text": "酒类",
         "scope": "consolidated", "status": "verified"},
        {"thscode": "600519.SH", "period": "2025-12-31", "report_type": "annual",
         "field": "segment_gross_margin__产品", "value": 0.935, "value_text": "茅台酒",
         "scope": "consolidated", "status": "verified"},
    ])

    res = run(az.analyze("600519.SH", store=store))
    dims = res["narratives"]["business_segments"]
    assert "产品" in dims, f"分部维度应保留，实际 {list(dims)}"
    rows = dims["产品"]["rows"]
    names = [x["segment"] for x in rows]
    assert names == ["酒类", "茅台酒"], f"分部被压成了 {names}"
    # 同一维度内 share 之和应为 1（它们是同一总量的切分）
    total = sum(x["share"] for x in rows if x["share"])
    assert abs(total - 1.0) < 0.02, f"份额应归一，实得 {total}"
    assert res["narratives"]["segment_count"] == 2
