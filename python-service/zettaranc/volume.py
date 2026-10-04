"""
量价分析 — 威科夫方法 + 量价关系 + 假突破检测

对应 Go: internal/agent/tools/hithink_finance/analysis/volume.go

行序：rows[0] 是最新一根 K 线。
缺失的指标（None）不参与计算，也不该被当成 0。
"""

from typing import Dict, List, Optional

from .utils import min_int, num, nz, round2

MIN_BARS = 10

# ── 缩量回踩形态的窗口与阈值 ─────────────────────────────────────
#
# 来源是用户自有的 a-stock/b1.md（「放量突破 → 缩量回踩 → 不破前低」）。
# 搬到这里而不是留在 a-stock 的理由：那边只有 SQL 和文档，没有可复用实现，
# 而且那份文档与它自己的 SQL 在三处阈值上互相矛盾（见下方偏差说明）。
#
# ⚠️ **与 b1.md 有三处有意偏差**，都不是笔误，改动前先读下面的注释：
#
# 1. 窗口 70 根，不是 52 周（250 根）。b1.md 正文写 `low_52w`，但它自己的
#    b1_screen.sql 写的是 `low_70d` —— 两份不一致，而 70 根正好是选股池
#    单次快照能负担的量级（见 screener.LOOKBACK_DAYS）。这里跟 SQL 走。
# 2. `from_btm` 的分母用 70 日低，不是 52 周低。同上，SQL 口径。
# 3. KDJ 的 J<-5 不在这里判。b1.md 里 J 有三个互相冲突的取值（README 写
#    -5、zettaranc 的建仓波写 -10、P6 写 -13），在裁决之前不把任何一个
#    焊进形态判定 —— J 是单根指标，由 signals.py 用同一份 J 值单独发信号，
#    阈值留在策略规则层，可改不改这里。
SHRINK_PULLBACK_WINDOW = 70       # 形态判定需要的 K 线根数
SHRINK_PULLBACK_RECENT = 5        # 近 5 日：均量、最低价
SHRINK_PULLBACK_TREND = 15        # 近 15 日：最大量、阳/阴线均量
SHRINK_PULLBACK_BASE_START = 19   # 基准量起点（跳过最近 19 根，取更早的一段）
SHRINK_PULLBACK_BASE_LEN = 51     # 基准量长度 → 合计 70 根

SHRINK_MAX = 0.5                  # shrink = 近5日均量 / 近15日最大量
SHRINK_DN_UP_MAX = 0.8            # dn_up = 近15日阴线均量 / 阳线均量
SHRINK_MAX_DN_RATIO_MAX = 1.8     # max_dn_vol / vol_base（下跌日的恐慌放量）
SHRINK_FLOOR_MULT = 1.02          # low_5d > low_70d * 1.02 才算守住前低
SHRINK_FROM_BTM_MAX_PCT = 30.0    # (close - low_70d) / low_70d 的百分数上限


def _vol_of(row: Optional[Dict]) -> Optional[float]:
    """取一行的成交量。

    两套行结构并存：集合式选股池的价量快照列名是 `volume`
    （screener.PRICE_SNAPSHOT_COLUMNS），而本模块其余函数读的是 `vol`。
    以 `volume` 为主、`vol` 兜底 —— 少一个键名兜底，这个形态在只提供 `vol`
    的单只扫描路径上就会整段返回 None，而且不报错。
    """
    if row is None:
        return None
    value = num(row.get("volume"))
    return value if value is not None else num(row.get("vol"))


def _calc_price_range(rows: List[Dict]) -> float:
    if not rows:
        return 0.0
    high = max(nz(r.get("high")) for r in rows)
    low = min(nz(r.get("low")) for r in rows)
    if low == 0:
        return 0.0
    return (high - low) / low


def _mean_of(rows: List[Dict], key: str, count: int, start: int = 0) -> Optional[float]:
    """窗口均值。窗口内有缺失则返回 None（不拿 0 凑数）。"""
    window = rows[start:start + count]
    if len(window) < count:
        return None
    values = [num(r.get(key)) for r in window]
    if any(v is None for v in values):
        return None
    return sum(values) / len(values)


