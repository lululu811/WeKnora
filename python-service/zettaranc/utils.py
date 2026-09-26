"""
zettaranc 工具函数 — 类型转换与摆动点检测

行序约定（贯穿全模块，勿改）
--------------------------------
`fetch_market_data` / `fetch_indicators_only` 返回的行按日期**倒序**：
`rows[0]` 是最新一根 K 线，`rows[i]` 越大越老。因此
"最近一次"永远是**最小**的下标，`rows[i+1]` 是 `rows[i]` 的前一天。
`find_swings` 返回的下标同样是升序（0 最老、末尾最新），所以
`swings[-1]` 才是最近的摆动点。

缺失值约定
--------------------------------
`num()` 把 None / 空串 / 无法解析的值转成 **None**，代表"数据缺失"。
缺失值绝不能被当成 0 参与阈值判断——那会把没有数据的标的报成
"RSI6 超卖"这类假买点（见 BUG-4）。需要"缺失即中性"的地方显式调用
`nz()`，并在调用处注明理由。
"""

import math
from typing import Any, Dict, List, Optional, Tuple


def num(v: Any) -> Optional[float]:
    """把任意值转成 float；缺失（None / 空串 / 不可解析）返回 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return None if math.isnan(v) else float(v)
    if isinstance(v, str):
        text = v.strip()
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
        return None if math.isnan(parsed) else parsed
    return None


def nz(v: Any, default: float = 0.0) -> float:
    """缺失安全的算术：None -> default。仅用于"缺失取中性值即正确"的场景。"""
    parsed = num(v)
    return default if parsed is None else parsed


def has_value(v: Any) -> bool:
    """该字段是否真的有数据。用于门控信号发射。"""
    return num(v) is not None


def to_str(v: Any) -> str:
    """安全地将任意值转换为字符串"""
    if v is None:
        return ""
    return str(v)


def round2(v: float) -> float:
    """四舍五入到 2 位小数"""
    return round(v, 2)


def round_n(v: float, n: int = 2) -> float:
    """四舍五入到 n 位小数"""
    factor = 10 ** n
    return math.floor(v * factor + 0.5) / factor


def min_int(a: int, b: int) -> int:
    """返回两个整数中较小的"""
    return min(a, b)


def find_swings(rows: List[Dict], window: int) -> Tuple[List[int], List[int]]:
    """
    用简单窗口法检测摆动高点和低点。
    摆动高点要求 high 严格大于 ±window 内的其它值（平台价不算拐点）。

    Returns:
        (highs, lows) — 摆动点在 rows 中的下标，**升序**（`i` 从小到大遍历）。

        注意方向：rows 是倒序的（`rows[0]` 最新），而下标是升序，
        所以 **列表开头 = 最近的摆动点，列表末尾 = 最老的**。
        要"最近 vs 上一个"取 `[0]` 和 `[1]`，不是 `[-1]` 和 `[-2]`。
    """
    n = len(rows)
    highs: List[int] = []
    lows: List[int] = []
    for i in range(window, n - window):
        is_high = True
        is_low = True
        for j in range(i - window, i + window + 1):
            if j == i:
                continue
            if nz(rows[j]["high"], -math.inf) >= nz(rows[i]["high"], -math.inf):
                is_high = False
            if nz(rows[j]["low"], math.inf) <= nz(rows[i]["low"], math.inf):
                is_low = False
        if is_high:
            highs.append(i)
        if is_low:
            lows.append(i)
    return highs, lows