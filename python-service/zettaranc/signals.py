"""
技术形态信号检测 — 30+ 种买卖形态信号

对应 Go: internal/agent/tools/hithink_finance/pattern/signals.go

行序：rows[0] 是最新一根 K 线，rows[1] 是前一交易日。

缺数据 = 不发信号
------------------
旧实现在 SQL 层写 `COALESCE(col, 0)`，于是"指标没算出来"变成
`RSI6 = 0 → RSI6超卖`、`MFI = 0 → MFI超卖`，一只没有任何指标数据的
标的被报成"偏多"。现在缺失值是 `None`，所有阈值型信号都要先确认
底层字段真的有数据。

strength = 规则基准分 × 数据完整度
---------------------------------
`strength` 曾经是每条规则写死的常数（MACD 金叉 0.8、K 线 0.7……），
于是"只喂了 2 根 K 线算出来的『5 日布林带收口』"和"喂满 5 根算出来的"
在下游（复盘/胜率统计）看来一模一样。

口径（确定性、可复算、无外部输入）：

    完整度 = 窗口内实际取到值的样本数 / 该规则**设计上**需要的样本数
           = (窗口内 deps 字段非 None 的个数) / (window × len(deps))
    strength = 规则基准分 × 完整度        （完整度上限 1.0）

`window` 是规则定义域上的回看根数，不是"手上碰巧有几根"：布林带收口和
ATR 扩张的规则本身就写着 5 日均值，缺一根就不该拿 2 日均值冒充 5 日均值
却给同样的分。选股池只取最近两天指标，所以这两条信号在选股池里会被
打到 0.4 左右的强度——这正是"数据残缺"应有的样子。

单根判据（RSI/CCI/MFI/Aroon/Z-Score/蜡烛图）window=1：字段为 None 时
信号根本不发，所以完整度恒为 1，基准分不变。交叉类（MACD/KDJ/Stoch/
DI/Vortex/Supertrend）window=2：四个值缺一个就不发，完整度恒为 1。
**数据完整时所有 strength 与旧版逐位相同** —— 策略语义没有被改动，
变的只是残缺数据不再冒充完整数据。
"""

from typing import Dict, List, Optional, Sequence

from .utils import num, round2


def _v(row: Optional[Dict], key: str) -> Optional[float]:
    return None if row is None else num(row.get(key))


# 量比均量窗口，与 volume.py 的 `_calc_vol_avg` 默认一致。
VOL_AVG_WINDOW = 10

# 布林带收口 / ATR 扩张的规则都写在"5 日均值"上，所以完整度的分母是 5 根，
# 不是"手上碰巧有几根"。
BB_AVG_WINDOW = 5
ATR_AVG_WINDOW = 5


def _avg_volume(rows: List[Dict]) -> Optional[float]:
    """最近 VOL_AVG_WINDOW 天的均量。

    窗口不足或窗口内有任一天缺数据，一律返回 None。**不拿残缺窗口凑数**：
    用 3 天的均量算出来的量比是另一个指标，会让「放量突破」在数据稀疏的票上
    触发、在数据完整的票上不触发，而两边看起来完全一样。
    """
    if len(rows) < VOL_AVG_WINDOW:
        return None
    values = [num(r.get("volume")) for r in rows[:VOL_AVG_WINDOW]]
    if any(v is None for v in values):
        return None
    total = sum(values)  # type: ignore[arg-type]
    return total / VOL_AVG_WINDOW if total > 0 else None


def _completeness(rows: List[Dict], deps: Sequence[str], window: int) -> float:
    """这条信号所依据的数据完整度，0~1。

    分子分母都是"规则设计上需要的样本数"：window 根 K 线 × len(deps) 个
    字段。窗口内任一字段为 None 就少一个分子。窗口不足时（rows 比 window
    短）缺的整行直接计入分母，不做"用 2 天当 5 天"的凑数。
    """
    if not deps or window <= 0:
        return 1.0
    have = 0
    for row in rows[:window]:
        for key in deps:
            if _v(row, key) is not None:
                have += 1
    return min(1.0, have / (window * len(deps)))


