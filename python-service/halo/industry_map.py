"""行业分类（本地 index 库）——替代 halo-skill 的行业名字符串匹配。

为什么换源
----------
halo-skill 用「行业名里含某个词」判定重/混合/轻资产。未命中就默认 mixed。
问题有三：未分类不等于混合型（而 mixed 有自己一套阈值，分错一档六维全偏）；
行业名是滞后信息；它自己的文档写的是「固定资产占总资产比例 + 行业特性」，
实现却只做了字符串匹配。

本模块提供**第二判据**：行业归属本身。它不替代财务比例（那是
:mod:`halo.industry` 的主判据），而是给分析者一个可核查的行业标签，以及
一个「这家公司的主营是否与它的行业标签相符」的交叉验证点。

数据结构
--------
``index.v_index_constituents``（行业成分）× ``v_index_universe``（tag 标记）
提供两级归属，实测覆盖 100%（5572 / 5571 只股票）：

    宝钢 600019  钢铁(881112, 44 只) + 普钢(884052, 22 只)
    中芯 688981  半导体(881121, 189 只) + 集成电路制造(884227, 7 只)
    茅台 600519  白酒(881273, 19 只)              —— 只到一级

层级判定用**指数编码前缀**（881 = 一级 / 884 = 二级），成分数作为交叉印证
而非判据 —— 成分数会随指数调样变动，拿它当判据等于埋一颗定时炸弹。

前视偏差（已知限制，如实标注）
------------------------------
``v_index_constituents`` 只有 (index_thscode, thscode, ticker, name)，
**没有 in_date / out_date**，因此无法回答「这只股票在 2023 年属于哪个行业」。
用今天的行业归属解释历史财务数据，对转型公司是错的（例如从面板代工转半导体的
公司，用当前半导体标签解释它做面板那年的数据）。

本模块只把这件事**显式化**（返回 ``as_of_note``），不假装没有这个问题。要彻底
解决需要申万行业变迁史这类带生效日期的数据源。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 指数编码前缀 → 层级。同花顺行业指数库里 881 段是一级行业、884 段是二级。
_LEVEL_PREFIX = {"881": 1, "884": 2}

#: 成分数上限的粗略上限，仅用于「两个归属谁更细」的兜底判断（编码前缀缺失时）。
_FINE_LEVEL_HINT_MAX_MEMBERS = 40


async def get_industry_map(
    src: Any, thscode: str
) -> Dict[str, Any]:
    """查一只股票的行业归属。

    Args:
        src: 满足 ``AsyncSQLExecutor`` 的对象（``datasources.registry.get("index")``）。

    Returns:
        ``level1`` / ``level2`` / ``memberships`` / ``basis`` / ``as_of_note``。
        查不到时 ``level1`` 为 None，调用方应据此跳过行业相关判断而不是猜。
    """
    empty = {
        "level1": None,
        "level2": None,
        "memberships": [],
        "basis": "not_found",
        "as_of_note": "index 库无该股票的行业归属",
    }
    if src is None:
        return {**empty, "as_of_note": "index 数据源未就绪"}

    sql = """
    SELECT u.thscode AS index_code, u.name AS industry_name,
           (SELECT COUNT(*) FROM v_index_constituents c2
             WHERE c2.index_thscode = u.thscode) AS members
    FROM v_index_constituents c
    JOIN v_index_universe u ON c.index_thscode = u.thscode
    WHERE c.thscode = ? AND u.tag = 'industry'
    ORDER BY members DESC
    """
    try:
        rows = await src.execute(sql, [thscode])
    except Exception as exc:  # noqa: BLE001
        logger.warning("查行业归属失败 %s: %s", thscode, exc)
        return {**empty, "as_of_note": f"查询失败：{exc}"}

    if not rows:
        return empty

    memberships: List[Dict[str, Any]] = []
    for r in rows:
        code = str(r.get("index_code") or "")
        prefix = code.split(".")[0][:3]
        level = _LEVEL_PREFIX.get(prefix)
        if level is None:
            # 编码不在已知段位里：退回「成分少者更细」的启发式，并标注来源
            level = 2 if (r.get("members") or 0) <= _FINE_LEVEL_HINT_MAX_MEMBERS else 1
        memberships.append({
            "industry": r.get("industry_name"),
            "index_code": code,
            "members": r.get("members"),
            "level": level,
            "level_source": "code_prefix" if prefix in _LEVEL_PREFIX else "members_heuristic",
        })

    l1 = next((m["industry"] for m in memberships if m["level"] == 1), None)
    l2 = next((m["industry"] for m in memberships if m["level"] == 2), None)
    # 只有一级归属时，若它其实很细（如「白酒」19 只），把一级同时当作细分暴露出去，
    # 免得调用方以为「没有二级」就等于「不知道更细的分类」。
    effective_fine = l2 or (l1 if len(memberships) == 1 else None)

    return {
        "level1": l1,
        "level2": l2,
        "fine": effective_fine,
        "memberships": memberships,
        "basis": "local_index_constituent",
        "as_of_note": (
            "成分表无 in_date/out_date，只能反映**当前**归属；"
            "用当前行业解释历史财务存在前视偏差，转型公司尤甚。"
        ),
    }


async def get_concept_tags(src: Any, thscode: str, limit: int = 20) -> List[str]:
    """概念/地域标签（``tag='cn_concept'``）。

    这些是题材标签，给 AI 判断「公司当前被市场归为什么叙事」用；它们**不能**
    当基本面依据 —— 概念指数成分会随行情热炒增删。
    """
    if src is None:
        return []
    sql = """
    SELECT u.name
    FROM v_index_constituents c
    JOIN v_index_universe u ON c.index_thscode = u.thscode
    WHERE c.thscode = ? AND u.tag IN ('cn_concept', 'region')
    ORDER BY u.name
    LIMIT ?
    """
    try:
        rows = await src.execute(sql, [thscode, limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("查概念标签失败 %s: %s", thscode, exc)
        return []
    return [r["name"] for r in rows if r.get("name")]


async def get_anomaly_narratives(
    src: Any, thscode: str, limit: int = 5, min_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """异动归因文本（``special.v_anomaly_list``）。

    这批文本的形态是「行业原因：…公司原因：…」，是本地已有的、**成稿质量**的
    叙述素材，正好是定性维度（资金面、风险、情绪）缺的输入。不引外网就有。
    """
    if src is None:
        return []
    sql = """
    SELECT capture_date, tag_name, analysis_content
    FROM v_anomaly_list
    WHERE thscode = ? AND analysis_content IS NOT NULL
    ORDER BY capture_date DESC
    LIMIT ?
    """
    try:
        rows = await src.execute(sql, [thscode, limit])
    except Exception as exc:  # noqa: BLE001
        logger.warning("查异动归因失败 %s: %s", thscode, exc)
        return []
    return [
        {
            "date": str(r.get("capture_date") or ""),
            "tag": r.get("tag_name"),
            "content": (r.get("analysis_content") or "").strip(),
        }
        for r in rows
        if (r.get("analysis_content") or "").strip()
    ]
