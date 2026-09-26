"""
zettaranc — Z哥交易体系分析模块（Python 版）

从 Go 侧的 hithink_finance/analysis 和 pattern 模块翻译而来。
所有数据来自本地 DuckDB 数据库。
"""

from .data_loader import fetch_market_data, fetch_indicators_only
from .trend import analyze_trend
from .volume import analyze_volume
from .pattern import analyze_chart_pattern
from .levels import analyze_levels
from .signals import detect_signals, summarize_signals
from .scan import scan_patterns