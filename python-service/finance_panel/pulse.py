"""
量价异动引擎 — 纯函数，不依赖 FastAPI / DuckDB。

为什么是纯函数
--------------
所有数字都在这里算完，HTTP 层只负责取数与序列化。把规则放在纯函数里有两个
好处：一是可以在不连 DuckDB 的情况下把四象限和边界值穷举测试掉；二是以后
要换阈值口径（0.618 / 1.382）时，改动只落在一个文件里，不会散进 handler。

不变式
------
* **缺失 ≠ 0。** 窗口不足、中枢为 0、收盘价缺失，一律返回 `{missing: True,
  reason: ...}`，绝不返回 multiple=0 参与统计 —— 0 倍数在金融语义里是
  "当日零成交"，用它顶替"没数据"会读出一个假结论。
* **中枢不含当日。** 中枢 = 当日之前 `window` 个交易日成交量的算术均值。把当日
  算进去会让放量日自己的中枢被抬高，放量倍数被系统性低估。
* **复权价才算涨跌。** 除权日原始价是断崖，qfq 价才是连续的真实涨跌。
  调用方必须传 qfq_close，传原始 close 会在除权日读出 -50% 这种假暴跌。
"""

from __future__ import annotations

from statistics import fmean
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 阈值默认值。0.618 / 1.382 是斐波那契回撤位，语义是"缩到中枢的六成以下"
# 算缩量、"放到中枢的一倍三八以上"算放量。
# 中枢窗口默认 5：与目标产品截图（2026-10-08 收盘口径 6 个样本点）逐点对账，
# 近 5 日均量 4 个点误差 <3%、2 个点 <13%（残差来自数据商口径差异）；
# 20 日中位数的误差大一个数量级，已弃用。
DEFAULT_WINDOW = 5
DEFAULT_LOW = 0.618
DEFAULT_HIGH = 1.382

VOLUME_DOWN = "down"
VOLUME_UP = "up"
VOLUME_NORMAL = "normal"

VERDICT_PUSH = "推升"
VERDICT_DISTRIBUTE = "出货"

# 平盘判定容差：qfq 收盘价是 DOUBLE，(a/b-1)*100 在 a≈b 时会有 1e-15 量级的
# 浮点残差。不给容差的话，一支横盘票会被四舍五入成"涨 0.0%"然后判成推升。
_FLAT_EPSILON = 1e-9

SeriesRow = Tuple[Any, float, float]


def _missing(thscode: str, reason: str) -> Dict[str, Any]:
    """缺失结果的统一形状。

    `thscode` 一定有值 —— 调用方要靠它把缺失项对齐回请求列表，
    没有代码的缺失项在 UI 上无法定位。
    """
    return {"thscode": thscode, "missing": True, "reason": reason}


def classify_volume(multiple: float, low: float = DEFAULT_LOW, high: float = DEFAULT_HIGH) -> str:
    """倍数 → 缩量/放量/正常。

    用**严格不等号**：倍数正好等于 low 或 high 时算 "normal"。边界值归到
    常态而不是异动，是因为 0.618 和 1.382 本身是斐波那契回撤位，贴着阈值
    的量能通常是"放量到关键位但没突破"，把它算成异动会在面板上多出一排
    噪音行。测试 `test_boundary_*` 锁的就是这条。
    """
    if multiple < low:
        return VOLUME_DOWN
    if multiple > high:
        return VOLUME_UP
    return VOLUME_NORMAL


def classify_verdict(pct_change: float) -> Optional[str]:
    """涨跌幅 → 推升/出货/None（平盘）。

    平盘返回 None 而不是空串：调用方要能区分"平盘"和"没算出来"。
    """
    if pct_change > _FLAT_EPSILON:
        return VERDICT_PUSH
    if pct_change < -_FLAT_EPSILON:
        return VERDICT_DISTRIBUTE
    return None


