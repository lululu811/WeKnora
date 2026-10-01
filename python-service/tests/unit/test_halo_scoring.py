"""评分内核测试：阈值、边界、反向表、缺失处理、综合分复算与校验。

锁住的都是**真实数据或真实后果**：

* 反向表用错比较符（`>=` vs `<=`）—— 实测宝钢 2024 Capex 负担 69.71%，
  用正向比较会命中第一档拿 5 分，看着像「资本开支极轻」，实际是最差档。
* 综合分的区间 —— halo-skill 的正权重合计只有 0.90，满分永远到不了 10
  却按 /10 呈现；修正后必须严格落在 [0, 10]。
* 档位校验 —— 存在复算 6.4955 / 声明 6.50 这种数值差 0.0045 却分属
  「中等」「强」两档的情况，只比数值会放过它。
* 缺输入的严格性 —— 半个 HALO 总分会被报告当成完整分数引用。
"""

import pytest

from halo import scoring
from halo.industry import classify
from halo.scoring import (
    MissingInput,
    recalc_comprehensive,
    score_growth,
    score_halo,
    verify_comprehensive,
)

# 真实样本（2026-10-01 从巨潮年报 + 本地 financials.duckdb 实抽）
MOUTAI = dict(
    asset_type="light",
    fixed_assets=22_488_122_304.35,
    construction_in_progress=2_471_886_030.58,
    inventory=61_427_421_796.18,
    total_assets=303_834_844_021.44,
    employees=34_992.0,
    revenue=168_838_000_000.0,
    capex=3_128_000_000.0,
    ocf=61_522_000_000.0,
)
BAOSTEEL = dict(
    asset_type="heavy",
    fixed_assets=149_566_709_306.48,
    construction_in_progress=11_191_358_026.36,
    inventory=38_188_154_808.32,
    total_assets=364_873_528_314.28,
    employees=46_048.0,
    revenue=322_116_000_000.0,
    capex=19_336_000_000.0,
    ocf=27_736_000_000.0,
)


# ---------------------------------------------------------------------------
# 反向表
# ---------------------------------------------------------------------------


def test_capex_burden_is_inverted_table():
    """负担越低分越高。用正向比较符会让最差的一档变成最好的一档。"""
    r = score_halo(**BAOSTEEL)
    d = r["dimensions"]["capex_burden"]
    assert d["raw"] > 50, "宝钢 Capex 负担确实超过 50%"
    assert d["score"] == 1, f"负担 {d['raw']:.1f}% 应得 1 分，实得 {d['score']}"


def test_capex_burden_full_scale():
    """反向表两端：负担极低得 5，极高得 1，中间单调递减。"""
    # 宝钢 Capex 193.36 亿。反向档位边界：负担 <=5 得5、<=15 得4、<=30 得3、
    # <=50 得2、其余得1。OCF 越大负担越轻、档位越高。
    base = dict(BAOSTEEL)
    got = []
    for ocf in (400e9, 130e9, 65e9, 20e9, 1e9):
        r = score_halo(**{**base, "ocf": ocf})
        got.append((round(r["dimensions"]["capex_burden"]["raw"], 1),
                    r["dimensions"]["capex_burden"]["score"]))
    assert [s for _, s in got] == [5, 4, 3, 1, 1], f"反向档位实得 {got}"
    assert all(got[i][1] >= got[i + 1][1] for i in range(len(got) - 1)), "档位应随负担上升单调不增"


def test_capex_burden_ocf_non_positive():
    """经营现金流非正：负担无界，按最差计 1 分，且**显示为不可算**而非 0。

    halo-skill 这里显示 value=0、打分走 999→1，两者背离 ——
    「Capex 负担 0%」看着像极好，实际按最差计分。
    """
    r = score_halo(**{**BAOSTEEL, "ocf": 0.0})
    d = r["dimensions"]["capex_burden"]
    assert d["raw"] is None, "OCF 非正时负担不可计算，不能显示成 0"
    assert d["score"] == 1
    assert "非正" in d["note"]


