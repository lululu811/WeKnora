"""
图表形态识别 — 反转形态、持续形态、蜡烛图形态、布林带

对应 Go: internal/agent/tools/hithink_finance/analysis/pattern.go

行序：rows[0] 是最新一根 K 线（见 utils 模块文档）。
`find_swings` 返回的下标升序，**列表末尾是最近的摆动点**。
"""

from typing import Dict, List, Optional

from .utils import find_swings, min_int, num, nz, round2

def _point(rows: List[Dict], idx: int, price: float, label: str) -> Dict:
    """
    一个可绘制的顶点。

    `index` 是 rows 里的下标（rows[0] 最新），`date` 用于前端把它对齐到 K 线
    ——前端按日期匹配（与 annotate 同口径），比传下标稳。
    """
    return {
        "index": int(idx),
        "date": str(rows[idx].get("date") or ""),
        "price": round2(price),
        "label": label,
    }


def _boundary_lines(rows: List[Dict], n: int, step: int) -> List[Dict]:
    """
    按固定步长取样，用**最老与最新**的样本连出上下两条边界线。

    三角形/楔形这类形态没有离散的顶点，它们的形状就是两条收敛的边界线。
    取样步长与检测器内部保持一致，画出来的线与判定用的线是同一组数据。
    """
    idxs = list(range(0, n, step))
    if len(idxs) < 2:
        return []
    newest, oldest = idxs[0], idxs[-1]
    return [
        _line(rows, oldest, nz(rows[oldest]["high"]), newest, nz(rows[newest]["high"]), "上边界"),
        _line(rows, oldest, nz(rows[oldest]["low"]), newest, nz(rows[newest]["low"]), "下边界"),
    ]


def _line(rows: List[Dict], i1: int, p1: float, i2: int, p2: float, label: str) -> Dict:
    """
    一条可绘制的参考线（颈线 / 目标位 / 趋势线）。

    统一成"两端点"的形状：水平线两端价格相同，斜线两端不同——前端一种画法通吃。
    """
    return {
        "label": label,
        "points": [_point(rows, i1, p1, label), _point(rows, i2, p2, label)],
    }


MIN_BARS = 10

# 旗杆的最大长度（根）。旗形是"急涨/急跌 + 短整理"，旗杆是十几根以内的事；
# 不封顶的话，真实数据里它会横跨整段历史，识别与绘制都失真。
FLAG_POLE_MAX = 15
SWING_WINDOW = 8
SHOULDER_TOLERANCE = 0.05
PEAK_TOLERANCE = 0.03


def detect_head_and_shoulders(rows: List[Dict]) -> Optional[Dict]:
    """
    检测头肩顶。

    摆动下标升序 + rows 倒序 ⇒ `highs[0]` 最近（右肩）、`highs[1]` 是头、
    `highs[2]` 最老（左肩）。
    """
    if len(rows) < 3 * SWING_WINDOW + 4:
        return None

    highs, lows = find_swings(rows, SWING_WINDOW)
    if len(highs) < 3 or len(lows) < 2:
        return None

    right = nz(rows[highs[0]]["high"])
    head = nz(rows[highs[1]]["high"])
    left = nz(rows[highs[2]]["high"])
    if head <= left or head <= right:
        return None

    shoulder_avg = (left + right) / 2
    if shoulder_avg <= 0 or abs(left - right) / shoulder_avg > SHOULDER_TOLERANCE:
        return None

    # 颈线取左肩与右肩之间的两个谷（不是"头之后的谷"）。
    neckline = (nz(rows[highs[2]]["low"]) + nz(rows[highs[0]]["low"])) / 2
    # 量度目标：颈线减去头到颈线的高度。旧实现写成 head - (head - neckline)，
    # 恒等于 neckline，等于没给目标价。
    target = neckline - (head - neckline)

    close = num(rows[0].get("close"))
    confidence = 0.6
    if close is not None and close < neckline:
        confidence += 0.2

    right_shoulder_vol = nz(rows[highs[0]]["vol"])
    volume_confirm = nz(rows[0]["vol"]) < right_shoulder_vol

    # 可绘制坐标。find_swings 的下标升序、rows 倒序 => highs[0] 最新（右肩），
    # 所以按时间顺序排是 [左肩, 头, 右肩]。
    points = [
        _point(rows, highs[2], left, "左肩"),
        _point(rows, highs[1], head, "头"),
        _point(rows, highs[0], right, "右肩"),
    ]
    lines = [
        _line(rows, highs[2], neckline, highs[0], neckline, "颈线"),
        _line(rows, highs[2], target, highs[0], target, "目标"),
    ]

    return {
        "name": "头肩顶",
        "type": "reversal",
        "direction": "bearish",
        "confidence": confidence,
        "points": points,
        "lines": lines,
        "key_levels": {
            "left_shoulder": left,
            "head": head,
            "right_shoulder": right,
            "neckline": neckline,
            "target": target,
        },
        "volume_confirm": volume_confirm,
        "desc": (
            f"头肩顶：左肩={left:.2f} 头={head:.2f} 右肩={right:.2f} "
            f"颈线={neckline:.2f} 目标={target:.2f}"
        ),
    }


