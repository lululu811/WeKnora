"""
支撑阻力位分析 — 枢轴点、斐波那契、摆动高/低、均线、整数关口、布林带、VWAP

对应 Go: internal/agent/tools/hithink_finance/analysis/levels.go

行序：rows[0] 是最新一根 K 线。缺失指标为 None，不参与价位计算。
"""

import math
from typing import Dict, List, Optional, Tuple

from .utils import find_swings, num, nz

MIN_BARS = 1
SWING_WINDOW = 5


def calc_pivot_points(h: float, l: float, c: float) -> Dict[str, float]:
    """经典地板交易员枢轴点"""
    pp = (h + l + c) / 3
    r = h - l
    return {
        "pp": pp,
        "r1": 2 * pp - l,
        "r2": pp + r,
        "r3": h + 2 * (pp - l),
        "s1": 2 * pp - h,
        "s2": pp - r,
        "s3": l - 2 * (h - pp),
    }


def calc_fibonacci(high: float, low: float, trend_down: bool) -> Dict:
    """斐波那契回撤位"""
    span = high - low
    levels = {}
    for label, fraction in (
        ("23.6", 0.236), ("38.2", 0.382), ("50.0", 0.500),
        ("61.8", 0.618), ("78.6", 0.786),
    ):
        levels[label] = high - span * fraction if trend_down else low + span * fraction
    return {"high": high, "low": low, "levels": levels}


def round_numbers(price: float) -> Tuple[List[float], List[float]]:
    """返回最接近的上下两个整数关口"""
    if price > 1000:
        step = 50
    elif price > 100:
        step = 10
    elif price > 10:
        step = 5
    else:
        step = 1

    lower = math.floor(price / step) * step
    upper = lower + step

    below = [v for v in (lower - step, lower) if v < price]
    above = [v for v in (upper, upper + step) if v > price]
    return above, below


def cluster_levels(levels: List[Tuple[str, float]], tol: float) -> List[Dict]:
    """聚类相近价位为支撑阻力区域。"""
    if len(levels) < 2 or tol <= 0:
        return []
    sorted_levels = sorted(levels, key=lambda x: x[1])

    zones = []
    cluster = [sorted_levels[0]]
    for i in range(1, len(sorted_levels)):
        if sorted_levels[i][1] - cluster[0][1] <= tol:
            cluster.append(sorted_levels[i])
        else:
            if len(cluster) >= 2:
                zones.append(_build_zone(cluster))
            cluster = [sorted_levels[i]]
    if len(cluster) >= 2:
        zones.append(_build_zone(cluster))

    zones.sort(key=lambda z: (-len(z["levels"]), z["price"]))
    return zones


def _build_zone(cluster: List[Tuple[str, float]]) -> Dict:
    return {
        "price": sum(p for _, p in cluster) / len(cluster),
        "levels": [name for name, _ in cluster],
        "strength": "strong" if len(cluster) >= 3 else "medium",
    }


def find_nearest(
    levels: List[Tuple[str, float]],
    price: float,
) -> Tuple[Optional[Dict], Optional[Dict]]:
    """最近支撑（低于价格的最高位）和阻力（高于价格的最低位）"""
    sup = res = None
    for name, lv in levels:
        if lv < price:
            if sup is None or lv > sup["price"]:
                sup = {"price": lv, "type": name}
        elif lv > price:
            if res is None or lv < res["price"]:
                res = {"price": lv, "type": name}
    return sup, res