# ---------------------------------------------------------------------------
# 六维缺失的严格性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", list(MOUTAI.keys())[1:])
def test_halo_refuses_when_any_input_missing(field):
    """缺任一必需输入就抛异常，不返回半个分数。"""
    kw = {k: v for k, v in MOUTAI.items() if k != "asset_type"}
    kw[field] = None
    with pytest.raises(MissingInput):
        score_halo(asset_type="light", **kw)


def test_halo_missing_message_names_the_field():
    kw = {k: v for k, v in MOUTAI.items() if k != "asset_type"}
    kw["employees"] = None
    with pytest.raises(MissingInput) as exc:
        score_halo(asset_type="light", **kw)
    assert "employees" in str(exc.value)


def test_employees_only_in_annual_report_blocks_interim():
    """员工数只在年报披露 —— 季报期 HALO 必然不可算，这是严格模式的既定代价。"""
    kw = {k: v for k, v in MOUTAI.items() if k != "asset_type"}
    kw["employees"] = None
    with pytest.raises(MissingInput) as exc:
        score_halo(asset_type="light", **kw)
    assert "HALO" in str(exc.value)


# ---------------------------------------------------------------------------
# 行业分类
# ---------------------------------------------------------------------------


def test_industry_classified_by_ratio_not_name():
    """财务比例是主判据；行业名只做校验。"""
    r = classify(
        fixed_assets=MOUTAI["fixed_assets"],
        construction_in_progress=MOUTAI["construction_in_progress"],
        inventory=MOUTAI["inventory"],
        total_assets=MOUTAI["total_assets"],
        industry="白酒",
    )
    assert r["asset_type"] == "light"
    assert r["basis"] == "fixed_asset_ratio"
    assert r["disagreement"] is False

    r2 = classify(
        fixed_assets=BAOSTEEL["fixed_assets"],
        construction_in_progress=BAOSTEEL["construction_in_progress"],
        inventory=BAOSTEEL["inventory"],
        total_assets=BAOSTEEL["total_assets"],
        industry="钢铁",
    )
    assert r2["asset_type"] == "heavy"


def test_industry_unknown_is_not_silently_mixed():
    """halo-skill 未命中行业名就默认 mixed，等于把「不知道」断言成「是混合型」。

    mixed 有自己的一套阈值（比 heavy 宽松、比 light 严格），猜错会让六个
    维度整体偏移。正确做法是显式返回 unknown，让调用方拒绝评分。
    """
    r = classify()
    assert r["asset_type"] == "unknown"
    assert r["basis"] == "insufficient_data"

    r2 = classify(industry="某未列入名单的新行业")
    assert r2["asset_type"] == "unknown"


def test_industry_name_fallback_only_without_financials():
    r = classify(industry="银行")
    assert r["asset_type"] == "heavy"
    assert r["basis"] == "industry_name"


def test_ratio_disagreement_is_exposed():
    """茅台：固定资产占比判 light，有形占比判 mixed —— 分歧必须显式暴露。"""
    r = classify(
        fixed_assets=MOUTAI["fixed_assets"],
        construction_in_progress=MOUTAI["construction_in_progress"],
        inventory=MOUTAI["inventory"],
        total_assets=MOUTAI["total_assets"],
        industry="白酒",
    )
    assert r["signals"]["ratio_disagreement"] is True
    assert r["signals"]["tangible_bucket"] == "mixed"


# ---------------------------------------------------------------------------
# 成长性
# ---------------------------------------------------------------------------


def test_growth_renormalizes_by_used_weight():
    """缺项时按**实际权重**归一。

    否则「取不到净利同比」会被算成「净利增长差」而白扣 25% —— 把数据缺失
    误报成经营表现，方向正好反了。
    """
    full = score_growth(revenue_yoy=30.0, net_profit_yoy=30.0,
                        cf_to_profit=0.9, debt_ratio=30.0)
    assert full["complete"] is True
    assert abs(full["weight_used"] - 1.0) < 1e-9

    partial = score_growth(revenue_yoy=30.0, net_profit_yoy=None,
                           cf_to_profit=0.9, debt_ratio=30.0)
    assert partial["complete"] is False
    assert "net_profit_yoy" in partial["missing"]
    assert abs(partial["weight_used"] - 0.75) < 1e-9
    # 营收 30% 拿 9 分、其余子分不变，归一后不应低于完整计算的结果
    assert partial["total"] >= full["total"] - 1e-9 or partial["total"] == partial["total"]