def detect_double_top_bottom(rows: List[Dict]) -> Optional[Dict]:
    """检测双顶或双底。取最近两个摆动点。"""
    if len(rows) < 2 * SWING_WINDOW + 2:
        return None

    highs, lows = find_swings(rows, SWING_WINDOW)

    if len(highs) >= 2:
        p1 = nz(rows[highs[1]]["high"])
        p2 = nz(rows[highs[0]]["high"])
        avg = (p1 + p2) / 2
        if avg > 0 and abs(p1 - p2) / avg < PEAK_TOLERANCE:
            neckline = min(nz(rows[highs[1]]["low"]), nz(rows[highs[0]]["low"]))
            target = neckline - (avg - neckline)
            return {
                "name": "双顶",
                "type": "reversal",
                "direction": "bearish",
                "confidence": 0.65,
                "points": [
                    _point(rows, highs[1], p1, "顶1"),
                    _point(rows, highs[0], p2, "顶2"),
                ],
                "lines": [
                    _line(rows, highs[1], neckline, highs[0], neckline, "颈线"),
                    _line(rows, highs[1], target, highs[0], target, "目标"),
                ],
                "key_levels": {
                    "peak1": p1, "peak2": p2,
                    "neckline": neckline, "target": target,
                },
                "volume_confirm": nz(rows[0]["vol"]) < nz(rows[highs[0]]["vol"]),
                "desc": f"双顶：顶1={p1:.2f} 顶2={p2:.2f} 颈线={neckline:.2f}",
            }

    if len(lows) >= 2:
        p1 = nz(rows[lows[1]]["low"])
        p2 = nz(rows[lows[0]]["low"])
        avg = (p1 + p2) / 2
        if avg > 0 and abs(p1 - p2) / avg < PEAK_TOLERANCE:
            neckline = max(nz(rows[lows[1]]["high"]), nz(rows[lows[0]]["high"]))
            target = neckline + (neckline - avg)
            return {
                "name": "双底",
                "type": "reversal",
                "direction": "bullish",
                "confidence": 0.65,
                "points": [
                    _point(rows, lows[1], p1, "底1"),
                    _point(rows, lows[0], p2, "底2"),
                ],
                "lines": [
                    _line(rows, lows[1], neckline, lows[0], neckline, "颈线"),
                    _line(rows, lows[1], target, lows[0], target, "目标"),
                ],
                "key_levels": {
                    "bottom1": p1, "bottom2": p2,
                    "neckline": neckline, "target": target,
                },
                "volume_confirm": nz(rows[0]["vol"]) > nz(rows[lows[0]]["vol"]),
                "desc": f"双底：底1={p1:.2f} 底2={p2:.2f} 颈线={neckline:.2f}",
            }

    return None


