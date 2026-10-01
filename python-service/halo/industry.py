"""资产类型分类：重 / 混合 / 轻。

为什么财务比例优先
------------------
halo-skill 的代码只用行业名字符串匹配（13 个重资产 + 14 个轻资产行业名），
未命中就默认 mixed。但它自己的 SKILL.md 写的是「分类依据：固定资产占总
资产比例 + 行业特性」—— 文档和实现对不上，而且纯字符串方案有两个硬伤：

* 「未分类」不等于「混合型」。一家名字不在名单里的重资产公司会被错分，而
  六维阈值是按资产类型分档的，分错一档，六个维度全部偏移。
* 行业名是滞后信息。一家公司从代工转成自有品牌，名字可能没变，但资产
  结构已经翻转了；而我们手上的年报数据是刚披露的。

实测两个极端样本（都是刚跑通链路抽出来的合并口径数）：

    茅台 600519（白酒）    固定资产/总资产 7.40%    有形/总资产 28.43%
    宝钢 600019（钢铁）    固定资产/总资产 40.99%   有形/总资产 54.53%

**注意两个判据并不总是互相印证。** 宝钢上一致（都 heavy），茅台上却打架：
固定资产占比 7.40% 判 light，有形占比 28.43% 判 mixed —— 因为白酒的存货
（基酒）常年占总资产两成，把有形占比抬进了混合区间，但它的轻资产属性是
品牌而非设备。

所以这里明确**以固定资产占比为主判据**（更直接回答「重不重」），有形占比
作为辅助信号记录下来，并在两者不一致时把分歧显式暴露 —— 而不是静默取
一个，或假装它们一致。

行业名不是丢掉而是降级为**校验**：财务比例说了算，行业名用来检查是否
打架。不一致时如实返回 disagreement，而不是静默选一个 —— 打架本身是
值得看的信号（可能意味着行业归类错了，或资产结构正在转型）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .store import SCOPE_CONSOLIDATED  # noqa: F401  （供调用方统一从 store 取常量）

ASSET_HEAVY = "heavy"
ASSET_MIXED = "mixed"
ASSET_LIGHT = "light"
ASSET_UNKNOWN = "unknown"

#: 固定资产/总资产 的分界（%）。
#:
#: 7.40%（白酒）与 40.99%（钢铁）之间就是混合型的活动区间。25% 取自
#: halo-skill 的 fixed_share 阈值最高档（固定资产占总资产 ≥25% 得 5 分）——
#: 与既有评分口径对齐，而不是另发明一个数。
FIXED_ASSET_RATIO = ((25.0, ASSET_HEAVY), (10.0, ASSET_MIXED))

#: 有形资产（固定+在建+存货）/总资产 的分界（%）。
#:
#: 比上一条更贴近「重不重」的本意：钢铁的存货占比可以很低，但固定资产
#: 极重；反过来一家存货极重的流通企业固定资产占比可能不高。两条判据
#: 一起看，能同时覆盖「设备重」和「存货重」两种重资产形态。
TANGIBLE_RATIO = ((40.0, ASSET_HEAVY), (15.0, ASSET_MIXED))

#: 行业名回落名单。保留 halo-skill 的原表（它是对的，只是不该当主判据）。
HEAVY_INDUSTRIES = (
    "钢铁", "煤炭", "石油石化", "建筑材料", "建筑装饰", "银行", "房地产",
    "公用事业", "交通运输", "有色金属", "基础化工", "采掘", "非银金融",
)
LIGHT_INDUSTRIES = (
    "计算机", "传媒", "通信", "电子", "食品饮料", "白酒", "休闲服务",
    "商业贸易", "纺织服装", "综合", "软件", "互联网", "游戏", "影视",
)


def _bucket(value: float, table) -> str:
    for boundary, label in table:
        if value >= boundary:
            return label
    return ASSET_LIGHT


def classify_by_industry_name(industry: Optional[str]) -> Optional[str]:
    """行业名匹配，命中返回类型，未命中返回 None（**不**默认 mixed）。

    与 halo-skill 的差别：那里未命中直接给 mixed，于是「没在名单里」被
    当成了「是混合型」这个事实断言。这里如实返回"不知道"。
    """
    if not industry:
        return None
    name = industry.strip()
    # 重资产优先：同一家公司可能同时命中两个名单（如「有色金属」也含
    # 「材料」），与 halo-skill 保持同样的优先级。
    for token in HEAVY_INDUSTRIES:
        if token in name:
            return ASSET_HEAVY
    for token in LIGHT_INDUSTRIES:
        if token in name:
            return ASSET_LIGHT
    return None


def classify(
    *,
    fixed_assets: Optional[float] = None,
    construction_in_progress: Optional[float] = None,
    inventory: Optional[float] = None,
    total_assets: Optional[float] = None,
    industry: Optional[str] = None,
) -> Dict[str, Any]:
    """判定资产类型。

    Returns:
        ``asset_type`` / ``basis`` / ``disagreement`` / ``signals``

        ``basis`` 取值：
          ``fixed_asset_ratio``    以固定资产占比判定
          ``tangible_ratio``       固定资产占比缺失，退到有形资产占比
          ``industry_name``        财务数据缺失且行业名命中
          ``insufficient_data``    财务数据缺失且行业名未命中 —— 此时
                                   ``asset_type`` 为 ``unknown``，调用方
                                   **不得**按 mixed 继续评分
        ``disagreement`` 为 True 表示财务比例与行业名判定打架，如实暴露
        给调用方而不是静默取一个。
    """
    signals: Dict[str, Any] = {"industry": industry}

    total = total_assets if total_assets and total_assets > 0 else None
    signals["total_assets"] = total

    ratio_type: Optional[str] = None
    tangible_type: Optional[str] = None
    basis = "insufficient_data"

    if total:
        if fixed_assets is not None:
            fixed_pct = fixed_assets / total * 100.0
            signals["fixed_asset_pct"] = fixed_pct
            ratio_type = _bucket(fixed_pct, FIXED_ASSET_RATIO)
            basis = "fixed_asset_ratio"
        tangible = None
        parts = [fixed_assets, construction_in_progress, inventory]
        if all(p is not None for p in parts):
            tangible = sum(parts)  # type: ignore[arg-type]
        elif fixed_assets is not None and inventory is not None:
            # 在建工程允许缺失：它通常只占 1–3%，缺了会轻微低估有形资产，
            # 但比因为一个次要科目就让整条判据失效要好。
            tangible = fixed_assets + inventory
        if tangible is not None:
            tangible_pct = tangible / total * 100.0
            signals["tangible_pct"] = tangible_pct
            signals["tangible_composition"] = "full" if all(
                p is not None for p in parts
            ) else "partial"
            tangible_type = _bucket(tangible_pct, TANGIBLE_RATIO)

    # 两条判据打架时不静默取一个：主判据（固定资产占比）的结论照用，但把
    # 辅助判据的结论一并带出去，让调用方知道「这家公司有形资产占比偏高但
    # 设备占比低」—— 这种公司通常是存货驱动的，不是设备驱动的。
    if ratio_type is not None and tangible_type is not None:
        signals["tangible_bucket"] = tangible_type
        signals["ratio_disagreement"] = ratio_type != tangible_type

    name_type = classify_by_industry_name(industry)
    signals["industry_match"] = name_type

    if ratio_type is not None:
        asset_type = ratio_type
    elif name_type is not None:
        asset_type = name_type
        basis = "industry_name"
    else:
        asset_type = ASSET_UNKNOWN
        basis = "insufficient_data"

    disagreement = bool(ratio_type and name_type and ratio_type != name_type)

    return {
        "asset_type": asset_type,
        "basis": basis,
        "disagreement": disagreement,
        "signals": signals,
    }


def rating_5(score: float) -> str:
    """HALO 六维评级（5 分制）。"""
    if score >= 4.0:
        return "极强"
    if score >= 3.0:
        return "强"
    if score >= 2.0:
        return "中等"
    return "弱"


def rating_10(score: float) -> str:
    """10 分制维度评级。"""
    if score >= 8.0:
        return "极强"
    if score >= 6.5:
        return "强"
    if score >= 5.0:
        return "中等"
    return "弱"
