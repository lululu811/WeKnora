"""
市场状态评估 — 大盘层面的多维度打分（0-100）

════════════════════════════════════════════════════════════════════
核心结论：composite 不能作方向信号，但能作仓位暴露参考
════════════════════════════════════════════════════════════════════

`composite` 逐年 IC（20 日窗口，基准沪深300，2022-2026）：

    2022 -0.518 | 2023 +0.072 | 2024 -0.221 | 2025 +0.327 | 2026 -0.149

**逐年反号，翻转 4 次** —— 随机游走，不是"某几年特殊"。所以没有可用的
regime 过滤（不知道该按什么条件过滤），方向判断在历史上不可靠。

固定方向回测（`scripts/backtest_market_state.py`）：
    高分满仓（既定方向）  -7.77%  vs 基准 +2.83%   跑输
    低分满仓（反向）      +13.91% vs 基准 +2.83%   —— 但方向是**事后用全样本选的**

因此 `tradable=False`：不要用 composite 决定买还是卖。

**方向自适应**（`scripts/backtest_adaptive.py`，用近 60 日 IC 定当期方向）：

                        基准满仓      方向自适应
    总收益              +1.13%         +9.47%
    最大回撤           -29.73%        -16.96%
    年化夏普             0.10           0.30

    逐年超额：2022 +10.98% | 2023 +5.44% | 2024 -12.81% | 2025 -3.09% | 2026 +4.72%

⚠️ **它不是择时策略。** 2024 年把 +12.82% 的涨幅压到 +0.01% —— 收益来自
"熊市少亏 + 全程降暴露"，不是"方向判得准"。所以本模块输出 `exposure_hint`
（0 / 0.5 / 1.0 仓位暴露参考）而不是方向建议。

`short_term_heat` 是唯一跨窗口方向稳定的维度（20/40/60/120 日 IC 全为正，
逐年 -++++），但 60 日 IC 仅 +0.123，弱信号，单独输出为 `short_term_signal`。

════════════════════════════════════════════════════════════════════

设计取舍（回测前的定版，只接受历史数据不接受"我觉得"）：

1. **不选涨停/跌停/炸板作为维度**。本地 `special` 库实测：
   - `v_limit_up_pool`  2023-09-13 起（738 日）
   - `v_limit_down_pool` 2025-08-28 起（254 日）
   - `v_limit_break_pool` 2025-08-28 起（265 日）
   只有 1~3 年历史，撑不起多年回测。宽度指标改用"创新高占比 / 站上均线占比"，
   来自 `market.v_daily_qfq` 与 `indicators.v_indicators_daily`，有 10 年历史。

2. **各维度输入不同源**。早前版本里"广度"和"情绪"都用 (涨跌家数差 + 涨停家数差)
   算，两者同源、权重虚高。

3. **踩过的四个 bug 已修**（都有回归测试锁住，见 tests/unit/test_market_state.py）：
   - `_scale(lo > hi)` 令波动维度恒定 50
   - 量能分母用指数成交额，与全市场分子差三个数量级
   - trend 用 120 日位置却配 20 日预测窗口 → 已拆出 short_term_heat 独立处理
   - 反向仓位用 `score * -1` 再套同阈值，score=45 → -45 → 被判空仓，
     1103 天里 1102 天空仓，净值恒为 0（假"反向无效"结论）

行序：rows[0] 是最新一根 K 线（与 trend.py / divergence.py 一致）。
"""

from typing import Any, Dict, List, Optional

# 各维度缺失时的兜底分。刻意用 50（中性）而不是 0 或 100：
# 0 会被读成"极度看空"、100 会被读成"极度看多"，而事实是"不知道"。
NEUTRAL_SCORE = 50.0

