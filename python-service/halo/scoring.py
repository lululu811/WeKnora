"""HALO 评分内核：阈值单一真相源 + 六维计算 + 成长性 + 综合分复算。

本模块只做**确定性计算**。定性维度（护城河/滞胀/ESG/管理层/资金面/估值/
风险）由调用方给分，:func:`recalc_comprehensive` 负责复算校验。

阈值来源
--------
移植自 halo-skill 的 ``halo_thresholds.py``，并修正了它与自身文档不一致的
四处（详见每个常量旁的注释）。权重原本在 ``halo_thresholds.py`` 和
``generate_report.py`` 各存一份，这里合并为唯一一份。

边界算子统一为 ``>=``
----------------------
halo-skill 里成长性用严格 ``>``、HALO 六维用 ``>=``。混用会导致边界值
（如营收增速正好 30%）在两个体系里得到不同对待 —— 同一份数据、同一个
30%，一个体系给 9 分另一个给 7 分。这里统一成 ``>=``：阈值表写作
``(下界, 分数)``，含义是「值 ≥ 下界则得该分数」。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .industry import rating_5, rating_10

# ---------------------------------------------------------------------------
# 阈值单一真相源
# ---------------------------------------------------------------------------
# 语义：(下界, 分数)，值 >= 下界时得该分数。必须按分数从高到低排列。
# 单位：比率类为百分数（28.43 表示 28.43%），资本-劳动力为万元/人。

#: ① 有形资产密集度 =（固定+在建+存货）/ 总资产
#: halo-skill：轻资产型用「核心资产」（额外含无形资产+商誉），我们不做这个
#: 替换 —— 白酒/软件这类公司把无形资产算进「有形」会让该维度失去意义。
#: 这里统一用狭义有形资产，跨行业可比。
TANGIBLE_INTENSITY: Dict[str, List[Tuple[float, int]]] = {
    "heavy": [(80.0, 5), (60.0, 4), (40.0, 3), (20.0, 2), (0.0, 1)],
    "mixed": [(70.0, 5), (50.0, 4), (30.0, 3), (15.0, 2), (0.0, 1)],
    "light": [(60.0, 5), (40.0, 4), (20.0, 3), (10.0, 2), (0.0, 1)],
}

#: ② 固定资产密集度 = 固定资产 / 营业收入
#: 修正：halo-skill 只有 4 档（下界 60/80/100，最低 2 分），文档的表格却
#: 隐含存在 1 分档。缺的那一档没有对应分数，被迫打 2 分，等于「固定资产很
#: 轻」的公司在重资产标准下也拿不到最低分。补齐 1 分档。
FIXED_INTENSITY: Dict[str, List[Tuple[float, int]]] = {
    "heavy": [(100.0, 5), (80.0, 4), (60.0, 3), (40.0, 2), (0.0, 1)],
    "mixed": [(60.0, 5), (40.0, 4), (20.0, 3), (10.0, 2), (0.0, 1)],
    "light": [(30.0, 5), (15.0, 4), (5.0, 3), (2.0, 2), (0.0, 1)],
}

#: ③ 固定资产份额 = 固定资产 / 总资产（全行业统一，不随资产类型变化）
FIXED_SHARE: List[Tuple[float, int]] = [
    (25.0, 5), (15.0, 4), (8.0, 3), (4.0, 2), (0.0, 1),
]

#: ④ 资本-劳动力比率 = 有形资产（万元）/ 员工数
CAPITAL_LABOR: List[Tuple[float, int]] = [
    (200.0, 5), (100.0, 4), (50.0, 3), (20.0, 2), (0.0, 1),
]

#: ⑤ Capex 密集度 = Capex / 营业收入
#: 同 ②：补齐缺失的 1 分档。
CAPEX_INTENSITY: Dict[str, List[Tuple[float, int]]] = {
    "heavy": [(15.0, 5), (10.0, 4), (5.0, 3), (2.0, 2), (0.0, 1)],
    "mixed": [(10.0, 5), (5.0, 4), (2.0, 3), (1.0, 2), (0.0, 1)],
    "light": [(5.0, 5), (2.0, 4), (0.5, 3), (0.2, 2), (0.0, 1)],
}

#: ⑥ Capex 负担 = Capex / 经营现金流。**反向**：负担越低分越高。
#: 必须用 :func:`_score_by_table_inverted` 取值，不能用正向比较 ——
#: 实测宝钢 2024 负担 69.71%，用 `>=` 会命中第一档拿到 5 分（看着像
#: 「资本开支极轻」），实际是最差的一档。反向表的最后一档 (0, 1) 是兜底，
#: 不是「负担为零得 1 分」——负担为零其实是最好的情况。
CAPEX_BURDEN: List[Tuple[float, int]] = [
    (5.0, 5), (15.0, 4), (30.0, 3), (50.0, 2), (0.0, 1),
]

#: HALO 六维内部权重（和为 1）
HALO_WEIGHTS: Dict[str, float] = {
    "tangible_intensity": 0.20,
    "fixed_intensity": 0.15,
    "fixed_share": 0.15,
    "capital_labor": 0.15,
    "capex_intensity": 0.15,
    "capex_burden": 0.20,
}

#: 成长性：营收/利润同比（10 分制）。负增长统一 2 分（halo-skill 的代码行为，
#: 与其文档写的「1-2」不一致 —— 这里保留代码行为并把文档口径统一过来：
#: 给负增长 1 分会让「-1%」和「-40%」同分，失去区分度）。
GROWTH_YOY: List[Tuple[float, int]] = [
    (30.0, 9), (15.0, 7), (5.0, 5), (0.0, 3), (float("-inf"), 2),
]

#: 成长质量/持续性的加减分规则（base 5 分）
GROWTH_QUALITY_RULES = (
    ("cf_to_profit", "gt", 0.8, +1, "经营现金流/净利润 > 0.8，利润有现金支撑"),
    ("debt_ratio", "lt", 40.0, +1, "资产负债率 < 40%"),
    ("debt_ratio", "gt", 70.0, -2, "资产负债率 > 70%"),
    ("cf_to_profit", "lt", 0.0, -2, "经营现金流为负"),
)
GROWTH_QUALITY_BASE = 5.0
GROWTH_SUSTAIN_RULES = (
    ("revenue_yoy", "gt", 10.0, +1, "营收增速 > 10%"),
    ("revenue_yoy", "lt", 0.0, -1, "营收负增长"),
)
GROWTH_SUSTAIN_BASE = 5.0

# ---------------------------------------------------------------------------
# 综合分权重
# ---------------------------------------------------------------------------
#: 8 项正向维度的权重，用**整数**表示（和为 90）。
#:
#: 比例取自 halo_harness.COMPREHENSIVE_WEIGHTS 的 8 项正权重（0.15/0.15/
#: 0.15/0.10/0.10/0.10/0.05/0.10，和为 0.90），乘 100 转整数以保住各维度之间
#: 的相对关系，同时避免一个具体的坑：用 0.1667 这样的小数近似会让权重和
#: 变成 1.0001，满分算出来是 10.0009 而不是 10.0 —— 满分是这套评分的
#: 上界锚点，不该带小数尾巴。计算时先加权和再除以 90，中途不做逐项除法。
COMPREHENSIVE_WEIGHTS: Dict[str, int] = {
    "halo": 15,
    "growth": 15,
    "moat": 15,
    "stag": 10,
    "esg": 10,
    "management": 10,
    "shareholder": 5,
    "valuation": 10,
}

#: 权重之和。分母即此值。
COMPREHENSIVE_WEIGHT_SUM = 90

#: 8 项正向维度合计的实际权重。
#:
#: halo-skill 的正权重合计只有 0.90，再减 0.10 的风险项，导致**满分永远
#: 到不了 10**（最好 9.0），且全维崩盘时总分为负 —— 却按 ``/10`` 呈现，
#: 于是「≥8.0 极强」这个档在数学上可及但几乎不可达。归一到 0.90 + 风险
#: 0.10 = 1.00 后区间回到 [0, 10]。
COMPREHENSIVE_SCALE = 0.90

#: 风险维度的权重，以「正向贡献」形式进入：贡献 = (10 − risk) × RISK_WEIGHT。
#:
#: 风险分越高（越危险）→ 贡献越低。转成正向的好处不只是区间对称：负权重
#: 在实现上容易漏算（一个减号丢失就静默改变全部评分），正向加法没有这个
#: 失效模式。
RISK_WEIGHT = 0.10

#: 九个参与综合分的维度。注意**不是 11 个** —— halo-skill 的「11 维」是报告
#: 章节数，权重表只有 9 项（护城河与低淘汰率是同一个维度）。
COMPREHENSIVE_DIMENSIONS: Tuple[str, ...] = (
    "halo", "growth", "moat", "stag", "esg", "management",
    "shareholder", "valuation", "risk",
)

#: 复算容差。halo-skill 用 0.5，而最低评级档位间隔是 5.0−6.5 = 1.5，
#: 0.5 已超过其三分之一 —— AI 给 8.2（极强）而复算 7.8（强）会被判「通过」，
#: 分错一个档。收紧到 0.05：足以容纳浮点与四舍五入噪声，远小于档位间隔。
RECHECK_TOLERANCE = 0.05


def _score_by_table(value: float, table: Sequence[Tuple[float, int]]) -> int:
    """正向表：值越大分越高（``value >= 下界`` 命中该档）。"""
    for boundary, score in table:
        if value >= boundary:
            return score
    return table[-1][1]


def _score_by_table_inverted(value: float, table: Sequence[Tuple[float, int]]) -> int:
    """反向表：值越小分越高（``value <= 上界`` 命中该档）。

    表按「上界」从严到宽排列。全部上界都不命中时返回最后一档的分数。
    """
    for boundary, score in table:
        if value <= boundary:
            return score
    return table[-1][1]


# ---------------------------------------------------------------------------
# HALO 六维
# ---------------------------------------------------------------------------


class MissingInput(Exception):
    """必需输入缺失。

    Q2 定的严格模式：缺一维就不算 HALO 总分，不外推、不用别的口径顶替。
    调用方必须把它当成「本股 HALO 不可计算」如实呈现。
    """


def score_halo(
    *,
    asset_type: str,
    fixed_assets: Optional[float],
    construction_in_progress: Optional[float],
    inventory: Optional[float],
    total_assets: Optional[float],
    employees: Optional[float],
    revenue: Optional[float],
    capex: Optional[float],
    ocf: Optional[float],
) -> Dict[str, Any]:
    """算 HALO 六维。

    Raises:
        MissingInput: 任一必需输入缺失。**不返回部分分数** —— 半个 HALO
            总分在报告里会被当成完整分数引用，比明确报缺失危险得多。
    """
    required = {
        "fixed_assets": fixed_assets,
        "construction_in_progress": construction_in_progress,
        "inventory": inventory,
        "total_assets": total_assets,
        "employees": employees,
        "revenue": revenue,
        "capex": capex,
        "ocf": ocf,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise MissingInput(
            "HALO 六维不可计算，缺以下输入：" + "、".join(missing)
            + "。按数据铁律不做外推或补值，请如实标注缺失。"
        )

    assert fixed_assets is not None and total_assets is not None  # for type checkers
    assert revenue is not None and capex is not None and ocf is not None
    assert employees is not None
    if total_assets <= 0 or revenue <= 0 or employees <= 0:
        raise MissingInput("总资产/营业收入/员工数必须为正，当前数据不可用于比率计算")

    tangible = fixed_assets + (construction_in_progress or 0.0) + (inventory or 0.0)
    if tangible < 0:
        raise MissingInput("有形资产为负，数据异常，拒绝计算")

    # 经营现金流为负或零时，Capex 负担是**无界**的（除以极小的正数）。
    # halo-skill 在这种情况下把展示值记成 0、打分却走 999→1 分，两者背离
    # （显示「Capex 负担 0%」看着像极好，实际按最差计分）。这里统一：
    # 负担记为 None 并带上原因，分数取最低。
    ocf_non_positive = ocf <= 0
    burden = (capex / ocf * 100.0) if not ocf_non_positive else None

    dims: Dict[str, Dict[str, Any]] = {}

    def add(key: str, value: Optional[float], unit: str, note: str = "") -> None:
        dims[key] = {
            "raw": value,
            "unit": unit,
            "weight": HALO_WEIGHTS[key],
            "note": note,
        }

    v = tangible / total_assets * 100.0
    add("tangible_intensity", v, "%", f"有形资产 {tangible / 1e8:.2f} 亿 / 总资产 {total_assets / 1e8:.2f} 亿")
    dims["tangible_intensity"]["score"] = _score_by_table(v, TANGIBLE_INTENSITY[asset_type])

    v = fixed_assets / revenue * 100.0
    add("fixed_intensity", v, "%", f"固定资产 {fixed_assets / 1e8:.2f} 亿 / 营业收入 {revenue / 1e8:.2f} 亿")
    dims["fixed_intensity"]["score"] = _score_by_table(v, FIXED_INTENSITY[asset_type])

    v = fixed_assets / total_assets * 100.0
    add("fixed_share", v, "%")
    dims["fixed_share"]["score"] = _score_by_table(v, FIXED_SHARE)

    v = tangible / 1e4 / employees  # 有形资产(元) → 万元，再除以人数
    add("capital_labor", v, "万元/人", f"{employees:,.0f} 人")
    dims["capital_labor"]["score"] = _score_by_table(v, CAPITAL_LABOR)

    v = capex / revenue * 100.0
    add("capex_intensity", v, "%", f"Capex {capex / 1e8:.2f} 亿")
    dims["capex_intensity"]["score"] = _score_by_table(v, CAPEX_INTENSITY[asset_type])

    if ocf_non_positive:
        add("capex_burden", None, "%", "经营现金流非正，Capex 负担无界，按最差计 1 分")
        dims["capex_burden"]["score"] = 1
    else:
        assert burden is not None
        add("capex_burden", burden, "%", f"经营现金流 {ocf / 1e8:.2f} 亿")
        dims["capex_burden"]["score"] = _score_by_table_inverted(burden, CAPEX_BURDEN)

    total = sum(d["score"] * d["weight"] for d in dims.values())
    return {
        "total": round(total, 4),
        "rating": rating_5(total),
        "asset_type": asset_type,
        "dimensions": dims,
    }


# ---------------------------------------------------------------------------
# 成长性
# ---------------------------------------------------------------------------


def score_growth(
    *,
    revenue_yoy: Optional[float],
    net_profit_yoy: Optional[float],
    cf_to_profit: Optional[float],
    debt_ratio: Optional[float],
) -> Dict[str, Any]:
    """算成长性（10 分制）。

    口径：**最新期累计 vs 去年同期累计**。利润表是中国财报惯例的年初至今
    累计口径，拿 2026H1 累计去比 2025Q2 单季会得到完全错误的同比。

    缺失时不抛异常 —— 成长性缺一两个输入还能给出标注过的部分分（与 HALO
    六维的严格处理不同：六维的四个输入是年报特有，缺了整个框架就废了；
    成长性三项子分各自独立，缺一项仍能算另外两项）。但缺项会在结果里
    显式列出，且不参与加权。
    """
    subs: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []

    if revenue_yoy is None:
        missing.append("revenue_yoy")
    else:
        subs["revenue"] = {
            "raw": revenue_yoy, "unit": "%", "weight": 0.25,
            "score": _score_by_table(revenue_yoy, GROWTH_YOY),
        }
    if net_profit_yoy is None:
        missing.append("net_profit_yoy")
    else:
        subs["profit"] = {
            "raw": net_profit_yoy, "unit": "%", "weight": 0.25,
            "score": _score_by_table(net_profit_yoy, GROWTH_YOY),
        }

    quality_anchor = {"cf_to_profit": cf_to_profit, "debt_ratio": debt_ratio}
    quality = GROWTH_QUALITY_BASE
    applied: List[str] = []
    if cf_to_profit is None or debt_ratio is None:
        missing.extend(k for k, v in quality_anchor.items() if v is None)
    else:
        for name, op, bound, delta, desc in GROWTH_QUALITY_RULES:
            val = quality_anchor[name]
            hit = (val > bound) if op == "gt" else (val < bound)
            if hit:
                quality += delta
                applied.append(f"{desc} {delta:+d}")
    quality = max(0.0, min(10.0, quality))
    subs["quality"] = {
        "raw": quality_anchor, "weight": 0.25, "score": quality,
        "applied": applied,
    }

    sustain = GROWTH_SUSTAIN_BASE
    applied_s: List[str] = []
    if revenue_yoy is None:
        pass
    else:
        for _name, op, bound, delta, desc in GROWTH_SUSTAIN_RULES:
            val = revenue_yoy
            hit = (val > bound) if op == "gt" else (val < bound)
            if hit:
                sustain += delta
                applied_s.append(f"{desc} {delta:+d}")
    sustain = max(0.0, min(10.0, sustain))
    subs["sustainability"] = {
        "raw": {"revenue_yoy": revenue_yoy}, "weight": 0.25,
        "score": sustain, "applied": applied_s,
    }

    weighted = sum(s["score"] * s["weight"] for s in subs.values())
    weight_used = sum(s["weight"] for s in subs.values())
    # 缺项时按**实际权重**归一，否则缺一项就会凭空掉 25% 的分——那等于把
    # 「取不到数据」误报成「公司表现差」，方向正好反了。
    total = weighted / weight_used if weight_used else 0.0
    return {
        "total": round(total, 4),
        "rating": rating_10(total),
        "sub_scores": subs,
        "missing": missing,
        "weight_used": round(weight_used, 4),
        "complete": not missing,
    }


# ---------------------------------------------------------------------------
# 综合分：复算与校验
# ---------------------------------------------------------------------------


def recalc_comprehensive(scores: Dict[str, float]) -> Dict[str, Any]:
    """按权重复算综合分。

    ``scores`` 是 9 个维度的分数（0–10；HALO 六维传的是 5 分制原值，此处
    内部会做 ÷5×10 归一）。

    风险以正向贡献进入：``(10 − risk) × 0.10``。
    """
    missing = [d for d in COMPREHENSIVE_DIMENSIONS if scores.get(d) is None]
    if missing:
        return {
            "ok": False,
            "missing_dimensions": missing,
            "reason": "缺维度分数，无法复算：" + "、".join(missing),
        }

    parts: Dict[str, Any] = {}
    weighted_sum = 0.0  # Σ(score × 整数权重)
    for dim, weight in COMPREHENSIVE_WEIGHTS.items():
        raw = float(scores[dim])
        normalized = raw / 5.0 * 10.0 if dim == "halo" else raw
        weighted_sum += normalized * weight
        parts[dim] = {
            "raw": round(raw, 4),
            "normalized": round(normalized, 4),
            "weight": weight,
            "effective_weight": round(weight / COMPREHENSIVE_WEIGHT_SUM * COMPREHENSIVE_SCALE, 6),
        }
    total = weighted_sum / COMPREHENSIVE_WEIGHT_SUM * COMPREHENSIVE_SCALE

    for dim, p in parts.items():
        p["contribution"] = round(
            p["normalized"] * p["weight"] / COMPREHENSIVE_WEIGHT_SUM * COMPREHENSIVE_SCALE, 4
        )

    risk = float(scores["risk"])
    risk_contrib = (10.0 - risk) * RISK_WEIGHT
    parts["risk"] = {
        "raw": round(risk, 4),
        "inverted": round(10.0 - risk, 4),
        "weight": int(RISK_WEIGHT * 100),
        "effective_weight": RISK_WEIGHT,
        "contribution": round(risk_contrib, 4),
    }
    total += risk_contrib

    # 全维满分时必须**精确**落在 10.0，零风险零维时精确落在 0.0。
    return {
        "ok": True,
        "total": round(total, 4),
        "rating": rating_10(total),
        "parts": parts,
        "scale": COMPREHENSIVE_SCALE,
        "weight_sum": COMPREHENSIVE_WEIGHT_SUM,
        "risk_weight": RISK_WEIGHT,
    }


def verify_comprehensive(
    declared_total: Optional[float],
    scores: Dict[str, float],
    *,
    declared_rating: Optional[str] = None,
    tolerance: float = RECHECK_TOLERANCE,
) -> Dict[str, Any]:
    """复算并校验调用方声明的综合分。

    两道校验（Q7 定的）：
    1. 数值校验：|声明 − 复算| ≤ 0.05
    2. 档位校验：声明的评级与复算分的评级**同档**。这一条不能省 ——
       即使数值差 0.04，6.49 与 6.50 也分属「中等」和「强」两档。
    """
    recalc = recalc_comprehensive(scores)
    if not recalc.get("ok"):
        return {"ok": False, "recalc": recalc, "issues": ["无法复算"]}

    issues: List[str] = []
    notes: List[str] = []
    diff = None
    if declared_total is None:
        # 「只想拿复算值」是合法用法（agent 还没决定自己的分），不算问题。
        # 记成 note 而不是 issue，否则 ok=false 会让调用方以为复算本身失败了。
        notes.append("未提供声明综合分，本次只返回复算值，未做数值校验")
    else:
        diff = round(float(declared_total) - recalc["total"], 4)
        if abs(diff) > tolerance:
            issues.append(
                f"综合分与复算不符：声明 {declared_total:.2f}，复算 {recalc['total']:.2f}，"
                f"差 {diff:+.2f}（容差 {tolerance}）"
            )
    rating_ok = None
    if declared_rating is not None:
        rating_ok = declared_rating == recalc["rating"]
        if not rating_ok:
            # 用 4 位小数而不是 2 位：跨档恰恰发生在四舍五入的缝隙里，
            # 显示成 6.50 却说它是「中等」会让读错误判成 bug。
            issues.append(
                f"评级跨档：声明「{declared_rating}」，复算分 {recalc['total']:.4f} "
                f"应属「{recalc['rating']}」"
            )
    elif declared_total is None:
        notes.append("未提供声明评级，未做档位校验")

    return {
        "ok": not issues,
        "recalc": recalc,
        "diff": diff,
        "rating_match": rating_ok,
        "tolerance": tolerance,
        "issues": issues,
        "notes": notes,
        "checked": declared_total is not None or declared_rating is not None,
    }