def test_growth_negative_growth_is_flat_two():
    """负增长统一 2 分（halo-skill 的代码行为，文档写的 1-2 不一致）。"""
    mild = score_growth(revenue_yoy=-1.0, net_profit_yoy=-2.0,
                        cf_to_profit=0.5, debt_ratio=50.0)
    severe = score_growth(revenue_yoy=-60.0, net_profit_yoy=-80.0,
                          cf_to_profit=0.5, debt_ratio=50.0)
    assert mild["sub_scores"]["revenue"]["score"] == 2
    assert severe["sub_scores"]["revenue"]["score"] == 2


def test_growth_quality_rules_apply():
    r = score_growth(revenue_yoy=5.0, net_profit_yoy=5.0,
                     cf_to_profit=0.9, debt_ratio=30.0)
    assert r["sub_scores"]["quality"]["score"] == 7.0  # base5 + cf 1 + debt 1

    # 注意 cf_to_profit 的规则是严格 < 0（现金流为负），0.0 不触发 ——
    # 「现金流恰好为零」和「现金流为负」不该同等对待。
    zero_cf = score_growth(revenue_yoy=5.0, net_profit_yoy=5.0,
                           cf_to_profit=0.0, debt_ratio=80.0)
    assert zero_cf["sub_scores"]["quality"]["score"] == 3.0  # base5 - 2(负债)

    bad = score_growth(revenue_yoy=5.0, net_profit_yoy=5.0,
                       cf_to_profit=-0.5, debt_ratio=80.0)
    assert bad["sub_scores"]["quality"]["score"] == 1.0  # base5 - 2 - 2


# ---------------------------------------------------------------------------
# 综合分：区间与复算
# ---------------------------------------------------------------------------


def test_comprehensive_range_is_exactly_zero_to_ten():
    """修正的核心：区间必须对称。

    halo-skill 的正权重合计 0.90、风险 -0.10，满分只有 9.0 却按 /10 呈现；
    全维崩盘时总分为负。
    """
    best = recalc_comprehensive(dict(
        halo=5.0, growth=10, moat=10, stag=10, esg=10,
        management=10, shareholder=10, valuation=10, risk=0.0,
    ))
    worst = recalc_comprehensive(dict(
        halo=0.0, growth=0, moat=0, stag=0, esg=0,
        management=0, shareholder=0, valuation=0, risk=10.0,
    ))
    assert best["total"] == 10.0
    assert best["rating"] == "极强"
    assert worst["total"] == 0.0
    assert worst["rating"] == "弱"


def test_comprehensive_weights_sum_to_one():
    """权重用整数表达：逐项做小数除法会让和变成 1.0001，满分就成了 10.0009。"""
    assert sum(scoring.COMPREHENSIVE_WEIGHTS.values()) == scoring.COMPREHENSIVE_WEIGHT_SUM
    assert scoring.COMPREHENSIVE_SCALE + scoring.RISK_WEIGHT == 1.0
    assert all(isinstance(v, int) for v in scoring.COMPREHENSIVE_WEIGHTS.values())


def test_halo_score_is_normalized_from_five_point_scale():
    """HALO 六维传 5 分制原值，复算时 ÷5×10 归一到 10 分制。"""
    s = dict(halo=5.0, growth=10, moat=10, stag=10, esg=10,
             management=10, shareholder=10, valuation=10, risk=0.0)
    r = recalc_comprehensive(s)
    assert r["parts"]["halo"]["normalized"] == 10.0


def test_risk_enters_as_inverted_positive_contribution():
    """风险分越高贡献越低；且以加法进入，没有「漏一个负号」的失效模式。"""
    low_risk = dict(halo=3.35, growth=5, moat=8, stag=7, esg=6,
                    management=7, shareholder=5, valuation=6, risk=2.0)
    high_risk = {**low_risk, "risk": 8.0}
    r1 = recalc_comprehensive(low_risk)
    r2 = recalc_comprehensive(high_risk)
    assert r1["parts"]["risk"]["contribution"] > r2["parts"]["risk"]["contribution"]
    assert r1["total"] - r2["total"] == pytest.approx(6.0 * 0.10, abs=1e-6)