def _calc_vol_trend(rows: List[Dict]) -> Optional[float]:
    """近期 5 日均量 vs 较老 5 日均量变化率。"""
    if len(rows) < 10:
        return None
    recent = _mean_of(rows, "vol", 5, 0)
    old = _mean_of(rows, "vol", 5, 5)
    if recent is None or old is None or old == 0:
        return None
    return (recent - old) / old


def shrink_pullback_metrics(rows: List[Dict]) -> Dict:
    """缩量回踩形态的五个量 + 三个布尔结论。

    返回 insufficient=True 时不给任何结论，只如实回报缺多少根 K 线 ——
    与本模块"缺数据就标 unknown，不拿 0 凑数"的既有口径一致。

    为什么形态判定放在这里而不塞进 signals.detect_signals：
    那个函数的信号契约是"单根或少数几根 K 线的原语"，而这里的 shrink /
    dn_up / max_dn_ratio / from_btm / floor 全部是**跨 70 根的聚合量**。
    硬塞进去会让 STRATEGY_RULES 那个 {match_signals, min_count} 字典
    退化成什么都装的黑洞。所以分工是：这里算数，这里发信号，
    STRATEGY_RULES 只负责把信号组合成策略。

    rows[0] 是最新一根（与本模块其余函数一致）。
    """
    if len(rows) < SHRINK_PULLBACK_WINDOW:
        return {
            "insufficient": True,
            "required_bars": SHRINK_PULLBACK_WINDOW,
            "actual_bars": len(rows),
        }

    win = rows[:SHRINK_PULLBACK_WINDOW]
    recent = win[:SHRINK_PULLBACK_RECENT]
    trend = win[:SHRINK_PULLBACK_TREND]
    base = win[SHRINK_PULLBACK_BASE_START:
               SHRINK_PULLBACK_BASE_START + SHRINK_PULLBACK_BASE_LEN]

    def _avg(values: List[Optional[float]]) -> Optional[float]:
        return None if any(v is None for v in values) else \
            sum(values) / len(values)  # type: ignore[arg-type]

    def _need(name: str, values: List[Optional[float]]) -> Optional[str]:
        """任一元素缺失就返回缺失原因名，供调用方如实回报。"""
        return name if any(v is None for v in values) else None

    # ── 量能三件套 ──
    vol_recent = [_vol_of(r) for r in recent]
    vol_trend = [_vol_of(r) for r in trend]
    vol_base = [_vol_of(r) for r in base]

    missing = next((m for m in (
        _need("近5日成交量", vol_recent),
        _need("近15日成交量", vol_trend),
        _need("基准量成交量", vol_base),
    ) if m), None)
    if missing is not None:
        return {
            "insufficient": True,
            "required_bars": SHRINK_PULLBACK_WINDOW,
            "actual_bars": len(rows),
            "missing": missing,
        }

    vol_5d = _avg(vol_recent)
    vol_base_avg = _avg(vol_base)
    max_vol_15d = max(vol_trend)  # type: ignore[type-var]

    # ── 阳/阴线拆分：b1.md 的口径是 close > open 记阳、close <= open 记阴 ──
    up_vol: List[Optional[float]] = []
    dn_vol: List[Optional[float]] = []
    for r in trend:
        c, o = num(r.get("close")), num(r.get("open"))
        v = _vol_of(r)
        if c is None or o is None or v is None:
            return {
                "insufficient": True,
                "required_bars": SHRINK_PULLBACK_WINDOW,
                "actual_bars": len(rows),
                "missing": "近15日开收盘价",
            }
        (up_vol if c > o else dn_vol).append(v)

    # 15 个交易日全是阳（或全阴）时，均量比无定义 —— 那种形态本来也不该
    # 被判成"下跌不放量"，返回 None 让调用方不发信号，而不是拿 0 当分母。
    vol_up = _avg(up_vol) if up_vol else None
    vol_dn = _avg(dn_vol) if dn_vol else None

    # ── 价格位置 ──
    lows_recent = [num(r.get("low")) for r in recent]
    lows_all = [num(r.get("low")) for r in win]
    close_now = num(win[0].get("close"))

    missing = next((m for m in (
        _need("近5日最低价", lows_recent),
        _need("70日最低价", lows_all),
    ) if m), None)
    if missing is not None or close_now is None:
        return {
            "insufficient": True,
            "required_bars": SHRINK_PULLBACK_WINDOW,
            "actual_bars": len(rows),
            "missing": missing or "最新收盘价",
        }

    low_5d = min(lows_recent)   # type: ignore[type-var]
    low_70d = min(lows_all)     # type: ignore[type-var]

    # 每一项都独立判 None，不让一个除数缺失把整段拉成 0。
    shrink = (vol_5d / max_vol_15d) if (vol_5d and max_vol_15d) else None
    dn_up = (vol_dn / vol_up) if (vol_dn and vol_up) else None
    max_dn_ratio = (
        max(dn_vol) / vol_base_avg if (dn_vol and vol_base_avg) else None
    )
    from_btm_pct = (
        (close_now - low_70d) / low_70d * 100 if low_70d else None
    )

    return {
        "insufficient": False,
        "date": win[0].get("date"),
        "vol_5d": round2(vol_5d) if vol_5d is not None else None,
        "max_vol_15d": round2(max_vol_15d) if max_vol_15d is not None else None,
        "shrink": round2(shrink) if shrink is not None else None,
        "dn_up": round2(dn_up) if dn_up is not None else None,
        "max_dn_ratio": round2(max_dn_ratio) if max_dn_ratio is not None else None,
        "from_btm_pct": round2(from_btm_pct) if from_btm_pct is not None else None,
        "low_5d": round2(low_5d),
        "low_70d": round2(low_70d),
        "is_shrunk": shrink is not None and shrink < SHRINK_MAX,
        "no_panic_volume": dn_up is not None and dn_up < SHRINK_DN_UP_MAX
        and (max_dn_ratio is None or max_dn_ratio < SHRINK_MAX_DN_RATIO_MAX),
        "floor_held": low_70d > 0 and low_5d > low_70d * SHRINK_FLOOR_MULT,
        "near_bottom": from_btm_pct is not None
        and from_btm_pct < SHRINK_FROM_BTM_MAX_PCT,
    }


