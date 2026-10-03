"""成长性同比查询的回归测试。

现场症状：HALO 弹窗里「利润增长」的原始值恒为 0%，成长性被稳定算成
4.50/10 弱。根因不是数据缺失，而是 `halo/analyze.py` 里算同比的 SQL 把
「去年同期」写成了同一个年度——四个子查询的 WHERE 与参数完全一致，
`prev == cur`，于是 `(x/x-1)*100` 永远是 `0.0`。任何有 FY 数据的公司都
会得到这个结果，所以它不报错，只是恒错。

这里不写字符串断言，而是把源码里那条 SQL 抽出来、灌进内存 DuckDB 跑一遍：
「结构断言」只能证明代码没被改回原样，「行为断言」才能证明同比真的算对了。
"""

import ast
import pathlib
import unittest

ANALYZE = pathlib.Path(__file__).resolve().parents[2] / "halo" / "analyze.py"

# 只有两行是刻意的：营收和净利，同比分别涨 25% 和 50%。
ROWS = [
    ("300850.SZ", "annual", "FY", 2024, 100.0, 40.0),
    ("300850.SZ", "annual", "FY", 2025, 125.0, 60.0),
]


def _growth_sql() -> str:
    """从 analyze.py 里抠出成长性那一条 SQL 文本。

    直接抄一份到测试里的话，测试和实现就会各改各的；这里按调用现场
    （`src.execute` + 参数含 period 年份）定位，保证测的就是线上那一条。
    """
    tree = ast.parse(ANALYZE.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "execute"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        sql = node.args[0].value
        if not isinstance(sql, str):
            continue
        if "prev_rev" in sql and "prev_np" in sql:
            found.append(sql)
    assert len(found) == 1, f"应恰好找到一条同比 SQL，实际 {len(found)} 条"
    return found[0]


class GrowthYoqTest(unittest.TestCase):
    def setUp(self) -> None:
        import duckdb

        self.con = duckdb.connect(":memory:")
        self.con.execute(
            """
            CREATE TABLE v_income_statement (
                thscode VARCHAR, period VARCHAR, fiscal_period VARCHAR,
                fiscal_year INTEGER, operating_income DOUBLE, net_profit DOUBLE
            )
            """
        )
        self.con.executemany(
            "INSERT INTO v_income_statement VALUES (?, ?, ?, ?, ?, ?)", ROWS
        )

    def tearDown(self) -> None:
        self.con.close()

    def _yoy(self, period: str = "2025") -> tuple[float | None, float | None]:
        row = self.con.execute(
            _growth_sql(), ["300850.SZ", int(period[:4])] * 4
        ).fetchone()
        cols = [d[0] for d in self.con.description]
        got = dict(zip(cols, row))
        rev = (
            (got["cur_rev"] / got["prev_rev"] - 1) * 100
            if got["prev_rev"]
            else None
        )
        np_ = (
            (got["cur_np"] / got["prev_np"] - 1) * 100
            if got["prev_np"]
            else None
        )
        return rev, np_

    def test_同比取的是上一年度而不是同一年(self):
        rev, prof = self._yoy()
        self.assertIsNotNone(rev)
        self.assertAlmostEqual(rev, 25.0, places=6, msg="营收同比应为 125/100-1 = 25%")
        self.assertAlmostEqual(prof, 50.0, places=6, msg="净利同比应为 60/40-1 = 50%")

    def test_同比不再恒为零(self):
        # 这条就是现场那个 bug 的形状：任何公司、任何年份都算出 0%。
        for period in ("2025",):
            rev, prof = self._yoy(period)
            self.assertNotEqual(rev, 0.0)
            self.assertNotEqual(prof, 0.0)

    def test_缺少上一年度时返回空而不是零(self):
        # 2022 年没有上一年度数据。此时应当是「拿不到」(None)，
        # 而不是把「拿不到」渲染成 0%——后者会让缺数据看起来像零增长。
        rev, prof = self._yoy("2022")
        self.assertIsNone(rev)
        self.assertIsNone(prof)


if __name__ == "__main__":
    unittest.main()
