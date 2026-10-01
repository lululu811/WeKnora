"""HALO 规则/LLM 双通道抽取的单元测试。

断言重点只有三件事：

1. **单位归一化**做对了 —— 不归一就会把「1.2万元」和「12000元」判成冲突；
2. **口径（scope）没串** —— 合并和母公司同名科目必须分开；
3. **抽不到就是抽不到** —— 绝不用 0 或估值填充。

不联网、不读真实 PDF：这里喂的都是从真实巨潮年报版式抄下来的文本片段，
所以「格式对不对」这件事由 fixture 负责，不由运行环境负责。
"""

import asyncio

import pytest

from halo import extractor as ex
from halo.store import (
    EXTRACT_LLM,
    EXTRACT_RULE,
    SCOPE_CONSOLIDATED,
    SCOPE_PARENT,
    STATUS_DISPUTED,
    STATUS_PENDING,
    STATUS_VERIFIED,
)


# ---------------------------------------------------------------------------
# 数值解析
# ---------------------------------------------------------------------------


class TestParseNumber:
    def test_thousands_separator(self):
        assert ex.parse_number("2,847,653,126.87") == 2847653126.87

    def test_minus_sign_negative(self):
        assert ex.parse_number("-1,234.56") == -1234.56

    def test_accounting_paren_negative(self):
        """会计括号负数是中文年报的主流写法，不带任何符号位。"""
        assert ex.parse_number("(1,234.56)") == -1234.56

    def test_fullwidth_minus_and_paren(self):
        assert ex.parse_number("－1,234.56") == -1234.56
        assert ex.parse_number("（1,234.56）") == -1234.56

    def test_paren_must_be_paired(self):
        """只有一侧括号是残缺 token（PDF 换行错位），当没抽到而不是当成正数。"""
        assert ex.parse_number("(1,234.56") is None
        assert ex.parse_number("1,234.56)") is None

    def test_zero(self):
        assert ex.parse_number("0.00") == 0.0

    def test_garbage_returns_none_not_zero(self):
        """把抽不到伪装成 0，会让「缺失」静默变成「该项为零」。"""
        assert ex.parse_number("") is None
        assert ex.parse_number("---") is None
        assert ex.parse_number("不适用") is None
        assert ex.parse_number("1,2,3") is None


class TestUnitNormalization:
    @pytest.mark.parametrize(
        "raw,unit,expected",
        [
            (1.2, ex.UNIT_CNY_10K, 12000.0),
            (1.2, ex.UNIT_CNY_100M, 120000000.0),
            (1500.0, ex.UNIT_CNY_1K, 1500000.0),
            (2.5, ex.UNIT_CNY_1M, 2500000.0),
            (7.0, ex.UNIT_CNY, 7.0),
        ],
    )
    def test_scales_to_yuan(self, raw, unit, expected):
        assert ex.normalize_to_yuan(raw, unit) == pytest.approx(expected)

    def test_person_is_not_scaled(self):
        """人数乘任何系数都是错的。"""
        assert ex.normalize_to_yuan(34992, ex.UNIT_PERSON) == 34992.0

    def test_unknown_unit_defaults_to_1to1(self):
        """多数年报默认就是「单位：元」。猜错量纲比默认元危险得多（差 1e4~1e8）。"""
        assert ex.normalize_to_yuan(100.0, None) == 100.0

    def test_detect_unit_variants(self):
        assert ex.detect_unit("单位：元") == ex.UNIT_CNY
        assert ex.detect_unit("单位:人民币万元") == ex.UNIT_CNY_10K
        assert ex.detect_unit("金 额 单 位 ： 千 元") == ex.UNIT_CNY_1K
        assert ex.detect_unit("单位：亿元") == ex.UNIT_CNY_100M
        assert ex.detect_unit("没有任何单位声明") is None


# ---------------------------------------------------------------------------
# 规则通道
# ---------------------------------------------------------------------------

BALANCE_PAGE = """合并资产负债表
编制单位：贵州茅台酒股份有限公司  单位：元
项目        期末余额        年初余额
流动资产：
  货币资金    1,234,567,890.12    1,100,000,000.00
  应收账款      (1,234.56)            2,345.67
存货        61,358,565,841.19    57,879,042,249.94
流动资产合计    80,000,000,000.00    70,000,000,000.00
非流动资产合计    220,000,000,000.00    230,000,000,000.00
固定资产    28,476,531,268.70    26,000,000,000.00
在建工程    2,847,653,126.87    2,000,000,000.00
无形资产    19,475,122,026.00    19,000,000,000.00
商誉          1,500,000,000.00    1,000,000,000.00
资产总计    309,050,784,569.31    303,834,844,021.44

母公司资产负债表
编制单位：贵州茅台酒股份有限公司  单位：元
项目        期末余额        年初余额
固定资产    11,000,000,000.00    10,000,000,000.00
资产总计    150,000,000,000.00    148,000,000,000.00
"""


