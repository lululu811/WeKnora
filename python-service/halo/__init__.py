"""HALO 年报事实抽取链路。

数据从巨潮资讯网（法定披露平台）来，落到本地事实库，权威源是巨潮原文；
DuckDB 里的 hithink 数据是对账的尺子，不是裁判。
"""

from .store import (
    EXTRACT_LLM,
    EXTRACT_RULE,
    VERIFIED_BY_PIPELINE,
    VERIFIED_BY_RECONCILE,
    FactStore,
    REPORT_ANNUAL,
    REPORT_H1,
    REPORT_Q1,
    REPORT_Q3,
    SCOPE_CONSOLIDATED,
    SCOPE_PARENT,
    STATUS_DISPUTED,
    STATUS_PENDING,
    STATUS_VERIFIED,
)

__all__ = [
    "FactStore",
    "EXTRACT_LLM",
    "EXTRACT_RULE",
    "REPORT_ANNUAL",
    "REPORT_H1",
    "REPORT_Q1",
    "REPORT_Q3",
    "SCOPE_CONSOLIDATED",
    "SCOPE_PARENT",
    "STATUS_DISPUTED",
    "STATUS_PENDING",
    "STATUS_VERIFIED",
    "VERIFIED_BY_PIPELINE",
    "VERIFIED_BY_RECONCILE",
]
