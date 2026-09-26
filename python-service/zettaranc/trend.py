"""
趋势分析 — 基于道氏理论、均线系统和趋势强度指标

对应 Go: internal/agent/tools/hithink_finance/analysis/trend.go

行序：rows[0] 是最新一根 K 线（见 utils 模块文档）。
"""

from typing import Dict, List, Optional, Tuple

from .utils import find_swings, num, nz, round2

MIN_BARS = 20
MIN_BARS_FOR_STRUCTURE = 12


def classify_adx(adx: Optional[float]) -> Tuple[str, str]:
    """ADX 分级。数据缺失时返回 ("unknown", "数据不足")。"""
    value = num(adx)
    if value is None:
        return "unknown", "数据不足"
    if value < 20:
        return "weak", "弱趋势"
    if value < 25:
        return "developing", " developing"
    if value < 50:
        return "strong", "强趋势"
    return "very_strong", "极强趋势"


def classify_supertrend(r: Dict) -> Tuple[str, Optional[float]]:
    """Supertrend 方向。"""
    st_dir = num(r.get("st_dir"))
    st_val = num(r.get("st_val"))
    if st_dir is None or st_dir == 0:
        return "unknown", st_val
    return ("bullish" if st_dir > 0 else "bearish"), st_val


def analyze_dow_structure(rows: List[Dict]) -> Tuple[str, str]:
    """
    道氏理论趋势结构（基于摆动点）。

    两个方向叠加：`find_swings` 返回的下标是**升序**（`i` 从小到大遍历），
    而 rows 是倒序的（`rows[0]` 最新）。两者相乘 ⇒ **列表开头是最近的摆动点**，
    列表末尾是最老的。

    所以"最近 vs 上一个"要取 `swings[0]` 和 `swings[1]`。
    旧实现取 `highs[-1]` / `highs[-2]`，拿到的是最老的两个摆动点，
    于是标准的 Higher High + Higher Low 上升趋势被判成下降趋势。
    """
    if len(rows) < MIN_BARS_FOR_STRUCTURE:
        return "consolidation", "数据不足，无法判断趋势结构"

    highs, lows = find_swings(rows, 5)
    if len(highs) < 2 or len(lows) < 2:
        return "consolidation", "摆动点不足，无法判断趋势结构"

    recent_high, prev_high = highs[0], highs[1]
    recent_low, prev_low = lows[0], lows[1]

    recent_h = nz(rows[recent_high]["high"])
    prev_h = nz(rows[prev_high]["high"])
    recent_l = nz(rows[recent_low]["low"])
    prev_l = nz(rows[prev_low]["low"])

    hh = recent_h > prev_h
    hl = recent_l > prev_l
    lh = recent_h < prev_h
    ll = recent_l < prev_l

    if hh and hl:
        return "uptrend", (
            f"Higher High ({recent_h:.2f} > {prev_h:.2f}) + "
            f"Higher Low ({recent_l:.2f} > {prev_l:.2f}) → 上升趋势结构"
        )
    if lh and ll:
        return "downtrend", (
            f"Lower High ({recent_h:.2f} < {prev_h:.2f}) + "
            f"Lower Low ({recent_l:.2f} < {prev_l:.2f}) → 下降趋势结构"
        )
    if hh and ll:
        return "consolidation", (
            f"Higher High ({recent_h:.2f} > {prev_h:.2f}) + "
            f"Lower Low ({recent_l:.2f} < {prev_l:.2f}) → 收敛/扩张震荡"
        )
    if lh and hl:
        return "consolidation", (
            f"Lower High ({recent_h:.2f} < {prev_h:.2f}) + "
            f"Higher Low ({recent_l:.2f} > {prev_l:.2f}) → 收敛三角形整理"
        )
    return "consolidation", "摆动结构不明确，处于整理阶段"


def analyze_ma_alignment(r: Dict) -> Tuple[str, str]:
    """
    均线排列 + 价格与 MA20 关系。

    两条结论彼此独立：MA60/MA120 缺失只应该让 alignment 退化成 mixed，
    不该把已经算得出的 price_vs_ma20 一起吞掉（旧实现早退返回 unknown）。
    """
    ma5, ma10 = num(r.get("ma5")), num(r.get("ma10"))
    ma20, ma60 = num(r.get("ma20")), num(r.get("ma60"))
    ma120, ma250 = num(r.get("ma120")), num(r.get("ma250"))

    has_stack = all(v is not None and v > 0 for v in (ma5, ma10, ma20))
    alignment = "mixed"
    if has_stack and ma60 and ma60 > 0:
        bullish = (
            ma5 > ma10 > ma20 > ma60
            and (ma120 is None or ma120 <= 0 or ma60 > ma120)
        )
        bearish = (
            ma5 < ma10 < ma20 < ma60
            and (ma120 is None or ma120 <= 0 or ma60 < ma120)
        )
        if ma120 and ma120 > 0:
            bullish = bullish and ma60 > ma120
            bearish = bearish and ma60 < ma120
        alignment = "bullish" if bullish else ("bearish" if bearish else "mixed")
    elif not has_stack:
        alignment = "unknown"

    close = num(r.get("close"))
    if ma20 is None or ma20 <= 0 or close is None:
        price_vs_ma20 = "unknown"
    else:
        price_vs_ma20 = "above" if close > ma20 else "below"

    return alignment, price_vs_ma20