# ── 长安三件套 / 双枪放量 ─────────────────────────────────────
#
# 两条都落在本形态层，但**理由不同**，别混为一谈：
#
# **长安三件套** —— 罕见但真实。全库 5,572 只 × 十年 **10,349,853 根 bar** 上，
#   七个条件同时成立恰好 **117 次**（0.0011%，约每 20 个交易日全市场一次）。
#   逐条拆开看并不离奇：J<-13 有 87,874 次、量<T1×0.7 有 1,731,253 次、
#   两者同时 12,472 次 —— 是七条 AND 把概率压到了 1/88,000。
#   run_signal_audit.sh 的 300 只定距样本只覆盖全库 2.7%，期望 3.2 次，因此报
#   `dead`；λ=3.2 时 P(0)≈4%，**那个 dead 是抽样波动，不是策略失效**。
#   它进不了 signals.go 的理由是**用途**：1/88,000 的 bar 率不属于"每根 K 报一次
#   状态"，而属于"今天恰好出现这个形态"。
#
#   ⚠️ 途中踩过一个真实的坑，值得留着：第一版守卫写成 hasData(..., latest.J)，
#   而 hasData 要求每个值 > 0、主判据却是 J < -13 —— 信号因此**永远打不出来**，
#   那个 dead 是我的 bug。教训：拿到 dead 判定，先确认信号在结构上可能触发，
#   再谈阈值松紧。
#
# **双枪放量** —— 15 根窗口，**结构上进不了频率审计**：auditWinSize 是 signals.go
#   里最大的 `len(rows) >= N` 守卫（7），审计统一喂 rows[t:t+7]。双枪要 15 根，
#   `len(rows) >= 15` 在审计里恒不成立，必然报 dead。那个 dead 说的是
#   "审计窗口不够"，不是"策略不成立" —— 与 shrink_pullback（70 根窗口）同类。
CHANGAN_BARS = 3
CHANGAN_J_DEEP = -13.0     # T 日 J 深度超卖
CHANGAN_T1_GAIN_MIN = 0.039  # T+1 涨幅下限
CHANGAN_J_RECOVERED = 55.0   # T+1 的 J 要回到 55 以下
CHANGAN_QUIET = 0.02         # T 日涨跌幅绝对值上限
CHANGAN_AMP_MAX = 0.07       # T 日振幅上限
CHANGAN_SHRINK_TO = 0.7      # T 日量 < T+1 的 70%

