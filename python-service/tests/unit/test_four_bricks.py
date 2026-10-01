"""四块砖的单元测试。

钉住两件事：

1. **周期常量**。四块砖不注册成图表指标，所以不在
   ``config/indicators.yaml`` 里，也就不受 ``TestSeriesParamsArePinned``
   那道闸门管。周期散在代码里意味着"改一次只改一处"全靠自觉 —— 这条
   测试把它钉住：改 MA5/EMA10/EMA14/BBI 的周期会让它红。
2. **与前端原实现的一致性**。2026-10-01 搬后端时与
   ``indicators.ts`` 的 ``calcFourBricksDetails``（已删）逐点比对过
   601398.SH 的 260 根 bar，零差异。这里保留几个**手算得出**的用例，
   挡住未来的改写：EMA 的种子必须是前 N 根的简单平均而不是首值，BBI
   的降级平均语义，BBI 均线未成形时记为看多。
"""

import pytest

from zettaranc.four_bricks import (
    BBI_PERIODS,
    PERIOD_SHORT,
    PERIOD_TREND_A,
    PERIOD_TREND_B,
    analyze_four_bricks,
    bbi,
    ema,
    four_bricks,
    sma,
)


# ----------------------------------------------------------------------
# 周期常量
# ----------------------------------------------------------------------

def test_periods_are_pinned():
    """改周期会让这条红 —— 届时请确认是刻意的行为变更。

    这些值来自战法定义，不是可调参数：短线 MA5、趋势 EMA10/EMA14、
    多空 BBI(3,6,12,24)。搬后端时它们从 indicators.ts 的硬编码搬到这里，
    四块砖不注册成图表指标，因此不在 indicators.yaml 的 param 闸门内。
    """
    assert PERIOD_SHORT == 5
    assert (PERIOD_TREND_A, PERIOD_TREND_B) == (10, 14)
    assert BBI_PERIODS == (3, 6, 12, 24)


# ----------------------------------------------------------------------
# 基础算法
# ----------------------------------------------------------------------

def test_sma_matches_hand_calculation():
    # 1..10 的 SMA5：第 5 根起有值，第 5 根 = (1+2+3+4+5)/5 = 3
    got = sma([float(i) for i in range(1, 11)], 5)
    assert got[:4] == [None, None, None, None]
    assert got[4] == 3.0
    assert got[9] == 8.0  # (6+7+8+9+10)/5


def test_ema_seed_is_the_sma_not_the_first_value():
    """EMA 的首个值必须是前 N 根的**简单平均**。

    如果用首值做种子，之后每根都会带着一条永不收敛的偏移 —— 前端
    indicators.ts 的 calcEMA 用的是 SMA 种子，diff 恒不为零。
    """
    closes = [float(i) for i in range(1, 21)]
    got = ema(closes, 10)
    assert got[:9] == [None] * 9
    assert got[9] == 5.5  # (1+..+10)/10
    # 第二根：k = 2/11，prev = 5.5, close = 11
    k = 2.0 / 11.0
    assert got[10] == pytest.approx(round(11 * k + 5.5 * (1 - k), 2))


def test_ema_returns_all_none_when_shorter_than_period():
    assert ema([1.0, 2.0], 10) == [None, None]


def test_bbi_degrades_to_available_mas():
    """不足 24 根时用**已有**均线做平均，不返回 None。

    跟随 stock-score.ts 的 calcBBI（降级语义）而非 indicators.ts 的
    （null 语义）—— KLineWorkspace 评分卡用的是前者，两边必须一致。
    """
    closes = [float(i) for i in range(1, 13)]  # 12 根：BBI 需要 3/6/12/24
    got = bbi(closes)
    assert got[0] is None and got[1] is None
    # 第 3 根起只有 MA3 可用 → 降级为 MA3
    assert got[2] == pytest.approx(2.0)  # (1+2+3)/3
    # 第 6 根起 MA3+MA6 都有
    assert got[5] is not None


# ----------------------------------------------------------------------
# 四块砖本身
# ----------------------------------------------------------------------