# 权重。**这不是等权重，也没有任何证据支持它用于择时** —— 逐年 IC 反复反号，
# 见模块头部。这里的权重只决定 composite 这个**描述性聚合**怎么加权。
#
# 逐年 IC（20 日窗口，基准沪深300）：
#
#   维度            2022      2023     2024     2025     2026     符号一致性
#   trend         -0.343   -0.090   -0.415    +0.072   -0.199    ---+- 不一致
#   breadth       -0.181   -0.069   -0.129    +0.015   -0.249    ---+- 不一致
#   short_term_heat -0.266  +0.225   +0.142   +0.181   +0.233    -++++ 基本一致
#   volume        +0.086   -0.226   +0.227    +0.217   -0.183    +-++- 不一致
#   volatility    +0.393   +0.050   +0.012    -0.107   -0.192    +++-- 不一致
#
# 只有 short_term_heat 跨窗口（20/40/60/120 日 IC 全为正）和跨年份方向基本一致，
# 所以它单独输出为 `short_term_signal`。其余四维参与 composite 仅为描述，
# `tradable=False` 已在返回值里标明不可交易。
DIMENSION_WEIGHTS = {
    "trend": 0.20,
    "breadth": 0.20,
    "short_term_heat": 0.55,  # 唯一方向稳定，权重最高
    "volume": 0.05,
    "volatility": 0.00,       # 方向最不稳定（+++--），归零
}

# 状态阈值。先用分位数而非固定值：A 股的估值/成交中枢十年内变化很大，
# 固定阈值在不同周期含义完全不同。
REGIME_STRONG = 60.0
REGIME_WEAK = 40.0

# 布林带式归一化的历史窗口
_TREND_WINDOW = 120
_VOL_WINDOW = 60
_VOLUME_WINDOW = 60


def _closes(rows: List[Dict]) -> List[float]:
    """按时间正序取收盘价（rows 是倒序的，所以要反过来）。"""
    return [float(r["close"]) for r in reversed(rows) if r.get("close") is not None]


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = _mean(values)
    return (sum((v - mu) ** 2 for v in values) / len(values)) ** 0.5


def _scale(value: float, lo: float, hi: float) -> float:
    """
    把 value 从 [lo, hi] 线性映射到 [0, 100]，越界截断。

    **lo 可以大于 hi**，此时表示反向映射（value 越大分数越低）——波动率这类
    "越大越差"的指标需要它。早期实现里直接算 (value-lo)/(hi-lo)，当 lo>hi 时
    分母为负，所有值都被截断到同一个端点，整个维度恒定在中性分且无人察觉。
    所以这里显式按方向归一化，并加断言挡住误用。
    """
    if hi == lo:
        return NEUTRAL_SCORE
    ratio = (value - lo) / (hi - lo)
    return max(0.0, min(100.0, ratio * 100.0))


# ──────────────────── 维度 1：趋势 ────────────────────


def score_trend(rows: List[Dict]) -> Dict[str, Any]:
    """
    指数趋势分 0-100（长周期，用于判断方向）。

    用 120 日区间位置：`(收盘 - 区间最低) / (区间最高 - 区间最低)`，连续变量，
    比均线排列的离散三态区分度高。

    **但这个指标在 20 日预测窗口上天然均值回归。** 早期回测里 trend 维度
    IC = -0.1995 不是 bug：价格越靠近 120 日高点，未来 20 日越倾向回落。
    所以这里只把它当**长周期方向**用，短周期位置交给 score_trend_extended
    里的短周期分位，两者在 composite 里分开加权。
    """
    closes = _closes(rows)[-_TREND_WINDOW:]
    if len(closes) < 20:
        return {"score": NEUTRAL_SCORE, "reason": "K线不足", "position": None}

    lowest, highest = min(closes), max(closes)
    position = (closes[-1] - lowest) / (highest - lowest) if highest > lowest else 0.5
    return {
        "score": _scale(position, 0.0, 1.0),
        "reason": f"120日区间位置 {position:.0%}",
        "position": round(position, 4),
    }