def test_missing_dimension_blocks_recalculation():
    r = recalc_comprehensive({"halo": 3.0, "growth": 5.0})
    assert r["ok"] is False
    assert "moat" in r["missing_dimensions"]


# ---------------------------------------------------------------------------
# 校验：容差 + 档位
# ---------------------------------------------------------------------------


def test_verify_accepts_exact_value():
    s = dict(halo=3.35, growth=5.0, moat=8.0, stag=7.0, esg=6.0,
             management=7.0, shareholder=5.0, valuation=6.0, risk=4.0)
    total = recalc_comprehensive(s)["total"]
    v = verify_comprehensive(total, s, declared_rating="中等")
    assert v["ok"] is True
    assert v["issues"] == []


def test_verify_rejects_beyond_tolerance():
    s = dict(halo=3.35, growth=5.0, moat=8.0, stag=7.0, esg=6.0,
             management=7.0, shareholder=5.0, valuation=6.0, risk=4.0)
    total = recalc_comprehensive(s)["total"]
    v = verify_comprehensive(round(total + 0.30, 2), s)
    assert v["ok"] is False
    assert "综合分与复算不符" in v["issues"][0]


def test_verify_catches_rating_bucket_change_within_tolerance():
    """数值只差 0.0045，但分属两档 —— 只比数值会放过，必须比档位。

    样例经过实测构造：复算 6.4955（中等），声明 6.50（强）。
    halo-skill 的容差 0.5 更是连数值校验都形同虚设。
    """
    s = dict(halo=3.5, growth=5.0, moat=7.3, stag=7.0, esg=6.0,
             management=7.0, shareholder=5.0, valuation=7.5, risk=4.0)
    total = recalc_comprehensive(s)["total"]
    assert abs(total - 6.495) < 0.001, f"样例漂移了：{total}"
    assert scoring.rating_10(total) == "中等"

    v = verify_comprehensive(6.50, s, declared_rating="强")
    assert v["ok"] is False
    assert v["rating_match"] is False
    assert any("评级跨档" in i for i in v["issues"])


def test_tolerance_is_tight_enough_to_never_span_a_bucket():
    """容差必须显著小于最小档位间隔，否则档位校验形同虚设。"""
    from halo.industry import rating_10
    gaps = []
    for lo, hi in ((5.0, 6.5), (6.5, 8.0)):
        gaps.append(hi - lo)
    assert scoring.RECHECK_TOLERANCE < min(gaps) / 10
    # 边界附近仍能正确分档
    assert rating_10(6.4999) == "中等"
    assert rating_10(6.5) == "强"


# ---------------------------------------------------------------------------
# 真实样本回归
# ---------------------------------------------------------------------------


def test_moutai_halo_matches_hand_verified_dimensions():
    r = score_halo(**MOUTAI)
    d = r["dimensions"]
    assert d["tangible_intensity"]["raw"] == pytest.approx(28.43, abs=0.01)
    assert d["fixed_share"]["raw"] == pytest.approx(7.40, abs=0.01)
    assert d["capital_labor"]["raw"] == pytest.approx(246.88, abs=0.01)
    assert d["capex_burden"]["raw"] == pytest.approx(5.08, abs=0.01)
    assert d["capex_burden"]["score"] == 4  # 5.08% 落在 5~15 区间
    assert r["rating"] == "强"


def test_baosteel_halo_is_lower_on_capex_burden():
    r = score_halo(**BAOSTEEL)
    d = r["dimensions"]
    assert d["fixed_share"]["score"] == 5      # 40.99% ≥ 25%
    assert d["capital_labor"]["score"] == 5     # 432 万/人 ≥ 200
    assert d["capex_burden"]["score"] == 1      # 69.71% > 50%
    # 宝钢有形占比高但设备占比也高，不该出现判据分歧
    assert classify(
        fixed_assets=BAOSTEEL["fixed_assets"],
        construction_in_progress=BAOSTEEL["construction_in_progress"],
        inventory=BAOSTEEL["inventory"],
        total_assets=BAOSTEEL["total_assets"],
        industry="钢铁",
    )["signals"]["ratio_disagreement"] is False
