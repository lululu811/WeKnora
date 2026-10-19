"""
内存多周期聚合与战法大势评估器 (In-Memory Multi-Timeframe Resampler)

纯内存无数据库依赖：直接将日线 OHLCV 序列在内存中快速聚合为周线 (Week)、月线 (Month)、季线 (Quarter)。
并在聚合出的月线与周线序列上计算重剑无锋大势战法指标：
1. 月线量价总闸门 (Monthly Volume-Price Gate):
   - 铁律：月线量价必占其一（收盘站上5月线 OR 月量站上5月均量线），两者皆失强制空仓。
2. 60月均线 (MA60M):
   - 全市场筹码平均价、立杆阳回调极限位、月线二买、牛熊分界线。
3. 周线双上五 (Weekly Dual Above 5):
   - 资金方向铁律：量上5周均量 + 价上5周均线。
"""

from typing import Any, Dict, List, Optional
from datetime import datetime


def _parse_date(date_val: Any) -> Optional[datetime]:
    if not date_val:
        return None
    s = str(date_val).strip()
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        return None


def resample_bars(bars: List[Dict[str, Any]], freq: str = "week") -> List[Dict[str, Any]]:
    """
    将日线 OHLCV 列表聚合成指定周期。
    输入 bars: 支持任意顺序（函数内部按日期升序排序后聚合，输出按时间升序）。
    freq: 'week' | 'month' | 'quarter'
    """
    if not bars:
        return []

    # 规范化并过滤无效日期
    valid_bars = []
    for b in bars:
        dt = _parse_date(b.get("date"))
        if dt:
            valid_bars.append((dt, b))

    if not valid_bars:
        return []

    # 按时间升序排序
    valid_bars.sort(key=lambda x: x[0])

    # 分组聚合
    groups: Dict[str, List[Dict[str, Any]]] = {}
    group_last_dt: Dict[str, datetime] = {}

    for dt, bar in valid_bars:
        if freq == "week":
            # 以 ISO 年和周号作为 key: 2026-W18
            iso_year, iso_week, _ = dt.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
        elif freq == "month":
            key = f"{dt.year}-{dt.month:02d}"
        elif freq == "quarter":
            quarter = (dt.month - 1) // 3 + 1
            key = f"{dt.year}-Q{quarter}"
        else:
            raise ValueError(f"不支持的聚合周期: {freq}")

        if key not in groups:
            groups[key] = []
        groups[key].append(bar)
        group_last_dt[key] = dt

    aggregated: List[Dict[str, Any]] = []
    for key, bar_list in groups.items():
        if not bar_list:
            continue
        first_bar = bar_list[0]
        last_bar = bar_list[-1]

        o = float(first_bar.get("open", 0))
        c = float(last_bar.get("close", 0))
        h = max(float(b.get("high", 0)) for b in bar_list)
        l = min(float(b.get("low", 0)) for b in bar_list)

        vols = [float(b.get("volume")) if b.get("volume") is not None else float(b.get("vol", 0)) for b in bar_list]
        total_vol = sum(vols)

        turnovers = [float(b.get("turnover", 0)) for b in bar_list if b.get("turnover") is not None]
        total_turnover = sum(turnovers)

        last_date_str = str(last_bar.get("date"))[:10]

        aggregated.append({
            "period_key": key,
            "date": last_date_str,
            "open": round(o, 3),
            "high": round(h, 3),
            "low": round(l, 3),
            "close": round(c, 3),
            "volume": round(total_vol, 2),
            "turnover": round(total_turnover, 2),
            "bar_count": len(bar_list),
        })

    return aggregated


def _calc_sma(values: List[float], period: int) -> List[Optional[float]]:
    res: List[Optional[float]] = []
    for i in range(len(values)):
        if i < period - 1:
            res.append(None)
        else:
            w = values[i - period + 1 : i + 1]
            res.append(sum(w) / period)
    return res