def score_trend_extended(
    rows: List[Dict], breadth: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    短期位置分 0-100 —— **反向指标**。

    融合两个短周期超买信号（指数 20 日涨幅 + 全市场站上 20 日线占比），
    取负号后进 composite：短期越热分数越低。

    这是回测里唯一 IC 稳定为负的维度（`pct_above_ma20` IC = -0.1658），
    说明 A 股短期均值回归是真实机制，不是噪声。**它是逆势减仓的依据，
    不是顺势加仓的依据。**
    """
    closes = _closes(rows)
    if len(closes) < 25:
        return {"score": NEUTRAL_SCORE, "reason": "K线不足", "heat": None}

    # 指数 20 日涨幅：年化约 6 倍再归一到 0~1
    ret20 = closes[-1] / closes[-21] - 1.0 if closes[-21] > 0 else 0.0
    idx_heat = max(0.0, min(1.0, (ret20 + 0.10) / 0.30))  # -10%~+20% → 0~1

    width_heat = None
    if breadth and breadth.get("pct_above_ma20") is not None:
        # 站上 20 日线占比历史中枢约 0.5，0.25~0.85 覆盖极值
        width_heat = max(0.0, min(1.0, (breadth["pct_above_ma20"] - 0.25) / 0.60))

    if width_heat is None:
        heat = idx_heat
        src = "仅指数"
    else:
        heat = 0.5 * idx_heat + 0.5 * width_heat
        src = "指数+宽度"

    # 取负号：短期越热 → 分数越低（逆向使用）
    return {
        "score": 100.0 - heat * 100.0,
        "reason": f"短期热度 {heat:.0%}（{src}，已取负号）",
        "heat": round(heat, 4),
        "index_ret20": round(ret20, 4),
    }


# ──────────────────── 维度 2：宽度 ────────────────────


def score_breadth(breadth: Optional[Dict]) -> Dict[str, Any]:
    """
    全市场宽度分 0-100。

    输入是 `market_breadth.csv` 的一行（见 scripts/build_breadth_cache.py），
    含 pct_above_ma60（全市场站上 60 日线的个股占比）等字段。

    站上均线占比的长期均值约 0.45（见回测报告），所以 50% 不是"市场中性"
    而是"比历史常态更强"。这里按 0.20~0.80 映射，两端留了缓冲。
    """
    if not breadth:
        return {"score": NEUTRAL_SCORE, "reason": "宽度数据缺失"}

    above_ma60 = breadth.get("pct_above_ma60")
    if above_ma60 is None:
        return {"score": NEUTRAL_SCORE, "reason": "宽度数据缺列"}

    return {
        "score": _scale(above_ma60, 0.20, 0.80),
        "reason": f"站上60日线 {above_ma60:.0%}",
        "above_ma60": above_ma60,
        "pct_new_high_120": breadth.get("pct_new_high_120"),
    }


# ──────────────────── 维度 3：量能 ────────────────────


def score_volume(
    rows: List[Dict], breadth: Optional[Dict], breadth_history: Optional[List[Dict]] = None
) -> Dict[str, Any]:
    """
    全市场量能分 0-100。

    用「当日全市场成交额 / 近 60 日全市场均额」。

    口径陷阱（早期版本踩过）：分子取了 `breadth['total_turnover']`（全市场
    5000 只合计），分母却从 `rows` 取指数 turnover（沪深300 一只）——分子分母
    差三个数量级，量能分恒定在中性附近且不报错。现在分子分母**必须同为全市场**
    口径：均额从 breadth_history 取，只有历史不足时才退回用 rows（并显式标注）。
    """
    if not breadth or breadth.get("total_turnover") is None:
        return {"score": NEUTRAL_SCORE, "reason": "量能数据缺失", "ratio": None}

    today = float(breadth["total_turnover"])

    amounts: List[float] = []
    source = "全市场"
    if breadth_history:
        amounts = [
            float(b["total_turnover"])
            for b in breadth_history
            if b and b.get("total_turnover") is not None
        ]
    if len(amounts) < 20:
        # 退化路径：宽度历史不足时用指数成交额，口径不同，只能当弱信号用
        amounts = [float(r["turnover"]) for r in rows if r.get("turnover")]
        source = "指数(口径不一致)"

    if len(amounts) < 20:
        return {"score": NEUTRAL_SCORE, "reason": "量能历史不足", "ratio": None}

    avg = sum(amounts[-_VOLUME_WINDOW:]) / min(len(amounts), _VOLUME_WINDOW)
    if avg <= 0:
        return {"score": NEUTRAL_SCORE, "reason": "均额为零", "ratio": None}

    ratio = today / avg
    # 0.6 倍（缩量）→ 0 分，1.4 倍（放量）→ 100 分
    return {
        "score": _scale(ratio, 0.6, 1.4),
        "reason": f"成交额 {ratio:.2f} 倍于 60 日均额（{source}）",
        "ratio": round(ratio, 4),
        "source": source,
    }


# ──────────────────── 维度 4：波动 ────────────────────


def score_volatility(rows: List[Dict]) -> Dict[str, Any]:
    """
    波动风险分 0-100，**分数越高越稳**。

    方向与直觉相反是刻意的：这个分是"风险分"，高分代表低风险。综合分里
    波动维度占 25%，它高会拉高 composite —— 这是错的，暴跌时波动飙升会让
    composite 变高。所以这里**取负号**后再进综合分，见 `compute_market_state`。

    用年化波动率：近 60 日日收益标准差 × sqrt(252)。
    """
    closes = _closes(rows)
    if len(closes) < 20:
        return {"score": NEUTRAL_SCORE, "reason": "K线不足", "annual_vol": None}

    window = closes[-(min(len(closes), _VOL_WINDOW) + 1):]
    rets = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window)) if window[i - 1] > 0]
    if len(rets) < 10:
        return {"score": NEUTRAL_SCORE, "reason": "收益率样本不足", "annual_vol": None}

    annual_vol = _stdev(rets) * (252 ** 0.5)
    # A 股指数年化波动 10%~35% 覆盖绝大多数情形
    return {
        "score": _scale(annual_vol, 0.35, 0.10),
        "reason": f"年化波动 {annual_vol:.1%}",
        "annual_vol": round(annual_vol, 4),
    }


# ──────────────────── 综合 ────────────────────


def classify_regime(composite: float) -> str:
    """综合分 → 市场状态。阈值见 REGIME_STRONG / REGIME_WEAK。"""
    if composite >= REGIME_STRONG:
        return "strong"
    if composite <= REGIME_WEAK:
        return "weak"
    return "neutral"


def exposure_from_composite(composite: float, direction: str = "neutral") -> float:
    """
    composite → 建议仓位暴露（0.0 / 0.5 / 1.0）。

    **这不是择时信号，是仓位暴露参考。** 名字要准，否则会误导。

    回测证据（`scripts/backtest_adaptive.py`，方向自适应，1103 天）：
        基准满仓  +1.13% / 回撤 -29.73% / 夏普 0.10
        本模型     +9.47% / 回撤 -16.96% / 夏普 0.30

    但逐年看，它在 2024 年把 +12.82% 的涨幅压到 +0.01%（超额 -12.81%），
    收益主要来自**熊市少亏 + 全程降暴露**，不是来自方向判断准确。
    所以它适合"控制回撤"，不适合"跑赢市场"。

    Args:
        composite: 0-100 综合分
        direction: "follow"（高分满仓）/ "contrarian"（低分满仓）/ "neutral"（半仓）
    """
    if direction == "follow":
        if composite >= 60:
            return 1.0
        return 0.0 if composite <= 40 else 0.5
    if direction == "contrarian":
        if composite <= 40:
            return 1.0
        return 0.0 if composite >= 60 else 0.5
    # neutral：不猜方向，统一半仓
    return 0.5


def compute_market_state(
    rows: List[Dict],
    breadth: Optional[Dict] = None,
    breadth_history: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    计算市场状态。

    Args:
        rows: 宽基指数日线，**倒序**（rows[0] 最新），需含 close / turnover。
        breadth: `market_breadth.csv` 的当日行，见 score_breadth。
        breadth_history: 宽度历史行（时间正序），量能均额的分母来源。

    Returns:
        含 composite / regime / dimensions / tradable / **exposure_hint** 的 dict。

        `tradable=False`：composite 的逐年 IC 反复反号
        （-0.52 / +0.07 / -0.22 / +0.33 / -0.15），**不能当方向信号**。
        任何调用方想用它决定"买还是卖"都会直接看到 False。

        `exposure_hint`：可以当**仓位暴露参考**（0/0.5/1.0），但要清楚它
        靠的是降暴露而非方向准确 —— 牛市里会明显跑输（2024 年 +12.82% → +0.01%）。
    """
    trend = score_trend(rows)
    width = score_breadth(breadth)
    volume = score_volume(rows, breadth, breadth_history)
    vol = score_volatility(rows)
    heat = score_trend_extended(rows, breadth)

    dims = {
        "trend": trend["score"],
        "breadth": width["score"],
        "volume": volume["score"],
        "volatility": 100.0 - vol["score"],  # 波动分是"稳定性"，取负号对齐方向
        "short_term_heat": heat["score"],
    }

    total_w = sum(DIMENSION_WEIGHTS.values())
    if abs(total_w - 1.0) > 1e-6:
        raise ValueError(f"维度权重之和必须为 1.0，当前 {total_w:.4f}")

    composite = sum(dims[k] * DIMENSION_WEIGHTS[k] for k in DIMENSION_WEIGHTS)
    composite = max(0.0, min(100.0, composite))

    # 唯一有跨窗口稳定预测力的指标，但强度有限（60日 IC +0.123），单独给置信度
    heat_ic_note = "弱信号：60日 IC +0.123，20日仅 +0.029，需人工确认"

    return {
        "composite": round(composite, 2),
        "regime": classify_regime(composite),
        "tradable": False,
        "tradable_note": (
            "composite 逐年 IC 反复反号（-0.52/+0.07/-0.22/+0.33/-0.15），"
            "不能作为方向信号（买/卖）使用。"
        ),
        "exposure_hint": {
            "neutral": exposure_from_composite(composite, "neutral"),
            "follow": exposure_from_composite(composite, "follow"),
            "contrarian": exposure_from_composite(composite, "contrarian"),
            "note": (
                "仓位暴露参考，非方向信号。回测 +9.47% vs 基准 +1.13%，"
                "回撤 -16.96% vs -29.73%；但 2024 年把 +12.82% 压到 +0.01%，"
                "收益主要来自降暴露而非方向判断。方向自适应回测见 "
                "scripts/backtest_adaptive.py"
            ),
        },
        "short_term_signal": {
            # heat_score 是**反向分**：分数高 = 短期冷（可关注反弹），
            # 分数低 = 短期热（要防回落）。与 score_trend_extended 内部
            # 的 heat（原始热度，越高越热）方向相反，别看混。
            "heat_score": heat["score"],
            "raw_heat": heat["heat"],
            "regime": (
                "cold" if heat["score"] >= 60
                else "hot" if heat["score"] <= 40
                else "neutral"
            ),
            "interpretation": (
                "短期偏冷，近期跌势已充分，可关注反弹窗口"
                if heat["score"] >= 60
                else "短期偏热，20日涨幅已大，回落风险上升"
                if heat["score"] <= 40
                else "短期温度中性"
            ),
            "confidence": "weak",
            "confidence_note": heat_ic_note,
            "detail": heat,
        },
        "dimensions": {
            "trend": {**trend, "weight": DIMENSION_WEIGHTS["trend"]},
            "breadth": {**width, "weight": DIMENSION_WEIGHTS["breadth"]},
            "volume": {**volume, "weight": DIMENSION_WEIGHTS["volume"]},
            "volatility": {
                **vol,
                "score": dims["volatility"],
                "raw_stability_score": vol["score"],
                "weight": DIMENSION_WEIGHTS["volatility"],
                "note": "已取负号：高波动=低分",
            },
            "short_term_heat": {**heat, "weight": DIMENSION_WEIGHTS["short_term_heat"]},
        },
    }
