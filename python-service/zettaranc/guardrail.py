"""
确定性风控总闸门引擎与 8 条负面禁做清单 (Risk Guardrail Engine & 8 Negative Rules)

编制依据：《重剑无锋交易框架技术手册》第五部分（仓位与硬风控铁律）
核心原则：
交易纪律代码化！LLM 负责逻辑解释，底层强类型风控引擎掌管生死总闸门。

8 大负面禁做清单 (The 8 Negative Rules)：
1. RULE_SHRINK_DROP_NO_BOTTOM: 缩量下跌不可抄 (无量阴跌不是底，必须先有放量恐慌盘宣泄)
2. RULE_MONTHLY_GATE_CLOSED: 月线量价双失锁死仓位 (月线破5月线且缩量，只准 0-10% 试仓，禁止大仓位)
3. RULE_NO_POSITION_INVERSION: 禁止仓位倒挂 (底部轻仓、高位追涨重仓，严格禁止倒挂加仓)
4. RULE_SINGLE_LOSS_LIMIT: 单笔硬止损 2% 原则 (单笔交易极限亏损不得冲击总资金超过 2%)
5. RULE_THREE_DAYS_TIMEOUT: 三天白玩离场法则 (短线买入 3 天内未涨脱离成本区必须无条件市价离场)
6. RULE_THREE_SYNC_EXHAUSTION: 三同步多头竭尽空仓警报 (两波价格新高+量堆放大+MACD柱递增触发90%空头宣泄)
7. RULE_CAPPING_YIN_EXIT: 灌顶阴断头铡紧急离场 (高位放巨量灌顶阴且破5日线，禁止做多)
8. RULE_518_DEAD_CROSS_NO_OVERNIGHT: 518 死叉开口下行严禁隔夜趋势仓
"""

from typing import Any, Dict, List, Optional
from .resampler import evaluate_monthly_gate
from .ma518 import analyze_ma518
from .sun_moon import analyze_sun_moon
from .systems import classify_trading_system