def _rows(closes, opens=None):
    """构造 rows，**按该包约定 newest-first**（rows[0] 是最新一根）。

    four_bricks 与本包其余模块一样假定 rows[0] 是最新一根：均线/EMA 从
    前往后算，输出与 rows 同序，调用方取 [0] 当"当前状态"。

    opens 缺省取**前一根收盘**（即隔夜无跳空），不是当期收盘 —— 当期开收
    相等会让 bull4（close >= open）恒真，跌势也测不出负分。
    """
    if opens is None:
        opens = [closes[0]] + list(closes[:-1])
    rows = [
        {"date": f"2026-01-{i + 1:02d}", "open": o, "high": max(o, c) + 1,
         "low": min(o, c) - 1, "close": c}
        for i, (o, c) in enumerate(zip(opens, closes))
    ]
    rows.reverse()  # newest first
    return rows


def test_score_equals_sum_of_four_flags():
    rows = _rows([float(i) for i in range(1, 61)])
    for item in four_bricks(rows):
        expected = (1 if item["bull1"] else -1) + (1 if item["bull2"] else -1) \
            + (1 if item["bull3"] else -1) + (1 if item["bull4"] else -1)
        assert item["score"] == expected
        assert -4 <= item["score"] <= 4


def test_text_matches_score_band():
    rows = _rows([float(i) for i in range(1, 61)])
    for item in four_bricks(rows):
        s = item["score"]
        if s == 4:
            assert item["text"] == "四砖全红(+4)"
        elif s >= 2:
            assert item["text"] == f"多头共振(+{s})"
        elif s == -4:
            assert item["text"] == "四砖翻绿(-4)"
        elif s <= -2:
            assert item["text"] == f"空头承压({s})"
        else:
            assert item["text"] == f"多空博弈({s:+d})"


def test_rising_market_is_all_bull():
    """持续上涨 → 四砖全红。"""
    rows = _rows([10.0 * 1.02 ** i for i in range(60)])
    latest = four_bricks(rows)[0]
    assert latest["score"] == 4
    assert all(latest[k] for k in ("bull1", "bull2", "bull3", "bull4"))
    assert latest["text"] == "四砖全红(+4)"


def test_falling_market_is_all_bear():
    rows = _rows([100.0 * 0.98 ** i for i in range(60)])
    latest = four_bricks(rows)[0]
    assert latest["score"] == -4
    assert not any(latest[k] for k in ("bull1", "bull2", "bull3", "bull4"))


def test_output_is_same_order_and_length_as_rows():
    """返回必须与 rows 同序（rows[0] 是最新一根），否则整个端点报的是昨天的状态。"""
    rows = _rows([float(i % 7) + 1 for i in range(40)])
    got = four_bricks(rows)
    assert len(got) == len(rows)
    for r, g in zip(rows, got):
        assert g["date"] == r["date"]


def test_ma_not_ready_defaults_to_bullish():
    """均线还没成形时记为**看多**，与前端一致。

    数据不足时偏向多头，而不是凭空给一个空头判断 —— 空砖是结论，
    而这里只是"还没算出来"。
    """
    rows = _rows([10.0, 10.1, 10.2])
    item = four_bricks(rows)[0]
    assert item["bull1"] is True and item["bull2"] is True
    assert item["bull3"] is True


# ----------------------------------------------------------------------
# agent 用的摘要
# ----------------------------------------------------------------------

def test_analyze_reports_periods_used():
    """周期必须报出来：换周期会让历史读数不可比，而调用方无从得知。"""
    rows = _rows([float(i) for i in range(1, 61)])
    r = analyze_four_bricks(rows)
    assert r["insufficient"] is False
    assert r["periods"] == {"short": 5, "trend": [10, 14], "bbi": [3, 6, 12, 24]}


def test_analyze_bull_count_matches_flags():
    rows = _rows([10.0 * 1.02 ** i for i in range(60)])
    r = analyze_four_bricks(rows)
    assert r["bull_count"] == sum(
        1 for k in ("bull1", "bull2", "bull3", "bull4") if r[k])
    assert r["bull_count"] == 4


def test_analyze_says_so_when_bars_are_insufficient():
    r = analyze_four_bricks(_rows([10.0, 10.1]))
    assert r["insufficient"] is True
    assert r["actual_bars"] == 2
    assert r["required_bars"] > 2
    assert "根 K 线" in r["note"]


def test_analyze_note_points_at_knowledge_base_for_interpretation():
    """数值归这里，解释归知识库 —— note 必须把这条界线说出来。"""
    rows = _rows([float(i) for i in range(1, 61)])
    assert "知识库" in analyze_four_bricks(rows)["note"]
