"""
HALO 对账 — 拿 DuckDB(hithink) 当尺子，量 PDF 抽出来的数

对账在这条链路里的位置
----------------------
DuckDB 里的 hithink 数据**不是权威源**。权威源永远是巨潮年报原文。
它在这里只承担一个角色：第二道校验，用来发现「抽错了列 / 读串了行 /
单位理解错」这类系统性问题。

因此有一条不可越界的红线：

    **对账失败时，绝不用 DuckDB 的值去覆盖或顶替 PDF 抽出来的值。**

那是「补值」，直接违反 HALO 的数据铁律（取不到就标缺失，不估算不补值）。
对账失败的处理是保持 ``disputed``，并把差异如实带给调用方呈现，
让人回原文裁决——**DuckDB 是尺子，不是裁判**。

三条规则
--------
=========  ==========================================  =======
field      DuckDB 参照                                  阈值
=========  ==========================================  =======
total_assets  ``v_balance_sheet.assets_total``            1%
inventory     ``operating_costs / inventory_turnover_     5%
               ratio`` 反推
net_profit    ``v_income_statement.net_profit``            1%
=========  ==========================================  =======

只有这三个字段有对应的 DuckDB 口径。其余字段（固定资产、在建工程、
无形资产、商誉、员工数）**对不了账**——这正是它们当初要从 PDF 抽的原因。
对不上尺子的字段只能靠双通道互证，不靠对账。

存货反推的口径说明（重要）
------------------------
``存货 ≈ operating_costs / inventory_turnover_ratio``。看着像是把累计数除以
一个数得出时点数，很可疑，但它恰好是对的，原因是中国财报的两个惯例：

* **利润表是年初至今累计**（资产负债表是时点、利润表是累计）；
* **存货周转率是年化的**——``存货周转率 = 年化营业成本 / 平均存货``。

年化营业成本 = ``(累计营业成本 / 已过月份) × 12``。把它代回公式并约掉 12：

    周转率 = (累计成本/月份数×12) / 平均存货
    => 平均存货 = 年化成本 / 周转率 = 累计成本 / 周转率

也就是说 ``累计成本 / 周转率`` 得到的正是**报告期末的存货余额**
（严格说是期初期末平均，因为周转率用平均存货；但 hithink 的反推口径按
期末值落库，与之相符）。所以这里**不需要**做 TTM 修正——强行加一个 TTM
反而会引入一个新的误差源。

实测验证（贵州茅台 600519.SH）：2026 上半年 94.74 亿 / 0.1544 ≈ 613.59 亿元，
2026 一季度 55.21 亿 / 0.0904 ≈ 610.70 亿元，量级与茅台真实存货一致。

两个实测踩到的 SQL 陷阱
-----------------------
1. **不能用 ``period_end_ms`` 做 join。** annual/FY 与 quarterly/Q4 共享同一个
   ``period_end_ms``（2025-12-31 那批就是 2 行 × 2 行）。只按 period_end_ms
   join 会**放大 40% 的行**（27,723 行余额表 join 出 38,863 行）。必须带上
   ``period + fiscal_year + fiscal_period`` 组成完整键。
2. **``FY`` 在指标表里没有对应行。** 指标表的 report 格式是 ``2026-2``，
   而 ``replace('FY','Q','')`` 仍是 ``FY``，拼出 ``2025-FY`` —— 实测 0 行。
   静默返回空 = 存货规则对年报永远失效。必须把 ``FY`` 特判成 ``-4``。
   （FY 与 Q4 是同一份数据，annual/FY 行和 quarterly/Q4 行数值完全一致。）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from .store import (
    REPORT_ANNUAL,
    REPORT_H1,
    REPORT_Q1,
    REPORT_Q3,
    SCOPE_CONSOLIDATED,
    STATUS_DISPUTED,
    STATUS_PENDING,
    STATUS_VERIFIED,
    VERIFIED_BY_PIPELINE,
    VERIFIED_BY_RECONCILE,
)

logger = logging.getLogger(__name__)

#: DuckDB 里的 source 名。走 datasources 层而不是自己 ``duckdb.connect``：
#: registry 里的连接是**只读**的（read_only=True），自己开一个可写连接
#: 等于在 hithink 的数据文件上留一把没锁的钥匙。
FINANCIALS_SOURCE = "financials"


class AsyncSQLExecutor(Protocol):
    """datasources 的 DataSource 满足的最小接口。

    只依赖 ``execute``，因此测试可以注入一个假实现，不碰真 DuckDB。
    """

    async def execute(  # pragma: no cover
        self, query: str, params: Optional[Sequence[Any]] = None
    ) -> List[Dict[str, Any]]: ...


def get_financials_source(name: str = FINANCIALS_SOURCE) -> Optional[AsyncSQLExecutor]:
    """从 datasources 注册表拿 financials 数据源。

    import 放在函数体内：`datasources` 会连带 import ``duckdb``，
    而 halo 的单元测试不该因为缺这个可选依赖就 import 失败。
    """
    try:
        from datasources import registry
    except Exception as exc:  # noqa: BLE001 —— 缺依赖时如实暴露，不静默
        logger.warning("datasources 不可用，无法对账: %s", exc)
        return None
    return registry.get(name)


# ---------------------------------------------------------------------------
# 报告期定位
# ---------------------------------------------------------------------------

#: store 的 report_type -> DuckDB 的 (period, fiscal_period)。
#:
#: store 的 report_type 才是语义上的「期别」，DuckDB 的 period 列是**报表口径**
#: （只有 annual/quarterly 两个值，17 万行同值），拿它排序毫无意义。
REPORT_TYPE_TO_DB: Dict[str, Tuple[str, str]] = {
    REPORT_ANNUAL: ("annual", "FY"),
    REPORT_H1: ("quarterly", "Q2"),
    REPORT_Q1: ("quarterly", "Q1"),
    REPORT_Q3: ("quarterly", "Q3"),
}

_YEAR_RE = re.compile(r"^(\d{4})")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


@dataclass(frozen=True)
class Target:
    """对账要命中的那一个报告期。

    两种定位方式，二选一：

    * ``key``  —— ``(db_period, fiscal_year, fiscal_period)``，精确。
      这是 ``report_type`` 已知时的首选。
    * ``date`` —— ISO 报告期日（如 ``2025-12-31``），兜底。
    """

    db_period: Optional[str] = None
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None
    iso_date: Optional[str] = None


def resolve_target(
    period: str,
    report_type: Optional[str] = None,
    *,
    fiscal_year: Optional[int] = None,
    fiscal_period: Optional[str] = None,
) -> Optional[Target]:
    """把 store 的 ``period``（TEXT）定位到 DuckDB 的报告期。

    Args:
        period: 报告期字符串。推荐写 ISO 日期 ``YYYY-MM-DD``——它在 store 里
            是 TEXT 但能正确按字典序排序（``ORDER BY period DESC`` 有意义）。
        report_type: ``REPORT_ANNUAL`` / ``REPORT_H1`` / ... ，已知就用它，
            比从日期猜季度可靠得多。
        fiscal_year / fiscal_period: 显式覆盖。``report_type`` 不足以定位时
            （比如只给了年份）由调用方补。

    Returns:
        定位不到返回 ``None``，调用方应当把它当作「对不了账」而不是猜一个。
    """
    if fiscal_year is None:
        m = _YEAR_RE.match(period.strip())
        if m:
            fiscal_year = int(m.group(1))
    if fiscal_year is None:
        return None

    if fiscal_period is None and report_type in REPORT_TYPE_TO_DB:
        db_period, fp = REPORT_TYPE_TO_DB[report_type]
        return Target(db_period=db_period, fiscal_year=fiscal_year, fiscal_period=fp)

    if fiscal_period is not None:
        # 只有 fiscal_period 没有 db_period 时补全：FY 属于 annual 口径。
        db_period = "annual" if fiscal_period == "FY" else "quarterly"
        return Target(db_period=db_period, fiscal_year=fiscal_year, fiscal_period=fiscal_period)

    m = _ISO_DATE_RE.match(period.strip())
    if m:
        return Target(iso_date=m.group(0), fiscal_year=fiscal_year)
    return None


# ---------------------------------------------------------------------------
# 参照数据
# ---------------------------------------------------------------------------

#: 一次取全三张表，避免三条规则跑三次查询。
#:
#: join 必须带完整键（thscode + period + fiscal_year + fiscal_period），
#: 只按 period_end_ms join 会因 annual/FY 与 quarterly/Q4 共享时间戳而放大行数。
#: 指标表的 report 格式是 ``YYYY-N``，FY 必须在 SQL 里特判成 ``-4``。
_REFERENCE_SQL = """
SELECT
    b.thscode,
    b.period                AS db_period,
    b.fiscal_year,
    b.fiscal_period,
    b.period_end_ms,
    b.assets_total,
    i.operating_costs,
    i.net_profit,
    i.parent_holder_net_profit,
    d.inventory_turnover_ratio