def detect_ma_crossovers(rows: List[Dict]) -> List[Dict]:
    """检测均线交叉信号（rows[0]=最新，rows[1]=前一交易日）。"""
    if len(rows) < 2:
        return []

    curr, prev = rows[0], rows[1]
    pairs = [
        ("ma5", "ma10", "MA5/MA10"),
        ("ma5", "ma20", "MA5/MA20"),
        ("ma10", "ma20", "MA10/MA20"),
        ("ma10", "ma60", "MA10/MA60"),
        ("ma20", "ma60", "MA20/MA60"),
    ]

    crosses = []
    for short_key, long_key, name in pairs:
        cs, cl = num(curr.get(short_key)), num(curr.get(long_key))
        ps, pl = num(prev.get(short_key)), num(prev.get(long_key))
        if None in (cs, cl, ps, pl) or min(cs, cl, ps, pl) <= 0:
            continue
        if ps <= pl and cs > cl:
            crosses.append({
                "type": "golden_cross", "mas": name,
                "date": curr["date"], "desc": f"{name} 金叉",
            })
        elif ps >= pl and cs < cl:
            crosses.append({
                "type": "death_cross", "mas": name,
                "date": curr["date"], "desc": f"{name} 死叉",
            })
    return crosses


def detect_granville_signals(rows: List[Dict]) -> List[Dict]:
    """检测葛兰碧法则信号。MA20 缺失时如实返回空，不猜。"""
    if len(rows) < 2:
        return []

    curr, prev = rows[0], rows[1]
    c_close, c_ma20 = num(curr.get("close")), num(curr.get("ma20"))
    p_close, p_ma20 = num(prev.get("close")), num(prev.get("ma20"))
    c_low, p_low = num(curr.get("low")), num(prev.get("low"))
    c_high, p_high = num(curr.get("high")), num(prev.get("high"))

    if None in (c_close, c_ma20, p_close, p_ma20) or c_ma20 <= 0 or p_ma20 <= 0:
        return []

    signals: List[Dict] = []

    def emit(signal: str, desc: str) -> None:
        signals.append({"signal": signal, "desc": desc, "date": curr["date"]})

    # 买入1：价格上穿 MA20
    if p_close <= p_ma20 and c_close > c_ma20:
        emit("buy_1", "价格突破MA20（葛兰碧买入信号1）")

    # 买入2：回踩 MA20 后反弹
    if p_close > p_ma20 and c_close > c_ma20:
        touched = (
            (c_low is not None and abs(c_low - c_ma20) / c_ma20 < 0.01)
            or (p_low is not None and abs(p_low - p_ma20) / p_ma20 < 0.01)
        )
        if touched:
            emit("buy_2", "价格回踩MA20后反弹（葛兰碧买入信号2）")

    # 买入3：假跌破后快速收回
    if p_close < p_ma20 and c_close > c_ma20 and p_close / p_ma20 > 0.97:
        emit("buy_3", "价格假跌破MA20后快速收回（葛兰碧买入信号3）")

    # 卖出1：跌破 MA20
    if p_close >= p_ma20 and c_close < c_ma20:
        emit("sell_1", "价格跌破MA20（葛兰碧卖出信号1）")

    # 卖出2：反弹至 MA20 受阻
    if p_close < p_ma20 and c_close < c_ma20:
        blocked = (
            (c_high is not None and abs(c_high - c_ma20) / c_ma20 < 0.01)
            or (p_high is not None and abs(p_high - p_ma20) / p_ma20 < 0.01)
        )
        if blocked:
            emit("sell_2", "价格反弹至MA20受阻（葛兰碧卖出信号2）")

    return signals


