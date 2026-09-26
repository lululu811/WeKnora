"""
量价分析 — 威科夫方法 + 量价关系 + 假突破检测

对应 Go: internal/agent/tools/hithink_finance/analysis/volume.go

行序：rows[0] 是最新一根 K 线。
缺失的指标（None）不参与计算，也不该被当成 0。
"""

from typing import Dict, List, Optional

from .utils import min_int, num, nz, round2

MIN_BARS = 10


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