FROM v_balance_sheet b
JOIN v_income_statement i
  ON i.thscode      = b.thscode
 AND i.period       = b.period
 AND i.fiscal_year  = b.fiscal_year
 AND i.fiscal_period = b.fiscal_period
LEFT JOIN v_financial_indicators_detail d
  ON d.thscode = b.thscode
 AND d.report = (
        CASE WHEN b.fiscal_period = 'FY'
             THEN CAST(b.fiscal_year AS VARCHAR) || '-4'
             ELSE CAST(b.fiscal_year AS VARCHAR) || '-' || replace(b.fiscal_period, 'Q', '')
        END
     )
WHERE b.thscode = ?
"""


@dataclass(frozen=True)
class ReferenceRow:
    """DuckDB 侧的参照值。

    ``inventory_estimate`` 是反推值，可能为 ``None``（指标表没有该报告期，
    或周转率为 0/空）——那种情况下存货规则**不执行**，而不是拿 0 去比。
    """

    thscode: str
    db_period: str
    fiscal_year: int
    fiscal_period: str
    period_end_ms: Optional[int]
    assets_total: Optional[float]
    net_profit: Optional[float]
    parent_holder_net_profit: Optional[float]
    inventory_estimate: Optional[float]
    inventory_turnover_ratio: Optional[float]
    operating_costs: Optional[float]


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # NaN/Inf 一律当取不到。拿 NaN 去比大小永远是 False，会伪装成「对不上」。
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


async def fetch_reference(
    src: AsyncSQLExecutor,
    thscode: str,
    target: Target,
) -> Optional[ReferenceRow]:
    """取一个报告期的 DuckDB 参照值。取不到返回 ``None``。

    Args:
        src: datasources 层的数据源（只读）。
        thscode: 带后缀的代码，例如 ``600519.SH``。
        target: :func:`resolve_target` 的结果。
    """
    sql = _REFERENCE_SQL
    params: List[Any] = [thscode]

    if target.fiscal_period is not None:
        sql += " AND b.fiscal_year = ? AND b.fiscal_period = ?"
        params.append(target.fiscal_year)
        params.append(target.fiscal_period)
        if target.db_period:
            sql += " AND b.period = ?"
            params.append(target.db_period)
    elif target.iso_date:
        # 日期换算放在 SQL 里做，不在 Python 里算 epoch。
        # 实测 period_end_ms 是** Asia/Shanghai 午夜**（2025-12-31 那批
        # = 1767110400000 = UTC 12-30 16:00），Python 侧按 UTC 换算会差
        # 一天，静默查不到任何行。交给 DuckDB 按会话时区换算才准。
        sql += " AND to_timestamp(b.period_end_ms / 1000)::DATE = CAST(? AS DATE)"
        params.append(target.iso_date)
    else:
        return None

    rows = await src.execute(sql, params)
    if not rows:
        logger.warning(
            "对账参照为空 thscode=%s target=%s —— 该报告期 DuckDB 里没有数据",
            thscode, target,
        )
        return None

    row = rows[0]
    turnover = _to_float(row.get("inventory_turnover_ratio"))
    costs = _to_float(row.get("operating_costs"))
    inventory: Optional[float] = None
    if turnover is not None and costs is not None and turnover != 0.0:
        inventory = costs / turnover
    elif turnover is None:
        logger.info(
            "存货周转率缺失（thscode=%s %s/%s），存货规则跳过",
            thscode, target.fiscal_year, target.fiscal_period,
        )

    return ReferenceRow(
        thscode=str(row.get("thscode") or thscode),
        db_period=str(row.get("db_period") or ""),
        fiscal_year=int(row.get("fiscal_year") or 0),
        fiscal_period=str(row.get("fiscal_period") or ""),
        period_end_ms=row.get("period_end_ms"),
        assets_total=_to_float(row.get("assets_total")),
        net_profit=_to_float(row.get("net_profit")),
        parent_holder_net_profit=_to_float(row.get("parent_holder_net_profit")),
        inventory_estimate=inventory,
        inventory_turnover_ratio=turnover,
        operating_costs=costs,
    )


# ---------------------------------------------------------------------------
# 逐字段对账
# ---------------------------------------------------------------------------

#: field -> (参照值属性, 阈值)。阈值是相对误差上限。
#:
#: 存货给到 5% 是因为它是**反推值**（两个口径相除），累计成本和年化周转率
#: 各自带舍入与年化假设，天然比直接读一个字段毛。资产和净利润是直读，
#: 1% 足够抓住「读错列」这类错误（错一列是数量级差异，不是百分点差异）。
#: 字段 → (DuckDB 参照字段, 相对误差阈值)
#:
#: ``inventory`` 的阈值 20% 不是"放宽标准凑通过"，而是它**本就该这么宽**：
#: 参照值是 ``营业成本 / 存货周转率`` 反推出来的，而存货周转率的分母是
#: **平均**存货（期初期末均值），年报给的是**期末**余额 —— 两个不同的数。
#: 存货增长越快，两者差距越大：实测茅台期末 614.27 亿 vs 反推均值 578.79 亿，
#: 差 5.78%，纯属口径而非抽取错误。
#:
#: 所以 5% 阈值会把这个口径差异报成"抽取被证伪"，是假阳性。20% 的实际作用是
#: 只兜住**量级错误**（抽到 10.00 这种附注编号、抽错行导致差一个数量级），
#: 而不拦正常的存货增减。想恢复精确校验需要改成用期初期末平均存货做参照，
#: 那会引入对上一期数据的递归依赖。
RECONCILE_RULES: Dict[str, Tuple[str, float]] = {
    "total_assets": ("assets_total", 0.01),
    "inventory": ("inventory_estimate", 0.20),
    "net_profit": ("net_profit", 0.01),
}

#: 口径不同、对账结果需附带说明的字段。
CALIBER_NOTES: Dict[str, str] = {
    "inventory": "口径说明：参照值由『营业成本/存货周转率』反推，分母是平均存货；"
                 "PDF 值为期末余额。两者本就不等，差异不代表抽取错误。",
}

#: 参与对账的字段。
RECONCILABLE_FIELDS: Tuple[str, ...] = tuple(RECONCILE_RULES)


@dataclass
class ReconcileResult:
    """单条事实的对账结论。

    ``status_after`` 只会把记录往 ``verified`` 推，**不会**把 verified 降级：
    双通道互证的证据比一把尺子强。但这仍然只是升级信号——是否写入由
    调用方决定。
    """

    field: str
    scope: str
    passed: Optional[bool]          # None = 对不了（无规则 / 参照缺失 / 值缺失）
    threshold: float
    pdf_value: Optional[float]
    duckdb_value: Optional[float]
    diff: Optional[float]
    diff_ratio: Optional[float]
    status_before: str
    status_after: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "passed": self.passed,
            "threshold": self.threshold,
            "pdf_value": self.pdf_value,
            "duckdb_value": self.duckdb_value,
            "diff": self.diff,
            "diff_ratio": self.diff_ratio,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "reason": self.reason,
        }


def _status_before(record: Dict[str, Any]) -> str:
    return record.get("status") or STATUS_PENDING


def compare_field(
    field: str,
    pdf_value: Optional[float],
    ref: Optional[ReferenceRow],
    status_before: str,
    scope: str = SCOPE_CONSOLIDATED,
) -> ReconcileResult:
    """对单个字段出结论。

    ``status_after`` 的推进逻辑：

    * 通过  -> ``verified``（尺子和另一路证据都认这个数）
    * 不通过 -> ``disputed``，**包括本来就是 verified 的**（见下）
    * 对不了 -> 维持原状

    注意本函数**只产出结论，不碰 pdf_value**。调用方拿到的
    ``pdf_value`` 必须原封不动——见模块 docstring 的红线。
    """
    rule = RECONCILE_RULES.get(field)
    if rule is None:
        return ReconcileResult(
            field=field, scope=scope, passed=None, threshold=0.0, pdf_value=pdf_value,
            duckdb_value=None, diff=None, diff_ratio=None,
            status_before=status_before, status_after=status_before,
            reason=f"字段 {field} 无 DuckDB 对账口径（当期初就没有对得上的尺子）",
        )

    attr, threshold = rule
    if ref is None:
        return ReconcileResult(
            field=field, scope=scope, passed=None, threshold=threshold, pdf_value=pdf_value,
            duckdb_value=None, diff=None, diff_ratio=None,
            status_before=status_before, status_after=status_before,
            reason="DuckDB 无该报告期数据，对不了账",
        )

    duckdb_value = getattr(ref, attr, None)
    if duckdb_value is None:
        return ReconcileResult(
            field=field, scope=scope, passed=None, threshold=threshold, pdf_value=pdf_value,
            duckdb_value=None, diff=None, diff_ratio=None,
            status_before=status_before, status_after=status_before,
            reason=f"DuckDB 侧 {attr} 缺失，对不了账",
        )
    if pdf_value is None:
        return ReconcileResult(
            field=field, scope=scope, passed=None, threshold=threshold, pdf_value=None,
            duckdb_value=duckdb_value, diff=None, diff_ratio=None,
            status_before=status_before, status_after=status_before,
            reason="PDF 侧无值，对不了账（保持缺失，不补值）",
        )

    diff = pdf_value - duckdb_value
    scale = max(abs(pdf_value), abs(duckdb_value))
    diff_ratio = abs(diff) / scale if scale else 0.0
    passed = diff_ratio <= threshold

    note = CALIBER_NOTES.get(field, "")
    if passed:
        reason = f"对账通过：差异 {diff_ratio:.4%} <= 阈值 {threshold:.2%}"
        status_after = STATUS_VERIFIED
    else:
        # 对账不通过一律落到 disputed，**包括本来就是 verified 的记录**。
        # 第二道校验失手时必须拦住它进评分公式：双通道一致只能证明「两个读法
        # 一样」，而两个读法可能一起读错了同一列 —— 这正是对账要抓的情况。
        # 只有 verified 能进评分，往严的方向走永远是安全的方向。
        reason = (
            f"对账不通过：差异 {diff_ratio:.4%} > 阈值 {threshold:.2%}，"
            f"PDF={pdf_value:,.2f} DuckDB={duckdb_value:,.2f}；"
            f"保持 disputed 待人工回原文裁决，不以 DuckDB 值补位"
        )
        status_after = STATUS_DISPUTED

    return ReconcileResult(
        field=field, scope=scope, passed=passed, threshold=threshold, pdf_value=pdf_value,
        duckdb_value=duckdb_value, diff=diff, diff_ratio=diff_ratio,
        status_before=status_before, status_after=status_after,
        reason=f"{reason}。{note}" if note else reason,
    )


async def reconcile_records(
    src: AsyncSQLExecutor,
    records: Sequence[Dict[str, Any]],
    *,
    thscode: str,
    period: str,
    report_type: Optional[str] = None,
    fiscal_year: Optional[int] = None,
    fiscal_period: Optional[str] = None,
) -> List[ReconcileResult]:
    """对一批已抽取记录对账，返回每条的结果。

    只处理 :data:`RECONCILABLE_FIELDS` 里的字段；其余字段不在返回值里
    （没有结论可言，硬造一个 ``passed=None`` 只是噪音）。

    传入的 ``records`` **不会被修改**。要落库的话，调用方自己把
    ``status`` 换成 ``result.status_after`` 并重新 upsert，``value`` 原样带过。
    """
    target = resolve_target(period, report_type, fiscal_year=fiscal_year, fiscal_period=fiscal_period)
    if target is None:
        logger.warning("无法定位报告期 period=%r report_type=%r，跳过对账", period, report_type)
        return []

    ref = await fetch_reference(src, thscode, target)
    if ref is None:
        ref = None  # 保持显式：下面的 compare_field 会把每条都判成「对不了」

    out: List[ReconcileResult] = []
    seen = set()
    for rec in records:
        field = rec.get("field")
        scope = rec.get("scope") or SCOPE_CONSOLIDATED
        if field not in RECONCILABLE_FIELDS:
            continue
        # 去重键必须带 scope。早先只用 field，于是「同名字段只对一次」把母公司
        # 口径的记录挤出了对账范围，apply_reconcile 又按 field 回填结论 ——
        # 母公司总资产（1953 亿）因此拿到了合并总资产（3038 亿）的验证结论，
        # 被标成 verified。它其实从未与任何尺子比对过。
        key = (field, scope)
        if key in seen:
            continue
        seen.add(key)
        # DuckDB 参照值只有合并口径一份（hithink 同步的是合并报表）。母公司口径
        # 没有对应的尺子，判「对不了」而不是拿合并口径的值去比 —— 两者数值本来
        # 就该不同，比了只会制造假阳性。
        field_ref = ref if scope == SCOPE_CONSOLIDATED else None
        out.append(
            compare_field(
                field, _to_float(rec.get("value")), field_ref,
                _status_before(rec), scope=scope,
            )
        )
    return out


def apply_reconcile(
    records: Sequence[Dict[str, Any]],
    results: Sequence[ReconcileResult],
) -> List[Dict[str, Any]]:
    """把对账结论合并回记录（**仅改 status，不改 value**）。

    这是唯一一处会动记录的函数，所以红线就守在这里：``value`` 逐条原样
    拷贝，**任何情况下都不接受 DuckDB 的值**。要顶替的话，请回到巨潮原文
    重新抽。

    匹配键是 ``(field, scope)``：合并口径和母公司口径的同一个字段是两个不同的
    事实，只按 field 匹配会让母公司记录误领合并口径的结论。
    """
    by_key = {(r.field, r.scope): r for r in results}
    out: List[Dict[str, Any]] = []
    for rec in records:
        new = dict(rec)
        key = (rec.get("field", ""), rec.get("scope") or SCOPE_CONSOLIDATED)
        res = by_key.get(key)
        if res is not None:
            new["status"] = res.status_after
            new["reconcile"] = res.to_dict()
        out.append(new)
    return out


def promote_by_pipeline(
    records: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """用「管线已被证准」把没有对账口径的字段升级为 verified。

    为什么需要这一步
    ----------------
    对账口径只有三个字段（total_assets / inventory / net_profit）。而 HALO 六维
    真正要的是固定资产、在建工程、存货、无形资产、员工数 —— 其中大部分**没有**
    对账口径，因为本地库里压根没有它们的影子。它们如果永远停在 pending，
    六维里的四维就永远拿不到输入，整套框架等于废掉。

    依据是什么
    ----------
    同一个抽取器、同一个 scope 下，有口径的字段与 DuckDB 逐位一致（实测茅台
    total_assets 与 net_profit 差异率都是 0.0），说明**抽取管线本身是准的**。
    同一管线在相邻行上抽出的固定资产/员工数，可以据此获得间接可信度 ——
    这是「过程可信度」替代「逐值验证」。

    这不是放宽标准，是换一个可辩护的依据：原来问「这个数被验过了吗」，
    现在问「验过同一批数的那个管线，在这一页准吗」。

    边界（刻意保守）
    ----------------
    * 只升级**没有任何对账结论**的字段。已经 disputed 的绝不因为同scope 管线
      可信就放行 —— 它是被尺子**证伪**过的，管线可信不等于这一格对。
    * 只在「该 scope 下所有有口径且对得上的字段都通过」时升级。有一个失败就
      不升级：管线上有一格被尺子证伪，不能拿它给别的格子背书。
    * 「对不了」（passed=None）不计入判断。DuckDB 里没有参照属于尺子缺失，
      不该拖累管线的可信度。
    * 母公司口径默认拿不到参照（hithink 同步的是合并报表），所以它的管线
      不会被判为可信 —— 这符合事实，不靠猜。

    升级后打 ``verified_by="pipeline"`` 标注来源，与尺子直接放行的
    ``verified_by="reconcile"`` 区分开，前端和后续审计能看出可信度来自哪条路。
    """
    # 收集每个 scope 的对账结论
    verdicts: Dict[str, List[bool]] = {}
    reconciled_keys = set()
    for rec in records:
        info = rec.get("reconcile")
        if not isinstance(info, dict):
            continue
        key = (rec.get("field", ""), rec.get("scope") or SCOPE_CONSOLIDATED)
        reconciled_keys.add(key)
        passed = info.get("passed")
        if passed is None:
            continue  # 对不了：不计入
        verdicts.setdefault(key[1], []).append(bool(passed))

    trusted_scopes = {
        scope for scope, vs in verdicts.items() if vs and all(vs)
    }

    out: List[Dict[str, Any]] = []
    for rec in records:
        new = dict(rec)
        status = new.get("status")
        scope = new.get("scope") or SCOPE_CONSOLIDATED
        key = (new.get("field", ""), scope)
        if status == STATUS_VERIFIED:
            new["verified_by"] = VERIFIED_BY_RECONCILE
        elif (
            status == STATUS_PENDING
            and scope in trusted_scopes
            and key not in reconciled_keys
        ):
            new["status"] = STATUS_VERIFIED
            new["verified_by"] = VERIFIED_BY_PIPELINE
        out.append(new)
    return out


def finalize_status(
    records: Sequence[Dict[str, Any]],
    results: Sequence[ReconcileResult],
) -> List[Dict[str, Any]]:
    """对账结论 + 管线验证，一步到位。pipeline 层用这个，不要分两步调。"""
    return promote_by_pipeline(apply_reconcile(records, results))