def _generate_verdict(
    price: float,
    pivot: Dict[str, float],
    ma_levels: Dict[str, Optional[float]],
    sup: Optional[Dict],
    res: Optional[Dict],
) -> Dict:
    """生成综合判断。均线缺失时如实说明，不用 0 冒充。"""
    key_levels: List[str] = []
    if sup:
        key_levels.append(f"近端支撑 {sup['type']} @ {sup['price']:.2f}")
    if res:
        key_levels.append(f"近端阻力 {res['type']} @ {res['price']:.2f}")

    valid_mas = [v for v in ma_levels.values() if v is not None and v > 0]
    above_mas = sum(1 for v in valid_mas if price >= v)
    below_mas = sum(1 for v in valid_mas if price < v)

    if not valid_mas:
        verdict = "均线数据缺失，无法判断均线排列"
    elif above_mas >= 4:
        verdict = "多头排列，价格站稳大多数均线之上，偏强运行"
    elif below_mas >= 4:
        verdict = "空头排列，价格运行于大多数均线之下，偏弱整理"
    else:
        verdict = "均线交织，方向不明，关注关键支撑阻力位突破情况"

    if len(valid_mas) < len(ma_levels):
        verdict += f"；仅 {len(valid_mas)}/{len(ma_levels)} 条均线有数据"

    verdict += (
        "；当前位于枢轴点上方，短期偏多"
        if price > pivot["pp"]
        else "；当前位于枢轴点下方，短期偏空"
    )
    return {"verdict": verdict, "key_levels": key_levels}


def analyze_levels(rows: List[Dict]) -> Dict:
    """支撑阻力位分析入口。"""
    if len(rows) < MIN_BARS:
        raise ValueError("数据为空")
    latest = rows[0]
    price = num(latest.get("close"))
    if price is None or price <= 0:
        raise ValueError("最新收盘价缺失或为 0，数据可能不完整")

    high, low = nz(latest.get("high")), nz(latest.get("low"))
    pivot = calc_pivot_points(high, low, price)

    fib_high = max(nz(r.get("high")) for r in rows)
    fib_low = min(nz(r.get("low")) for r in rows)
    fib = calc_fibonacci(fib_high, fib_low, trend_down=price < fib_high)

    swing_high_idx, swing_low_idx = find_swings(rows, SWING_WINDOW)
    # rows 倒序：find_swings 下标升序，末尾最近。展示最近的 5 个。
    resist_swings = [
        {"price": nz(rows[idx]["high"]), "date": rows[idx]["date"], "type": "swing_high"}
        for idx in reversed(swing_high_idx[:5])
    ]
    support_swings = [
        {"price": nz(rows[idx]["low"]), "date": rows[idx]["date"], "type": "swing_low"}
        for idx in reversed(swing_low_idx[:5])
    ]

    ma_levels = {key: num(latest.get(key)) for key in
                 ("ma5", "ma10", "ma20", "ma60", "ma120", "ma250")}

    round_above, round_below = round_numbers(price)
    bollinger = {"upper": num(latest.get("bb_upper")),
                 "lower": num(latest.get("bb_lower"))}
    vwap = num(latest.get("vwap"))

    all_levels: List[Tuple[str, float]] = []

    def add(name: str, value: Optional[float]) -> None:
        if value is not None and value > 0:
            all_levels.append((name, value))

    for key, value in pivot.items():
        add(f"Pivot_{key.upper()}", value)
    for pct, value in fib["levels"].items():
        add(f"Fib_{pct}%", value)
    for swing in resist_swings:
        add(f"SwingHigh_{swing['date']}", swing["price"])
    for swing in support_swings:
        add(f"SwingLow_{swing['date']}", swing["price"])
    for key, value in ma_levels.items():
        add(f"MA_{key}", value)
    for value in round_above + round_below:
        add(f"Round_{value:g}", value)
    add("BB_Upper", bollinger["upper"])
    add("BB_Lower", bollinger["lower"])
    add("VWAP", vwap)

    atr = num(latest.get("atr"))
    tol = atr if atr and atr >= price * 0.005 else price * 0.01
    zones = cluster_levels(all_levels, tol)

    nearest_sup, nearest_res = find_nearest(all_levels, price)
    verdict = _generate_verdict(price, pivot, ma_levels, nearest_sup, nearest_res)

    return {
        "current_price": price,
        "pivot_points": pivot,
        "fibonacci": fib,
        "swing_levels": {"resistance": resist_swings, "support": support_swings},
        "ma_levels": ma_levels,
        "ma_missing": [k for k, v in ma_levels.items() if v is None],
        "round_numbers": {"above": round_above, "below": round_below},
        "bollinger": bollinger,
        "vwap": vwap,
        "confluence_zones": zones,
        "nearest": {"support": nearest_sup, "resistance": nearest_res},
        "summary": verdict,
    }