def detect_triangle(rows: List[Dict]) -> Optional[Dict]:
    """检测三角形形态（对称/上升/下降）。"""
    n = min_int(30, len(rows))
    if n < 15:
        return None
    segment = rows[:n]

    highs: List[float] = []
    lows: List[float] = []
    for i in range(0, n, 5):
        highs.append(nz(segment[i]["high"]))
        lows.append(nz(segment[i]["low"]))

    if len(highs) < 3:
        return None

    # rows 倒序：samples[0] 最新、[n-1] 最老。
    # 用 (新 - 老) 归一，upper_slope < 0 = 高点在下移。
    upper_slope = sum(highs[i - 1] - highs[i] for i in range(1, len(highs))) / (len(highs) - 1)
    lower_slope = sum(lows[i - 1] - lows[i] for i in range(1, len(lows))) / (len(lows) - 1)

    if upper_slope < -0.1 and lower_slope > 0.1:
        return {
            "name": "对称三角形", "type": "continuation", "direction": "neutral",
            "confidence": 0.6,
            "lines": _boundary_lines(rows, n, 5),
            "desc": f"高点递减(斜率{upper_slope:.3f}) + 低点递增(斜率{lower_slope:.3f})，收敛整理中",
        }
    if abs(upper_slope) < 0.3 and lower_slope > 0.1:
        return {
            "name": "上升三角形", "type": "continuation", "direction": "bullish",
            "confidence": 0.65,
            "lines": _boundary_lines(rows, n, 5),
            "desc": f"高点持平 + 低点递增(斜率{lower_slope:.3f})，看涨突破形态",
        }
    if upper_slope < -0.1 and abs(lower_slope) < 0.3:
        return {
            "name": "下降三角形", "type": "continuation", "direction": "bearish",
            "confidence": 0.65,
            "lines": _boundary_lines(rows, n, 5),
            "desc": f"高点递减(斜率{upper_slope:.3f}) + 低点持平，看跌突破形态",
        }
    return None


def detect_wedge(rows: List[Dict]) -> Optional[Dict]:
    """
    检测楔形形态（上升/下降楔形）。

    旧实现用 `rows[i].high - rows[i+1].high`（i 从 0=最新开始）求和，
    符号整体反了：上涨行情算出 peak_trend<0，于是被标成"上升楔形/看跌"。
    这里显式把符号归一为"高点在上升 / 高点在下降"，并且真正检查收敛。
    """
    n = min_int(25, len(rows))
    if n < 12:
        return None

    peak_trend = 0.0
    trough_trend = 0.0
    samples = 0
    for i in range(0, n - 1, 3):
        j = i + 1
        # rows 倒序：i 更新、j 更老，所以 (新 - 老) 为正 = 在上升。
        peak_trend += nz(rows[i]["high"]) - nz(rows[j]["high"])
        trough_trend += nz(rows[i]["low"]) - nz(rows[j]["low"])
        samples += 1
    if samples == 0:
        return None
    peak_trend /= samples
    trough_trend /= samples

    rising = peak_trend > 0 and trough_trend > 0
    falling = peak_trend < 0 and trough_trend < 0
    if not (rising or falling):
        return None

    # 收敛：近端振幅比远端小
    near_span = max(nz(r["high"]) for r in rows[:5]) - min(nz(r["low"]) for r in rows[:5])
    far_span = max(nz(r["high"]) for r in rows[5:12]) - min(nz(r["low"]) for r in rows[5:12])
    converging = far_span > 0 and near_span < far_span

    if rising:
        return {
            "name": "上升楔形", "type": "reversal", "direction": "bearish",
            "confidence": 0.65 if converging else 0.55,
            "lines": _boundary_lines(rows, n, 3),
            "desc": (
                f"高点和低点同时上升（高点斜率{peak_trend:.3f}，低点斜率{trough_trend:.3f}）"
                + ("且振幅收敛" if converging else "但未见收敛")
                + " → 上涨动能衰减，看跌反转形态"
            ),
        }
    return {
        "name": "下降楔形", "type": "reversal", "direction": "bullish",
        "confidence": 0.65 if converging else 0.55,
        "lines": _boundary_lines(rows, n, 3),
        "desc": (
            f"高点和低点同时下降（高点斜率{peak_trend:.3f}，低点斜率{trough_trend:.3f}）"
            + ("且振幅收敛" if converging else "但未见收敛")
            + " → 下跌动能衰减，看涨反转形态"
        ),
    }


