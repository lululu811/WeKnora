"""
定期报告「前十名持有人」抽取 — 规则层，不碰网络与文件。

为什么规则抽取
--------------
与 halo 年报链路同一套理由：LLM 读 PDF 表格会偶发改数（份额 189.15 亿 → 18.915 亿
这类十倍错位很难事后发现），而这一列的价值全在数字准。表格结构在跨管理人
版式上不稳定，但「锚点标题 → 逐行数值」是可正则定位的，所以走规则。

不变式（沿用 halo）
------------------
* **数字只能来自原文**，本模块不做任何推算、缩放或补齐。
* **对不上的标 disputed 并保留原始值**，绝不"修正"成看起来自洽的数字。
* 抽取不到就返回空列表，由调用方回固定 shape —— 绝不抛异常给端点。

锚点策略
--------
基金季报的章节标题在各家管理人手里写法不一，实测可命中这几种：
    「前十名持有人」/「前十大持有人」/「前十名基金份额持有人」
表头可能是「序号 / 持有人名称 / 持有份额（份）/ 占总份额比例」。
表格**跨页断裂**时下一页没有表头，本模块按"锚点之后、下一个锚点之前"的
文本块整体解析，因此断裂不影响抽取（这正是用锚点而非固定页码的理由）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 章节锚点。**顺序有意义**：更长的写法在前，避免「前十名持有人」被
# 「前十大持有人」的模糊匹配吃掉（虽然二者不等价，但先长后短更稳）。
ANCHOR_PATTERNS = (
    r"前十名(?:基金份额)?持有人",
    r"前十大(?:基金份额)?持有人",
    r"前十名股东",
)

# 段落终止锚点：命中即认为表格结束。没有终止锚点时会把后面章节的表格
# 一起吞进来，抽出根本不在这张表里的持有人。
STOP_PATTERNS = (
    r"第[一二三四五六七八九十]+节",
    r"\d+\.\s*\S+报告",
    r"备查文件",
    r"基金管理人承诺",
)

_NUM = r"[\d,，]+(?:\.\d+)?"
# 数值 token。**不要求带 %**：pypdf 文本层里百分号只出现在表头
# （「占总份额比例（%）」），数据行是裸数字 "18.75"。要求带 % 会让整张表
# 一行都匹配不上 —— 这正是实测踩到的坑。
_NUM_RE = re.compile(_NUM)
_PCT_RE = re.compile(rf"{_NUM}\s*%")
# 持有份额：允许 "1,234,567,890.00" / "12.34亿" / "-" 三种形态。
_SHARE_RE = re.compile(rf"(?P<share>{_NUM})\s*(?P<unit>亿|万)?")

# 表头行：出现即从它之后开始收数据行。
_HEADER_RE = re.compile(r"(持有人名称|持有人|持有份额|份额数量|占总份额|占总资产)")


def find_anchor_span(text: str) -> Optional[tuple]:
    """在整篇文本里定位「前十名持有人」章节的字符区间。

    Returns:
        `(start, end)`，end 为下一个章节锚点位置；找不到返回 None。
    """
    if not text:
        return None
    start = None
    for pat in ANCHOR_PATTERNS:
        m = re.search(pat, text)
        if m and (start is None or m.start() < start):
            start = m.start()
    if start is None:
        return None

    end = len(text)
    for pat in STOP_PATTERNS:
        m = re.search(pat, text[start + 4:])
        if m:
            end = min(end, start + 4 + m.start())
    return (start, end)


def _to_share(raw: str, unit: Optional[str]) -> Optional[float]:
    """'1,234.5' + '亿' → 123450000000.0。

    单位缺失时**不猜**：ETF 报告表的份额列单位要么在表头声明、要么列名带
    （亿份）。这里只在 unit 明确给出时缩放，否则原样返回，由调用方决定可信度。
    """
    try:
        val = float(str(raw).replace(",", "").replace("，", ""))
    except (TypeError, ValueError):
        return None
    if unit == "亿":
        return val * 1e8
    if unit == "万":
        return val * 1e4
    return val


def _is_data_line(line: str) -> bool:
    """一行是不是数据行：至少两个数值 token，或带 %。

    门槛设成"两个数值"是因为名称里可能本身带数字（"上证50ETF"），
    单个数值不足以判定是数据行。
    """
    return len(_NUM_RE.findall(line)) >= 2 or bool(_PCT_RE.search(line))


def parse_rows(text: str) -> List[Dict[str, Any]]:
    """解析「前十名持有人」章节文本 → 持有人行列表。

    逐行识别：含「份额 + 占比」两个数值的数据行。**按位置取数** —— 行内最后
    一个数值是占比、倒数第二个是份额，它之前的部分就是名称。这样同时扛住
    pypdf 的空格拆分和「上证**50**ETF」这类名称里本身带数字的情形（按列位置
    或"取第一个数值"都会把名称腰斩）。

    序号行容忍 `1. ` / `1、` / `1 ` 三种分隔（PDF 文本层三种都见过）。

    Returns:
        `[{holder_name, hold_share, hold_pct, row_no}]`；解析不到返回空列表。
    """
    if not text:
        return []

    # 先切出锚点区间，避免把别处的表格收进来。
    span = find_anchor_span(text)
    if span is None:
        return []
    body = text[span[0]:span[1]]

    rows: List[Dict[str, Any]] = []
    current: Optional[str] = None

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 跨页断裂时下一页会重复表头，跳过即可，不清空 current。
        if _HEADER_RE.search(line):
            continue

        m = re.match(r"^(\d{1,2})\s*[.、)）]?\s+(\S.*)$", line)
        if m:
            # 序号行：整段余下部分就是「名称 … 份额 … 占比」
            line = m.group(2)
            current = line
        elif current is not None and not _is_data_line(line):
            # 数值不足的短片段 → 名称被跨行拆开，拼到当前名称后面。
            if len(line) <= 40:
                current = f"{current} {line}"
                line = ""

        if not line or not _is_data_line(line):
            continue

        tokens = list(_NUM_RE.finditer(line))
        if len(tokens) < 2:
            current = None
            continue

        pct_tok = tokens[-1]
        share_tok = tokens[-2]
        share = _to_share(share_tok.group(0), _unit_after(line, share_tok.end()))
        name = line[: share_tok.start()].strip(" \t:：,，。.-—　")

        if not name or share is None:
            # 切不出名称/份额就跳过，**不填 0**。
            current = None
            continue

        rows.append(
            {
                "holder_name": name,
                "hold_share": share,
                "hold_pct": float(pct_tok.group(0).replace(",", "").replace("，", "")),
                "row_no": len(rows) + 1,
            }
        )
        current = None

    return rows


def _unit_after(line: str, pos: int) -> Optional[str]:
    """数值 token 后面紧跟的量纲字（亿/万），没有返回 None。"""
    tail = line[pos:pos + 1]
    return tail if tail in {"亿", "万"} else None


def extract_holders(
    text: str,
    thscode: str,
    report_period: str,
    total_shares: Optional[float] = None,
    source_title: Optional[str] = None,
    tolerance: float = 5.0,
) -> List[Dict[str, Any]]:
    """从整篇 PDF 文本抽出持有人行，并打 status。

    Args:
        total_shares: 同报告期的份额总量，用于交叉验证。None → 全部 pending。

    Returns:
        `[{thscode, report_period, holder_name, hold_share, hold_pct, status, ...}]`
    """
    from finance_panel.etf_flow import is_huijin, reconcile_status

    parsed = parse_rows(text)
    out: List[Dict[str, Any]] = []
    for row in parsed:
        # 占比与份额总量对不上 → disputed，**原始值原样保留**。
        status = reconcile_status(
            row.get("hold_pct"), row.get("hold_share"), total_shares, tolerance
        )
        out.append(
            {
                "thscode": thscode,
                "report_period": report_period,
                "holder_name": row["holder_name"],
                "hold_share": row.get("hold_share"),
                "hold_pct": row.get("hold_pct"),
                "status": status,
                "is_huijin": is_huijin(row["holder_name"]),
                "source_title": source_title,
            }
        )
    return out