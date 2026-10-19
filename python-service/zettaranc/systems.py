"""
重剑无锋三大交易系统状态机与抄底五条件评估器 (Trading Systems & Bottom Fishing Engine)

编制依据：《重剑无锋交易框架技术手册》第四部分与第五部分
核心体系：
1. 三大交易系统 (Three Trading Systems)：
   - 反弹交易系统 (REBOUND)：道氏一买，左侧减亏博超跌，轻仓(1-2成)，3-5天，三天白玩离场；
   - 转折交易系统 (REVERSAL)：道氏二买，底量超顶量/量切线突破/518第一次与第二次金叉/立杆阳，波段中仓(3-5成)；
   - 趋势交易系统 (TREND)：道氏三买，站稳60月线/周线双上五/518第三次金叉跃迁，主升重仓(5-8成)，锁仓吃足；
   - 防御空仓系统 (DEFENSIVE_HOLD_CASH)：量价双失或破位阴跌，坚决空仓防守。

2. 抄底五条件评估器 (Bottom Fishing 5 Conditions)：
   - 严禁盲目接飞刀！在大级别死叉下跌市中，必须同时或充分满足五大条件方可试仓：
     1. drop_exhaustion: 空头释放充分 (跌幅>=12% 或 调整>=10 bar)；
     2. ma_divergence: 518 死叉开口过大 (Spread <= -2.5%，负乖离极限)；
     3. wave_five_done: 下跌五段结构走完 (波段结构完整)；
     4. panic_volume_bar: 恐慌放量阴线杀出 (主力承接盘入场)；
     5. sentiment_freezing: 情绪冰点 (TD 序列低九或极限地量)。
"""

from typing import Any, Dict, List, Optional
from .ma518 import analyze_ma518
from .micro_volume import analyze_micro_volume
from .td_setup import analyze_td_setup
from .resampler import evaluate_monthly_gate, evaluate_weekly_dual_up5