def evaluate_monthly_gate(bars_daily: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    内存评估月线量价总闸门与 60 月均线：
    1. 月线量价必占其一 (MA5M / VOL_MA5M)
    2. 60 月均线相对位置 (MA60M)
    """
    m_bars = resample_bars(bars_daily, freq="month")
    if len(m_bars) < 5:
        return {
            "status": "unknown",
            "desc": f"月K线数据不足5根 (当前 {len(m_bars)} 根)，无法评估月线总闸门",
            "gate_open": True,  # 宽容放行
            "max_allowed_position": 0.5,
        }

    closes = [b["close"] for b in m_bars]
    vols = [b["volume"] for b in m_bars]

    ma5_series = _calc_sma(closes, 5)
    vol_ma5_series = _calc_sma(vols, 5)
    ma60_series = _calc_sma(closes, 60)

    curr_close = closes[-1]
    curr_vol = vols[-1]
    ma5_val = ma5_series[-1]
    vol_ma5_val = vol_ma5_series[-1]
    ma60_val = ma60_series[-1]

    price_up = curr_close >= ma5_val if ma5_val else False
    vol_up = curr_vol >= vol_ma5_val if vol_ma5_val else False

    # 月线量价必占其一判断
    gate_open = price_up or vol_up
    both_hold = price_up and vol_up
    both_lost = (not price_up) and (not vol_up)

    if both_hold:
        gate_status = "DOUBLE_PASS"
        gate_desc = "月线量价双占（收盘在5月线上，成交量大于5月均量），多头旺盛，总闸门完全放行"
        max_pos = 1.0
    elif price_up and (not vol_up):
        gate_status = "PRICE_ONLY"
        gate_desc = "月线站上5月均线但量未过5月均量，量价占其一，允许中仓，不可激进追高"
        max_pos = 0.6
    elif (not price_up) and vol_up:
        gate_status = "VOL_ONLY"
        gate_desc = "月线放量承接但收在5月线下方，量比价重要，允许试仓，等待价格收复"
        max_pos = 0.4
    else:
        gate_status = "DOUBLE_LOST"
        gate_desc = "月线量价双失（跌破5月均线且缩量），触发系统硬红线！总闸门关闭，只准空仓或极轻仓防守"
        max_pos = 0.1

    # 60月均线相对位置
    ma60_info = None
    if ma60_val is not None:
        bias_60m = ((curr_close - ma60_val) / ma60_val) * 100.0
        ma60_info = {
            "ma60m": round(ma60_val, 3),
            "bias_pct": round(bias_60m, 2),
            "above_ma60m": curr_close >= ma60_val,
            "desc": "处于60月均线牛熊线上方" if curr_close >= ma60_val else "跌破60月均线（大级别下跌/熊市区域）",
        }

    return {
        "latest_month": m_bars[-1]["date"][:7],
        "close": curr_close,
        "volume": curr_vol,
        "ma5m": round(ma5_val, 3) if ma5_val else None,
        "vol_ma5m": round(vol_ma5_val, 2) if vol_ma5_val else None,
        "price_above_ma5m": price_up,
        "vol_above_ma5m": vol_up,
        "gate_open": gate_open,
        "gate_status": gate_status,
        "max_allowed_position": max_pos,
        "desc": gate_desc,
        "ma60m_context": ma60_info,
    }


def evaluate_weekly_dual_up5(bars_daily: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    内存评估周线双上五：
    周量上 5 周均量 + 周价上 5 周均线
    """
    w_bars = resample_bars(bars_daily, freq="week")
    if len(w_bars) < 5:
        return {
            "status": "unknown",
            "desc": f"周K线不足5根 (当前 {len(w_bars)} 根)",
            "is_weekly_dual_up5": False,
        }

    closes = [b["close"] for b in w_bars]
    vols = [b["volume"] for b in w_bars]

    ma5w = _calc_sma(closes, 5)[-1]
    vol_ma5w = _calc_sma(vols, 5)[-1]

    curr_c = closes[-1]
    curr_v = vols[-1]

    price_up = curr_c >= ma5w if ma5w else False
    vol_up = curr_v >= vol_ma5w if vol_ma5w else False
    is_dual_up = price_up and vol_up

    return {
        "latest_week": w_bars[-1]["date"],
        "close": curr_c,
        "ma5w": round(ma5w, 3) if ma5w else None,
        "volume": curr_v,
        "vol_ma5w": round(vol_ma5w, 2) if vol_ma5w else None,
        "price_up": price_up,
        "vol_up": vol_up,
        "is_weekly_dual_up5": is_dual_up,
        "desc": "周线双上五确立（周线资金方向偏多）" if is_dual_up else "未满足周线双上五",
    }