def detect_flag(rows: List[Dict]) -> Optional[Dict]:
    """
    检测旗形形态（牛市/熊市旗形）。

    行序修正：rows 倒序，所以**旗杆（急涨/急跌）在最老的一侧**，
    整理段在最近的一侧。旧实现把这两段装反了，标准旗形一个都识别不出来。
    """
    if len(rows) < 15:
        return None

    # consolidation 是最近的一段；flag_len 为其长度
    for consolidation_len in range(3, 11):
        consolidation = rows[:consolidation_len]
        # 旗杆必须**紧邻**整理段，所以窗口要封顶。
        #
        # 原来取 `rows[consolidation_len:]`（剩下的全部历史），短窗口下单测看不出来，
        # 但接真实数据（250+ 根）时"旗杆"会横跨一两年，change_pct 变成区间总涨跌幅，
        # 判出来的旗形和画出来的旗杆线都是错的。标准旗形的旗杆是几根到十几根。
        pole = rows[consolidation_len:consolidation_len + FLAG_POLE_MAX]
        if len(pole) < 2:
            continue

        pole_old, pole_new = pole[-1], pole[0]  # 旗杆起点(最老) / 终点(最近)
        start_close, end_close = num(pole_old.get("close")), num(pole_new.get("close"))
        if start_close is None or end_close is None or start_close <= 0:
            continue

        change_pct = (end_close - start_close) / start_close * 100
        if abs(change_pct) < 5:
            continue

        drifts = [num(r.get("close")) for r in consolidation]
        if any(d is None for d in drifts):
            continue
        # 整理段净漂移
        cons_change = (drifts[0] - drifts[-1]) / drifts[-1] if drifts[-1] > 0 else 0.0

        # 可绘制坐标：旗杆是一条斜线（旗杆起点 -> 整理段起点），整理段是一个矩形。
        cons_high = max(nz(r["high"]) for r in consolidation)
        cons_low = min(nz(r["low"]) for r in consolidation)
        i_pole_start = consolidation_len + len(pole) - 1
        flag_lines = [
            _line(rows, i_pole_start, start_close, consolidation_len, end_close, "旗杆"),
            _line(rows, consolidation_len - 1, cons_high, 0, cons_high, "整理上沿"),
            _line(rows, consolidation_len - 1, cons_low, 0, cons_low, "整理下沿"),
        ]

        if change_pct > 0 and -0.03 < cons_change < 0.03:
            return {
                "name": "牛市旗形", "type": "continuation", "direction": "bullish",
                "confidence": 0.55,
                "lines": flag_lines,
                "desc": (
                    f"{change_pct:.1f}% 急涨后横向整理{consolidation_len}日"
                    f"（净漂移{cons_change * 100:+.2f}%），看涨中继形态"
                ),
            }
        if change_pct < 0 and -0.03 < cons_change < 0.03:
            return {
                "name": "熊市旗形", "type": "continuation", "direction": "bearish",
                "confidence": 0.55,
                "lines": flag_lines,
                "desc": (
                    f"{change_pct:.1f}% 急跌后横向整理{consolidation_len}日"
                    f"（净漂移{cons_change * 100:+.2f}%），看跌中继形态"
                ),
            }
    return None