def _by_field(facts):
    return {(f.field, f.scope): f for f in facts}


class TestRuleExtraction:
    def test_extracts_all_fields(self):
        facts = ex.extract_by_rule(42, BALANCE_PAGE)
        got = {(f.field, f.scope) for f in facts}
        assert ("fixed_assets", SCOPE_CONSOLIDATED) in got
        assert ("construction_in_progress", SCOPE_CONSOLIDATED) in got
        assert ("inventory", SCOPE_CONSOLIDATED) in got
        assert ("intangible_assets", SCOPE_CONSOLIDATED) in got
        assert ("goodwill", SCOPE_CONSOLIDATED) in got
        assert ("total_assets", SCOPE_CONSOLIDATED) in got

    def test_thousands_separator_parsed(self):
        f = _by_field(ex.extract_by_rule(42, BALANCE_PAGE))[("fixed_assets", SCOPE_CONSOLIDATED)]
        assert f.value == pytest.approx(28476531268.70)
        assert f.unit == ex.UNIT_CNY
        assert f.source_page == 42
        assert f.extract_by == EXTRACT_RULE

    def test_takes_first_column_not_prior_year(self):
        """资产负债表两列（期末/年初）。取最后一列会安静地拿到上一期的数。"""
        f = _by_field(ex.extract_by_rule(42, BALANCE_PAGE))[("inventory", SCOPE_CONSOLIDATED)]
        assert f.value == pytest.approx(61358565841.19)   # 期末
        assert f.value != pytest.approx(57879042249.94)   # 年初

    def test_negative_paren_survives_the_whole_chain(self):
        """括号负号要能一路带到 Fact 上（解析 -> 抽取 -> 落库前的值）。

        科目本身现实中不会是负的，这里测的是**符号在管道里没被丢掉**。
        """
        facts = ex.extract_by_rule(42, "合并资产负债表\n单位：元\n固定资产 (1,234.56) 0.00\n")
        f = _by_field(facts)[("fixed_assets", SCOPE_CONSOLIDATED)]
        assert f.value == pytest.approx(-1234.56)

    def test_scope_separates_consolidated_from_parent(self):
        """同名科目在合并表和母公司表都出现，必须靠表标题区分而不是取第一个。"""
        m = _by_field(ex.extract_by_rule(42, BALANCE_PAGE))
        cons = m[("fixed_assets", SCOPE_CONSOLIDATED)].value
        parent = m[("fixed_assets", SCOPE_PARENT)].value
        assert cons == pytest.approx(28476531268.70)
        assert parent == pytest.approx(11000000000.00)
        assert cons != parent

    def test_total_assets_not_polluted_by_subtotals(self):
        """「流动资产合计」「非流动资产合计」都含「资产合计」，漏排除就会取错。"""
        m = _by_field(ex.extract_by_rule(42, BALANCE_PAGE))
        assert m[("total_assets", SCOPE_CONSOLIDATED)].value == pytest.approx(309050784569.31)

    def test_scattered_spaced_label_still_matches(self):
        """PDF 会把科目名拉散成「固 定 资 产」。"""
        facts = ex.extract_by_rule(1, "单位：万元\n固 定 资 产   1,234.56   1,000.00\n")
        assert facts[0].value == pytest.approx(12345600.0)   # 万元 -> 元
        assert facts[0].unit == ex.UNIT_CNY_10K

    def test_unit_scales_rule_values(self):
        facts = ex.extract_by_rule(1, "单位：万元\n固定资产 12.34 10.00\n")
        assert facts[0].value == pytest.approx(123400.0)

    def test_employees_total_is_people_not_money(self):
        facts = ex.extract_by_rule(7, "五、员工情况\n在职员工的数量合计 34,992\n")
        f = _by_field(facts)[("employees_total", SCOPE_CONSOLIDATED)]
        assert f.value == 34992.0
        assert f.unit == ex.UNIT_PERSON
        # 员工数没有金额单位，不该被页面的「单位：万元」污染
        facts = ex.extract_by_rule(7, "单位：万元\n在职员工的数量合计 34,992\n")
        assert _by_field(facts)[("employees_total", SCOPE_CONSOLIDATED)].value == 34992.0

    def test_no_fabrication_when_absent(self):
        """字段不存在就返回空，绝不补 0。"""
        facts = ex.extract_by_rule(3, "合并利润表\n单位：元\n营业总收入 100,000.00\n")
        assert facts == []

    def test_related_reserve_items_excluded(self):
        """「存货跌价准备」是备抵科目，不是存货余额。"""
        facts = ex.extract_by_rule(1, "存货跌价准备 123,456.00\n固定资产清理 222,222.00\n")
        assert facts == []