DOUBLE_GUN_BARS = 15
DOUBLE_GUN_BASE_SLICE = slice(5, 15)   # SQL 叫它 vol_5d，实际取 rn 6~15 = 十根
DOUBLE_GUN_MID_SLICE = slice(1, 4)     # 中间三根
DOUBLE_GUN_VOL_RATIO = 1.5
DOUBLE_GUN_BODY_PCT = 0.02
DOUBLE_GUN_MID_SHRINK = 0.8


def changan_metrics(rows: List[Dict]) -> Dict:
    """长安三件套的七个条件逐条判，返回是否全部成立。

    ⚠️ a-stock 的 p6_chang_an_screen.sql 把**最新**那根叫 T、往前数叫 T+1 / T+2，
    标号方向是反的，照字面读会整体错位两根。落到本模块的 rows[0]=T、rows[1]=T+1、
    rows[2]=T+2，`j_t` 取的是**最新**那根（T）的 J。
    """
    if len(rows) < CHANGAN_BARS:
        return {"insufficient": True, "required_bars": CHANGAN_BARS,
                "actual_bars": len(rows)}
    t, t1, t2 = rows[0], rows[1], rows[2]
    vals = [num(t.get(k)) for k in ("close", "open", "high", "low", "volume", "j")]
    vals += [num(t1.get(k)) for k in ("close", "volume", "j")]
    vals += [num(t2.get(k)) for k in ("close", "volume")]
    if any(v is None for v in vals):
        return {"insufficient": True, "missing": "长安三件套所需字段"}
    c0, _, h0, l0, v0, j0 = vals[0:6]
    c1, v1, j1 = vals[6:9]
    c2, v2 = vals[9:11]
    if c1 <= 0 or c2 <= 0 or v1 <= 0 or v2 <= 0:
        return {"insufficient": True, "missing": "零价或零量"}
    t1_gain = (c1 - c2) / c2
    t0_gain = (c0 - c1) / c1
    t0_amp = (h0 - l0) / c1
    checks = {
        "J深度超卖": j0 < CHANGAN_J_DEEP,
        "T1放量长阳": t1_gain >= CHANGAN_T1_GAIN_MIN and v1 > v2,
        "T1的J拐头": j1 < CHANGAN_J_RECOVERED,
        "T日窄幅": abs(t0_gain) < CHANGAN_QUIET,
        "T日振幅小": t0_amp < CHANGAN_AMP_MAX,
        "T日缩半量": v0 < v1 * CHANGAN_SHRINK_TO,
    }
    return {
        "insufficient": False,
        "checks": checks,
        "hit": all(checks.values()),
        "t1_gain_pct": round(t1_gain * 100, 2),
        "t0_gain_pct": round(t0_gain * 100, 2),
        "t0_amp_pct": round(t0_amp * 100, 2),
        "vol_ratio": round(v0 / v1, 2) if v1 else None,
        "j_t": round2(j0),
        "j_t1": round2(j1),
    }