def detect_candlesticks(rows: List[Dict]) -> List[Dict]:
    """检测蜡烛图形态。值为 NULL 时不发信号（缺失 != 0）。"""
    if not rows:
        return []
    r = rows[0]

    candle_map = {
        "cdl_hammer": ("锤子线", "bullish", "下影线长，潜在底部反转"),
        "cdl_shooting_star": ("流星线", "bearish", "上影线长，潜在顶部反转"),
        "cdl_doji": ("十字星", "neutral", "多空平衡，变盘信号"),
        "cdl_engulfing": ("看涨吞没", "bullish", "阳线吞没前日阴线"),
        "cdl_harami": ("孕线", "neutral", "趋势放缓"),
        "cdl_morning_star": ("晨星", "bullish", "底部反转形态"),
        "cdl_evening_star": ("暮星", "bearish", "顶部反转形态"),
        "cdl_piercing": ("刺透线", "bullish", "看涨刺透形态"),
        "cdl_dark_cloud": ("乌云盖顶", "bearish", "看跌乌云形态"),
        "cdl_3white": ("三白兵", "bullish", "连续三阳，强势上涨"),
        "cdl_3black": ("三乌鸦", "bearish", "连续三阴，强势下跌"),
    }

    signals = []
    for key, (name, signal, desc) in candle_map.items():
        value = num(r.get(key))
        if value is not None and value != 0:
            signals.append({
                "name": name, "signal": signal,
                "date": r["date"], "desc": desc,
            })
    return signals


def analyze_bollinger(rows: List[Dict]) -> Optional[Dict]:
    """
    布林带状态分析。

    布林带为 NULL 时整体返回 None。旧实现对 NULL 用 0 兜底，
    于是 `close >= 0` 每根都成立，把没有数据的标的报成
    "连续 N 日触及上轨，强势运行"。
    """
    if len(rows) < 5:
        return None
    r = rows[0]
    if any(num(r.get(k)) is None for k in ("bb_upper", "bb_mid", "bb_lower", "close")):
        return None

    # 带宽序列同样要求有数据
    widths: List[float] = []
    for row in rows[:5]:
        upper, lower = num(row.get("bb_upper")), num(row.get("bb_lower"))
        if upper is not None and lower is not None:
            widths.append(upper - lower)

    width_trend = "stable"
    if len(widths) >= 3:
        newest, oldest = widths[0], widths[-1]
        if newest < oldest * 0.7:
            width_trend = "narrowing"
        elif newest > oldest * 1.3:
            width_trend = "expanding"

    close = num(r["close"])
    upper_band, lower_band = num(r["bb_upper"]), num(r["bb_lower"])
    if close >= upper_band:
        position = "upper"
    elif close <= lower_band:
        position = "lower"
    else:
        position = "middle"

    # Band walk：从最新往老数连续触轨天数，触到另一轨就清零
    upper_walk, lower_walk = 0, 0
    for row in rows:
        row_close = num(row.get("close"))
        row_upper = num(row.get("bb_upper"))
        row_lower = num(row.get("bb_lower"))
        if None in (row_close, row_upper, row_lower):
            break
        if row_close >= row_upper:
            upper_walk += 1
            lower_walk = 0
        elif row_close <= row_lower:
            lower_walk += 1
            upper_walk = 0
        else:
            break

    squeeze = width_trend == "narrowing"
    if squeeze:
        desc = "布林带收口，变盘前兆"
    elif upper_walk >= 3:
        desc = f"连续{upper_walk}日触及上轨，强势运行"
    elif lower_walk >= 3:
        desc = f"连续{lower_walk}日触及下轨，弱势运行"
    else:
        desc = f"价格位于布林带{position}区域"

    return {
        "upper": upper_band,
        "middle": num(r["bb_mid"]),
        "lower": lower_band,
        "position": position,
        "width_trend": width_trend,
        "squeeze": squeeze,
        "upper_band_walk": upper_walk,
        "lower_band_walk": lower_walk,
        "desc": desc,
    }


