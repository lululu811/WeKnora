"""板块 + 财务风险筛选的单元测试。

断言重点只有一个：**不淘汰没有数据的票，也不把 NULL 变成 0**。
风险筛选的整个意义是让人能复核"为什么这只过了、那只没过"，
所以淘汰原因必须具体到实际值与阈值。
"""

import pytest

from zettaranc import filters


class TestLikeEscaping:
    def test_wraps_with_wildcards(self):
        assert filters.escape_like_pattern("半导体") == "%半导体%"

    def test_underscore_is_escaped(self):
        """`_` 在 LIKE 里匹配任意单字符。不转义的话「科创_板」
        会把「科创X板」之类不相干的板块也拉进来。"""
        out = filters.escape_like_pattern("科创_板")
        assert "\\_" in out
        assert out == "%科创\\_板%"

    def test_percent_is_escaped(self):
        assert filters.escape_like_pattern("50%持股") == "%50\\%持股%"

    def test_backslash_is_escaped_first(self):
        """反斜杠必须最先转义，否则会把后面加的转义符再转义一遍。"""
        assert filters.escape_like_pattern("a\\b") == "%a\\\\b%"


class TestRiskFilters:
    def _entry(self, **risk):
        return {"name": "测试股", "risk": risk or None}

    def test_high_debt_is_rejected_with_actual_numbers(self):
        e = self._entry(debt_ratio=0.85, current_ratio=2.0)
        reason = filters.evaluate_risk_filters(e, max_debt_ratio=0.7)
        assert reason is not None
        assert "85.0%" in reason and "70.0%" in reason, \
            f"淘汰原因必须含实际值与阈值：{reason}"

    def test_low_debt_passes(self):
        assert filters.evaluate_risk_filters(
            self._entry(debt_ratio=0.3), max_debt_ratio=0.7) is None

    def test_missing_data_does_not_reject(self):
        """算不出来的指标**不淘汰**。资产负债率返回 0 会被读成"零负债"，
        那比"没有这个数据"危险得多。"""
        e = self._entry(debt_ratio=None, current_ratio=None)
        assert filters.evaluate_risk_filters(
            e, max_debt_ratio=0.7, min_current_ratio=1.0) is None
        assert "资产负债率" in (e.get("risk_missing") or [])

    def test_zero_debt_is_a_real_value_not_missing(self):
        """0 是合法值：真的零负债的票应该通过 0.7 阈值。"""
        assert filters.evaluate_risk_filters(
            self._entry(debt_ratio=0.0), max_debt_ratio=0.7) is None

    def test_st_is_rejected(self):
        e = {"name": "ST海龙", "is_st": True}
        reason = filters.evaluate_risk_filters(e, exclude_st=True)
        assert reason is not None and "ST" in reason

    def test_st_flag_absent_passes(self):
        assert filters.evaluate_risk_filters(
            {"name": "正常股", "is_st": False}, exclude_st=True) is None

    def test_loss_is_rejected_with_amount(self):
        e = {"name": "亏损股", "profit": {"parent_holder_net_profit": -3.2e8}}
        reason = filters.evaluate_risk_filters(e, require_profit=True)
        assert reason is not None and "3.20" in reason

    def test_multiple_reasons_are_all_listed(self):
        e = self._entry(debt_ratio=0.9, current_ratio=0.1)
        reason = filters.evaluate_risk_filters(
            e, max_debt_ratio=0.7, min_current_ratio=1.0)
        assert "资产负债率" in reason and "流动比率" in reason

    def test_no_filters_always_passes(self):
        assert filters.evaluate_risk_filters(self._entry()) is None


class TestFilterSql:
    def test_sector_sql_defaults_to_exact_match(self):
        """默认必须精确匹配。

        实测 `LIKE '%银行%'` 会同时命中行业「银行」42 只、「股份制银行」9 只、
        「国有大型银行」6 只，以及概念「参股银行」194 只 —— 后者把
        塔牌集团（水泥）、广东明珠（家电）这类只是参股了银行的公司拉进来。
        用户说"银行"要的是前者。
        """
        sql = filters.build_sector_filter_sql()
        assert "=" in sql and "LIKE" not in sql.upper()
        assert "?" in sql, "板块名必须参数绑定"

    def test_fuzzy_variant_exists_and_is_opt_in(self):
        sql = filters.build_sector_filter_sql_fuzzy()
        assert "LIKE" in sql.upper()
        assert "DISTINCT" in sql.upper()
        assert "LIMIT" not in sql.upper(), \
            "全量成员关系 122,368 行 > 100k 上限，展开必被静默截断"

    def test_sector_names_sql_reports_what_matched(self):
        """模糊匹配必须能说出命中了哪几个板块，否则并集数字无法复核。"""
        sql = filters.build_sector_names_sql()
        assert "u.tag" in sql
        assert "COUNT" in sql.upper()
        assert "GROUP BY" in sql.upper()

    def test_risk_sql_returns_null_not_zero_for_missing(self):
        """CASE WHEN 缺 else 分支 → NULL。写 COALESCE(x, 0) 会把
        "算不出"变成"零负债"。"""
        sql = filters.build_risk_filter_sql()
        assert "COALESCE" not in sql.upper()
        for col in ("debt_ratio", "current_ratio", "receivable_ratio"):
            assert col in sql
        # 零分母保护
        assert sql.count("> 0") >= 3

    def test_profit_sql_takes_only_the_latest_period(self):
        sql = filters.build_profit_filter_sql()
        assert "ROW_NUMBER() OVER" in sql
        assert "parent_holder_net_profit" in sql

    def test_st_sql_matches_both_st_and_star_st(self):
        sql = filters.build_st_names_sql()
        assert "%ST%" in sql
        assert "v_symbol" in sql


class TestSourcesAreDeclared:
    def test_every_source_names_a_real_table(self):
        for src in (filters.SECTOR_SOURCE, filters.BALANCE_SOURCE,
                    filters.INCOME_SOURCE):
            assert "." in src, f"{src} 必须是 库.表 的形式"

    def test_st_source_admits_it_is_a_name_match(self):
        """ST 是名称匹配而非监管标记，必须在出处里说清楚，
        否则用户会以为这是官方口径。"""
        assert "名称匹配" in filters.ST_SOURCE
        assert "非监管标记" in filters.ST_SOURCE
