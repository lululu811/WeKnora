"""
选股筛选维度：板块归属 + 财务风险代理

为什么有这一层
--------------
形态信号（超卖组合、放量突破、Donchian 突破…）只回答"图形像不像"，
不回答"这家公司会不会暴雷"。实际用法是：形态筛出候选 → 人工看图 →
重点看基本面有没有雷。所以最后一关必须是**能不能被数据证伪**的风险维度。

能算什么、不能算什么
--------------------
本地 financials 库里**没有**：商誉、股权质押、大股东减持、审计意见、
监管处罚、业绩预告 —— 逐列扫过全部视图，一个都没有。这些是真正的暴雷
信号，要接得另找数据源。

**能算的**（v_balance_sheet 16 列，覆盖全部 5,571 只）：

    资产负债率 = total_debt / assets_total
    流动比率   = total_current_assets / total_debt
    应收占比   = accounts_receivable / assets_total

这三个是真风险代理，不是包装：最新期实际分布 p50=0.40 / p90=0.71、
流动比率 p50=1.50 / p10=0.60，有区分度，不是所有票一个样。

ST 标记靠 `v_symbol.name LIKE '%ST%'` 硬匹配 —— 库里**没有 ST 标记列**。
全 A 204 只。这是唯一的取法，必须在返回值里说明它是名字匹配而非
官方标记，否则用户会以为这是监管口径。

三分原则
--------
分母为 0 或字段为 NULL 时一律返回 None，**不返回 0**。资产负债率算不出
是"没有这个数"，返回 0 会被读成"零负债"——那是最危险的误读。
"""

from typing import Any, Dict, List, Optional

# 指标出处。返回给前端的每个数字都要能追回源表，这是悬浮框和工具层
# 已经在执行的同一套规矩。
SECTOR_SOURCE = "index.v_index_constituents ⋈ index.v_index_universe"
BALANCE_SOURCE = "financials.v_balance_sheet"
INCOME_SOURCE = "financials.v_income_statement"
ST_SOURCE = "market.v_symbol.name LIKE '%ST%'（名称匹配，非监管标记）"


def build_sector_filter_sql() -> str:
    """按板块名反查成分股。

    **不展开全量成员关系**：index.v_index_constituents 有 122,368 行，
    超过 python-service 单查询 100k 上限。展开必被静默截断。
    按板块名反查只有几百行，且这正是筛选需要的形态 —— 用户要的是
    "半导体里符合超卖组合的票"，不是"每只票的全部 20 个标签"。

    默认**精确匹配**（`name = ?`），不默认 LIKE。实测 `LIKE '%银行%'` 会
    同时命中四个板块：行业「银行」42 只、「股份制银行」9 只、
    「国有大型银行」6 只，以及概念「参股银行」194 只 —— 后者是把
    塔牌集团、广东明珠这类建材/家电公司拉进来的原因（它们只是参股了
    银行）。用户说"银行"时想要的是前者，拿到 235 只"含银行字样的票"
    是明确的误导。

    需要模糊时用 `build_sector_filter_sql_fuzzy()`，并在返回值里说明
    命中了哪些板块。
    """
    return """
        SELECT DISTINCT c.thscode
        FROM v_index_constituents c
        JOIN v_index_universe u ON u.thscode = c.index_thscode
        WHERE u.name = ?
    """


def build_sector_filter_sql_fuzzy() -> str:
    """板块名的**片段**匹配。

    明确更宽：会把概念板块一起卷进来。调用方必须在返回里列出命中的
    板块名，否则用户无从知道自己拿到的是哪几个板块的并集。
    """
    return """
        SELECT DISTINCT c.thscode
        FROM v_index_constituents c
        JOIN v_index_universe u ON u.thscode = c.index_thscode
        WHERE u.name LIKE ?
    """


def build_sector_names_sql() -> str:
    """列出与给定名字匹配到的板块及其类型、规模。

    供返回体说明"到底命中了哪几个板块"。没有这一步，模糊匹配的结果
    就是一个无法复核的数字。
    """
    return """
        SELECT u.name, u.tag, COUNT(DISTINCT c.thscode) AS constituents
        FROM v_index_universe u
        LEFT JOIN v_index_constituents c ON c.index_thscode = u.thscode
        WHERE u.name LIKE ?
        GROUP BY u.name, u.tag
        ORDER BY constituents DESC
    """