def double_gun_metrics(rows: List[Dict]) -> Dict:
    """双枪放量：rows[7] 与 rows[0] 各为放量阳柱，中间 rows[1..3] 缩量且含阴线。

    ⚠️ SQL 里那个叫 `vol_5d` 的基准量实际取 `rn BETWEEN 6 AND 15`，是**十根**的
    均量。这里跟 SQL 走（rows[5:15]），因为 1.5 / 0.8 这两个阈值是配着这个分母
    标定的；换成 5 根均量会把量比整体抬高、双枪变得太容易成立。
    """
    if len(rows) < DOUBLE_GUN_BARS:
        return {"insufficient": True, "required_bars": DOUBLE_GUN_BARS,
                "actual_bars": len(rows)}
    base = rows[DOUBLE_GUN_BASE_SLICE]
    mid = rows[DOUBLE_GUN_MID_SLICE]
    gun1, gun2 = rows[7], rows[0]

    def _ohcv(r):
        return (num(r.get("open")), num(r.get("high")),
                num(r.get("low")), num(r.get("close")), _vol_of(r))

    pool = [_ohcv(r) for r in (gun1, gun2) + tuple(base) + tuple(mid)]
    if any(None in p or p[3] <= 0 or p[0] <= 0 or p[4] <= 0 for p in pool):
        return {"insufficient": True, "missing": "双枪所需 OHLCV"}
    base_vol = sum(p[4] for p in pool[2:12]) / 10.0      # rows[5:15]
    mid_vol = sum(pool[12 + i][4] for i in range(3)) / 3.0
    if base_vol <= 0:
        return {"insufficient": True, "missing": "基准均量为零"}
    g1_o, _, _, g1_c, g1_v = pool[0]
    g2_o, _, _, g2_c, g2_v = pool[1]
    g1_body = (g1_c - g1_o) / g1_o
    g2_body = (g2_c - g2_o) / g2_o
    mid_has_bear = any(p[3] < p[0] for p in pool[12:15])
    checks = {
        "两枪皆阳": g1_c > g1_o and g2_c > g2_o,
        "两枪量比达标": (g1_v / base_vol >= DOUBLE_GUN_VOL_RATIO
                     and g2_v / base_vol >= DOUBLE_GUN_VOL_RATIO),
        "两枪实体达标": (g1_body >= DOUBLE_GUN_BODY_PCT
                     and g2_body >= DOUBLE_GUN_BODY_PCT),
        "中段缩量": mid_vol / base_vol < DOUBLE_GUN_MID_SHRINK,
        "中段含阴线": mid_has_bear,
    }
    return {
        "insufficient": False,
        "checks": checks,
        "hit": all(checks.values()),
        "g1_body_pct": round(g1_body * 100, 2),
        "g2_body_pct": round(g2_body * 100, 2),
        "g1_vol_ratio": round(g1_v / base_vol, 2),
        "g2_vol_ratio": round(g2_v / base_vol, 2),
        "mid_vol_ratio": round(mid_vol / base_vol, 2),
    }


