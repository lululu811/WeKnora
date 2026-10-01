"""分红与股东户数抽取（年报侧）。

口径陷阱（实测茅台 2025 / 宝钢 2024 得到）
----------------------------------------
同一份年报里，「每股分红」和「分红总额」**可能不是同一口径**：

    茅台  2025 年度累计派发现金红利 650.33 亿元   ← 全年累计，含中期分红
          每股派发现金红利 27.993 元              ← 仅年度部分
          （27.993 × 12.52 亿股 = 350 亿 ≠ 650 亿）

    宝钢  2024 年度每股现金分红 0.21 元 / 分红总额 45.16 亿  ← 口径一致
          且年报直接给出分红率 61.34%

所以：

* ``dividend_per_share`` 与 ``dividend_total`` 分开存，**不做一致性校验**
  （校验会逼着人把两者当成同口径，反而诱导算错）；
* ``payout_ratio`` **只从证监会标准表里取**，不用「总额 ÷ 归母净利润」反算 ——
  茅台那种混合口径下反算会得到 76%，看起来合理但它回答的是另一个问题。

茅台没有那张标准表（只有叙述性段落），因此它的 ``payout_ratio`` 应当是**缺失**，
而不是用估算值补上。这符合整体的数据铁律。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .extractor import EXTRACT_RULE
from .pdf_extract import ExtractResult
from .store import SCOPE_CONSOLIDATED, STATUS_PENDING

# 证监会《格式准则第 2 号》要求的近 3 年现金分红表。宝钢 2024 原文：
#   1 每股现金分红（含税）(元) 0.21 0.10 0.11 0.31 0.28
#   2 现金分红总额（亿元） 45.16 21.50 23.66 67.78 62.07
#   6 现金分红总额占合并报表归属于|母公司股东的净利润比例（%） 61.34 76.33 52.05 …
# 表格在 PDF 文本层里会跨行，所以模式允许换行；只取**第一列**（当年预计）。
_TBL_DIV_PS = re.compile(
    r"每股现金分红\s*[（(]?\s*含税\s*[)）]?[^0-9\n]{0,8}[（(]\s*元\s*[)）]\s*[\s|]*"
    r"(\d+(?:\.\d+)?)"
)
_TBL_DIV_TOTAL_YI = re.compile(
    r"现金分红总额\s*[（(]\s*亿元\s*[)）]\s*[\s|]*(\d+(?:\.\d+)?)"
)
_TBL_PAYOUT = re.compile(
    r"母公司股东的净利润\s*比例\s*[（(]\s*%\s*[)）]\s*[\s|]*(\d+(?:\.\d+)?)"
)

# 叙述性回退（茅台型：没有标准表）
_DIV_PS_NARRATIVE = re.compile(r"每股派发现金红利\s*([\d,.]+)\s*元")
_DIV_TOTAL_YI_NARRATIVE = re.compile(r"累计派发现金红利\s*([\d,.]+)\s*亿元")
_DIV_TOTAL_YUAN_NARRATIVE = re.compile(r"(?:预计)?分红总额\s*([\d,.]+)\s*元")

# 股东户数。两条都抽：期末值是年报口径，披露前一月末值与本地缓存的行情快照
# 时点不同，两个一起留着，调用方能判断用的是哪一期。
_HOLDER_PERIOD_END = re.compile(
    r"截至报告期末普通股股东总数\s*[（(]?\s*户\s*[)）]?\s*[\s|]*([\d,]+)"
)
_HOLDER_PREV_MONTH = re.compile(
    r"上一月末的普通股股东总数\s*[（(]?\s*户\s*[)）]?\s*[\s|]*([\d,]+)"
)

logger = logging.getLogger(__name__)

_YI = 1e8  # 亿元 → 元

#: 主营构成表的单位声明。年报在同一张表里可能用元 / 千元 / 万元 / 百万元 /
#: 亿元（实测茅台 2025 用元、宝钢 2024 用百万元），不换算就是 1e6 量级的错。
_UNIT_RE = re.compile(r"单位\s*[：:]\s*(元|千元|万元|百万元|亿元)")
_UNIT_SCALE = {
    "元": 1.0, "千元": 1e3, "万元": 1e4, "百万元": 1e6, "亿元": 1e8,
}

#: 绝不可能是「业务分部名」的词。利润表 / 现金流量表与主营构成表常同页出现
#: （宝钢 2024 就在一页上），不加这道闸门会把「销售费用」「研发费用」
#: 「经营活动产生的现金流量净额」当成业务分部抽出去。
_SEGMENT_NAME_BLOCKLIST = (
    "合计", "总计", "分行业", "分产品", "分地区", "单位", "小计",
    "营业收入", "营业成本", "营业总收入", "营业总成本", "营业外收入", "营业外支出",
    "销售费用", "管理费用", "研发费用", "财务费用", "税金及附加", "资产减值损失",
    "信用减值损失", "投资收益", "公允价值变动", "资产处置收益", "其他收益",
    "营业利润", "利润总额", "所得税费用", "净利润", "归属于母公司", "少数股东损益",
    "基本每股收益", "稀释每股收益", "综合收益总额",
    "经营活动", "投资活动", "筹资活动", "现金及现金等价物", "汇率变动",
)


def _f(tok: Optional[str]) -> Optional[float]:
    if tok is None:
        return None
    try:
        return float(tok.replace(",", ""))
    except ValueError:
        return None


def _rec(
    field: str,
    value: float,
    unit: str,
    page: int,
    raw: str,
    *,
    value_text: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "field": field,
        "value": value,
        "value_text": value_text,
        "unit": unit,
        "scope": SCOPE_CONSOLIDATED,
        "source_page": page,
        "raw_text": raw[:500],
        "extract_by": EXTRACT_RULE,
        "status": STATUS_PENDING,
        "confidence": 1.0,
    }


def _search(pages: ExtractResult, pattern: re.Pattern) -> List[tuple]:
    """(页码, 匹配文本, 捕获组) —— 第一个命中即止。"""
    for page in pages.pages:
        if not page.text:
            continue
        m = pattern.search(page.text)
        if m:
            return [(page.page, m.group(0), m.group(1))]
    return []


def extract_shareholder_facts(pages: ExtractResult) -> List[Dict[str, Any]]:
    """股东户数。"""
    out: List[Dict[str, Any]] = []
    for field, pat in (
        ("holder_count", _HOLDER_PERIOD_END),
        ("holder_count_prior", _HOLDER_PREV_MONTH),
    ):
        hit = _search(pages, pat)
        if not hit:
            continue
        page_no, raw, tok = hit[0]
        val = _f(tok)
        if val is not None:
            out.append(_rec(field, val, "count", page_no, raw))
    return out


def extract_dividend_facts(pages: ExtractResult) -> List[Dict[str, Any]]:
    """每股分红、分红总额、分红率。

    分红率只从标准表取（见模块 docstring 的口径说明）；茅台那种只有叙述性
    段落的年报会缺这一项，如实缺失而不是反算。
    """
    out: List[Dict[str, Any]] = []
    has_table = False

    hit = _search(pages, _TBL_DIV_PS)
    if hit:
        has_table = True
        page_no, raw, tok = hit[0]
        val = _f(tok)
        if val is not None:
            out.append(_rec("dividend_per_share", val, "CNY/share", page_no, raw))

    hit = _search(pages, _TBL_DIV_TOTAL_YI)
    if hit:
        has_table = True
        page_no, raw, tok = hit[0]
        val = _f(tok)
        if val is not None:
            out.append(_rec("dividend_total", val * _YI, "CNY", page_no, raw))
    elif not has_table:
        # 叙述性回退：优先「累计派发…亿元」，其次「分红总额…元」
        for pat, scale in ((_DIV_TOTAL_YI_NARRATIVE, _YI), (_DIV_TOTAL_YUAN_NARRATIVE, 1.0)):
            hit = _search(pages, pat)
            if not hit:
                continue
            page_no, raw, tok = hit[0]
            val = _f(tok)
            if val is not None:
                out.append(_rec("dividend_total", val * scale, "CNY", page_no, raw))
            break

    if not has_table:
        hit = _search(pages, _DIV_PS_NARRATIVE)
        if hit:
            page_no, raw, tok = hit[0]
            val = _f(tok)
            if val is not None:
                out.append(_rec("dividend_per_share", val, "CNY/share", page_no, raw))

    hit = _search(pages, _TBL_PAYOUT)
    if hit:
        page_no, raw, tok = hit[0]
        val = _f(tok)
        if val is not None:
            # 百分数 → 比率，与本地 DuckDB 的口径一致
            out.append(_rec("payout_ratio", val / 100.0, "ratio", page_no, raw))

    return out


def extract_business_segments(pages: ExtractResult) -> List[Dict[str, Any]]:
    """主营业务构成表（分行业/分产品/分地区）。

    这是产业链定位最可靠的输入 —— 真实营收结构 + 毛利率，比「经营范围里有没有
    『制造』两个字」这种关键词匹配强一个量级。

    年报里是长表，PDF 文本层每行形如：
        酒系列  17,000,000,000.00  3,000,000,000.00  82.35  -1.21  -0.85  1.23

    **必须读表头的单位**。实测茅台 2025 是「单位：元」、宝钢 2024 是
    「单位：百万元」—— 同类表、不同量纲。不读单位会把宝钢 322,116 百万元
    （=3221.16 亿）当成 32 万元，差 1e6 倍；这种错 AI 完全看不出来，因为
    数字本身「像」个金额，还会带着负毛利率这种荒谬但格式完好的结果。

    列含义随公司而变（有的没有同比列），所以这里只取**前两列**（收入、
    成本），并把整行原文留在 raw_text 里供 AI 读 —— 让 AI 看原文比让正则
    猜列语义可靠。
    """
    out: List[Dict[str, Any]] = []
    for page in pages.pages:
        if not page.text:
            continue
        if "主营业务分行业" not in page.text and "主营业务分产品" not in page.text:
            continue

        # 单位以本页表头为准。找不到就**不抽** —— 宁可缺一条分部数据，
        # 也不能给出量纲错误的数据（那比没有更危险）。
        unit_m = _UNIT_RE.search(page.text)
        if not unit_m:
            logger.info("p%d 主营构成表未找到单位声明，跳过该页分部抽取", page.page)
            continue
        scale = _UNIT_SCALE.get(unit_m.group(1))
        if not scale:
            continue

        # 从「主营业务分行业/分产品情况」这个表头**之后**才开始收行。
        # 宝钢 2024 的年报把利润表、现金流量表跟主营构成表放在同一页上，
        # 不锚定表头就会把「销售费用」「研发费用」「经营活动产生的现金流量净额」
        # 一并当成业务分部抽出来 —— 数字看着正常，语义完全错。
        lines = page.text.split("\n")
        # 四张表是**同一个总量的四种切分**，必须按表分组：
        #   主营业务分行业情况   酒类      1687.75 亿
        #   主营业务分产品情况   茅台酒    1465.00 亿
        #   主营业务分地区情况   国内      1639.24 亿
        #   主营业务分销售模式   直销       845.43 亿
        # 把它们平铺成一张列表，读者会以为这是四个独立业务（加起来 5600 亿），
        # 实际上每种切分的合计都 ≈ 总营收。dimension 字段告诉下游这是哪一刀。
        # 找出本页所有表头，逐表处理。四张表是**同一个总量的四种切分**：
        #   主营业务分行业情况   酒类      1687.75 亿
        #   主营业务分产品情况   茅台酒    1465.00 亿
        #   主营业务分地区情况   国内      1639.24 亿
        #   主营业务分销售模式   直销       845.43 亿
        # 平铺成一张列表会让读者以为这是四个独立业务（茅台：误加得到 5600 亿），
        # 实际上每种切分的合计都 ≈ 总营收。dimension 字段标记这是哪一刀。
        headers = [
            (m.start(), m.group(1))
            for m in re.finditer(r"主营业务分(行业|产品|地区|销售模式)情况", page.text)
        ]
        if not headers:
            continue
        lines = page.text.split("\n")
        # 把字符偏移映射到行号，逐表切段
        offsets, acc = [], 0
        for ln in lines:
            offsets.append(acc)
            acc += len(ln) + 1

        def line_of(pos: int) -> int:
            import bisect
            return max(0, bisect.bisect_right(offsets, pos) - 1)

        for idx, (pos, dimension) in enumerate(headers):
            from_line = line_of(pos) + 1
            to_line = line_of(headers[idx + 1][0]) if idx + 1 < len(headers) else len(lines)
            for line in lines[from_line:to_line]:
                if re.match(r"^\s*(公司业务|说明|注[：:]|其他说明)", line):
                    break
                m = re.match(
                    r"^\s*([^\s|]{2,20}?)\s+((?:[\d,]+\.\d{2})|(?:[\d,]{4,}))\s+"
                    r"((?:[\d,]+\.\d{2})|(?:[\d,]{4,}))\s*(.*)$",
                    line,
                )
                if not m:
                    continue
                name, rev_tok, cost_tok, _rest = m.groups()
                if any(k in name for k in _SEGMENT_NAME_BLOCKLIST):
                    continue
                rev, cost = _f(rev_tok), _f(cost_tok)
                if rev is None or rev <= 0:
                    continue
                rev *= scale
                if cost is not None:
                    cost *= scale
                    # 成本不可能远高于收入；越界说明列错位了，丢掉而不是产出负毛利
                    if cost > rev * 3 or cost < 0:
                        cost = None
                out.append(_rec(f"segment_revenue__{dimension}", rev, "CNY",
                                page.page, line, value_text=name))
                if cost is not None:
                    out.append(_rec(f"segment_cost__{dimension}", cost, "CNY",
                                    page.page, line, value_text=name))
                    out.append(_rec(
                        f"segment_gross_margin__{dimension}",
                        round((rev - cost) / rev, 6), "ratio",
                        page.page, line, value_text=name,
                    ))
    return out


def extract_finance_facts(pages: ExtractResult) -> Dict[str, Any]:
    """本轮新增的股东/分红/主营构成三类。"""
    facts = extract_shareholder_facts(pages) + extract_dividend_facts(pages)
    segments = extract_business_segments(pages)
    return {
        "facts": facts,
        "segments": segments,
        "has_business_breakdown": bool(segments),
    }
