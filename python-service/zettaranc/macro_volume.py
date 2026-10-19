"""
宏观量价分析模块 (Macro Volume Analysis)

来源：《重剑无锋交易框架技术手册》3.3, 3.8, 3.9 节：
1. 主导量与对应量中枢 (Dominant Volume & Corresponding Volume Pivot):
   - 主导量是套牢筹码的湖泊，中枢是筹码传递的河流，低位对应量中枢是主力建仓的盆。
   - 对应量：调整 1-4 个月后低位突然放量（与高位主导量同量级），回踩收到放量 K 线上影线范围内确认。
2. 底量超顶量 (Bottom Volume Exceeds Top Volume):
   - 底部区域量能超过前期头部量能，证明大资金低位建仓、自上而下转移充分，是长线筛选核心信号。
3. 量切线突破 (Volume Trendline Breakout):
   - 对缩量调整阶段画成交量下降切线；未突破量切线的上涨只是弱反弹，放量突破量切线且在相对低位才是真启动买点。
"""

from typing import Any, Dict, List, Optional
import math


def analyze_macro_volume(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    对日线序列执行宏观量价分析。
    bars: 按时间升序排序的日线列表 (需至少 30 根)。
    """
    if len(bars) < 30:
        return {
            "status": "insufficient_data",
            "desc": "数据不足30根，无法进行宏观量价分析",
        }

    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    vols = [float(b["volume"]) if b.get("volume") is not None else float(b.get("vol", 0)) for b in bars]
    dates = [str(b.get("date", ""))[:10] for b in bars]

    n = len(bars)

    # 1. 底量超顶量分析 (划分前半段为顶部/前期，后半段为底部/近期)
    # 找到前半段（最高价附近）的顶量峰值，与后半段（最低价附近）的底量峰值对比
    half = n // 2
    top_half_vol = max(vols[:half]) if half > 0 else 0
    bottom_half_vol = max(vols[half:])

    # 寻找最高点所在的量
    highest_idx = max(range(n), key=lambda i: highs[i])
    lowest_idx = min(range(n), key=lambda i: lows[i])

    peak_price_vol = vols[highest_idx]
    trough_price_vol = vols[lowest_idx]

    bottom_exceeds_top = (bottom_half_vol >= top_half_vol * 0.95) and (lowest_idx > highest_idx)
    bottom_over_top_ratio = round(bottom_half_vol / top_half_vol, 2) if top_half_vol > 0 else 1.0

    # 2. 对应量中枢检测 (低位异动量，与前期主导量量级相近)
    recent_window = min(20, n // 3)
    recent_max_vol = max(vols[-recent_window:])
    recent_max_idx = n - recent_window + vols[-recent_window:].index(recent_max_vol)

    is_corresponding_volume = False
    corr_desc = "未见低位对应量中枢"

    if top_half_vol > 0:
        vol_ratio_to_top = recent_max_vol / top_half_vol
        # 近期放量达到前期主导量的 70% 以上，且处于调整后低位
        if vol_ratio_to_top >= 0.70 and recent_max_idx > highest_idx:
            is_corresponding_volume = True
            corr_desc = f"出现对应量特征（近期放量达前期顶量 {vol_ratio_to_top*100:.0f}%，主力建仓的盆初显）"

    # 3. 量切线突破检测 (对近 10-20 根成交量检测是否突破下降量切线)
    vol_breakout = False
    vol_trendline_desc = "量能维持常态"

    if n >= 15:
        # 考察最新一根量与其前 10 根缩量均值对比
        lookback = 10
        prev_vols = vols[-lookback - 1 : -1]
        curr_vol = vols[-1]
        avg_prev_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 1.0

        # 判断前 10 根是否呈现成交量逐级萎缩（地量斜率负向）
        is_shrinking = prev_vols[-1] <= prev_vols[0] * 0.8
        is_break_line = curr_vol >= avg_prev_vol * 1.5

        if is_shrinking and is_break_line:
            vol_breakout = True
            vol_trendline_desc = f"放量突破下降量切线（今日成交量较前期地量均值放大 {curr_vol/avg_prev_vol:.1f} 倍，启动信号）"

    return {
        "bottom_exceeds_top": bottom_exceeds_top,
        "bottom_over_top_ratio": bottom_over_top_ratio,
        "is_corresponding_volume": is_corresponding_volume,
        "corresponding_vol_desc": corr_desc,
        "volume_breakout": vol_breakout,
        "volume_trendline_desc": vol_trendline_desc,
        "summary": (
            "宏观量价底量充沛，主力低位建仓特征明显"
            if (bottom_exceeds_top or is_corresponding_volume)
            else "宏观量价暂未出现底量超顶量或对应量中枢"
        ),
    }
