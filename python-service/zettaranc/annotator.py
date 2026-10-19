"""
Z 哥交易体系形态识别器 (Python 实现)

识别 B1/S1/关键K/暴力K 等形态标注。

2026-10-01 前有另一份 TypeScript 翻译（kline-studio 独立服务里的 annotator.ts，
已随该服务删除）。核对过它的 21 个数值阈值与 4 个检测器，与本文件逐条一致，
无独有规则 —— 所以本文件是这套识别规则的唯一实现。
输出结构直接供前端 KLineChart Pro 标注图层渲染。

命名约定（重要）
----------------
这里的 B1 是**形态标注**：建仓波之后的第一次缩量回调（KDJ J<13）。
它和选股策略 `STRATEGY_RULES["oversold_combo"]`（≥2 个超卖信号共振）
是两件不同的事 —— 历史上选股策略也叫 "B1"，两套同名不同义，模型和人
都会串。现已把选股策略改名为 oversold_combo，本模块的 B1 保持不变，
函数名带上「建仓波」前缀（detect_build_wave_b1）以示区分。

对外的 `type` 字段仍然是 `"b1"`：K 线工作台前端的形态调色板
（frontend/src/finance/components/kline/annotate-api.ts）按这个 key 取
标签和颜色，改 key 会让前端拿不到样式。这是**线上协议**，不是内部命名。
"""

from typing import Any, Dict, List, Optional
import math


def calc_sma(data: List[float], length: int) -> List[Optional[float]]:
    """简单移动平均 (SMA)"""
    res: List[Optional[float]] = []
    for i in range(len(data)):
        if i < length - 1:
            res.append(None)
        else:
            res.append(sum(data[i - length + 1 : i + 1]) / length)
    return res