def analyze_chart_pattern(rows: List[Dict]) -> Dict:
    """图表形态识别入口。"""
    if len(rows) < MIN_BARS:
        raise ValueError(f"数据不足（需要至少 {MIN_BARS} 个交易日）")

    patterns: List[Dict] = []
    for detector in (
        detect_head_and_shoulders,
        detect_double_top_bottom,
        detect_triple_top_bottom,
        detect_triangle,
        detect_wedge,
        detect_flag,
        detect_rectangle,
        detect_island_reversal,
    ):
        found = detector(rows)
        if found is not None:
            patterns.append(found)

    bull_count = sum(1 for p in patterns if p["direction"] == "bullish")
    bear_count = sum(1 for p in patterns if p["direction"] == "bearish")

    if bull_count > bear_count:
        verdict = "偏多"
    elif bear_count > bull_count:
        verdict = "偏空"
    else:
        verdict = "中性"

    return {
        "patterns": patterns,
        "candlestick_signals": detect_candlesticks(rows),
        "bollinger_status": analyze_bollinger(rows),
        "summary": {
            "total_patterns": len(patterns),
            "bullish": bull_count,
            "bearish": bear_count,
            "verdict": verdict,
        },
    }


# ---------------------------------------------------------------------------
# 补充几何形态（三重顶底 / 矩形整理 / 岛形反转）
#
# 与上面几个检测器同一套约定：rows[0] 最新、返回 points（离散顶点）+ lines
# （参考线，统一两端点形状），前端一种画法通吃。
# ---------------------------------------------------------------------------

# 三个极值之间的容差。比双顶（PEAK_TOLERANCE=0.05）严一点：三次都落在同一个
# 价位附近本身就是更强的约束，放宽了会把"震荡上行"也判成三重顶。
TRIPLE_TOLERANCE = 0.035

# 矩形：上下沿的间距必须落在区间内，否则不是箱体
RECTANGLE_MIN_HEIGHT = 0.03
RECTANGLE_MAX_HEIGHT = 0.18
# 上下沿各自至少被触碰这么多次（否则只是一条趋势线，不是矩形）
RECTANGLE_MIN_TOUCHES = 2
RECTANGLE_TOUCH_TOLERANCE = 0.015