def escape_like_pattern(name: str) -> str:
    """把板块名转成 LIKE 模式，通配符由调用方决定。

    板块名本身可能含 `%` 或 `_`（如「科创_板」类命名），不转义会让
    `_` 匹配任意单字符，把不相干的板块也拉进来。
    """
    escaped = (
        name.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def build_risk_filter_sql() -> str:
    """取每只票最新一期的风险代理指标。

    每只票只出一行（row_number 取最新 period），所以行数约等于标的数
    （5,571），不需要分片。

    三个比率都用 CASE WHEN 显式处理 NULL 与零分母：算不出来就是 NULL，
    不返回 0。资产负债率返回 0 会被读成"零负债"——那是最危险的误读。
    """
    return """
        WITH ranked AS (
            SELECT thscode, period, total_debt, total_current_assets, assets_total,
                   accounts_receivable, holder_equity_total,
                   ROW_NUMBER() OVER (PARTITION BY thscode ORDER BY period DESC) AS rn
            FROM v_balance_sheet
        )
        SELECT thscode,
               CAST(period AS VARCHAR) AS period,
               CASE WHEN assets_total > 0
                    THEN total_debt / assets_total END AS debt_ratio,
               CASE WHEN total_debt > 0
                    THEN total_current_assets / total_debt END AS current_ratio,
               CASE WHEN assets_total > 0
                    THEN accounts_receivable / assets_total END AS receivable_ratio,
               total_debt,
               holder_equity_total
        FROM ranked
        WHERE rn = 1
    """


def build_profit_filter_sql() -> str:
    """取每只票最新一期的归母净利润（亏损筛选用）。"""
    return """
        WITH ranked AS (
            SELECT thscode, period, parent_holder_net_profit, operating_income,
                   ROW_NUMBER() OVER (PARTITION BY thscode ORDER BY period DESC) AS rn
            FROM v_income_statement
        )
        SELECT thscode,
               CAST(period AS VARCHAR) AS period,
               parent_holder_net_profit,
               operating_income
        FROM ranked
        WHERE rn = 1
    """


def build_st_names_sql() -> str:
    """列出名称含 ST 的票。

    库里没有 ST 标记列，只能匹配名称。全 A 204 只。
    `*ST`（退市风险警示）与 `ST`（其他风险警示）都覆盖。
    """
    return """
        SELECT thscode, name
        FROM v_symbol
        WHERE name LIKE '%ST%'
    """


def num(v: Any) -> Optional[float]:
    return v if isinstance(v, (int, float)) else None


def evaluate_risk_filters(
    entry: Dict[str, Any],
    max_debt_ratio: Optional[float] = None,
    min_current_ratio: Optional[float] = None,
    max_receivable_ratio: Optional[float] = None,
    require_profit: bool = False,
    exclude_st: bool = False,
) -> Optional[str]:
    """判断一只票是否通过风险筛选。返回 None 表示通过，否则返回**淘汰原因**。

    淘汰原因必须具体到"哪一项、实际多少、阈值多少"。只说"不符合条件"
    等于让用户自己猜，而这套筛选的整个意义就是让人能复核。

    算不出的指标（NULL）**不淘汰**，但在 reasons 里标记出来 ——
    "没有这个数据"和"这个数据不合格"是两回事，前者不该让票消失。
    """
    reasons: List[str] = []
    missing: List[str] = []

    risk = entry.get("risk") or {}
    debt_ratio = num(risk.get("debt_ratio"))
    current_ratio = num(risk.get("current_ratio"))
    receivable_ratio = num(risk.get("receivable_ratio"))

    if max_debt_ratio is not None:
        if debt_ratio is None:
            missing.append("资产负债率")
        elif debt_ratio > max_debt_ratio:
            reasons.append(
                f"资产负债率 {debt_ratio:.1%} > 阈值 {max_debt_ratio:.1%}"
            )

    if min_current_ratio is not None:
        if current_ratio is None:
            missing.append("流动比率")
        elif current_ratio < min_current_ratio:
            reasons.append(
                f"流动比率 {current_ratio:.2f} < 阈值 {min_current_ratio:.2f}"
            )

    if max_receivable_ratio is not None:
        if receivable_ratio is None:
            missing.append("应收占比")
        elif receivable_ratio > max_receivable_ratio:
            reasons.append(
                f"应收账款占总资产 {receivable_ratio:.1%} > 阈值 {max_receivable_ratio:.1%}"
            )

    if require_profit:
        profit = num((entry.get("profit") or {}).get("parent_holder_net_profit"))
        if profit is None:
            missing.append("归母净利润")
        elif profit < 0:
            reasons.append(f"最新期归母净利润为负（{profit / 1e8:.2f} 亿）")

    if exclude_st:
        if entry.get("is_st"):
            reasons.append(f"ST/*ST 风险警示（{entry.get('name', '')}）")

    if reasons:
        return "；".join(reasons)
    # 通过，但把"算不出来的项"挂出去，由调用方决定要不要报给用户
    if missing:
        entry["risk_missing"] = missing
    return None
