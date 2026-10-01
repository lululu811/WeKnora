"""
指标背离 — 价格创了新高/新低，而指标没有跟上。

## 为什么这一类单独做

几何形态看的是**价格自己的形状**；背离看的是**价格与指标的关系**。前者画不出来
"关系"，只能画轮廓。所以这里的产出带两个枢轴点，前端把两点连成一条虚线 ——
与形态轮廓共用同一套画法，不需要新的绘制代码。

## 判定

标准定义（以顶背离为例）：价格创**更高**的高点，而指标的高点**更低**。
底背离对称：价格创**更低**的低点，指标的低点**更高**。

三条约束，缺一条就会满屏误报：
  1. 两个枢轴至少隔 `MIN_PIVOT_GAP` 根 —— 相邻两根的比较就是噪音；
  2. 指标值两侧都必须存在（缺失不当 0 用，那是把"没有数据"读成"指标等于 0"）；
  3. 价格必须**真的**创了新高/新低（严格不等），平高不平底不算。

## 关于指标

用的是指标库里的 `dif`（MACD 线）与 `rsi14`。这两个是行业默认口径；换成
`macd_hist` 或 `rsi6` 会得到不同的枢轴，不是"更准/更不准"，是另一个定义。
"""

import logging
from typing import Any, Dict, List, Optional, Sequence

from .pattern import _line, _point
from .utils import find_swings, num, nz

logger = logging.getLogger(__name__)

# 与 pattern.py 的 SWING_WINDOW 保持一致：同一批枢轴，别出现"形态认它是拐点、
# 背离不认"这种自相矛盾。
DIVERGENCE_WINDOW = 8

# 两个枢轴至少隔这么多根。相邻两根的"价格新高 + 指标走低"遍地都是，
# 那不叫背离，叫波动。
MIN_PIVOT_GAP = 5

# 每种背离最多报最近几对。报多了图上一片连线，反而看不出哪条是重点。
MAX_PAIRS_PER_KIND = 2

# 指标口径：key -> (列名, 展示名)
DIVERGENCE_INDICATORS: List[Dict[str, str]] = [
    {"key": "macd", "column": "dif", "label": "MACD"},
    {"key": "rsi", "column": "rsi14", "label": "RSI"},
]

# 别名 -> DuckDB 列名。与分析层（data_loader.INDICATOR_COLUMNS）同源，
# 写在这里是为了让本模块的查询自给自足，不必依赖调用方先查好字段表。
_COLUMN_SQL: Dict[str, str] = {
    "dif": "momentum_macd_12_26_9_macd",
    "rsi14": "momentum_rsi_14",
}


def build_divergence_sql() -> str:
    """
    背离所需列的查询。字段名来自本模块常量，不含用户输入；
    thscode / limit 走绑定参数。
    """
    select = ", ".join(f"{_COLUMN_SQL[ind['column']]} AS {ind['column']}" for ind in DIVERGENCE_INDICATORS)
    return f"""
        SELECT CAST(date AS VARCHAR) AS date, {select}
        FROM v_indicators_daily
        WHERE thscode = ?
        ORDER BY date DESC
        LIMIT ?
    """


def _indicator_by_date(indicator_rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
    """date -> {column: value}。缺的保留 None（不当 0 用）。"""
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for row in indicator_rows:
        date = row.get("date")
        if not date:
            continue
        out[str(date)] = {ind["column"]: num(row.get(ind["column"])) for ind in DIVERGENCE_INDICATORS}
    return out


def _pivot(
    rows: Sequence[Dict[str, Any]],
    idx: int,
    price_key: str,
    series: Dict[str, Dict[str, Optional[float]]],
    column: str,
) -> Optional[Dict[str, Any]]:
    """取一个枢轴的价格与指标值；任一缺失就返回 None（不猜）。"""
    date = str(rows[idx].get("date") or "")
    if not date:
        return None
    price = num(rows[idx].get(price_key))
    values = series.get(date)
    indicator = None if values is None else values.get(column)
    if price is None or indicator is None:
        return None
    return {"idx": idx, "date": date, "price": float(price), "indicator": float(indicator)}


def _scan(
    rows: Sequence[Dict[str, Any]],
    pivots: Sequence[int],
    price_key: str,
    series: Dict[str, Dict[str, Optional[float]]],
    indicator_key: str,
    indicator_label: str,
    column: str,
    kind: str,
) -> List[Dict[str, Any]]:
    """`kind` 取 'top'（顶背离）或 'bottom'（底背离）。"""
    out: List[Dict[str, Any]] = []
    # pivots 升序，开头 = 最近。逐对比较"最近 vs 上一个"。
    for a in range(len(pivots) - 1):
        newer = _pivot(rows, pivots[a], price_key, series, column)
        older = _pivot(rows, pivots[a + 1], price_key, series, column)
        if newer is None or older is None:
            continue
        if abs(newer["idx"] - older["idx"]) < MIN_PIVOT_GAP:
            continue

        if kind == "top":
            price_made_new = newer["price"] > older["price"]
            indicator_lagged = newer["indicator"] < older["indicator"]
            direction, name, high_label, low_label = "bearish", f"{indicator_label}顶背离", "前高", "后高"
        else:
            price_made_new = newer["price"] < older["price"]
            indicator_lagged = newer["indicator"] > older["indicator"]
            direction, name, high_label, low_label = "bullish", f"{indicator_label}底背离", "前低", "后低"

        if not (price_made_new and indicator_lagged):
            continue

        # 两张对比图分开看更清楚：价格这一腿画在图上，指标那一段只能靠文字交代。
        out.append({
            "name": name,
            "type": "divergence",
            "indicator": indicator_key,
            "direction": direction,
            "confidence": 0.6,
            "points": [
                _point(rows, older["idx"], older["price"], high_label),
                _point(rows, newer["idx"], newer["price"], low_label),
            ],
            "lines": [
                _line(rows, older["idx"], older["price"], newer["idx"], newer["price"], "背离"),
            ],
            "desc": (
                f"价格 {older['price']:.2f} -> {newer['price']:.2f}"
                f"（{'新高' if kind == 'top' else '新低'}），"
                f"而 {indicator_label} {older['indicator']:.3f} -> {newer['indicator']:.3f}"
                f"（反向）"
            ),
        })
        if len(out) >= MAX_PAIRS_PER_KIND:
            break
    return out


def detect_divergence(
    market_rows: Sequence[Dict[str, Any]],
    indicator_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    检测 MACD / RSI 的顶底背离。

    Args:
        market_rows: 行情行，**rows[0] 必须是最新一根**（与 pattern.py 同口径），
                     需含 date / high / low。
        indicator_rows: 指标行，需含 date 与 DIVERGENCE_INDICATORS 里的列。

    Returns:
        形态形状的列表（含 points 与 lines，可直接画），按方向聚类后返回。
        数据不足或指标缺失时返回空列表 —— **不猜、不用 0 冒充**。
    """
    if len(market_rows) < 3 * DIVERGENCE_WINDOW + 4:
        return []

    series = _indicator_by_date(indicator_rows)
    if not series:
        return []

    highs, lows = find_swings(list(market_rows), DIVERGENCE_WINDOW)
    out: List[Dict[str, Any]] = []
    for ind in DIVERGENCE_INDICATORS:
        column, label = ind["column"], ind["label"]
        if highs:
            out.extend(_scan(market_rows, highs, "high", series, ind["key"], label, column, "top"))
        if lows:
            out.extend(_scan(market_rows, lows, "low", series, ind["key"], label, column, "bottom"))
    return out
