"""ETF 定期报告披露持有人抽取。

与 `finance_panel` 的**份额变动线**是两条独立数据线：
* 份额线 = 高频代理指标（谁在买），口径来自东财 F10 规模变动页（季频）；
* 持有人线 = 低频但权威的原始披露（谁持有），口径来自巨潮定期报告 PDF。

两条线共用 `etf_pool.yaml` 的追踪池。**所有数字由 Python 规则算完**，
LLM 不参与任何数值判断；**缺失 ≠ 0**；对不上的披露标 `disputed` 并保留原始值。
"""

from .extractor import (
    ANCHOR_PATTERNS,
    STOP_PATTERNS,
    extract_holders,
    find_anchor_span,
    parse_rows,
)
from .sync import extract_pdf_text, sync_one_etf, sync_pool

__all__ = [
    "ANCHOR_PATTERNS",
    "STOP_PATTERNS",
    "extract_holders",
    "extract_pdf_text",
    "find_anchor_span",
    "parse_rows",
    "sync_one_etf",
    "sync_pool",
]