def analyze_wyckoff_phase(rows: List[Dict]) -> Dict:
    """威科夫阶段判断。关键输入缺失时如实标记 unknown。"""
    n = min_int(40, len(rows))
    recent = rows[:n]

    price_range = _calc_price_range(recent)
    vol_trend = _calc_vol_trend(recent)
    cmf_avg = _mean_of(recent, "cmf", min_int(20, n), 0)
    mfi_avg = _mean_of(recent, "mfi", min_int(20, n), 0)
    obv_trend = None
    if len(recent) >= 11:
        now, ago = num(recent[0].get("obv")), num(recent[10].get("obv"))
        obv_trend = None if None in (now, ago) else now - ago

    missing = [
        name for name, value in (
            ("成交量趋势", vol_trend), ("CMF均值", cmf_avg),
            ("MFI均值", mfi_avg), ("OBV趋势", obv_trend),
        ) if value is None
    ]
    if missing:
        return {
            "phase": "unknown",
            "confidence": 0.0,
            "evidence": [f"关键量能指标缺失（{'、'.join(missing)}），无法判断威科夫阶段"],
        }

    is_in_range = price_range < 0.08
    is_vol_declining = vol_trend < -0.1
    higher_highs = _is_making_higher_highs(recent)
    lower_lows = _is_making_lower_lows(recent)

    if is_in_range and is_vol_declining and mfi_avg < 40 and -0.05 < cmf_avg < 0.1:
        evidence = [
            f"价格在区间内波动（幅度 {price_range * 100:.1f}%），成交量萎缩",
            f"MFI 均值 {mfi_avg:.1f} < 40，资金流出压力减轻",
        ]
        if cmf_avg > 0:
            evidence.append(f"CMF 均值 {cmf_avg:.3f} > 0，轻微资金流入")
        return {"phase": "accumulation", "confidence": 0.7, "evidence": evidence}

    if is_in_range and is_vol_declining and mfi_avg > 60 and -0.1 < cmf_avg < 0.05:
        evidence = [
            f"价格在区间内波动（幅度 {price_range * 100:.1f}%），成交量萎缩",
            f"MFI 均值 {mfi_avg:.1f} > 60，资金流入动力衰减",
        ]
        if cmf_avg < 0:
            evidence.append(f"CMF 均值 {cmf_avg:.3f} < 0，轻微资金流出")
        return {"phase": "distribution", "confidence": 0.7, "evidence": evidence}

    if higher_highs and obv_trend > 0 and cmf_avg > 0 and mfi_avg > 50:
        return {
            "phase": "markup", "confidence": 0.75,
            "evidence": [
                "价格创出近期新高，上涨趋势确立",
                "OBV 上升趋势，量价配合良好",
                f"CMF 均值 {cmf_avg:.3f} > 0，资金持续流入",
                f"MFI 均值 {mfi_avg:.1f} > 50，多方占优",
            ],
        }

    if lower_lows and obv_trend < 0 and cmf_avg < 0 and mfi_avg < 50:
        return {
            "phase": "markdown", "confidence": 0.75,
            "evidence": [
                "价格创出近期新低，下跌趋势确立",
                "OBV 下降趋势，量价配合向下",
                f"CMF 均值 {cmf_avg:.3f} < 0，资金持续流出",
                f"MFI 均值 {mfi_avg:.1f} < 50，空方占优",
            ],
        }

    return {
        "phase": "neutral", "confidence": 0.5,
        "evidence": ["未检测到明确的威科夫阶段，市场处于过渡期"],
    }


def _is_making_higher_highs(rows: List[Dict]) -> bool:
    if len(rows) < 10:
        return False
    recent = max(nz(r.get("high")) for r in rows[:5])
    old = max(nz(r.get("high")) for r in rows[5:10])
    return recent > old


def _is_making_lower_lows(rows: List[Dict]) -> bool:
    if len(rows) < 10:
        return False
    recent = min(nz(r.get("low")) for r in rows[:5])
    old = min(nz(r.get("low")) for r in rows[5:10])
    return recent < old


def _calc_vol_avg(rows: List[Dict], window: int, start_idx: int) -> Optional[float]:
    """指定窗口的均量，窗口不完整或缺失则返回 None。"""
    if start_idx + window > len(rows):
        return None
    values = [num(rows[i].get("vol")) for i in range(start_idx, start_idx + window)]
    if any(v is None for v in values):
        return None
    return sum(values) / len(values)


def analyze_volume_price(rows: List[Dict]) -> List[Dict]:
    """分析最近 5-10 天的量价关系。"""
    results = []
    n = min_int(10, len(rows) - 1)

    for i in range(n):
        curr, prev = rows[i], rows[i + 1]
        c_close, p_close = num(curr.get("close")), num(prev.get("close"))
        c_vol, p_vol = num(curr.get("vol")), num(prev.get("vol"))
        if None in (c_close, p_close, c_vol, p_vol) or p_close == 0:
            continue

        price_change = (c_close - p_close) / p_close
        vol_change = (c_vol - p_vol) / p_vol if p_vol > 0 else 0.0

        vol_window = _calc_vol_avg(rows, min_int(10, len(rows)), i + 1)
        vol_ratio = c_vol / vol_window if vol_window else None

        vp_type = ""
        desc = ""

        if price_change > 0.01 and vol_change > 0.1:
            vp_type = "量价齐升"
            desc = f"价格上涨 {price_change * 100:.2f}%，成交量放大 {vol_change * 100:.1f}%，多方力量强劲"
        elif price_change > 0.01 and vol_change < -0.1:
            vp_type = "量价背离（顶背离）"
            desc = f"价格上涨 {price_change * 100:.2f}%，但成交量萎缩 {-vol_change * 100:.1f}%，上涨动能不足"
        elif price_change < -0.01 and vol_change > 0.1:
            vp_type = "放量下跌"
            desc = f"价格下跌 {-price_change * 100:.2f}%，成交量放大 {vol_change * 100:.1f}%，抛压沉重"
        elif -0.03 < price_change < -0.005 and vol_change < -0.3:
            vp_type = "缩量回调"
            desc = f"价格小幅下跌 {price_change * 100:.2f}%，成交量显著萎缩 {-vol_change * 100:.1f}%，健康调整"
        elif price_change > 0.03 and vol_ratio is not None and vol_ratio > 1.5:
            vp_type = "放量突破"
            desc = f"价格大涨 {price_change * 100:.2f}%，成交量是 {vol_ratio - 1:.0f} 日均量的 {vol_ratio:.2f} 倍，突破信号"

        if vp_type:
            results.append({"type": vp_type, "date": curr["date"], "desc": desc})

    return results


