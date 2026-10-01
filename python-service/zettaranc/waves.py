"""
艾略特波浪识别 — ZigZag 枢轴提取 + 驱动浪/调整浪标注。

对应关系：本模块是**新建**能力，Go 侧没有对应实现（仓库此前完全没有数浪逻辑，
`trend.py` 里的道氏结构只是 HH/HL 分类，不是画浪）。

## 关于自动化数浪的诚实说明

艾略特波浪**本身是主观的**：同一段行情，不同的人可以数出不同的浪。本模块做的是
**规则化的近似**——先按阈值提取 ZigZag 枢轴，再用艾略特的三条硬规则去校验候选浪型，
校验不过就不产出。所以：

- 输出的 `confidence` 反映的是「通过了几条规则」，**不是**「这个数浪是对的」；
- 图上画出来的是**一种可能的数法**，不是结论；
- 数据不够或规则冲突时返回 None，宁可不画也不硬凑。

三条被校验的硬规则（驱动浪）：
1. 浪 2 不能完全回撤浪 1（不能跌破浪 1 的起点）；
2. 浪 3 不能是 1/3/5 里最短的那一浪；
3. 浪 4 不能与浪 1 的价格区间重叠。

行序沿用本仓库约定：`rows[0]` 是最新一根（见 utils 模块文档）。
"""

from typing import Any, Dict, List, Optional

from .utils import num, nz, round2

# 枢轴判定阈值：价格反向走这么多才确认一个拐点。
# 太小会把噪声当拐点（数出一堆没意义的浪），太大则漏掉真实的小级别浪。
DEFAULT_ZIGZAG_PCT = 6.0

# 由粗到细扫描的阈值序列。从大往小试，先命中的是较大级别的结构。
SCAN_THRESHOLDS_PCT = (18.0, 15.0, 12.0, 10.0, 8.0, 6.0, 5.0)

# 至少需要多少个枢轴才谈得上数浪（驱动浪 5 浪需要 6 个点）。
MIN_PIVOTS_IMPULSE = 6
MIN_PIVOTS_CORRECTIVE = 4
MIN_BARS = 30


def zigzag(rows: List[Dict], threshold_pct: float = DEFAULT_ZIGZAG_PCT) -> List[Dict[str, Any]]:
    """
    提取 ZigZag 枢轴，**按时间顺序**（最老 -> 最新）返回。

    每个枢轴：`{"index": rows 下标, "date": str, "price": float, "kind": "high"|"low"}`。

    算法是经典 ZigZag：维持当前极值，价格从极值反向走够 `threshold_pct` 就确认
    一个枢轴，并把极值切到反方向。
    """
    if len(rows) < 2:
        return []

    # 先把 rows 转成时间顺序（最老 -> 最新），这样下标语义与直觉一致，
    # 也免得调用方每处都要记得 rows 是倒序的。
    chrono = list(reversed(rows))
    threshold = max(0.1, threshold_pct) / 100.0

    pivots: List[Dict[str, Any]] = []
    # 起点：第一根的价格
    first_price = num(chrono[0].get("close"))
    if first_price is None:
        return []

    def push(idx: int, price: float, kind: str) -> None:
        pivots.append(
            {
                "index": idx,
                "date": str(chrono[idx].get("date") or ""),
                "price": round2(price),
                "kind": kind,
            }
        )

    # 方向必须显式三分支处理。
    #
    # 第一版写成 `if direction >= 0: ...` 紧跟 `if direction <= 0: ...`，
    # 初值 0 同时满足两个条件、两个分支都会跑，状态当场被打乱——首段的价格
    # 直接丢掉了（单测里第一个枢轴变成了 36 而不是起点 10）。
    direction = 0  # 0 未定, 1 向上, -1 向下
    ext_price = first_price
    ext_idx = 0

    for i in range(1, len(chrono)):
        high = num(chrono[i].get("high"))
        low = num(chrono[i].get("low"))
        if high is None or low is None:
            continue

        if direction == 0:
            # 方向未定：看价格先走够哪一边，起点就是相反类型的枢轴。
            if (high - first_price) / first_price >= threshold:
                push(0, first_price, "low")
                direction, ext_price, ext_idx = 1, high, i
            elif (first_price - low) / first_price >= threshold:
                push(0, first_price, "high")
                direction, ext_price, ext_idx = -1, low, i
            continue

        if direction > 0:
            # 向上段：跟踪最高点，回落够多就确认
            if high >= ext_price:
                ext_price, ext_idx = high, i
            elif (ext_price - low) / ext_price >= threshold:
                push(ext_idx, ext_price, "high")
                direction, ext_price, ext_idx = -1, low, i
        else:
            if low <= ext_price:
                ext_price, ext_idx = low, i
            elif (high - ext_price) / ext_price >= threshold:
                push(ext_idx, ext_price, "low")
                direction, ext_price, ext_idx = 1, high, i

    # 收尾：把当前正在走的那一端也补上（它还没被反向确认过）
    if direction > 0:
        push(ext_idx, ext_price, "high")
    elif direction < 0:
        push(ext_idx, ext_price, "low")

    # 去掉相邻同类型的枢轴（保留更极端的那个）
    cleaned: List[Dict[str, Any]] = []
    for p in pivots:
        if cleaned and cleaned[-1]["kind"] == p["kind"]:
            prev = cleaned[-1]
            if p["kind"] == "high":
                if p["price"] > prev["price"]:
                    cleaned[-1] = p
            else:
                if p["price"] < prev["price"]:
                    cleaned[-1] = p
            continue
        cleaned.append(p)
    return cleaned


def _segments(points: List[Dict[str, Any]]) -> List[float]:
    """相邻枢轴之间的价格变动（带符号）。"""
    return [points[i + 1]["price"] - points[i]["price"] for i in range(len(points) - 1)]


