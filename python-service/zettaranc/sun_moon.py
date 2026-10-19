"""
2026 最新「日月神功」与月线平衡态引擎 (Sun-Moon Engine)

编制依据：《重剑无锋交易框架技术手册》第七部分
核心哲学：“月线定多空边界，日线找切入买点”

主要能力：
1. 月线 MACD 序列与平衡态计算：
   - 芝麻点 (Sesame Dot)：月线 MACD 柱在零轴附近收缩至极限微小值，代表多空力量绝衡态；
   - 绿红芝麻点顺序定律：
     * 先绿后红（Green then Red）：空头动能耗尽后多头初启，大级别转折向上确立 (BULLISH_REVERSAL)；
     * 先红后绿（Red then Green）：大多头耗尽后空头击穿，大级别转折向下预警 (BEARISH_REVERSAL)；
2. 快慢线三状态 (DIF/DEA)：
   - 水下深处（超跌反弹区域）；
   - 临近零轴（变盘与主升浪酝酿区域）；
   - 零轴上方高位（高位防守警戒区域）；
3. 三同步空仓铁律 (Triple-Sync Exhaustion Alert)：
   - 价格两波新高 + 量堆同步放大 + MACD 红柱同步递增；
   - 表面看似“量价齐升与指标共振”，实为多头能量总宣泄与诱多见顶，90% 概率触发大级别空头宣泄。
"""

from typing import Any, Dict, List, Optional
import math
from .resampler import resample_bars


def _calc_ema(series: List[float], period: int) -> List[float]:
    """计算指数移动平均线 (EMA)"""
    if not series:
        return []
    alpha = 2.0 / (period + 1)
    ema = [series[0]]
    for i in range(1, len(series)):
        val = alpha * series[i] + (1.0 - alpha) * ema[-1]
        ema.append(val)
    return ema