def detect_triple_top_bottom(rows: List[Dict]) -> Optional[Dict]:
    """
    三重顶 / 三重底：三个大致等高的极值 + 两次回调。

    双顶的推广。要求三次极值的相对偏差都在 TRIPLE_TOLERANCE 之内 ——
    逐对比较会漏掉"1、2 接近但 3 明显高"的情况，而那恰恰是上升三角形。
    """
    if len(rows) < 4 * SWING_WINDOW:
        return None

    highs, lows = find_swings(rows, SWING_WINDOW)

    if len(highs) >= 3:
        # highs 升序，开头 = 最近。取最近三个，按时间顺序（老 -> 新）重排。
        idx = [highs[2], highs[1], highs[0]]
        peaks = [nz(rows[i]["high"]) for i in idx]
        avg = sum(peaks) / 3
        if avg > 0 and all(abs(p - avg) / avg < TRIPLE_TOLERANCE for p in peaks):
            neckline = min(nz(rows[i]["low"]) for i in idx[1:2] + [idx[0]])
            target = neckline - (avg - neckline)
            return {
                "name": "三重顶",
                "type": "reversal",
                "direction": "bearish",
                "confidence": 0.7,
                "points": [_point(rows, idx[k], peaks[k], f"顶{k + 1}") for k in range(3)],
                "lines": [
                    _line(rows, idx[0], neckline, idx[2], neckline, "颈线"),
                    _line(rows, idx[0], target, idx[2], target, "目标"),
                ],
                "key_levels": {"peak1": peaks[0], "peak2": peaks[1], "peak3": peaks[2],
                               "neckline": neckline, "target": target},
                "desc": f"三重顶：{peaks[0]:.2f} / {peaks[1]:.2f} / {peaks[2]:.2f}，颈线={neckline:.2f}",
            }

    if len(lows) >= 3:
        idx = [lows[2], lows[1], lows[0]]
        troughs = [nz(rows[i]["low"]) for i in idx]
        avg = sum(troughs) / 3
        if avg > 0 and all(abs(p - avg) / avg < TRIPLE_TOLERANCE for p in troughs):
            neckline = max(nz(rows[i]["high"]) for i in idx[1:2] + [idx[0]])
            target = neckline + (avg - neckline)
            return {
                "name": "三重底",
                "type": "reversal",
                "direction": "bullish",
                "confidence": 0.7,
                "points": [_point(rows, idx[k], troughs[k], f"底{k + 1}") for k in range(3)],
                "lines": [
                    _line(rows, idx[0], neckline, idx[2], neckline, "颈线"),
                    _line(rows, idx[0], target, idx[2], target, "目标"),
                ],
                "key_levels": {"bottom1": troughs[0], "bottom2": troughs[1], "bottom3": troughs[2],
                               "neckline": neckline, "target": target},
                "desc": f"三重底：{troughs[0]:.2f} / {troughs[1]:.2f} / {troughs[2]:.2f}，颈线={neckline:.2f}",
            }

    return None


def detect_rectangle(rows: List[Dict]) -> Optional[Dict]:
    """
    矩形整理（箱体）：上下沿各自被反复触碰，中间的走势没有方向。

    判据分两条，缺一不可：
      1. 上下沿间距落在 [3%, 18%]。太窄是横线，太宽是趋势；
      2. 上下沿**各自**至少被碰到 RECTANGLE_MIN_TOUCHES 次。
    只算极值点不行 —— 只碰过一次的上下沿是一条通道，不是箱体，画出来会误导。
    """
    n = min_int(40, len(rows))
    if n < 20:
        return None

    segment = rows[:n]
    highs = [nz(r["high"]) for r in segment]
    lows = [nz(r["low"]) for r in segment]
    upper, lower = max(highs), min(lows)
    if lower <= 0:
        return None
    height = (upper - lower) / lower
    if not (RECTANGLE_MIN_HEIGHT <= height <= RECTANGLE_MAX_HEIGHT):
        return None

    up_band = upper * (1 - RECTANGLE_TOUCH_TOLERANCE)
    low_band = lower * (1 + RECTANGLE_TOUCH_TOLERANCE)
    touches_up = sum(1 for h in highs if h >= up_band)
    touches_low = sum(1 for l in lows if l <= low_band)
    if touches_up < RECTANGLE_MIN_TOUCHES or touches_low < RECTANGLE_MIN_TOUCHES:
        return None

    # 箱体本身不预判方向：突破方向未知，所以给 neutral，别替用户站边。
    latest = nz(rows[0]["close"])
    if latest > upper * (1 + RECTANGLE_TOUCH_TOLERANCE):
        direction, verdict = "bullish", "已向上突破"
    elif latest < lower * (1 - RECTANGLE_TOUCH_TOLERANCE):
        direction, verdict = "bearish", "已向下突破"
    else:
        direction, verdict = "neutral", "仍在箱体内"

    return {
        "name": "矩形整理",
        "type": "continuation",
        "direction": direction,
        "confidence": 0.55,
        "points": [],
        "lines": [
            _line(rows, n - 1, upper, 0, upper, "箱体上沿"),
            _line(rows, n - 1, lower, 0, lower, "箱体下沿"),
        ],
        "key_levels": {"upper": upper, "lower": lower, "height_pct": round2(height * 100)},
        "desc": (
            f"近 {n} 根在 {lower:.2f}~{upper:.2f} 之间横向整理"
            f"（振幅{height * 100:.1f}%，上沿碰到{touches_up}次、下沿{touches_low}次），{verdict}"
        ),
    }