def evaluate_risk_guardrail(
    bars: List[Dict[str, Any]],
    monthly_gate: Optional[Dict[str, Any]] = None,
    ma518_result: Optional[Dict[str, Any]] = None,
    sun_moon_result: Optional[Dict[str, Any]] = None,
    system_result: Optional[Dict[str, Any]] = None,
    annotated_patterns: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    风控总闸门综合评估：
    输入日线 OHLCV 序列及各战法模块结果，输出风控总评与违规清单。
    """
    if len(bars) < 5:
        return {
            "allowed_to_buy": False,
            "risk_level": "CRITICAL",
            "violations": [{"code": "INSUFFICIENT_DATA", "name": "数据不足", "severity": "BLOCKER"}],
            "max_recommended_position": 0.0,
            "suggested_stop_loss_price": None,
            "summary_advice": "数据过少无法执行风控检验，禁止开仓",
        }

    closes = [float(b.get("close", 0)) for b in bars]
    opens = [float(b.get("open", 0)) for b in bars]
    highs = [float(b.get("high", 0)) for b in bars]
    lows = [float(b.get("low", 0)) for b in bars]
    vols = [float(b.get("volume", 0)) for b in bars]
    curr_close = closes[-1]

    if monthly_gate is None:
        monthly_gate = evaluate_monthly_gate(bars)

    if ma518_result is None:
        ma518_result = analyze_ma518(bars)

    if sun_moon_result is None:
        sun_moon_result = analyze_sun_moon(bars)

    if system_result is None:
        system_result = classify_trading_system(
            bars,
            ma518_result=ma518_result,
            monthly_gate=monthly_gate,
            sun_moon_result=sun_moon_result,
        )

    violations: List[Dict[str, Any]] = []

    # -------------------------------------------------------------
    # 规则 1: RULE_SHRINK_DROP_NO_BOTTOM (缩量下跌不可抄)
    # 若近 5 日处于连阴下跌，且成交量持续萎缩（无恐慌宣泄）
    # -------------------------------------------------------------
    if len(bars) >= 5:
        recent_closes = closes[-5:]
        recent_vols = vols[-5:]
        is_dropping = recent_closes[-1] < recent_closes[0] * 0.96
        # 均量下行
        is_shrinking = sum(recent_vols[-2:]) < sum(recent_vols[:2]) * 0.8
        # 且没有出现放量恐慌柱
        if is_dropping and is_shrinking and ma518_result.get("cross_state") == "DEAD_CROSS":
            violations.append({
                "code": "RULE_SHRINK_DROP_NO_BOTTOM",
                "name": "缩量下跌不可抄",
                "severity": "BLOCKER",
                "detail": "当前处于缩量阴跌无量阶段，属于流动性干涸无底洞，严禁左侧接飞刀！必须等待爆量恐慌宣泄。",
            })

    # -------------------------------------------------------------
    # 规则 2: RULE_MONTHLY_GATE_CLOSED (月线量价双失锁死仓位)
    # -------------------------------------------------------------
    if not monthly_gate.get("gate_open", True):
        violations.append({
            "code": "RULE_MONTHLY_GATE_CLOSED",
            "name": "月线量价双失总闸门关闭",
            "severity": "RESTRICTION",
            "detail": "月K线收盘在5月均线下方且月量低于5月均量，大周期总闸门关闭！严禁大仓位操作，最大允许仓位锁死在 10% 以内。",
        })

    # -------------------------------------------------------------
    # 规则 3: RULE_NO_POSITION_INVERSION (禁止仓位倒挂)
    # -------------------------------------------------------------
    # 检验价格是否严重偏离 18 日线 (> 15%)，此时若开仓或加仓属于严重高位倒挂
    ma18_val = ma518_result.get("ma18")
    if ma18_val and curr_close > ma18_val * 1.18:
        violations.append({
            "code": "RULE_NO_POSITION_INVERSION",
            "name": "禁止仓位倒挂（防高位重仓）",
            "severity": "WARNING",
            "detail": f"股价偏离 18 日均线高达 {round(((curr_close - ma18_val)/ma18_val)*100, 1)}%，严禁在高位激进追加重仓！",
        })

    # -------------------------------------------------------------
    # 规则 6: RULE_THREE_SYNC_EXHAUSTION (三同步多头竭尽空仓警报)
    # -------------------------------------------------------------
    if sun_moon_result.get("three_sync_warning", False):
        violations.append({
            "code": "RULE_THREE_SYNC_EXHAUSTION",
            "name": "日月神功三同步空仓铁律",
            "severity": "BLOCKER",
            "detail": "触发价格两波新高+量堆放大+MACD柱递增之三同步见顶特征，90% 概率空头大宣泄！禁止做多，执行避险清仓。",
        })

    # -------------------------------------------------------------
    # 规则 7: RULE_CAPPING_YIN_EXIT (灌顶阴断头铡紧急离场)
    # -------------------------------------------------------------
    if annotated_patterns:
        for p in annotated_patterns[-3:]:
            if p.get("pattern") == "capping_yin":
                violations.append({
                    "code": "RULE_CAPPING_YIN_EXIT",
                    "name": "高位灌顶阴断头铡",
                    "severity": "BLOCKER",
                    "detail": f"在 {p.get('date')} 出现高开低走放量灌顶阴，主力大单出货，80%看空，禁止开仓买入！",
                })
                break

    # -------------------------------------------------------------
    # 规则 8: RULE_518_DEAD_CROSS_NO_OVERNIGHT (518死叉开口下行严禁隔夜趋势仓)
    # -------------------------------------------------------------
    if ma518_result.get("cross_state") == "DEAD_CROSS" and ma518_result.get("dead_cross_warning", False):
        violations.append({
            "code": "RULE_518_DEAD_CROSS_NO_OVERNIGHT",
            "name": "518死叉下行期禁做隔夜趋势",
            "severity": "RESTRICTION",
            "detail": "518 死叉开口正在向下加速扩大，严禁建立隔夜趋势仓，仅允许日内极短反抽。",
        })

    # -------------------------------------------------------------
    # 计算阻断性与建议仓位
    # -------------------------------------------------------------
    has_blocker = any(v["severity"] == "BLOCKER" for v in violations)
    has_restriction = any(v["severity"] == "RESTRICTION" for v in violations)

    base_pos = system_result.get("recommended_position", 0.0)
    monthly_max_pos = monthly_gate.get("max_allowed_position", 1.0)
    final_pos = min(base_pos, monthly_max_pos)

    if has_blocker:
        allowed = False
        final_pos = 0.0
        risk_level = "CRITICAL"
    elif has_restriction:
        allowed = True
        final_pos = min(final_pos, 0.10)
        risk_level = "HIGH"
    elif violations:
        allowed = True
        final_pos = min(final_pos, 0.40)
        risk_level = "MEDIUM"
    else:
        allowed = True
        risk_level = "LOW"

    # 建议止损位：以最近 5 日最低价，或者 18 日均线，或者买入 K 线低点
    recent_min_low = min(lows[-5:])
    suggested_stop = round(recent_min_low * 0.985, 2)

    # 总结性操作建议
    if not allowed:
        summary_advice = "【风控阻断】触发系统硬红线（存在阻断性违规），总闸门关闭，坚决空仓防守，禁止买入！"
    elif risk_level == "HIGH":
        summary_advice = f"【高危受限】仅允许极轻仓试探（上限 {int(final_pos*100)}%），严格落实单笔 2% 止损与三天白玩离场法则。"
    elif risk_level == "MEDIUM":
        summary_advice = f"【中度风险】可按 {system_result.get('name')} 进行中仓布局（建议 {int(final_pos*100)}%），严密跟踪支撑位。"
    else:
        summary_advice = f"【安全顺势】环境健康，月线总闸门放行，可按 {system_result.get('name')} 稳健执行（建议仓位 {int(final_pos*100)}%）。"

    return {
        "allowed_to_buy": allowed,
        "risk_level": risk_level,
        "violations": violations,
        "max_recommended_position": round(final_pos, 2),
        "suggested_stop_loss_price": suggested_stop,
        "single_loss_max_pct": 2.0,  # 规则 4: 单笔硬止损 2% 原则
        "three_days_exit_rule": "短线买入 3 天内未涨脱离成本区必须市价离场 (三天白玩离场法则)",  # 规则 5
        "summary_advice": summary_advice,
        "system_type": system_result.get("system"),
        "monthly_gate_status": monthly_gate.get("gate_status"),
    }
