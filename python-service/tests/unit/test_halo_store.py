"""``halo.store.FactStore`` 的落表测试。

只锁一条性质：**同一格事实写两次只留一行，且留下的是后写的那次**。

它不是洁癖。``analyze`` 取标量事实用的是 ``facts.setdefault(field, row)``
（先到先得），而 ``query`` 不带 ORDER BY —— 返回顺序就是 rowid 顺序。所以
「堆积行」等价于「**最老的值永久胜出**」：重新抽取修好的数会被旧数盖住，
而且没有任何报错。

实测触发：北京金山办公 2025 年报（688111.SH）二次同步后，每个标量字段各多出
一行，其中一行是上一轮抽取错出来的 ``net_profit=0.00``。
"""

import sqlite3

import pytest

from halo.store import (
    SCOPE_CONSOLIDATED,
    SCOPE_PARENT,
    STATUS_DISPUTED,
    STATUS_PENDING,
    STATUS_VERIFIED,
    FactStore,
)


def _rec(field, value, *, status=STATUS_PENDING, scope=SCOPE_CONSOLIDATED,
         value_text=None, page=1):
    return {
        "thscode": "688111.SH", "period": "2025-12-31", "report_type": "annual",
        "field": field, "value": value, "value_text": value_text,
        "unit": "CNY", "scope": scope, "source_page": page,
        "raw_text": f"{field} {value}", "extract_by": "rule",
        "status": status, "confidence": 1.0,
    }


@pytest.fixture()
def store(tmp_path):
    return FactStore(str(tmp_path / "halo.sqlite"))


def test_scalar_fact_is_overwritten_not_appended(store):
    """标量字段的 value_text 是 NULL，而 SQLite 的唯一索引视每个 NULL 互不相同。

    旧实现用 ``INSERT … ON CONFLICT (…, value_text)``，那条冲突子句对 NULL 永远
    匹配不上，于是二次同步给同一格追加一行；``analyze`` 的先到先得就把旧值留下了。
    """
    store.upsert([_rec("net_profit", 0.0, status=STATUS_DISPUTED, page=111)])
    store.upsert([_rec("net_profit", 1821869488.91, status=STATUS_VERIFIED, page=110)])

    rows = store.query("688111.SH", only_verified=False)
    assert len(rows) == 1, f"同一格事实堆了 {len(rows)} 行：{[(r['value'], r['status']) for r in rows]}"
    assert rows[0]["value"] == pytest.approx(1821869488.91)
    assert rows[0]["status"] == STATUS_VERIFIED
    assert rows[0]["source_page"] == 110


def test_reupsert_collapses_rows_left_by_the_old_bug(store, tmp_path):
    """旧实现留下的重复行，靠再写一次同键收敛掉 —— 事实表本就可重建。"""
    store.upsert([_rec("inventory", 522439.52)])
    with sqlite3.connect(str(tmp_path / "halo.sqlite")) as conn:
        conn.execute(
            "INSERT INTO halo_filing_facts (thscode, period, report_type, field, value,"
            " value_text, unit, scope, source_page, status, confidence)"
            " VALUES ('688111.SH','2025-12-31','annual','inventory',522439.52,NULL,"
            "'CNY','consolidated',105,'pending',1.0)"
        )
    assert len(store.query("688111.SH", only_verified=False)) == 2

    store.upsert([_rec("inventory", 522439.52, status=STATUS_VERIFIED, page=105)])
    rows = store.query("688111.SH", only_verified=False)
    assert len(rows) == 1
    assert rows[0]["status"] == STATUS_VERIFIED


def test_same_field_in_two_scopes_stays_two_rows(store):
    """合并口径与母公司口径是两条事实，删同键时不能把对方一起删掉。"""
    store.upsert([
        _rec("fixed_assets", 453218785.42),
        _rec("fixed_assets", 4349249.62, scope=SCOPE_PARENT, page=108),
    ])
    store.upsert([_rec("fixed_assets", 453218785.42)])

    rows = store.query("688111.SH", only_verified=False)
    assert {r["scope"]: r["value"] for r in rows} == {
        SCOPE_CONSOLIDATED: pytest.approx(453218785.42),
        SCOPE_PARENT: pytest.approx(4349249.62),
    }


def test_duplicate_keys_inside_one_batch_collapse_to_one_row(store):
    """分部数据来自多张表，同一 (field, value_text) 会在一批里出现两次。

    旧实现靠 ON CONFLICT 让后一条覆盖前一条；换成「删一遍再插」之后必须先在
    Python 侧按主键去重，否则批内第二条会撞唯一约束（实测 688111 的 segment_*
    行就把整个同步打成了 503）。
    """
    store.upsert([
        _rec("segment_revenue__行业", 1.0, value_text="软件"),
        _rec("segment_revenue__行业", 5926704478.63, value_text="软件"),
    ])
    rows = store.query("688111.SH", only_verified=False)
    assert len(rows) == 1
    assert rows[0]["value"] == pytest.approx(5926704478.63)   # 后写胜，与旧语义一致


def test_segments_with_different_value_text_are_separate_facts(store):
    """分部字段靠 value_text 区分同 field 的多行，不能被压成一行。"""
    store.upsert([
        _rec("segment_revenue__行业", 5926704478.63, value_text="软件"),
        _rec("segment_revenue__行业", 120140789.17, value_text="其他"),
    ])
    store.upsert([_rec("segment_revenue__行业", 5926704478.63, value_text="软件")])

    rows = store.query("688111.SH", only_verified=False)
    assert {r["value_text"]: r["value"] for r in rows} == {
        "软件": pytest.approx(5926704478.63),
        "其他": pytest.approx(120140789.17),
    }