def calc_macd(
    closes: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Dict[str, List[float]]:
    """
    计算标准 MACD 指标 (DIF, DEA, HIST)
    HIST = 2 * (DIF - DEA)
    """
    if len(closes) < 2:
        return {"dif": [0.0] * len(closes), "dea": [0.0] * len(closes), "hist": [0.0] * len(closes)}

    ema_fast = _calc_ema(closes, fast_period)
    ema_slow = _calc_ema(closes, slow_period)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _calc_ema(dif, signal_period)
    hist = [2.0 * (d - a) for d, a in zip(dif, dea)]

    return {"dif": dif, "dea": dea, "hist": hist}


def analyze_sun_moon(
    bars: List[Dict[str, Any]],
    monthly_bars: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    日月神功综合分析：
    输入日线 bars 或已聚合好的月线 bars。
    """
    if monthly_bars is None:
        monthly_bars = resample_bars(bars, freq="month")

    if len(monthly_bars) < 3:
        return {
            "status": "insufficient_data",
            "desc": f"月K线数据不足 (当前 {len(monthly_bars)} 根，至少需要 3 根)",
            "monthly_bars_count": len(monthly_bars),
            "sesame_dots": [],
            "sequence_law": "NEUTRAL",
            "three_sync_warning": False,
            "fast_slow_state": "UNKNOWN",
            "regime_safe": True,
        }

    closes = [float(b.get("close", 0)) for b in monthly_bars]
    vols = [float(b.get("volume", 0)) for b in monthly_bars]
    highs = [float(b.get("high", 0)) for b in monthly_bars]
    dates = [str(b.get("date", ""))[:7] for b in monthly_bars]

    macd_data = calc_macd(closes)
    dif = macd_data["dif"]
    dea = macd_data["dea"]
    hist = macd_data["hist"]

    # 1. 计算历史 hist 的典型波动幅度（用于动态判定芝麻点阈值）
    abs_hists = [abs(h) for h in hist]
    mean_abs_hist = sum(abs_hists) / len(abs_hists) if abs_hists else 0.1
    # 动态阈值：柱高的绝对值低于历史均值的 25%，或者绝对值小于 0.05
    sesame_threshold = max(0.03, mean_abs_hist * 0.28)

    sesame_dots = []
    for i in range(len(monthly_bars)):
        h_val = hist[i]
        c_val = closes[i] if closes[i] > 0 else 1.0
        # 归一化柱高 (|hist| / close) 或绝对柱高
        is_sesame = abs(h_val) <= sesame_threshold or (abs(h_val) / c_val) <= 0.005

        if is_sesame:
            dot_type = "RED_SESAME" if h_val >= 0 else "GREEN_SESAME"
            sesame_dots.append({
                "month": dates[i],
                "index": i,
                "hist": round(h_val, 4),
                "dif": round(dif[i], 4),
                "dea": round(dea[i], 4),
                "type": dot_type,
            })

    # 2. 绿红芝麻点顺序定律检验 (Sequence Law)
    # 观察最近出现的芝麻点类型演变
    sequence_law = "NEUTRAL"
    sequence_desc = "当前月线未检测到显著的芝麻点顺序转换"
    if len(sesame_dots) >= 2:
        # 取最近两个不同的芝麻点
        last_dot = sesame_dots[-1]
        prev_dot = sesame_dots[-2]

        if prev_dot["type"] == "GREEN_SESAME" and last_dot["type"] == "RED_SESAME":
            sequence_law = "GREEN_THEN_RED"
            sequence_desc = (
                f"绿红芝麻点顺序确立：前月空头收敛极值 ({prev_dot['month']})，"
                f"随后翻出红芝麻点 ({last_dot['month']})，大级别空翻多转折向上！"
            )
        elif prev_dot["type"] == "RED_SESAME" and last_dot["type"] == "GREEN_SESAME":
            sequence_law = "RED_THEN_GREEN"
            sequence_desc = (
                f"红绿芝麻点顺序确立：前有多头高潮极值 ({prev_dot['month']})，"
                f"随后翻出绿芝麻点 ({last_dot['month']})，大级别多头衰竭转折向下！"
            )
    elif len(sesame_dots) == 1:
        single_dot = sesame_dots[0]
        if single_dot["type"] == "RED_SESAME":
            sequence_desc = f"当期出现孤立红芝麻点 ({single_dot['month']})，多头初启，等待进一步确认"
        else:
            sequence_desc = f"当期出现孤立绿芝麻点 ({single_dot['month']})，空头衰竭中，密切关注转折"

    # 3. 快慢线三状态评估
    curr_c = closes[-1] if closes[-1] > 0 else 1.0
    curr_dif = dif[-1]
    curr_dea = dea[-1]
    dif_ratio = (curr_dif / curr_c) * 100.0  # DIF 占收盘价比重百分比

    if dif_ratio < -2.0:
        fast_slow_state = "DEEP_UNDERWATER"
        fast_slow_desc = "快慢线位于水下深处：属于大级别超跌态，操作仅限定性为左侧反弹"
    elif abs(dif_ratio) <= 1.2:
        fast_slow_state = "NEAR_ZERO_AXIS"
        fast_slow_desc = "快慢线临近零轴：属于关键变盘态与主升浪酝酿区，多空力量达成新平衡"
    else:
        fast_slow_state = "HIGH_ABOVE_AXIS"
        fast_slow_desc = "快慢线高悬零轴上方：多头成熟或高位防守区，警戒动能透支"

    # 4. 三同步空仓铁律 (Triple-Sync Exhaustion Alert)
    # 检验月线连续两波高点中：价格新高 + 量堆同步放大 + MACD 红柱递增
    three_sync_warning = False
    three_sync_detail = None

    if len(monthly_bars) >= 6:
        # 在近 12 根月线中寻找两个波峰
        window = monthly_bars[-12:]
        w_closes = closes[-len(window):]
        w_vols = vols[-len(window):]
        w_hist = hist[-len(window):]
        w_dates = dates[-len(window):]

        # 寻找局部峰值 (高点)
        peaks = []
        for i in range(1, len(window) - 1):
            if w_closes[i] >= w_closes[i - 1] and w_closes[i] >= w_closes[i + 1]:
                if w_hist[i] > 0:  # 红柱状态下的波峰
                    peaks.append(i)

        # 如果末尾一根也是创新高，也作为一个峰值候选
        if len(w_closes) >= 2 and w_closes[-1] > w_closes[-2] and w_hist[-1] > 0:
            if not peaks or peaks[-1] != len(window) - 1:
                peaks.append(len(window) - 1)

        if len(peaks) >= 2:
            p1 = peaks[-2]
            p2 = peaks[-1]
            price_higher = w_closes[p2] > w_closes[p1]
            vol_higher = w_vols[p2] > w_vols[p1]
            hist_higher = w_hist[p2] > w_hist[p1] and w_hist[p2] > 0 and w_hist[p1] > 0

            if price_higher and vol_higher and hist_higher:
                three_sync_warning = True
                three_sync_detail = {
                    "peak1_month": w_dates[p1],
                    "peak1_close": round(w_closes[p1], 2),
                    "peak1_vol": round(w_vols[p1], 2),
                    "peak1_hist": round(w_hist[p1], 4),
                    "peak2_month": w_dates[p2],
                    "peak2_close": round(w_closes[p2], 2),
                    "peak2_vol": round(w_vols[p2], 2),
                    "peak2_hist": round(w_hist[p2], 4),
                    "reason": (
                        "触发日月神功【三同步空仓铁律】！两波价格新高 + 量堆放大 + MACD红柱递增，"
                        "看似强势共振，实为多头能量总宣泄诱多见顶，90% 概率触发大空头宣泄，严禁追多并执行避险！"
                    ),
                }

    # 5. 综合安全环境定性
    regime_safe = True
    if sequence_law == "RED_THEN_GREEN" or three_sync_warning:
        regime_safe = False
    elif fast_slow_state == "HIGH_ABOVE_AXIS" and hist[-1] < 0:
        regime_safe = False

    return {
        "status": "success",
        "latest_month": dates[-1],
        "monthly_macd": {
            "dif": round(curr_dif, 4),
            "dea": round(curr_dea, 4),
            "hist": round(hist[-1], 4),
            "is_red_hist": hist[-1] >= 0,
        },
        "sesame_dots": sesame_dots,
        "latest_sesame_dot": sesame_dots[-1] if sesame_dots else None,
        "sequence_law": sequence_law,
        "sequence_desc": sequence_desc,
        "fast_slow_state": fast_slow_state,
        "fast_slow_desc": fast_slow_desc,
        "three_sync_warning": three_sync_warning,
        "three_sync_detail": three_sync_detail,
        "regime_safe": regime_safe,
    }
