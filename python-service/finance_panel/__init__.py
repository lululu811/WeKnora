"""自选股「量价异动面板」后端。

两块能力：
* **量价异动**（`pulse` / `service`）—— 纯 Python 规则算倍数与四象限，
  DuckDB 只读取行情；
* **财经日历**（`calendar` / `store`）—— 东财抓取落 SQLite，读接口只读本地，
  抓取失败不拖垮读接口。

不变式：所有数字在 Python 侧算完，不引入 LLM 做算术；**缺失 ≠ 0**。
"""

from .pulse import (
    DEFAULT_HIGH,
    DEFAULT_LOW,
    DEFAULT_WINDOW,
    VOLUME_DOWN,
    VOLUME_NORMAL,
    VOLUME_UP,
    VERDICT_DISTRIBUTE,
    VERDICT_PUSH,
    classify_verdict,
    classify_volume,
    compute_pulse,
    is_missing,
)

__all__ = [
    "DEFAULT_HIGH",
    "DEFAULT_LOW",
    "DEFAULT_WINDOW",
    "VOLUME_DOWN",
    "VOLUME_NORMAL",
    "VOLUME_UP",
    "VERDICT_DISTRIBUTE",
    "VERDICT_PUSH",
    "classify_verdict",
    "classify_volume",
    "compute_pulse",
    "is_missing",
]