# ---------------------------------------------------------------------------
# LLM 通道
# ---------------------------------------------------------------------------


class _StubLLM:
    def __init__(self, items):
        self._items = items
        self.calls = []

    async def extract(self, page, text):
        self.calls.append((page, len(text)))
        return self._items


class _BoomLLM:
    async def extract(self, page, text):
        raise TimeoutError("LLM 超时")


class TestLLMChannel:
    def test_absent_extractor_degrades_to_empty(self, caplog):
        with caplog.at_level("WARNING"):
            out = asyncio.run(ex.extract_by_llm(None, 1, "任意文本"))
        assert out == []
        assert any("LLM 通道不可用" in r.message for r in caplog.records)

    def test_exception_degrades_to_empty(self, caplog):
        with caplog.at_level("WARNING"):
            out = asyncio.run(ex.extract_by_llm(_BoomLLM(), 1, "文本"))
        assert out == []
        assert any("LLM 抽取失败" in r.message for r in caplog.records)

    def test_normalizes_llm_units_too(self):
        """LLM 返回「1.2万元」也必须折算到元，否则比对会误判冲突。"""
        stub = _StubLLM([{"field": "fixed_assets", "value": 1.2, "unit": "CNY_10K"}])
        out = asyncio.run(ex.extract_by_llm(stub, 5, "文本"))
        assert out[0].value == pytest.approx(12000.0)
        assert out[0].extract_by == EXTRACT_LLM
        assert out[0].source_page == 5

    def test_drops_unknown_fields_and_nan_values(self):
        stub = _StubLLM([
            {"field": "made_up_field", "value": 1.0},   # 不在白名单
            {"field": "goodwill", "value": None},         # 没值
            {"field": "goodwill", "value": 500.0},        # 保留
        ])
        out = asyncio.run(ex.extract_by_llm(stub, 1, "文本"))
        assert [f.field for f in out] == ["goodwill"]

    def test_default_scope_is_consolidated(self):
        stub = _StubLLM([{"field": "goodwill", "value": 1.0}])
        assert asyncio.run(ex.extract_by_llm(stub, 1, "t"))[0].scope == SCOPE_CONSOLIDATED


# ---------------------------------------------------------------------------
# 双通道比对
# ---------------------------------------------------------------------------


def _rule(field, value, scope=SCOPE_CONSOLIDATED, page=10):
    return ex.Fact(field, value, ex.UNIT_CNY, scope, EXTRACT_RULE, page,
                   f"规则原文 {field}", ex.RULE_CONFIDENCE)


def _llm(field, value, scope=SCOPE_CONSOLIDATED, page=20):
    return ex.Fact(field, value, ex.UNIT_CNY, scope, EXTRACT_LLM, page,
                   f"LLM原文 {field}", ex.LLM_DEFAULT_CONFIDENCE)