def _try_impulse(points: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    试着把 6 个连续枢轴标成驱动浪 0-1-2-3-4-5。

    只接受向上或向下的标准形态，并逐条校验艾略特硬规则；任一不通过就返回 None。
    """
    if len(points) < MIN_PIVOTS_IMPULSE:
        return None

    p = points[-MIN_PIVOTS_IMPULSE:]
    segs = _segments(p)
    # 驱动浪的六段方向必须交替：+ - + - +（向上）或 - + - + -（向下）
    bullish = segs[0] > 0
    expected = [1 if bullish else -1, -1 if bullish else 1, 1 if bullish else -1,
                -1 if bullish else 1, 1 if bullish else -1]
    for s, e in zip(segs, expected):
        if s * e <= 0:
            return None

    p0, p1, p2, p3, p4, p5 = (x["price"] for x in p)
    w1, w3, w5 = abs(p1 - p0), abs(p3 - p2), abs(p5 - p4)

    # 规则 1：浪 2 不能完全回撤浪 1（跌破/涨过浪 1 的起点）
    rule2 = (p2 > p0) if bullish else (p2 < p0)
    # 规则 2：浪 3 不能是最短的一浪
    rule3 = w3 >= min(w1, w5) and w3 > 0
    # 规则 3：浪 4 不与浪 1 的价格区间重叠
    rule4 = (p4 > p1) if bullish else (p4 < p1)

    if not (rule2 and rule3 and rule4):
        return None

    passed = sum([rule2, rule3, rule4])
    labels = ["起点", "1", "2", "3", "4", "5"]
    return {
        "name": "驱动浪 1-2-3-4-5",
        "kind": "impulse",
        "direction": "bullish" if bullish else "bearish",
        # 通过规则数换算成置信度：全过 0.7，过两条 0.55。刻意不给高分——
        # 这是"一种可能的数法"，不是结论。
        "confidence": 0.7 if passed == 3 else 0.55,
        "points": [
            {"index": pt["index"], "date": pt["date"], "price": pt["price"], "label": lb}
            for pt, lb in zip(p, labels)
        ],
        "rules": {
            "wave2_not_retrace_wave1": rule2,
            "wave3_not_shortest": rule3,
            "wave4_no_overlap_wave1": rule4,
        },
        "desc": (
            f"驱动浪：起点{p0:.2f} → 1浪顶{p1:.2f} → 2浪底{p2:.2f} → "
            f"3浪顶{p3:.2f} → 4浪底{p4:.2f} → 5浪顶{p5:.2f}"
        ),
    }


def _try_corrective(points: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """试着把最后 4 个枢轴标成调整浪 A-B-C。"""
    if len(points) < MIN_PIVOTS_CORRECTIVE:
        return None

    p = points[-MIN_PIVOTS_CORRECTIVE:]
    segs = _segments(p)
    if segs[0] == 0 or segs[1] == 0 or segs[2] == 0:
        return None
    # 调整浪方向必须交替
    if not (segs[0] * segs[1] < 0 and segs[1] * segs[2] < 0):
        return None

    a, b, c = segs[1], segs[2], None  # noqa: F841  (保留可读性)
    labels = ["A", "B", "C", "终点"]
    down_first = segs[0] < 0
    return {
        "name": "调整浪 A-B-C",
        "kind": "corrective",
        "direction": "bearish" if down_first else "bullish",
        "confidence": 0.5,
        "points": [
            {"index": pt["index"], "date": pt["date"], "price": pt["price"], "label": lb}
            for pt, lb in zip(p, labels)
        ],
        "rules": {},
        "desc": (
            f"调整浪：A={p[0]['price']:.2f} → B={p[1]['price']:.2f} → "
            f"C={p[2]['price']:.2f} → {p[3]['price']:.2f}"
        ),
    }


def detect_elliott_waves(
    rows: List[Dict], threshold_pct: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """
    识别最近一段的波浪结构。

    **由粗到细扫描阈值，优先取大级别结构。**

    为什么要扫：艾略特波浪是分形的，同一个标的在不同级别上都有浪。用单一阈值
    会踩到噪声——实测天顺风能 002531.SZ，阈值 4% 时最后 4 个枢轴是 12 天内
    7.98→7.02→8.35→7.37 的微观波动，却也被标成了「调整浪 A-B-C」；只有把阈值
    放大到 12%，拿到的才是 5.80→6.98→5.97→8.59→7.02→8.35 这个真正有意义的
    驱动浪。从粗到细找，先命中的就是较大级别的结构。

    优先级：驱动浪 > 调整浪（前者形态更明确）；同一类里粗阈值 > 细阈值。
    全都不成立时返回 None —— 宁可不画，也不硬凑一个浪型出来。

    传入 `threshold_pct` 则只试那一个值（测试与调试用）。
    """
    if len(rows) < MIN_BARS:
        return None

    thresholds = (
        [threshold_pct] if threshold_pct is not None else list(SCAN_THRESHOLDS_PCT)
    )

    # 先找驱动浪
    for pct in thresholds:
        pivots = zigzag(rows, pct)
        if len(pivots) < MIN_PIVOTS_IMPULSE:
            continue
        found = _try_impulse(pivots)
        if found is not None:
            result = dict(found)
            result["pivots"] = pivots
            result["threshold_pct"] = pct
            return result

    # 再退回调整浪
    for pct in thresholds:
        pivots = zigzag(rows, pct)
        if len(pivots) < MIN_PIVOTS_CORRECTIVE:
            continue
        found = _try_corrective(pivots)
        if found is not None:
            result = dict(found)
            result["pivots"] = pivots
            result["threshold_pct"] = pct
            return result

    return None
