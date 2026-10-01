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
# 四块砖：2026-10-01 从工作台前端搬到此处。此前只有 indicators.ts 算得出，
# 服务端取不到，所以 agent 对"四块砖什么状态"一律被要求回答"算不出来"。
from .four_bricks import analyze_four_bricks, four_bricks