def calc_kdj(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> Dict[str, List[Optional[float]]]:
    """KDJ 指标计算 (9, 3, 3)"""
    k_list: List[Optional[float]] = []
    d_list: List[Optional[float]] = []
    j_list: List[Optional[float]] = []

    for i in range(len(closes)):
        if i < n - 1:
            k_list.append(None)
            d_list.append(None)
            j_list.append(None)
            continue

        low_n = min(lows[i - n + 1 : i + 1])
        high_n = max(highs[i - n + 1 : i + 1])
        if high_n == low_n:
            rsv = 50.0
        else:
            rsv = ((closes[i] - low_n) / (high_n - low_n)) * 100.0

        prev_k = k_list[i - 1] if (i > 0 and k_list[i - 1] is not None) else 50.0
        curr_k = (rsv * (1.0 / m1)) + (prev_k * (1.0 - 1.0 / m1))
        k_list.append(curr_k)

        prev_d = d_list[i - 1] if (i > 0 and d_list[i - 1] is not None) else 50.0
        curr_d = (curr_k * (1.0 / m2)) + (prev_d * (1.0 - 1.0 / m2))
        d_list.append(curr_d)

        j_list.append(3.0 * curr_k - 2.0 * curr_d)

    return {"k": k_list, "d": d_list, "j": j_list}


class ZettarancAnnotator:
    """Z 哥交易体系形态识别器"""

    @staticmethod
    def detect_build_wave_b1(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测 B1 建仓波信号（形态标注，不是选股策略 oversold_combo）
        - 建仓波：底部连续中大阳线放量上升 (涨幅 20%~50%，至少3根大阳线)
        - B1 信号：建仓波后的第一次回调低点，KDJ 的 J 值 < 13 且回调缩量
        """
        patterns: List[Dict[str, Any]] = []
        if len(bars) < 30:
            return patterns

        closes = [float(b["close"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        volumes = [float(b["volume"]) for b in bars]

        kdj = calc_kdj(highs, lows, closes)
        j_values = kdj["j"]

        is_big_yang = []
        for b in bars:
            o, c = float(b["open"]), float(b["close"])
            body_pct = ((c - o) / o) * 100.0 if o > 0 else 0
            is_big_yang.append(c > o and 2.5 < body_pct < 9.5)

        for i in range(25, len(bars) - 3):
            window_start = max(0, i - 25)
            window_end = i - 5
            yang_count = sum(1 for idx in range(window_start, window_end + 1) if is_big_yang[idx])

            if yang_count >= 3:
                start_open = float(bars[window_start]["open"])
                end_close = float(bars[window_end]["close"])
                wave_gain = ((end_close - start_open) / start_open) * 100.0 if start_open > 0 else 0

                if 20.0 <= wave_gain <= 50.0:
                    j_val = j_values[i]
                    if j_val is not None and j_val < 13.0:
                        pullback_vol = sum(volumes[i - 5 : i]) / 5.0
                        wave_len = window_end - window_start + 1
                        wave_vol = sum(volumes[window_start : window_end + 1]) / wave_len if wave_len > 0 else 1.0

                        if pullback_vol < wave_vol * 0.7:
                            patterns.append({
                                "type": "b1",
                                "date": str(bars[i]["date"]),
                                "price": float(bars[i]["low"]),
                                "text": f"B1 (J={j_val:.1f})",
                                "confidence": min(0.95, 0.5 + (13.0 - j_val) / 30.0),
                                "metadata": {
                                    "wave_gain": round(wave_gain, 2),
                                    "j_value": round(j_val, 2),
                                    "yang_count": yang_count,
                                    # 确认延迟：上面的循环是 range(25, len(bars)-3)，
                                    # 也就是最后 3 根 bar 不参与判定 —— 形态要到
                                    # D+3 才可确认。这件事必须随标注一起走，否则
                                    # 看到图上有个 B1 的人会以为当天就能依据它
                                    # 做判断，而实际上它在当天根本还不存在。
                                    # 判据见 scripts/watchlist-replay/REPORT.md：
                                    # 36,362 个事件全样本回放，没有 edge。
                                    "confirm_delay_bars": 3,
                                    "detectable_at": str(bars[i + 3]["date"]),
                                    "admission": "descriptive",
                                },
                            })
        return patterns

    @staticmethod
    def detect_key_k(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测关键K：十字星 + 缩量 (成交量 < 20 日均量 70%)
        """
        patterns: List[Dict[str, Any]] = []
        if len(bars) < 20:
            return patterns

        volumes = [float(b["volume"]) for b in bars]
        vol_ma = calc_sma(volumes, 20)

        for i in range(20, len(bars)):
            bar = bars[i]
            o, c, h, l = float(bar["open"]), float(bar["close"]), float(bar["high"]), float(bar["low"])
            body = abs(c - o)
            upper_shadow = h - max(c, o)
            lower_shadow = min(c, o) - l

            is_doji = body < (upper_shadow + lower_shadow) * 0.3
            ma = vol_ma[i]
            low_vol = ma is not None and volumes[i] < ma * 0.7

            if is_doji and low_vol:
                patterns.append({
                    "type": "key_k",
                    "date": str(bar["date"]),
                    "price": c,
                    "text": "关键K (十字星)",
                    "confidence": 0.6,
                    "metadata": {
                        "volume_ratio": round(volumes[i] / ma, 2) if ma else 0,
                    },
                })
        return patterns

    @staticmethod
    def detect_s1(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测 S1 卖出信号：60 日高位放量 + 长上影线
        """
        patterns: List[Dict[str, Any]] = []
        if len(bars) < 60:
            return patterns

        volumes = [float(b["volume"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        vol_ma = calc_sma(volumes, 20)

        for i in range(60, len(bars)):
            bar = bars[i]
            o, c, h = float(bar["open"]), float(bar["close"]), float(bar["high"])

            high60 = max(highs[i - 59 : i + 1])
            is_high = h > high60 * 0.95

            ma = vol_ma[i]
            big_vol = ma is not None and volumes[i] > ma * 2.0

            body = abs(c - o)
            upper_shadow = h - max(c, o)
            long_upper = upper_shadow > body * 1.5

            if is_high and big_vol and long_upper:
                patterns.append({
                    "type": "s1",
                    "date": str(bar["date"]),
                    "price": h,
                    "text": "S1 (高位放量)",
                    "confidence": 0.75,
                    "metadata": {
                        "volume_ratio": round(volumes[i] / ma, 2) if ma else 0,
                    },
                })
        return patterns

    @staticmethod
    def detect_violent_k(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测暴力K：低位 (60日低点15%内) + 倍量 + 实体 > 5%
        """
        patterns: List[Dict[str, Any]] = []
        if len(bars) < 60:
            return patterns

        volumes = [float(b["volume"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        vol_ma = calc_sma(volumes, 20)

        for i in range(60, len(bars)):
            bar = bars[i]
            o, c, l = float(bar["open"]), float(bar["close"]), float(bar["low"])
            body_pct = (abs(c - o) / o) * 100.0 if o > 0 else 0

            ma = vol_ma[i]
            double_vol = ma is not None and volumes[i] > ma * 2.0

            low60 = min(lows[i - 59 : i + 1])
            is_low = l < low60 * 1.15

            if body_pct > 5.0 and double_vol and is_low:
                direction = "阳" if c > o else "阴"
                patterns.append({
                    "type": "violent_k",
                    "date": str(bar["date"]),
                    "price": c,
                    "text": f"暴力K ({direction}, {body_pct:.1f}%)",
                    "confidence": 0.8,
                    "metadata": {
                        "body_pct": round(body_pct, 2),
                        "volume_ratio": round(volumes[i] / ma, 2) if ma else 0,
                    },
                })
        return patterns

    @staticmethod
    def detect_pole_yang(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测立杆阳：标志一波行情起点的中大阳线，划横线锚定。
        判据：
        - 涨幅 >= 4.5% (收盘较开盘)
        - 阳线实体占比大 (实体 / 振幅 >= 0.65)
        - 放量 (成交量 >= 20 日均量 1.7 倍)
        - 处于低位 (60 日低点 30% 范围内) 或创 20 日新高
        """
        patterns: List[Dict[str, Any]] = []
        if len(bars) < 20:
            return patterns

        volumes = [float(b["volume"]) if b.get("volume") is not None else float(b.get("vol", 0)) for b in bars]
        lows = [float(b["low"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        vol_ma = calc_sma(volumes, 20)

        for i in range(20, len(bars)):
            bar = bars[i]
            o, c, h, l = float(bar["open"]), float(bar["close"]), float(bar["high"]), float(bar["low"])
            gain_pct = ((c - o) / o) * 100.0 if o > 0 else 0
            amplitude = h - l
            body = c - o

            ma = vol_ma[i]
            is_big_gain = c > o and gain_pct >= 4.5
            is_solid_body = amplitude > 0 and (body / amplitude) >= 0.65
            is_heavy_vol = ma is not None and volumes[i] >= ma * 1.7

            # 低位启动或突破新高
            start_idx = max(0, i - 60)
            low_window = min(lows[start_idx : i + 1])
            is_near_bottom = l <= low_window * 1.30

            prev_high_20 = max(highs[max(0, i - 20) : i])
            is_breakout = c > prev_high_20

            if is_big_gain and is_solid_body and is_heavy_vol and (is_near_bottom or is_breakout):
                patterns.append({
                    "type": "pole_yang",
                    "date": str(bar["date"]),
                    "price": c,
                    "text": f"立杆阳 (+{gain_pct:.1f}%)",
                    "confidence": 0.85,
                    "metadata": {
                        "gain_pct": round(gain_pct, 2),
                        "anchor_high": c,
                        "anchor_low": l,
                        "volume_ratio": round(volumes[i] / ma, 2) if ma else 0,
                    },
                })
        return patterns

    @staticmethod
    def detect_pile_yang(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测打桩阳：低开 + 反包 + 回踩三合一，强烈的空翻多信号。
        判据：
        - 前一日为阴线
        - 当日低开 (低开幅度 >= 0.5%)
        - 强势反包前日阴线实体 (close > prev_open)
        - 带有下影线探底回踩 (下影线 >= 实体 25% 或最低价破前低后收回)
        - 放量或温和放量 (成交量 > 前一日成交量)
        """
        patterns: List[Dict[str, Any]] = []
        if len(bars) < 2:
            return patterns

        for i in range(1, len(bars)):
            prev = bars[i - 1]
            curr = bars[i]

            prev_o, prev_c = float(prev["open"]), float(prev["close"])
            curr_o, curr_c = float(curr["open"]), float(curr["close"])
            curr_h, curr_l = float(curr["high"]), float(curr["low"])

            prev_vol = float(prev["volume"]) if prev.get("volume") is not None else float(prev.get("vol", 0))
            curr_vol = float(curr["volume"]) if curr.get("volume") is not None else float(curr.get("vol", 0))

            is_prev_yin = prev_c < prev_o
            is_low_open = curr_o < prev_c * 0.995  # 低开 >= 0.5%
            is_engulf = curr_c > prev_o            # 反包前日实体
            is_yang = curr_c > curr_o

            lower_shadow = curr_o - curr_l
            body = curr_c - curr_o
            has_pullback_shadow = body > 0 and (lower_shadow >= body * 0.25 or curr_l < float(prev["low"]))
            is_vol_expand = curr_vol >= prev_vol * 1.05

            if is_prev_yin and is_low_open and is_yang and is_engulf and has_pullback_shadow and is_vol_expand:
                patterns.append({
                    "type": "pile_yang",
                    "date": str(curr["date"]),
                    "price": curr_c,
                    "text": "打桩阳 (低开反包回踩)",
                    "confidence": 0.85,
                    "metadata": {
                        "gain_pct": round(((curr_c - curr_o) / curr_o) * 100.0, 2),
                        "rebound_from_low": round(((curr_c - curr_l) / curr_l) * 100.0, 2),
                    },
                })
        return patterns

    @staticmethod
    def detect_capping_yin(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测灌顶阴：高开低走放量大阴，80%看空，次高点是最后逃生位。
        判据：
        - 高开或平开 (open >= prev_close * 0.998)
        - 放量大阴 (实体跌幅 >= 3.0%)
        - 巨量 (成交量 >= 20日均量 1.8 倍)
        - 收盘跌破 5 日均线
        - 处于近 20 日相对高位 (近 20 日高点 8% 范围内)
        """
        patterns: List[Dict[str, Any]] = []
        if len(bars) < 20:
            return patterns

        volumes = [float(b["volume"]) if b.get("volume") is not None else float(b.get("vol", 0)) for b in bars]
        closes = [float(b["close"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        vol_ma = calc_sma(volumes, 20)
        ma5_series = calc_sma(closes, 5)

        for i in range(20, len(bars)):
            curr = bars[i]
            prev = bars[i - 1]

            curr_o, curr_c, curr_h = float(curr["open"]), float(curr["close"]), float(curr["high"])
            prev_c = float(prev["close"])

            drop_pct = ((curr_o - curr_c) / curr_o) * 100.0 if curr_o > 0 else 0
            is_big_yin = curr_c < curr_o and drop_pct >= 3.0
            is_high_open = curr_o >= prev_c * 0.998

            ma = vol_ma[i]
            is_heavy_vol = ma is not None and volumes[i] >= ma * 1.8

            ma5 = ma5_series[i]
            broken_ma5 = ma5 is not None and curr_c < ma5

            recent_high = max(highs[max(0, i - 20) : i + 1])
            is_high_level = curr_h >= recent_high * 0.92

            if is_big_yin and is_high_open and is_heavy_vol and broken_ma5 and is_high_level:
                patterns.append({
                    "type": "capping_yin",
                    "date": str(curr["date"]),
                    "price": curr_h,
                    "text": f"灌顶阴 (高开低走放量 -{drop_pct:.1f}%)",
                    "confidence": 0.80,
                    "metadata": {
                        "drop_pct": round(drop_pct, 2),
                        "volume_ratio": round(volumes[i] / ma, 2) if ma else 0,
                    },
                })
        return patterns

    @staticmethod
    def detect_fake_yin(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测伪阴线（高位震仓）：承接 + 新高 + 不破 5 日线 = 高位震仓。
        判据：
        - 收阴线 (close < open)
        - 创出阶段新高 (近 5 日最高价新高)
        - 坚决守住 5 日均线 (close >= ma5)
        - 下影线良好或未放天量出货 (成交量 <= 20日均量 2.2 倍)
        """
        patterns: List[Dict[str, Any]] = []
        if len(bars) < 20:
            return patterns

        closes = [float(b["close"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        volumes = [float(b["volume"]) if b.get("volume") is not None else float(b.get("vol", 0)) for b in bars]
        ma5_series = calc_sma(closes, 5)
        vol_ma = calc_sma(volumes, 20)

        for i in range(20, len(bars)):
            curr = bars[i]
            o, c, h, l = float(curr["open"]), float(curr["close"]), float(curr["high"]), float(curr["low"])

            is_yin = c < o
            ma5 = ma5_series[i]
            hold_ma5 = ma5 is not None and c >= ma5

            # 创近 5 日新高
            prev_high_5 = max(highs[i - 5 : i])
            is_new_high = h >= prev_high_5

            # 非天量出货
            ma = vol_ma[i]
            not_ceiling_vol = ma is not None and volumes[i] <= ma * 2.2

            if is_yin and hold_ma5 and is_new_high and not_ceiling_vol:
                patterns.append({
                    "type": "fake_yin",
                    "date": str(curr["date"]),
                    "price": c,
                    "text": "伪阴线 (高位震仓守住5日线)",
                    "confidence": 0.70,
                    "metadata": {
                        "hold_ma5": True,
                        "ma5": round(ma5, 2) if ma5 else 0,
                    },
                })
        return patterns

    @staticmethod
    def detect_rebound_trap(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        检测回光返照（弱反阳）：倒N + 518死叉 + 无量双下五弱阳 = C浪中继。
        判据：
        - 518 死叉状态 (ma5 < ma18)
        - 收小阳线 (close > open，涨幅 < 2.5%)
        - 极度缩量 (成交量 < 5日均量 75%)
        - 价格受制于 18 日线 (close < ma18)
        """
        patterns: List[Dict[str, Any]] = []
        if len(bars) < 20:
            return patterns

        closes = [float(b["close"]) for b in bars]
        volumes = [float(b["volume"]) if b.get("volume") is not None else float(b.get("vol", 0)) for b in bars]
        ma5_series = calc_sma(closes, 5)
        ma18_series = calc_sma(closes, 18)
        vol_ma5_series = calc_sma(volumes, 5)

        for i in range(20, len(bars)):
            curr = bars[i]
            o, c = float(curr["open"]), float(curr["close"])

            ma5 = ma5_series[i]
            ma18 = ma18_series[i]
            vol_ma5 = vol_ma5_series[i]

            if ma5 is None or ma18 is None or vol_ma5 is None:
                continue

            is_518_death = ma5 < ma18
            under_ma18 = c < ma18
            gain_pct = ((c - o) / o) * 100.0 if o > 0 else 0
            is_small_yang = c > o and gain_pct < 2.5
            is_dry_volume = volumes[i] < vol_ma5 * 0.75

            if is_518_death and under_ma18 and is_small_yang and is_dry_volume:
                patterns.append({
                    "type": "rebound_trap",
                    "date": str(curr["date"]),
                    "price": c,
                    "text": "回光返照 (518死叉下无量弱反，C浪中继)",
                    "confidence": 0.75,
                    "metadata": {
                        "gain_pct": round(gain_pct, 2),
                        "volume_ratio": round(volumes[i] / vol_ma5, 2) if vol_ma5 else 0,
                    },
                })
        return patterns

    @classmethod
    def annotate_all(
        cls,
        bars: List[Dict[str, Any]],
        pattern_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """综合形态标注并按日期升序排序"""
        if pattern_types is None:
            pattern_types = [
                "b1", "key_k", "s1", "violent_k",
                "pole_yang", "pile_yang", "capping_yin", "fake_yin", "rebound_trap"
            ]

        dispatch = {
            "b1": cls.detect_build_wave_b1,
            "key_k": cls.detect_key_k,
            "s1": cls.detect_s1,
            "violent_k": cls.detect_violent_k,
            "pole_yang": cls.detect_pole_yang,
            "pile_yang": cls.detect_pile_yang,
            "capping_yin": cls.detect_capping_yin,
            "fake_yin": cls.detect_fake_yin,
            "rebound_trap": cls.detect_rebound_trap,
        }

        all_patterns: List[Dict[str, Any]] = []
        for ptype in pattern_types:
            if ptype in dispatch:
                all_patterns.extend(dispatch[ptype](bars))

        all_patterns.sort(key=lambda x: str(x["date"]))

        for pattern in all_patterns:
            pattern.setdefault("source", "algorithm")

        return all_patterns


annotator = ZettarancAnnotator()