def analyze_spring_upthrust(rows: List[Dict]) -> List[Dict]:
    """检测 Spring（向下假突破）和 Upthrust（向上假突破）。"""
    results = []
    n = min_int(20, len(rows) - 1)

    for i in range(n):
        curr = rows[i]
        c_low, c_high, c_close, c_vol = (
            num(curr.get("low")), num(curr.get("high")),
            num(curr.get("close")), num(curr.get("vol")),
        )
        if None in (c_low, c_high, c_close, c_vol):
            continue

        window = min_int(10, len(rows) - i - 1)
        if window == 0:
            continue
        prior = rows[i + 1:i + 1 + window]
        prior_lows = [num(r.get("low")) for r in prior]
        prior_highs = [num(r.get("high")) for r in prior]
        if any(v is None for v in prior_lows) or any(v is None for v in prior_highs):
            continue
        lowest_low, highest_high = min(prior_lows), max(prior_highs)

        vol_avg = _calc_vol_avg(rows, window, i + 1)
        if not vol_avg:
            continue
        vol_ratio = c_vol / vol_avg

        if c_low < lowest_low and c_close > lowest_low and vol_ratio > 1.3:
            results.append({
                "type": "spring", "date": curr["date"], "price": c_close,
                "support": lowest_low, "volume_ratio": round2(vol_ratio),
                "desc": (
                    f"向下假突破支撑位 {lowest_low:.2f}，收盘价 {c_close:.2f} 收回，"
                    f"成交量放大 {vol_ratio:.2f} 倍，Spring 信号"
                ),
            })

        if c_high > highest_high and c_close < highest_high and vol_ratio > 1.3:
            results.append({
                "type": "upthrust", "date": curr["date"], "price": c_close,
                "resistance": highest_high, "volume_ratio": round2(vol_ratio),
                "desc": (
                    f"向上假突破阻力位 {highest_high:.2f}，收盘价 {c_close:.2f} 回落，"
                    f"成交量放大 {vol_ratio:.2f} 倍，Upthrust 信号"
                ),
            })

    return results


def analyze_obv(rows: List[Dict]) -> Dict:
    """OBV 趋势和背离分析。"""
    if len(rows) < 11:
        return {"trend": "unknown", "divergence": "none"}

    curr_obv, obv_10_ago = num(rows[0].get("obv")), num(rows[10].get("obv"))
    if curr_obv is None or obv_10_ago is None:
        return {"trend": "unknown", "divergence": "none"}

    obv_change = curr_obv - obv_10_ago
    trend = "rising" if obv_change >= 0 else "falling"

    divergence = "none"
    if len(rows) >= 21:
        price_now = num(rows[0].get("close"))
        price_10_ago = num(rows[10].get("close"))
        price_20_ago = num(rows[20].get("close"))
        if None not in (price_now, price_10_ago, price_20_ago):
            if price_now > price_10_ago > price_20_ago and obv_change < 0:
                divergence = "bearish"
            elif price_now < price_10_ago < price_20_ago and obv_change > 0:
                divergence = "bullish"

    return {"trend": trend, "divergence": divergence}