def compute_pulse(
    thscode: str,
    series: Sequence[SeriesRow],
    window: int = DEFAULT_WINDOW,
    low: float = DEFAULT_LOW,
    high: float = DEFAULT_HIGH,
) -> Dict[str, Any]:
    """计算单只标的在最后一个交易日上的量价异动。

    Args:
        thscode: 库内 thscode（如 "301190.SZ"）。原样回填到结果里。
        series: **按日期升序**的 `[(date, volume, qfq_close), ...]`。
            至少 `window + 1` 条（window 条构成中枢 + 1 条当日）。
        window: 中枢窗口，默认 5 个交易日（对账目标产品得出，见模块头注释）。
        low / high: 缩量/放量阈值。

    Returns:
        正常：`{thscode, date, multiple, pct_change, volume_state, verdict}`
        缺失：`{thscode, missing: True, reason}`

    注意：中枢窗口取的是**倒数第 window+1 条到倒数第 2 条**（不含最后一条
    当日），涨跌幅取最后两条 qfq 收盘价之比。
    """
    if window <= 0:
        return _missing(thscode, f"window 必须为正整数，收到 {window}")

    rows = list(series or [])
    # 需要 window + 1 条：window 条算中枢 + 1 条当日。
    # 少于这个数中枢就是"半个窗口"，用均值补齐会把当日自己算进中枢。
    if len(rows) < window + 1:
        return _missing(
            thscode,
            f"窗口内样本不足：需要 {window + 1} 条（{window} 日中枢 + 当日），实际 {len(rows)} 条",
        )

    # ---- 中枢：倒数第 window+1 .. 倒数第 2 条的成交量，不含当日 ----
    center_window = rows[-(window + 1):-1]
    volumes: List[float] = []
    for row in center_window:
        vol = row[1] if len(row) > 1 else None
        # 中枢窗口里出现 None 意味着这段历史不完整，均值不可信。
        # 这里判缺失而不是跳过 —— 跳过会让窗口缩水，"20 日中枢"名不副实。
        if vol is None:
            return _missing(thscode, f"中枢窗口内成交量缺失（date={row[0]}）")
        try:
            volumes.append(float(vol))
        except (TypeError, ValueError):
            return _missing(thscode, f"中枢窗口内成交量不可用（date={row[0]}）")

    center = float(fmean(volumes))
    if center == 0:
        # 中枢为 0（长期停牌 / 全零成交）：倍数是除零，返回缺失。
        # 不返回 0 倍数 —— 0 倍数在这里是除零的伪装。
        return _missing(thscode, "成交量中枢为 0（该窗口无成交），倍数不可计算")

    last = rows[-1]
    last_volume = last[1] if len(last) > 1 else None
    if last_volume is None:
        return _missing(thscode, f"当日成交量缺失（date={last[0]}）")
    try:
        last_volume = float(last_volume)
    except (TypeError, ValueError):
        return _missing(thscode, f"当日成交量不可用（date={last[0]}）")

    # ---- 倍数 ----
    multiple = last_volume / center

    # ---- 涨跌幅：qfq 收盘价之比 ----
    prev_close = rows[-2][2] if len(rows[-2]) > 2 else None
    last_close = last[2] if len(last) > 2 else None
    if prev_close is None or last_close is None:
        return _missing(thscode, f"前复权收盘价缺失（date={last[0]}）")
    try:
        prev_close_f = float(prev_close)
        last_close_f = float(last_close)
    except (TypeError, ValueError):
        return _missing(thscode, f"前复权收盘价不可用（date={last[0]}）")
    if prev_close_f == 0:
        # 前收为 0 时百分比无意义（除权/上市首日的异常值）。
        return _missing(thscode, "前一日前复权收盘价为 0，涨跌幅不可计算")

    pct_change = (last_close_f / prev_close_f - 1) * 100

    return {
        "thscode": thscode,
        "date": last[0],
        "multiple": round(multiple, 4),
        "pct_change": round(pct_change, 4),
        "volume_state": classify_volume(multiple, low, high),
        # normal 时 verdict 照样给 —— 是否展示由前端决定，规则层不替 UI 做裁剪。
        "verdict": classify_verdict(pct_change),
    }


def is_missing(result: Dict[str, Any]) -> bool:
    """判断结果是否为缺失标记。供聚合层做 assert-free 分流。"""
    return bool(result.get("missing"))
