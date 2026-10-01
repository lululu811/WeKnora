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
（frontend/src/components/workspace/kline/annotate-api.ts）按这个 key 取
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

    @classmethod
    def annotate_all(
        cls,
        bars: List[Dict[str, Any]],
        pattern_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """综合形态标注并按日期升序排序"""
        if pattern_types is None:
            pattern_types = ["b1", "key_k", "s1", "violent_k"]

        dispatch = {
            "b1": cls.detect_build_wave_b1,
            "key_k": cls.detect_key_k,
            "s1": cls.detect_s1,
            "violent_k": cls.detect_violent_k,
        }

        all_patterns: List[Dict[str, Any]] = []
        for ptype in pattern_types:
            if ptype in dispatch:
                all_patterns.extend(dispatch[ptype](bars))

        all_patterns.sort(key=lambda x: str(x["date"]))

        # 统一盖上 source 戳，而不是在四个 detect_* 里各写一遍。
        #
        # 前端要能区分「算法算出来的位」和「模型嘴上说的位」，两者会画在同一张
        # K 线上但形状与颜色不同——混成一种墨迹，用户就无法判断哪条线是数据、
        # 哪条线是观点。这里盖的是 algorithm；模型生成的那些走另一条通道，
        # 带 source="llm"。
        #
        # 盖在这一层是为了覆盖所有探测器：新增 detect_* 时不必记得补这个字段，
        # 漏了会让它在前端被当成未知来源而静默不画。
        for pattern in all_patterns:
            pattern.setdefault("source", "algorithm")

        return all_patterns


annotator = ZettarancAnnotator()