def evaluate_bottom_fishing_conditions(
    bars: List[Dict[str, Any]],
    ma518_result: Optional[Dict[str, Any]] = None,
    td_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    抄底五条件严格评估器：
    输入：日线 OHLCV 序列（正序，bars[0] 最老，bars[-1] 最新）。
    """
    if len(bars) < 15:
        return {
            "is_ready": False,
            "satisfied_count": 0,
            "conditions": {},
            "desc": "数据不足 15 根 K 线，无法评估抄底五条件",
        }

    closes = [float(b.get("close", 0)) for b in bars]
    highs = [float(b.get("high", 0)) for b in bars]
    lows = [float(b.get("low", 0)) for b in bars]
    opens = [float(b.get("open", 0)) for b in bars]
    vols = [float(b.get("volume", 0)) for b in bars]

    if ma518_result is None:
        ma518_result = analyze_ma518(bars)

    if td_result is None:
        td_result = analyze_td_setup(bars)

    conditions = {}

    # 1. 空头释放充分 (drop_exhaustion)
    # 计算近 20 根 K 线的最高点到当前的跌幅
    lookback = min(len(bars), 20)
    recent_high = max(highs[-lookback:])
    curr_close = closes[-1]
    drop_pct = ((recent_high - curr_close) / recent_high) * 100.0 if recent_high > 0 else 0.0

    # 跌幅 >= 12% 或者 处于持续死叉阴跌 >= 10 根 bar
    c1_satisfied = drop_pct >= 10.0 or (ma518_result.get("cross_state") == "DEAD_CROSS" and len(bars) >= 15)
    conditions["drop_exhaustion"] = {
        "name": "空头充分释放",
        "satisfied": c1_satisfied,
        "drop_pct": round(drop_pct, 2),
        "desc": f"距近期高点回撤 {round(drop_pct, 2)}%，空头做空动能已显著消耗"
        if c1_satisfied
        else f"跌幅仅 {round(drop_pct, 2)}%，空头宣泄尚不充分",
    }

    # 2. 518 死叉开口大 (ma_divergence)
    # Spread <= -2.5% (负乖离率严重超标)
    spread = float(ma518_result.get("spread_pct", 0.0))
    is_dead = ma518_result.get("cross_state") == "DEAD_CROSS"
    c2_satisfied = is_dead and (spread <= -2.5)
    conditions["ma_divergence"] = {
        "name": "518死叉开口过大",
        "satisfied": c2_satisfied,
        "spread_pct": round(spread, 2),
        "desc": f"518死叉负开口达 {round(spread, 2)}%，负乖离强烈，具备均线牵引回归动能"
        if c2_satisfied
        else f"当前开口为 {round(spread, 2)}%，未形成极端负向乖离",
    }

    # 3. 结构五段走完 (wave_five_done)
    # 简易波浪与结构判定：在下跌周期内，检测是否有至少两波下探低点破前低（形成五段 1-2-3-4-5 或 A-B-C）
    # 检测局部谷值
    valleys = []
    for i in range(len(lows) - lookback, len(lows) - 1):
        if i > 0 and lows[i] <= lows[i - 1] and lows[i] <= lows[i + 1]:
            valleys.append(i)

    # 如果有 2 个以上连续下移的谷值，代表至少经历了 3-5 段波浪下杀
    c3_satisfied = len(valleys) >= 2 or drop_pct >= 15.0
    conditions["wave_five_done"] = {
        "name": "下跌结构五段走完",
        "satisfied": c3_satisfied,
        "valleys_found": len(valleys),
        "desc": "下跌结构已现多波次探底，下跌浪形接近尾声"
        if c3_satisfied
        else "下跌浪形尚未完成充分的三段或五段调整",
    }

    # 4. 左侧恐慌放量阴线杀出 (panic_volume_bar)
    # 近 5 天内是否有明显的放量阴线（成交量 > 1.3 倍 5日均量，实体阴线或长下影）
    # 表明散户踩踏割肉、主力被动在底部承接
    c4_satisfied = False
    panic_date = None
    for i in range(max(0, len(bars) - 5), len(bars)):
        v = vols[i]
        # 计算该点的前 5 日均量
        start_idx = max(0, i - 4)
        avg_v = sum(vols[start_idx : i + 1]) / (i - start_idx + 1)
        is_yin = closes[i] < opens[i]
        has_volume = v >= avg_v * 1.25
        if is_yin and has_volume:
            c4_satisfied = True
            panic_date = str(bars[i].get("date", ""))
            break

    conditions["panic_volume_bar"] = {
        "name": "左侧恐慌放量阴线",
        "satisfied": c4_satisfied,
        "panic_bar_date": panic_date,
        "desc": f"在 {panic_date} 出现恐慌爆量阴线，主力已在低位进行筹码承接"
        if c4_satisfied
        else "近期无明显恐慌放量宣泄，警惕缩量阴跌无底洞",
    }

    # 5. 情绪冰点 (sentiment_freezing)
    # 神奇九转出现低九 (count == 9) 或者 近 3 日成交量极度地量 (低于 20 日均量的 60%)
    td_count = td_result.get("td_count", 0)
    td_type = td_result.get("td_type")
    is_d9 = td_type == "BUY_SETUP" and td_count == 9

    # 地量检查
    avg_v20 = sum(vols[-20:]) / 20.0 if len(vols) >= 20 else sum(vols) / len(vols)
    is_extreme_shrink = vols[-1] <= avg_v20 * 0.65

    c5_satisfied = is_d9 or is_extreme_shrink or (td_type == "BUY_SETUP" and td_count >= 7)
    conditions["sentiment_freezing"] = {
        "name": "情绪冰点衰竭",
        "satisfied": c5_satisfied,
        "is_td_d9": is_d9,
        "is_extreme_shrink": is_extreme_shrink,
        "td_count": td_count,
        "desc": "触发 TD 神奇九转低九或极限地量地价，做空情绪进入冰点衰竭区"
        if c5_satisfied
        else "情绪指标与成交量尚未达到极限冰点",
    }

    # 汇总满足数
    satisfied_count = sum(1 for c in conditions.values() if c["satisfied"])
    is_ready = satisfied_count >= 4

    if is_ready:
        advice = "抄底五条件满足 4 项及以上，具备左侧轻仓试探反弹条件！严格遵守单笔2%止损与三天白玩离场法则。"
    else:
        advice = f"抄底五条件仅满足 {satisfied_count}/5 项，严禁盲目左侧接飞刀！缩量阴跌永不抄底。"

    return {
        "is_ready": is_ready,
        "satisfied_count": satisfied_count,
        "total_conditions": 5,
        "conditions": conditions,
        "advice": advice,
    }


def classify_trading_system(
    bars: List[Dict[str, Any]],
    ma518_result: Optional[Dict[str, Any]] = None,
    micro_vol_result: Optional[Dict[str, Any]] = None,
    monthly_gate: Optional[Dict[str, Any]] = None,
    weekly_dual: Optional[Dict[str, Any]] = None,
    sun_moon_result: Optional[Dict[str, Any]] = None,
    macro_vol_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    三大交易系统状态机分类器：
    根据多周期量价、均线系统、筹码中枢判定当前标的归属于三大系统中的哪一个：
    1. TREND (趋势交易系统)
    2. REVERSAL (转折交易系统)
    3. REBOUND (反弹交易系统)
    4. DEFENSIVE_HOLD_CASH (防御空仓系统)
    """
    if len(bars) < 18:
        return {
            "system": "DEFENSIVE_HOLD_CASH",
            "name": "防御空仓系统",
            "recommended_position": 0.0,
            "desc": "数据不足，建议空仓观望",
        }

    if ma518_result is None:
        ma518_result = analyze_ma518(bars)

    if micro_vol_result is None:
        micro_vol_result = analyze_micro_volume(bars)

    if monthly_gate is None:
        monthly_gate = evaluate_monthly_gate(bars)

    if weekly_dual is None:
        weekly_dual = evaluate_weekly_dual_up5(bars)

    # 提取关键因子
    ma518_cross = ma518_result.get("cross_state")  # GOLDEN_CROSS / DEAD_CROSS
    golden_times = ma518_result.get("golden_cross_times", 0)
    dead_warning = ma518_result.get("dead_cross_warning", False)
    micro_state = micro_vol_result.get("state")  # BOTH_UP_5 / BOTH_DOWN_5 等
    gate_open = monthly_gate.get("gate_open", True)
    is_weekly_dual = weekly_dual.get("is_weekly_dual_up5", False)
    above_ma60m = monthly_gate.get("ma60m_context", {}).get("above_ma60m", True) if monthly_gate.get("ma60m_context") else True

    # 1. 趋势交易系统判定 (Trend System)
    # 条件：518 处于金叉态，金叉次数 >= 2（尤其是第3次跃迁），且周线双上五，价格在60月线上方
    if ma518_cross == "GOLDEN_CROSS" and is_weekly_dual and above_ma60m and gate_open:
        if golden_times >= 3:
            phase_desc = "第 3 次 518 金叉跃迁主升浪，多头极盛"
            rec_pos = 0.8
        else:
            phase_desc = "第 2 次 518 金叉右侧突破主攻，趋势确立"
            rec_pos = 0.6

        return {
            "system": "TREND",
            "name": "趋势交易系统",
            "recommended_position": rec_pos,
            "phase": phase_desc,
            "discipline": (
                "【趋势交易系统纪律】\n"
                "1. 顺势重仓持股（5-8成），锁仓吃满主升；\n"
                "2. 沿 5 日/18 日均线持有，出现伪阴线震仓不破 5 日线继续推进；\n"
                "3. 止盈离场信号：高位灌顶阴、TD 高九、月线三同步见顶、或 518 死叉破位。"
            ),
            "suitable_actions": ["HOLD_TREND", "PYRAMID_ADD"],
        }

    # 2. 转折交易系统判定 (Reversal System)
    # 条件：月线量价闸门开启，日线 518 完成第 1 或第 2 次金叉，或者出现底量超顶量/量切线突破
    has_bottom_vol = False
    if macro_vol_result:
        has_bottom_vol = macro_vol_result.get("bottom_over_top", {}).get("is_bottom_over_top", False)

    is_seq_green_red = False
    if sun_moon_result:
        is_seq_green_red = sun_moon_result.get("sequence_law") == "GREEN_THEN_RED"

    if gate_open and (ma518_cross == "GOLDEN_CROSS" or is_seq_green_red or has_bottom_vol):
        return {
            "system": "REVERSAL",
            "name": "转折交易系统",
            "recommended_position": 0.4,
            "phase": "底部转折与二买起爆阶段",
            "discipline": (
                "【转折交易系统纪律】\n"
                "1. 适度中仓建仓（3-5成），博取波段起爆点；\n"
                "2. 锚定立杆阳或打桩阳实体下沿为硬止损；\n"
                "3. 回踩 18 日均线或均量线不破可分批加仓，等待向趋势交易系统升级。"
            ),
            "suitable_actions": ["BUY_REVERSAL", "HOLD_SWING"],
        }

    # 3. 反弹交易系统判定 (Rebound System)
    # 条件：处于 518 死叉或下跌通道中，但抄底五条件评估通过，或出现 TD 低九 / 阴量上五
    bf_eval = evaluate_bottom_fishing_conditions(bars, ma518_result=ma518_result)
    is_bf_ready = bf_eval.get("is_ready", False)
    is_yin_vol_up = micro_state == "YIN_VOL_UP_5"

    if (ma518_cross == "DEAD_CROSS" or not gate_open) and (is_bf_ready or is_yin_vol_up):
        return {
            "system": "REBOUND",
            "name": "反弹交易系统",
            "recommended_position": 0.15,
            "phase": "大级别超跌短线脉冲反弹",
            "discipline": (
                "【反弹交易系统纪律】\n"
                "1. 严禁重仓！仅限 1-2 成轻仓试探；\n"
                "2. 短平快战术，获利目标 5%-10%，反弹到 18 日线附近坚决止盈；\n"
                "3. 严格执行【三天白玩离场法则】：买入 3 天内未涨脱离成本区必须无条件市价离场；\n"
                "4. 跌破买入 K 线最低点立即止损，绝不恋战。"
            ),
            "suitable_actions": ["PROBE_REBOUND", "QUICK_EXIT"],
            "bottom_fishing_eval": bf_eval,
        }

    # 4. 防御空仓系统 (Defensive Cash System)
    # 双下五、死叉开口向下、月线量价双失且不满足反弹条件
    return {
        "system": "DEFENSIVE_HOLD_CASH",
        "name": "防御空仓系统",
        "recommended_position": 0.0,
        "phase": "空头阴跌或破位调整期",
        "discipline": (
            "【防御空仓系统纪律】\n"
            "1. 强制空仓观望（0仓位），现金为王；\n"
            "2. 缩量阴跌绝不抄底，耐受空头释放；\n"
            "3. 紧盯底量超顶量与抄底五条件，静待恐慌盘砸出与首根放量立杆阳。"
        ),
        "suitable_actions": ["HOLD_CASH", "WAIT_OPPORTUNITY"],
        "bottom_fishing_eval": bf_eval,
    }