def detect_island_reversal(rows: List[Dict], max_island: int = 10) -> Optional[Dict]:
    """
    岛形反转：跳空离开，再反向跳空回来，中间那段孤立成岛。

    必须**两个缺口方向相反**且中间夹着不超过 max_island 根 —— 只有一个缺口是
    普通跳空，两个同向缺口是持续性缺口，都不是反转。

    实现上先转成**时间顺序**再判。第一版直接在倒序下标上推理，把顶部岛和底部岛
    判反了：倒序里"更老"是更大的下标，肉眼推演时极易把方向搞反，而这两种情形
    的判据恰好互为镜像 —— 写反了不会报错，只会给出完全相反的方向。
    """
    n = min_int(40, len(rows))
    if n < 6:
        return None

    chrono = list(reversed(rows[:n]))   # chrono[0] = 最老
    m = len(chrono)
    # rows 下标 = n - 1 - chrono 下标
    to_rows_index = lambda ci: n - 1 - ci

    for a in range(1, m - 2):
        for b in range(a, min(a + max_island, m - 1)):
            before, first = chrono[a - 1], chrono[a]
            last, after = chrono[b], chrono[b + 1]

            # 顶部岛形：向上跳空进来，向下跳空出去
            if nz(first["low"]) > nz(before["high"]) and nz(after["high"]) < nz(last["low"]):
                island_bars = b - a + 1
                island_high = max(nz(r["high"]) for r in chrono[a:b + 1])
                return {
                    "name": "顶部岛形反转",
                    "type": "reversal",
                    "direction": "bearish",
                    "confidence": 0.6,
                    "points": [
                        _point(rows, to_rows_index(a), nz(first["low"]), "上跳缺口"),
                        _point(rows, to_rows_index(a + (island_bars - 1)), island_high, "岛"),
                        _point(rows, to_rows_index(b + 1), nz(after["high"]), "下跳缺口"),
                    ],
                    "lines": [
                        _line(rows, to_rows_index(b + 1), nz(after["high"]),
                              to_rows_index(a), nz(first["low"]), "缺口区"),
                    ],
                    "key_levels": {"island_high": island_high, "island_bars": island_bars},
                    "desc": (
                        f"向上跳空后 {island_bars} 根又向下跳空，顶部岛形"
                        f"（岛高{island_high:.2f}，上跳缺口{first['date']}，下跳缺口{after['date']}）"
                    ),
                }

            # 底部岛形：向下跳空进来，向上跳空出去
            if nz(first["high"]) < nz(before["low"]) and nz(after["low"]) > nz(last["high"]):
                island_bars = b - a + 1
                island_low = min(nz(r["low"]) for r in chrono[a:b + 1])
                return {
                    "name": "底部岛形反转",
                    "type": "reversal",
                    "direction": "bullish",
                    "confidence": 0.6,
                    "points": [
                        _point(rows, to_rows_index(a), nz(first["high"]), "下跳缺口"),
                        _point(rows, to_rows_index(a + (island_bars - 1)), island_low, "岛"),
                        _point(rows, to_rows_index(b + 1), nz(after["low"]), "上跳缺口"),
                    ],
                    "lines": [
                        _line(rows, to_rows_index(b + 1), nz(after["low"]),
                              to_rows_index(a), nz(first["high"]), "缺口区"),
                    ],
                    "key_levels": {"island_low": island_low, "island_bars": island_bars},
                    "desc": (
                        f"向下跳空后 {island_bars} 根又向上跳空，底部岛形"
                        f"（岛低{island_low:.2f}，下跳缺口{first['date']}，上跳缺口{after['date']}）"
                    ),
                }

    return None
