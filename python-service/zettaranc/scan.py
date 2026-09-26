"""
技术形态扫描器入口 — 加载指标 + 检测信号 + 输出结果

对应 Go: internal/agent/tools/hithink_finance/pattern/scan.go
"""

from typing import Dict

from .data_loader import fetch_indicators_only
from .signals import detect_signals, summarize_signals

MIN_DAYS = 10
MAX_DAYS = 60

# 判定"这只票的指标算完整了吗"所看的核心字段
CORE_FIELDS = ["rsi6", "macd_hist", "mfi", "adx", "bb_upper", "atr", "obv"]


async def scan_patterns(indicators_source, thscode: str, days: int = 10) -> Dict:
    """
    扫描股票的技术形态信号。

    Args:
        indicators_source: 已注册的 indicators 数据源
        thscode: 同花顺股票代码
        days: 扫描天数（10-60）

    Returns:
        扫描结果 dict。`data_complete=False` 表示核心指标缺失，
        此时 `signals` 里的内容不足以支撑交易判断。
    """
    if not thscode:
        raise ValueError("thscode 不能为空")
    days = max(MIN_DAYS, min(MAX_DAYS, days))

    rows = await fetch_indicators_only(indicators_source, thscode, days)
    if not rows:
        raise ValueError(f"未找到指标数据：{thscode}")

    signals = detect_signals(rows)
    summary = summarize_signals(signals)

    latest = rows[0]
    missing = [f for f in CORE_FIELDS if latest.get(f) is None]
    complete = not missing

    return {
        "thscode": thscode,
        "days": len(rows),
        "latest_date": latest.get("date"),
        "data_complete": complete,
        "missing_indicators": missing,
        "latest": latest,
        "signals": signals,
        "summary": summary,
    }