def build_summary(
    dow_dir: str,
    alignment: str,
    price_vs_ma20: str,
    crossovers: List[Dict],
    granville: List[Dict],
    strength_label: str,
    st_dir: str,
    latest: Dict,
) -> Tuple[str, float, List[str]]:
    """
    构建综合判断。

    confidence 与 verdict 必须一致：方向票数接近时（verdict=中性）
    置信度不应该高。旧实现只看 |多-空|/总票数，1 票看空就输出
    "中性 + 0.95 置信度"，那不是置信度，是噪音。
    """
    bull_score = 0
    bear_score = 0
    key_signals: List[str] = []

    if dow_dir == "uptrend":
        bull_score += 2
    elif dow_dir == "downtrend":
        bear_score += 2

    if alignment == "bullish":
        bull_score += 2
    elif alignment == "bearish":
        bear_score += 2

    if price_vs_ma20 == "above":
        bull_score += 1
    elif price_vs_ma20 == "below":
        bear_score += 1

    if st_dir == "bullish":
        bull_score += 1
    elif st_dir == "bearish":
        bear_score += 1

    for cross in crossovers:
        if cross["type"] == "golden_cross":
            bull_score += 1
        else:
            bear_score += 1
        key_signals.append(cross["desc"])

    for signal in granville:
        if signal["signal"] in ("buy_1", "buy_2", "buy_3"):
            bull_score += 1
        else:
            bear_score += 1
        key_signals.append(signal["desc"])

    adx = num(latest.get("adx"))
    key_signals.append(
        f"ADX={adx:.1f}（{strength_label}）" if adx is not None
        else "ADX 数据不足"
    )
    di_plus, di_minus = num(latest.get("di_plus")), num(latest.get("di_minus"))
    if di_plus and di_minus and di_plus > 0 and di_minus > 0:
        key_signals.append(
            f"DI+>DI-（{di_plus:.1f}>{di_minus:.1f}）多方占优"
            if di_plus > di_minus
            else f"DI+<DI-（{di_plus:.1f}<{di_minus:.1f}）空方占优"
        )

    total = bull_score + bear_score
    if total == 0:
        return "中性", 0.3, key_signals

    diff = bull_score - bear_score
    if diff > 2:
        verdict = "看多"
    elif diff < -2:
        verdict = "看空"
    else:
        verdict = "中性"

    # 方向一致性：|diff| 相对总票数的占比，缩放到 [0.3, 0.95]。
    # verdict=中性 时上限压到 0.5，避免"高置信度的中性"。
    skew = abs(diff) / total
    if verdict == "中性":
        confidence = min(0.5, 0.3 + skew * 0.65)
    else:
        confidence = 0.3 + skew * 0.65
    return verdict, round(min(confidence, 0.95), 2), key_signals


def analyze_trend(rows: List[Dict], thscode: str = "") -> Dict:
    """
    趋势分析入口。

    Args:
        rows: 行情数据行（已通过 fetch_market_data 加载，rows[0] 最新）
        thscode: 透传给调用方；行数据里没有这个字段，不要再从 latest 里取。

    Returns:
        趋势分析结果 dict
    """
    if len(rows) < MIN_BARS:
        raise ValueError(f"数据不足：{len(rows)} 条，至少需要 {MIN_BARS} 条")

    latest = rows[0]

    dow_dir, dow_desc = analyze_dow_structure(rows)
    alignment, price_vs_ma20 = analyze_ma_alignment(latest)
    crossovers = detect_ma_crossovers(rows)
    granville = detect_granville_signals(rows)
    _, strength_label = classify_adx(num(latest.get("adx")))
    st_dir, st_val = classify_supertrend(latest)

    ma_values = {
        key: (
            None if num(latest.get(key)) is None
            else round2(num(latest.get(key)))
        )
        for key in ("ma5", "ma10", "ma20", "ma60", "ma120")
    }
    ma_missing = [k for k, v in ma_values.items() if v is None]

    verdict, confidence, key_signals = build_summary(
        dow_dir, alignment, price_vs_ma20,
        crossovers, granville, strength_label, st_dir, latest,
    )

    return {
        "thscode": thscode,
        "trend": {
            "direction": dow_dir,
            "dow_structure": dow_desc,
            "strength": strength_label,
            "adx": None if num(latest.get("adx")) is None else round2(num(latest["adx"])),
        },
        "moving_averages": {
            "alignment": alignment,
            "ma_values": ma_values,
            "ma_missing": ma_missing,
            "price_vs_ma20": price_vs_ma20,
            "crossovers": crossovers,
        },
        "granville_signals": granville,
        "supertrend": {
            "direction": st_dir,
            "value": None if st_val is None else round2(st_val),
        },
        "summary": {
            "verdict": verdict,
            "confidence": confidence,
            "key_signals": key_signals,
        },
    }