class TestMergeChannels:
    def test_agree_within_tolerance_is_verified(self):
        out = ex.merge_channels([_rule("goodwill", 1e9)], [_llm("goodwill", 1e9 * (1 + 1e-9))])
        assert out[0]["status"] == STATUS_VERIFIED
        assert out[0]["extract_by"] == EXTRACT_RULE

    def test_disagreement_is_disputed_and_keeps_both_sides(self):
        out = ex.merge_channels([_rule("goodwill", 1e9)], [_llm("goodwill", 2e9)])
        rec = out[0]
        assert rec["status"] == STATUS_DISPUTED
        # 人工裁决要能两边都查到出处
        assert rec["source_page"] == 10 and rec["alt_source_page"] == 20
        assert rec["raw_text"] and rec["alt_raw_text"]
        assert rec["diff"] == pytest.approx(-1e9)

    def test_only_one_channel_is_pending(self):
        assert ex.merge_channels([_rule("goodwill", 1.0)], [])[0]["status"] == STATUS_PENDING
        assert ex.merge_channels([], [_llm("goodwill", 1.0)])[0]["status"] == STATUS_PENDING

    def test_unit_mismatch_does_not_cause_false_conflict(self):
        """核心防坑：1.2万元 与 12000元 折算后必须判为一致。

        走真实的 LLM 通道入口（而不是手工构造 Fact）——``Fact.value`` 的
        不变式是「构造时就是元」，归一化由通道负责，比对处不再乘第二次。
        """
        stub = _StubLLM([{"field": "fixed_assets", "value": 1.2, "unit": "CNY_10K"}])
        llm_facts = asyncio.run(ex.extract_by_llm(stub, 20, "文本"))
        assert llm_facts[0].value == pytest.approx(12000.0)

        rule_facts = ex.extract_by_rule(10, "合并资产负债表\n单位：元\n固定资产 12,000.00 10,000.00\n")
        rule = [f for f in rule_facts if f.field == "fixed_assets"][0]
        assert rule.value == pytest.approx(12000.0)

        out = ex.merge_channels([rule], llm_facts)
        assert out[0]["status"] == STATUS_VERIFIED

    def test_different_scope_not_compared(self):
        """合并和母公司本来就该不相等，放一起比会造出假冲突。"""
        out = ex.merge_channels(
            [_rule("fixed_assets", 1e9, scope=SCOPE_CONSOLIDATED)],
            [_llm("fixed_assets", 2e9, scope=SCOPE_PARENT)],
        )
        assert {r["status"] for r in out} == {STATUS_PENDING}
        assert len(out) == 2

    def test_zero_vs_zero_agrees(self):
        assert ex.merge_channels([_rule("goodwill", 0.0)], [_llm("goodwill", 0.0)])[0]["status"] == \
            STATUS_VERIFIED

    def test_far_apart_values_are_not_wrongly_verified(self):
        """错一列是数量级差异，必须能被抓出来。"""
        out = ex.merge_channels([_rule("total_assets", 3.0e11)], [_llm("total_assets", 8.0e10)])
        assert out[0]["status"] == STATUS_DISPUTED


class TestNoteNumberColumn:
    """回归：会计报表项目编号（附注列）被当成金额。

    真实行结构是**四列**不是三列::

        存货 10 61,427,421,796.18 54,343,285,157.47
        科目名 ^附注编号  ^本期数       ^上期数

    旧实现「取行内第一个数字」把 ``10`` 当成了金额。这个 bug 特别阴险：
    抽出来的不是 None、unit 也没报错，看上去「有数据」，而
    fixed_assets / inventory / 在建工程 / 无形资产 在 DuckDB 里没有对账口径，
    永远不会被发现。fixture 直接抄自贵州茅台 2025 年报 p57。
    """

    NOTE_NUMBER_PAGE = """贵州茅台酒股份有限公司2025 年年度报告
57 / 143
存货 10 61,427,421,796.18 54,343,285,157.47
固定资产 19 22,488,122,304.35 21,871,446,747.14
在建工程 20 2,471,886,030.58 2,149,619,937.05
无形资产 22 8,685,618,688.56 8,850,205,831.00
资产总计 303,834,844,021.44 298,944,579,918.70
"""

    def test_note_number_is_not_taken_as_amount(self):
        m = _by_field(ex.extract_by_rule(57, self.NOTE_NUMBER_PAGE))
        assert m[("inventory", SCOPE_CONSOLIDATED)].value == pytest.approx(61427421796.18)
        assert m[("fixed_assets", SCOPE_CONSOLIDATED)].value == pytest.approx(22488122304.35)
        assert m[("construction_in_progress", SCOPE_CONSOLIDATED)].value == \
            pytest.approx(2471886030.58)
        assert m[("intangible_assets", SCOPE_CONSOLIDATED)].value == pytest.approx(8685618688.56)

    def test_none_of_them_equal_a_note_number(self):
        """显式锁死：任何一个字段都不能等于那个 1~3 位的编号。"""
        m = _by_field(ex.extract_by_rule(57, self.NOTE_NUMBER_PAGE))
        for key, f in m.items():
            if f.field == "total_assets":
                continue
            assert f.value > 1_000_000, f"{key} 抽成了 {f.value}，疑似编号列"

    def test_line_without_amount_shape_is_not_extracted(self):
        """行内只有编号、没有金额形态 => 判为抽不到，返回空而不是硬取一个。

        这是最关键的一条：退回「取第一个数字」就等于把编号写进库里。
        """
        # 商誉这行在 p57 上只有一个编号，没有金额
        facts = ex.extract_by_rule(57, "商誉 26\n")
        assert facts == []

    def test_line_with_only_small_integers_yields_nothing(self):
        facts = ex.extract_by_rule(57, "固定资产 19 20\n")
        assert facts == []

    def test_large_integer_without_decimal_still_counts_as_amount(self):
        """无小数点但位数 >= 6 仍算金额（不是靠小数点一条判据）。"""
        facts = ex.extract_by_rule(1, "单位：元\n固定资产 1234567 1000000\n")
        assert facts[0].value == pytest.approx(1234567.0)

    def test_total_assets_without_note_number_still_right(self):
        """没有编号列的行不受影响 —— 修复不能把原来对的行弄坏。"""
        m = _by_field(ex.extract_by_rule(57, self.NOTE_NUMBER_PAGE))
        assert m[("total_assets", SCOPE_CONSOLIDATED)].value == \
            pytest.approx(303834844021.44)

    def test_employees_not_broken_by_amount_shape_rule(self):
        """34,992 是 5 位纯整数，按金额判据会被误杀。人数必须走另一条路。"""
        facts = ex.extract_by_rule(31, "五、员工情况\n在职员工的数量合计 34,992\n")
        f = _by_field(facts)[("employees_total", SCOPE_CONSOLIDATED)]
        assert f.value == 34992.0 and f.unit == ex.UNIT_PERSON


