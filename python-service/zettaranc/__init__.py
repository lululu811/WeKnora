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
from .four_bricks import analyze_four_bricks, four_bricks

# 重剑无锋交易框架 2026 体系模块
from .micro_volume import analyze_micro_volume
from .ma518 import analyze_ma518
from .resampler import resample_bars, evaluate_monthly_gate, evaluate_weekly_dual_up5
from .macro_volume import analyze_macro_volume
from .td_setup import analyze_td_setup
from .sun_moon import analyze_sun_moon
from .systems import classify_trading_system, evaluate_bottom_fishing_conditions
from .guardrail import evaluate_risk_guardrail
from .annotator import annotator