def analyze_money_flow(rows: List[Dict]) -> Dict:
    """资金流向分析（CMF + MFI）。"""
    cmf, mfi = num(rows[0].get("cmf")), num(rows[0].get("mfi"))

    if cmf is None:
        verdict = "unknown"
    elif cmf > 0.1:
        verdict = "inflow"
    elif cmf < -0.1:
        verdict = "outflow"
    else:
        verdict = "neutral"

    if mfi is None:
        status = "数据不足"
    elif mfi < 20:
        status = "超卖"
    elif mfi > 80:
        status = "超买"
    elif mfi > 60:
        status = "多方占优"
    elif mfi < 40:
        status = "空方占优"
    else:
        status = "均衡"

    return {
        "cmf": None if cmf is None else round2(cmf),
        "mfi": None if mfi is None else round2(mfi),
        "verdict": verdict,
        "status": status,
    }


def analyze_vwap(rows: List[Dict]) -> Dict:
    """价格相对 VWAP 位置。"""
    price, vwap = num(rows[0].get("close")), num(rows[0].get("vwap"))
    if price is None or vwap is None or vwap == 0:
        return {
            "price": price, "vwap": None if vwap is None else vwap,
            "distance_pct": None, "position": "unknown",
        }

    distance_pct = (price - vwap) / vwap
    return {
        "price": price,
        "vwap": round2(vwap),
        "distance_pct": round2(distance_pct * 100),
        "position": "above" if price >= vwap else "below",
    }


def _generate_summary(result: Dict) -> Dict:
    """综合判断。"""
    key_findings: List[str] = []
    verdict = "观望"

    phase = result["wyckoff_phase"]["phase"]
    if phase == "accumulation":
        verdict = "关注吸筹信号，等待放量突破"
        key_findings.append("威科夫吸筹阶段，主力可能在底部建仓")
    elif phase == "markup":
        verdict = "上涨趋势，持有或逢低买入"
        key_findings.append("威科夫上涨阶段，趋势向上")
    elif phase == "distribution":
        verdict = "警惕派发信号，考虑减仓"
        key_findings.append("威科夫派发阶段，主力可能在顶部出货")
    elif phase == "markdown":
        verdict = "下跌趋势，回避或做空"
        key_findings.append("威科夫下跌阶段，趋势向下")
    elif phase == "unknown":
        verdict = "数据不足，无法给出量价结论"
        key_findings.extend(result["wyckoff_phase"]["evidence"])

    mf_verdict = result["money_flow"]["verdict"]
    if mf_verdict == "inflow":
        key_findings.append("资金持续流入，多方力量强劲")
    elif mf_verdict == "outflow":
        key_findings.append("资金持续流出，空方力量强劲")

    divergence = result["obv"]["divergence"]
    if divergence == "bearish":
        key_findings.append("OBV 与价格顶背离，警惕回调风险")
    elif divergence == "bullish":
        key_findings.append("OBV 与价格底背离，可能存在反弹机会")

    position = result["vwap"]["position"]
    if position == "above":
        key_findings.append("价格在 VWAP 上方，短期偏多")
    elif position == "below":
        key_findings.append("价格在 VWAP 下方，短期偏空")

    for signal in result.get("spring_upthrust", [])[:2]:
        if signal["type"] == "spring":
            key_findings.append("检测到 Spring 信号，可能是底部反转")
        elif signal["type"] == "upthrust":
            key_findings.append("检测到 Upthrust 信号，可能是顶部反转")

    return {"verdict": verdict, "key_findings": key_findings}


def analyze_volume(rows: List[Dict]) -> Dict:
    """量价分析入口。"""
    if len(rows) < MIN_BARS:
        raise ValueError(f"数据不足：至少需要 {MIN_BARS} 天数据")

    result = {
        "wyckoff_phase": analyze_wyckoff_phase(rows),
        "volume_price": analyze_volume_price(rows),
        "spring_upthrust": analyze_spring_upthrust(rows),
        "obv": analyze_obv(rows),
        "money_flow": analyze_money_flow(rows),
        "vwap": analyze_vwap(rows),
    }
    result["summary"] = _generate_summary(result)
    return result