class TestCrossPageScope:
    """回归：scope 状态机不跨页。

    母公司资产负债表标题在 p59，数据排到 p61。p60 整页没有任何表标题，
    单页调用时 scope 会退回默认的 consolidated，把母公司数字错标成合并口径，
    而且**不报任何错**。真实例子：p60 的「资产总计 195,350,142,529.19」
    是母公司口径。
    """

    P59 = """59 / 143
母公司资产负债表
2025 年 12 月 31 日
存货 57,457,249,539.17 54,343,285,157.47
"""

    P60 = """60 / 143
开发支出 117,009,982.85 98,522,418.42
资产总计 195,350,142,529.19 180,236,524,477.01
"""

    def test_single_page_call_falls_back_to_consolidated(self):
        """单页调用（不传状态）时行为不变 —— 兼容原有调用方。"""
        facts = ex.extract_by_rule(60, self.P60)
        assert _by_field(facts)[("total_assets", SCOPE_CONSOLIDATED)].value == \
            pytest.approx(195350142529.19)

    def test_extract_pages_carries_scope_across_pages(self):
        facts, scope, _ = ex.extract_pages([(59, self.P59), (60, self.P60)])
        m = _by_field(facts)
        # p60 的资产总计必须落在母公司口径
        assert m[("total_assets", SCOPE_PARENT)].value == pytest.approx(195350142529.19)
        assert ("total_assets", SCOPE_CONSOLIDATED) not in m
        assert scope == SCOPE_PARENT

    def test_scope_argument_lets_caller_thread_state(self):
        """单页入口也能由调用方显式续接状态（pipeline 侧按页序传）。"""
        facts = ex.extract_by_rule(60, self.P60, scope=SCOPE_PARENT)
        assert _by_field(facts)[("total_assets", SCOPE_PARENT)].value == \
            pytest.approx(195350142529.19)

    def test_scope_switches_back_to_consolidated_on_new_heading(self):
        """跨页状态不是单向的：新表标题必须能把 scope 切回去。"""
        pages = [
            (59, self.P59),
            (60, self.P60),
            (61, "合并利润表\n单位：元\n净利润 51,000,000,000.00 45,000,000,000.00\n"),
        ]
        facts, _, _ = ex.extract_pages(pages)
        m = _by_field(facts)
        assert m[("total_assets", SCOPE_PARENT)].value == pytest.approx(195350142529.19)
        assert m[("net_profit", SCOPE_CONSOLIDATED)].value == pytest.approx(51000000000.00)

    def test_unit_also_carries_across_pages(self):
        """单位声明常落在表格首页，跨页续接同样必要。"""
        p1 = "单位：万元\n固定资产 12,345.00 10,000.00\n"
        p2 = "在建工程 678.00 500.00\n"
        facts, _, unit = ex.extract_pages([(1, p1), (2, p2)])
        m = _by_field(facts)
        assert m[("fixed_assets", SCOPE_CONSOLIDATED)].value == pytest.approx(123450000.0)
        # p2 没有单位声明，靠跨页继承才不会被当成「元」
        assert m[("construction_in_progress", SCOPE_CONSOLIDATED)].value == pytest.approx(6780000.0)
        assert unit == ex.UNIT_CNY_10K

    def test_title_page_carries_metadata_for_bare_data_pages(self):
        """复刻真实版式：**表头元信息全在标题页，数据在续页**。

        贵州茅台 2025 年报的实测标记分布（用户实测）::

            p56 合并资产负债表·标题页: '单位：元 币种：人民币'
            p57 合并资产负债表·数据页: 无单位、无标题
            p61 合并利润表·标题页:     '单位：元 币种：人民币'
            p62 合并利润表·数据页:     无单位、无标题

        单位判错会让 normalize_to_yuan 走错基数，而折算错是**静默的**：
        数值仍然像个合理的人民币金额，不会有任何人察觉。母公司表恰好标题与
        数据同页，所以它一直没暴露这个问题。
        """
        pages = [
            (56, "合并资产负债表\n2025 年 12 月 31 日\n单位：元  币种：人民币\n项目 附注 期末余额 年初余额\n"),
            (57, "存货 10 61,427,421,796.18 54,343,285,157.47\n"
                 "固定资产 19 22,488,122,304.35 21,871,446,747.14\n"),
            (61, "合并利润表\n2025 年度\n单位：元  币种：人民币\n项目 附注 本期金额 上期金额\n"),
            (62, "五、净利润\n净利润 39 85,310,324,833.67 81,696,853,340.12\n"),
        ]
        facts, _, _ = ex.extract_pages(pages)
        m = _by_field(facts)
        # 数据页继承了标题页的单位 -> 不会被当成「元」再折算一次
        assert m[("inventory", SCOPE_CONSOLIDATED)].unit == ex.UNIT_CNY
        assert m[("fixed_assets", SCOPE_CONSOLIDATED)].unit == ex.UNIT_CNY
        assert m[("inventory", SCOPE_CONSOLIDATED)].value == pytest.approx(61427421796.18)
        assert m[("net_profit", SCOPE_CONSOLIDATED)].unit == ex.UNIT_CNY
        assert m[("net_profit", SCOPE_CONSOLIDATED)].value == pytest.approx(85310324833.67)

    def test_ten_thousand_yuan_title_page_scales_data_page(self):
        """标题页写「单位：万元」时，续页的数必须按万元折算（1e4），不能按元。"""
        pages = [
            (1, "合并资产负债表\n单位：万元\n"),
            (2, "固定资产 22,488.12 21,871.45\n"),
        ]
        facts, _, _ = ex.extract_pages(pages)
        f = _by_field(facts)[("fixed_assets", SCOPE_CONSOLIDATED)]
        assert f.unit == ex.UNIT_CNY_10K
        assert f.value == pytest.approx(224881200.0)   # 22488.12 * 1e4

    def test_no_unit_declaration_anywhere_stays_none(self):
        """整段都没有单位声明时保持 None（按元处理），不编一个单位出来。"""
        facts, _, unit = ex.extract_pages([(1, "固定资产 1,234.56 0.00\n")])
        assert _by_field(facts)[("fixed_assets", SCOPE_CONSOLIDATED)].unit is None
        assert unit is None


class TestExtractPage:
    def test_end_to_end_verified(self):
        text = "合并资产负债表\n单位：万元\n固定资产 2847.65 2600.00\n"
        # 规则侧：2847.65 万元 = 28,476,500 元
        stub = _StubLLM([{"field": "fixed_assets", "value": 28476500.0, "unit": "CNY"}])
        out = asyncio.run(ex.extract_page(1, text, llm_extractor=stub))
        assert out[0]["value"] == pytest.approx(28476500.0)
        assert out[0]["status"] == STATUS_VERIFIED

    def test_end_to_end_pending_without_llm(self):
        text = "合并资产负债表\n单位：元\n固定资产 28,476,531,268.70 26,000,000,000.00\n"
        out = asyncio.run(ex.extract_page(1, text))
        assert len(out) == 1
        assert out[0]["status"] == STATUS_PENDING

    def test_records_are_store_upsert_shaped(self):
        """产出必须能直接喂给 FactStore.upsert，缺的两列由 pipeline 补。"""
        out = asyncio.run(ex.extract_page(1, "单位：元\n商誉 1,500,000,000.00 1,000,000,000.00\n"))
        for k in ("field", "value", "unit", "scope", "source_page",
                  "raw_text", "status", "confidence"):
            assert k in out[0]