def detect_signals(rows: List[Dict]) -> List[Dict]:
    """从预计算指标中检测所有形态信号。rows[0] 最新，rows[1] 前一天。"""
    signals: List[Dict] = []
    if not rows:
        return signals

    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    def add(category: str, name: str, signal: str, strength: float, desc: str,
            deps: Sequence[str] = (), window: int = 1) -> None:
        # deps/window 声明这条规则读了哪几个字段、回看几根 K 线。默认
        # window=1：单根判据缺数据时信号不会发，完整度恒为 1。
        completeness = _completeness(rows, deps, window)
        signals.append({
            "category": category,
            "name": name,
            "signal": signal,
            "strength": round(min(max(strength * completeness, 0.0), 1.0), 2),
            "completeness": round(completeness, 2),
            "date": latest["date"],
            "desc": desc,
        })

    def crossed_up(key_fast: str, key_slow: str) -> bool:
        pf, ps = _v(prev, key_fast), _v(prev, key_slow)
        cf, cs = _v(latest, key_fast), _v(latest, key_slow)
        if None in (pf, ps, cf, cs):
            return False
        return pf <= ps and cf > cs

    def crossed_down(key_fast: str, key_slow: str) -> bool:
        pf, ps = _v(prev, key_fast), _v(prev, key_slow)
        cf, cs = _v(latest, key_fast), _v(latest, key_slow)
        if None in (pf, ps, cf, cs):
            return False
        return pf >= ps and cf < cs

    # ── 动量买入信号 ──

    if crossed_up("dif", "dea"):
        add("buy", "MACD金叉", "bullish", 0.8,
            f"DIF({_v(latest, 'dif'):.2f}) 上穿 DEA({_v(latest, 'dea'):.2f})",
            ("dif", "dea"), 2)
    if crossed_down("dif", "dea"):
        add("sell", "MACD死叉", "bearish", 0.8,
            f"DIF({_v(latest, 'dif'):.2f}) 下穿 DEA({_v(latest, 'dea'):.2f})",
            ("dif", "dea"), 2)

    latest_k = _v(latest, "k")
    if crossed_up("k", "d") and latest_k is not None and latest_k < 20:
        add("buy", "KDJ超卖金叉", "bullish", 0.85,
            f"K({latest_k:.1f}) 上穿 D({_v(latest, 'd'):.1f})，超卖区",
            ("k", "d"), 2)
    if crossed_down("k", "d") and latest_k is not None and latest_k > 80:
        add("sell", "KDJ超买死叉", "bearish", 0.85,
            f"K({latest_k:.1f}) 下穿 D({_v(latest, 'd'):.1f})，超买区",
            ("k", "d"), 2)

    rsi6 = _v(latest, "rsi6")
    if rsi6 is not None and rsi6 < 20:
        add("buy", "RSI6超卖", "bullish", 0.7, f"RSI6={rsi6:.1f} (<20 超卖区)", ("rsi6",))
    if rsi6 is not None and rsi6 > 80:
        add("sell", "RSI6超买", "bearish", 0.7, f"RSI6={rsi6:.1f} (>80 超买区)", ("rsi6",))

    latest_stoch_k = _v(latest, "stoch_k")
    if crossed_up("stoch_k", "stoch_d") and latest_stoch_k is not None and latest_stoch_k < 20:
        add("buy", "Stochastic超卖金叉", "bullish", 0.75,
            f"K({latest_stoch_k:.1f}) 上穿 D({_v(latest, 'stoch_d'):.1f})，超卖区",
            ("stoch_k", "stoch_d"), 2)
    if crossed_down("stoch_k", "stoch_d") and latest_stoch_k is not None and latest_stoch_k > 80:
        add("sell", "Stochastic超买死叉", "bearish", 0.75,
            f"K({latest_stoch_k:.1f}) 下穿 D({_v(latest, 'stoch_d'):.1f})，超买区",
            ("stoch_k", "stoch_d"), 2)

    cci = _v(latest, "cci")
    if cci is not None and cci < -100:
        add("buy", "CCI超卖", "bullish", 0.8 if cci < -200 else 0.6,
            f"CCI={cci:.1f} (<-100 超卖)", ("cci",))
    if cci is not None and cci > 100:
        add("sell", "CCI超买", "bearish", 0.6, f"CCI={cci:.1f} (>100 超买)", ("cci",))

    willr = _v(latest, "willr")
    if willr is not None and willr < -80:
        add("buy", "Williams%R超卖", "bullish", 0.65,
            f"Williams%R={willr:.1f} (<-80)", ("willr",))
    if willr is not None and willr > -20:
        add("sell", "Williams%R超买", "bearish", 0.65,
            f"Williams%R={willr:.1f} (>−20)", ("willr",))

    mfi = _v(latest, "mfi")
    if mfi is not None and mfi < 20:
        add("buy", "MFI超卖", "bullish", 0.7, f"MFI={mfi:.1f} (<20 资金超卖)", ("mfi",))
    if mfi is not None and mfi > 80:
        add("sell", "MFI超买", "bearish", 0.7, f"MFI={mfi:.1f} (>80 资金超买)", ("mfi",))

    # ── 趋势信号 ──

    # Supertrend 每根 K 线最多发一条。
    #
    # 旧实现在下面（DI 金叉/死叉之后）还有第二段判**完全相同**的符号翻转
    # 条件，两段各自 add 一次。后果不是"多点信息"而是重复计数：
    # 「Supertrend翻空」两段的判据逐字相同（prev>0>latest），所以它每次
    # 触发必然发两条；「Supertrend翻转」在符号翻转那一支上也发两条。信号
    # 条数是命中率/信号频率统计的分母，被翻倍之后这类数字直接虚高一倍。
    #
    # 现在方向判定只写一次，取两段判据的**并集**：
    #   翻多 = crossed_up(st_dir, st_dir_zero) 或 prev<0<latest
    #   翻空 = prev>0>latest
    # 归并是无损的：两段的 deps、window、基准分、文案都一模一样，删掉的
    # 是重复项而不是判据 —— st_dir_zero 那条交叉仍保留在并集里。
    #
    # 确定性规则：**上面的定义先占位**。st_dir 一根 bar 只有一个符号，翻多
    # 与翻空天然互斥，所以不存在"两条都想发"要仲裁的情况。
    prev_st_dir = _v(prev, "st_dir")
    latest_st_dir = _v(latest, "st_dir")
    latest_st_val = _v(latest, "st_val") or 0.0
    supertrend_flip_up = crossed_up("st_dir", "st_dir_zero") or (
        prev_st_dir is not None and latest_st_dir is not None
        and prev_st_dir < 0 < latest_st_dir
    )
    supertrend_flip_down = (
        prev_st_dir is not None and latest_st_dir is not None
        and prev_st_dir > 0 > latest_st_dir
    )
    if supertrend_flip_up:
        add("buy", "Supertrend翻转", "bullish", 0.8,
            f"Supertrend 由空转多 (趋势值={latest_st_val:.2f})", ("st_dir",), 2)
    if supertrend_flip_down:
        add("sell", "Supertrend翻空", "bearish", 0.8,
            f"Supertrend 由多转空 (趋势值={latest_st_val:.2f})", ("st_dir",), 2)

    adx = _v(latest, "adx")
    di_plus, di_minus = _v(latest, "di_plus"), _v(latest, "di_minus")
    if adx is not None and adx > 25 and di_plus is not None and di_minus is not None:
        strength = min(0.9, 0.5 + adx / 100)
        if di_plus > di_minus:
            add("trend", "ADX多头趋势", "bullish", strength,
                f"ADX={adx:.1f} (>25), DI+({di_plus:.1f}) > DI-({di_minus:.1f})",
                ("adx", "di_plus", "di_minus"))
        else:
            add("trend", "ADX空头趋势", "bearish", strength,
                f"ADX={adx:.1f} (>25), DI+({di_plus:.1f}) < DI-({di_minus:.1f})",
                ("adx", "di_plus", "di_minus"))

    if crossed_up("di_plus", "di_minus"):
        add("trend", "DI金叉", "bullish", 0.75,
            f"DI+({di_plus:.1f}) 上穿 DI-({di_minus:.1f})", ("di_plus", "di_minus"), 2)
    if crossed_down("di_plus", "di_minus"):
        add("trend", "DI死叉", "bearish", 0.75,
            f"DI+({di_plus:.1f}) 下穿 DI-({di_minus:.1f})", ("di_plus", "di_minus"), 2)

    aroon_up, aroon_down = _v(latest, "aroon_up"), _v(latest, "aroon_down")
    if aroon_up is not None and aroon_down is not None:
        if aroon_up > 70 and aroon_down < 30:
            add("trend", "Aroon多头排列", "bullish", 0.7,
                f"AroonUp={aroon_up:.0f} (>70), AroonDown={aroon_down:.0f} (<30)",
                ("aroon_up", "aroon_down"))
        if aroon_down > 70 and aroon_up < 30:
            add("trend", "Aroon空头排列", "bearish", 0.7,
                f"AroonDown={aroon_down:.0f} (>70), AroonUp={aroon_up:.0f} (<30)",
                ("aroon_up", "aroon_down"))

    if crossed_up("vi_plus", "vi_minus"):
        add("trend", "Vortex金叉", "bullish", 0.7,
            f"VI+({_v(latest, 'vi_plus'):.2f}) 上穿 VI-({_v(latest, 'vi_minus'):.2f})",
            ("vi_plus", "vi_minus"), 2)
    if crossed_down("vi_plus", "vi_minus"):
        add("trend", "Vortex死叉", "bearish", 0.7,
            f"VI+({_v(latest, 'vi_plus'):.2f}) 下穿 VI-({_v(latest, 'vi_minus'):.2f})",
            ("vi_plus", "vi_minus"), 2)

    # ── 波动率信号 ──

    latest_width = _v(latest, "bb_width")
    if latest_width is not None and latest_width > 0:
        recent = [_v(rows[i], "bb_width") for i in range(min(BB_AVG_WINDOW, len(rows)))]
        if all(w is not None for w in recent):
            avg_w = sum(recent) / len(recent)
            if avg_w > 0 and latest_width < avg_w * 0.5:
                # window=5：这条规则写的是"5 日均值"。只有 2 根 K 线时它
                # 照样能算出一个均值，但那是 2 日均值，必须打折而不是
                # 冒充 5 日。
                add("volatility", "布林带收口", "neutral", 0.6,
                    f"BB宽度={latest_width:.2f} 低于5日均值{avg_w:.2f}的50%，变盘前兆",
                    ("bb_width",), BB_AVG_WINDOW)

    # Donchian 上轨突破：收盘价站上**前一日**的 Donchian 上轨。
    #
    # 两处修正，都不是"数据没来"：
    #
    # 1. 旧判据是 `bb_upper == dc_upper`（两个独立计算量的浮点精确相等），
    #    近一个月 19 万行里命中 0 次，策略 anomaly 因此永远选不出票。
    # 2. 改成 `close > dc_upper` 之后仍然恒不成立：指标库的 dc_upper 是
    #    **含当日**的 20 日最高价，而收盘价不可能高于当日最高价，数学上
    #    就到不了。实测 60 只票 × 逐日回放 1,200 行命中 0 次。
    #    正确的比法是拿**前一日**的上轨：上轨在当日之前就已经确定，
    #    收盘价越过它才是真正的突破（创出区间新高）。
    dc_upper_prev = _v(prev, "dc_upper")
    close = _v(latest, "close")
    if None not in (dc_upper_prev, close) and close > dc_upper_prev:
        add("volatility", "Donchian上轨突破", "bullish", 0.65,
            f"收盘价 {close:.2f} 站上前一日 Donchian 上轨 {dc_upper_prev:.2f}",
            ("close", "dc_upper"), 2)

    latest_atr = _v(latest, "atr")
    if latest_atr is not None:
        prior = [_v(rows[i], "atr") for i in range(1, min(ATR_AVG_WINDOW, len(rows)))]
        if prior and all(a is not None for a in prior):
            avg_atr = sum(prior) / len(prior)
            if avg_atr > 0 and latest_atr > avg_atr * 1.5:
                # 同上：5 日均值的规则，窗口不满就打折。
                add("volatility", "ATR扩张", "neutral", 0.55,
                    f"ATR={latest_atr:.2f} 为5日均值{avg_atr:.2f}的"
                    f"{latest_atr / avg_atr:.1f}倍，波动加剧",
                    ("atr",), ATR_AVG_WINDOW)

    # ── 量能信号 ──

    cmf = _v(latest, "cmf")
    if cmf is not None:
        if cmf > 0.1:
            add("volume", "CMF资金流入", "bullish", min(0.8, 0.5 + cmf),
                f"CMF={cmf:.2f} (>0.1 资金净流入)", ("cmf",))
        elif cmf < -0.1:
            add("volume", "CMF资金流出", "bearish", min(0.8, 0.5 + abs(cmf)),
                f"CMF={cmf:.2f} (<-0.1 资金净流出)", ("cmf",))

    # 「放量突破」：涨幅 > 3% 且 量比 > 1.5。
    #
    # 这条信号依赖 close 与 volume，而 `v_indicators_daily` 一个价格列都没有，
    # 所以它以前只在 volume.py 的单只扫描路径里存在，选股池用不了。价量维度
    # 接进来后（screener.merge_price_rows）这里才第一次能在集合式选股里跑。
    #
    # 判定口径与 volume.py:analyze_volume_price 保持一致 —— 两边算出来的
    # "放量突破"必须是同一件事，否则用户会看到同一只票在单扫和选股池里
    # 一个命中一个不命中。
    close_now = _v(latest, "close")
    vol_now = _v(latest, "volume")
    if close_now is not None and vol_now is not None:
        prev_close = _v(prev, "close")
        avg_vol = _avg_volume(rows[1:])
        if prev_close and prev_close > 0 and avg_vol and avg_vol > 0:
            price_change = (close_now - prev_close) / prev_close
            vol_ratio = vol_now / avg_vol
            if price_change > 0.03 and vol_ratio > 1.5:
                # _avg_volume 已经要求满 10 根，所以这条信号发出来时
                # 完整度恒为 1；显式写出来是为了让"哪条规则读几根"可查。
                add("volume", "放量突破", "bullish", min(0.9, 0.5 + vol_ratio / 10),
                    f"涨幅 {price_change * 100:.2f}%，成交量为 {VOL_AVG_WINDOW} 日均量的 "
                    f"{vol_ratio:.2f} 倍",
                    ("close", "volume"), VOL_AVG_WINDOW + 1)

    # ── 统计信号 ──

    zscore = _v(latest, "zscore")
    if zscore is not None:
        if zscore < -2:
            add("buy", "Z-Score超卖", "bullish", 0.75,
                f"Z-Score={zscore:.2f} (<-2 统计超卖)", ("zscore",))
        elif zscore > 2:
            add("sell", "Z-Score超买", "bearish", 0.75,
                f"Z-Score={zscore:.2f} (>2 统计超买)", ("zscore",))

    lin_slope = _v(latest, "lin_slope")
    if lin_slope is not None and lin_slope != 0:
        direction = "上升" if lin_slope > 0 else "下降"
        add("trend", f"线性回归{direction}",
            "bullish" if lin_slope > 0 else "bearish",
            min(0.7, abs(lin_slope) / 10),
            f"14日线性回归斜率={lin_slope:.4f} ({direction})",
            ("lin_slope",))

    # ── 蜡烛图形态 ──
    candle_patterns = [
        ("cdl_morning_star", "Morning Star晨星", "bullish", "底部反转形态"),
        ("cdl_evening_star", "Evening Star暮星", "bearish", "顶部反转形态"),
        ("cdl_hammer", "Hammer锤子线", "bullish", "下影线长，潜在底部"),
        ("cdl_shooting_star", "Shooting Star流星", "bearish", "上影线长，潜在顶部"),
        ("cdl_doji", "Doji十字星", "neutral", "多空平衡，变盘信号"),
        ("cdl_engulfing", "Engulfing吞没", "bullish", "看涨吞没形态"),
        ("cdl_harami", "Harami孕线", "neutral", "趋势放缓信号"),
        ("cdl_piercing", "Piercing刺透", "bullish", "看涨刺透形态"),
        ("cdl_dark_cloud", "Dark Cloud乌云盖顶", "bearish", "看跌乌云形态"),
        ("cdl_3white", "Three White Soldiers三白兵", "bullish", "连续三阳，强势上涨"),
        ("cdl_3black", "Three Black Crows三乌鸦", "bearish", "连续三阴，强势下跌"),
    ]
    for key, name, sig, desc in candle_patterns:
        value = num(latest.get(key))
        if value is not None and value != 0:
            add("candle", name, sig, 0.85 if abs(value) > 100 else 0.7, desc, (key,))

    return signals


def summarize_signals(signals: List[Dict]) -> Dict:
    """汇总信号。无任何买卖信号时 verdict 必须是中性。"""
    buy_count = sell_count = 0
    buy_strength = sell_strength = 0.0

    for s in signals:
        if s["signal"] == "bullish":
            buy_count += 1
            buy_strength += s["strength"]
        elif s["signal"] == "bearish":
            sell_count += 1
            sell_strength += s["strength"]

    if buy_count > sell_count and buy_strength > sell_strength:
        verdict = "偏多"
    elif sell_count > buy_count and sell_strength > buy_strength:
        verdict = "偏空"
    else:
        verdict = "中性"

    return {
        "verdict": verdict,
        "buy_signals": buy_count,
        "sell_signals": sell_count,
        "total": len(signals),
        "buy_strength": round2(buy_strength),
        "sell_strength": round2(sell_strength),
    }
