"""
HALO 年报事实抽取 — 规则通道 + LLM 通道 + 双通道比对

为什么是双通道
--------------
两条通道的失效模式恰好相反：

* **规则通道**确定、可复现、可审计，但脆。年报 PDF 的版式每年都在变，
  一旦科目名或列序变了就静默抽不到。
* **LLM 通道**扛版式变化，但它是概率性的，可能把「年初余额」当成「期末余额」，
  也可能读串行——而且**它读错的时候不会报错**。

单走任何一条都违背 HALO 铁律：前者是「默默丢数据」，后者是「默默造数据」。
两通道独立产出、互不知道对方存在，比对之后才决定这条事实能不能信。

比对结论直接映射到 ``store.STATUS_*``：

===================  ==========================================
状态                 含义
===================  ==========================================
``verified``         两通道一致（相对误差 <= 1e-6）
``disputed``         两通道都有数但对不上，待人工回 ``source_page`` 裁决
``pending``          只有一路产出（另一路没抽到）
===================  ==========================================

**只有 ``verified`` 能进评分公式**，``disputed``/``pending`` 一律当缺失处理。

三个最容易踩的坑（本模块逐个处理了）
------------------------------------
1. **单位不归一就比对 = 假冲突。**「1.2 万元」和「12000 元」会被判成不一致，
   白白把一条本来正确的记录打成 ``disputed``。所以 :func:`merge_channels`
   在比对**之前**强制把所有金额折算到「元」。
2. **资产负债表每个科目有两列**（期末余额、年初余额）。取错列不会报错，
   只会安静地给你上一期的数。本模块一律取**科目名之后的第一个数字**（期末余额）。
3. **合并报表和母公司报表科目同名但数字不同。** 只按科目名正则全局匹配、
   命中就返回，必然有一半是母公司口径。所以抽取全程携带
   ``scope`` 上下文（由表标题切换），而不是「找到第一个就返回」。
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from .store import (
    EXTRACT_LLM,
    EXTRACT_RULE,
    SCOPE_CONSOLIDATED,
    SCOPE_PARENT,
    STATUS_DISPUTED,
    STATUS_PENDING,
    STATUS_VERIFIED,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 单位
# ---------------------------------------------------------------------------
# 落库的 value 一律是「元」。unit 列保留**原文声明的单位**而不是折算后的，
# 这样人工回原文核对时能一眼确认自己没有误解量纲。
UNIT_CNY = "CNY"            # 元
UNIT_CNY_1K = "CNY_1K"      # 千元
UNIT_CNY_10K = "CNY_10K"    # 万元
UNIT_CNY_1M = "CNY_1M"      # 百万元
UNIT_CNY_100M = "CNY_100M"  # 亿元
UNIT_PERSON = "person"      # 人数，不参与金额折算

#: 单位 -> 折算到「元」的乘数。人不在表里：人数乘任何系数都是错的。
UNIT_MULTIPLIER: Dict[str, float] = {
    UNIT_CNY: 1.0,
    UNIT_CNY_1K: 1e3,
    UNIT_CNY_10K: 1e4,
    UNIT_CNY_1M: 1e6,
    UNIT_CNY_100M: 1e8,
}

#: 年报里出现过的单位声明写法 -> 规范 unit。键必须先经过 NFKC 规范化再匹配。
_UNIT_DECLARATION: Dict[str, str] = {
    "元": UNIT_CNY,
    "千元": UNIT_CNY_1K,
    "万元": UNIT_CNY_10K,
    "百万元": UNIT_CNY_1M,
    "亿元": UNIT_CNY_100M,
}

# 「单位：人民币万元」「金额单位:元」「单位:万元」都算。必须允许单位声明内部
# 出现空白——PDF 抽取会把「金额单位」拉散成「金 额 单 位」，不放开就是整表抽不到。
# 单位声明与值之间同样可能有多个 tab。
_UNIT_RE = re.compile(
    r"(?:金\s*额)?\s*单\s*位\s*[:：]?\s*(?:人\s*民\s*币)?\s*"
    r"(千\s*元|百\s*万\s*元|万\s*元|亿\s*元|元)"
)

#: 数字 token：允许千分位、括号负数、正负号。NFKC 之后全角已变半角。
_NUMBER_RE = re.compile(
    r"\(?\s*[-+−]?\s*\d[\d,]*(?:\.\d+)?\s*\)?"
)

# ---------------------------------------------------------------------------
# 字段定义
# ---------------------------------------------------------------------------
AMOUNT = "amount"
COUNT = "count"


@dataclass(frozen=True)
class FieldSpec:
    """一个待抽字段的识别规则。

    Attributes:
        name: 落库的 ``field`` 值。
        kind: ``AMOUNT``（折算到元）或 ``COUNT``（人数，原样保留）。
        patterns: 科目名正则片段，**按顺序**先长后短，避免「固定资产」把
            「固定资产清理」也吃掉。
        exclude: 命中即放弃这一行。这些是真实年报里最常见的同前缀干扰项。
    """

    name: str
    kind: str
    patterns: Tuple[str, ...]
    exclude: Tuple[str, ...] = ()


#: 本模块负责的字段清单。刻意收窄：多认一个科目就多一份「抽错」的风险，
#: 而没用上的字段在评分阶段本来就当缺失处理。
FIELD_SPECS: Tuple[FieldSpec, ...] = (
    FieldSpec(
        "fixed_assets", AMOUNT,
        (r"固定资产",),
        # 「固定资产清理」「固定资产减值准备」是附注里的独立科目，值完全不同。
        # 母公司报表的固定资产与合并口径不同，但那是 scope 的事，这里不排除。
        (r"清理", r"减值", r"折旧", r"转固", r"在建", r"投资性房地产"),
    ),
    FieldSpec(
        "construction_in_progress", AMOUNT,
        (r"在建工程",),
        (r"减值", r"转固", r"工程物资", r"利息"),
    ),
    FieldSpec(
        "inventory", AMOUNT,
        (r"存货",),
        # 「存货跌价准备」在附注里，是备抵科目不是存货余额，两者不能混。
        (r"跌价", r"减值", r"周转", r"发出商品"),
    ),
    FieldSpec(
        "intangible_assets", AMOUNT,
        (r"无形资产",),
        (r"减值", r"摊销", r"开发支出", r"开发阶段", r"商誉"),
    ),
    FieldSpec(
        "goodwill", AMOUNT,
        (r"商誉",),
        (r"减值", r"账面", r"摊销"),
    ),
    FieldSpec(
        "total_assets", AMOUNT,
        (r"资产总计", r"资产合计"),
        # 「流动资产合计」「非流动资产合计」都含「资产合计」三个字。漏掉这个
        # 排除就会把流动资产当成总资产——数字小两个数量级但完全不会报错。
        (r"流动资产", r"非流动资产", r"总资产周转", r"资产支持"),
    ),
    FieldSpec(
        "net_profit", AMOUNT,
        (r"净利润",),
        # 归母净利润 / 少数股东损益都是「净利润」的后缀或前缀，但它们是
        # 另一个口径。DuckDB 对账规则 3 明确要求用净利润而非归母。
        (r"归属于母公司", r"归属母公司", r"归母", r"少数股东", r"扣除非经常"),
    ),
    FieldSpec(
        "employees_total", COUNT,
        (r"在职员工的数量合计", r"在职员工人数", r"员工的数量合计", r"员工总数"),
        (),
    ),
)

FIELD_NAMES: Tuple[str, ...] = tuple(s.name for s in FIELD_SPECS)
_BY_NAME: Dict[str, FieldSpec] = {s.name: s for s in FIELD_SPECS}

# ---------------------------------------------------------------------------
# scope 上下文
# ---------------------------------------------------------------------------
# 年报里「合并资产负债表」和「母公司资产负债表」科目同名、数字不同。
# 切换点就是表标题：标题是状态机，标题之后的所有行都继承该 scope。
_SCOPE_MARKERS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"合并资产负债表"), SCOPE_CONSOLIDATED),
    (re.compile(r"母公司资产负债表"), SCOPE_PARENT),
    (re.compile(r"合并利润表"), SCOPE_CONSOLIDATED),
    (re.compile(r"母公司利润表"), SCOPE_PARENT),
    (re.compile(r"合并现金流量表"), SCOPE_CONSOLIDATED),
    (re.compile(r"母公司现金流量表"), SCOPE_PARENT),
)

# ---------------------------------------------------------------------------
# 数值解析
# ---------------------------------------------------------------------------


def parse_number(token: str) -> Optional[float]:
    """把一个数字 token 解析成 float，解析不了返回 ``None``。

    支持三种负数写法中的两种（PDF 实际会出现的）：

    * ``-1,234.56``   前置负号
    * ``(1,234.56)``  会计括号负数 —— 中文年报里这才是主流写法，
      而且它不带任何符号位，靠「有没有配对的括号」判断正负
    * ``－1,234.56``  全角负号（调用方应先做 NFKC，这里一并兜底）

    解析不了就返回 ``None`` 而**不是** 0 或抛异常：抽不到是常态，
    把抽不到伪装成 0 会让「缺失」静默变成「该项为零」。
    """
    t = unicodedata.normalize("NFKC", token).strip()
    if not t:
        return None

    negative = False
    if t.startswith("(") and t.endswith(")"):
        # 括号负数。必须首尾配对，只有一侧括号说明这是残缺 token，当没抽到。
        negative = True
        t = t[1:-1].strip()
    elif t.startswith("(") or t.endswith(")"):
        return None

    t = t.replace("−", "-")
    if t.startswith("-"):
        negative = not negative
        t = t[1:].strip()
    elif t.startswith("+"):
        t = t[1:].strip()

    # 千分位必须校验分组：首段 1~3 位，后续每段恰好 3 位。
    # 无条件删逗号会把 "1,2,3" 这种明显是列错位的垃圾串解析成 123 ——
    # 那是一个看起来合理、实际完全错误的数。
    if not re.fullmatch(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?", t) and \
       not re.fullmatch(r"\d+(?:\.\d+)?", t):
        return None
    t = t.replace(",", "")

    try:
        val = float(t)
    except ValueError:
        return None
    return -val if negative else val


def normalize_to_yuan(value: Any, unit: Optional[str]) -> Optional[float]:
    """把金额折算到「元」。

    人数（``person``）原样返回——它不是金额，不存在量纲折算。
    ``unit`` 未知或为 ``None`` 时**按 1:1 处理**并在调用方留日志：多数年报默认
    就是「单位：元」，而「猜错量纲」比「默认元」危险得多（会差 1e4~1e8 倍）。
    """
    if value is None:
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    if unit is None or unit == UNIT_CNY:
        return val
    mult = UNIT_MULTIPLIER.get(unit)
    if mult is None:
        if unit != UNIT_PERSON:
            logger.warning("未知单位 %r，按 1:1 处理（未折算）", unit)
        return val
    return val * mult


def _unit_from_match(m: "re.Match") -> Optional[str]:
    """从单位声明里取出规范 unit。捕获组内部可能有空白，先去掉再查表。"""
    return _UNIT_DECLARATION.get(re.sub(r"\s+", "", m.group(1)))


def detect_unit(text: str) -> Optional[str]:
    """从整页文本里找单位声明，返回规范 unit。找不到返回 ``None``。

    找不到**不报错**：相当一部分 PDF 的单位声明落在前一页，调用方拿到的
    单页文本里根本没有。这种情况按「元」处理（见 :func:`normalize_to_yuan`）。
    """
    compact, _ = _compact(unicodedata.normalize("NFKC", text))
    m = _UNIT_RE.search(compact)
    if not m:
        return None
    return _unit_from_match(m)


def _compact(s: str) -> Tuple[str, List[int]]:
    """去掉所有空白，并保留每个保留字符在原串中的下标。

    为什么需要下标：PDF 会把科目名拉散成「固 定 资 产」。要匹配就得先把空白
    去掉，但匹配位置又必须能换算回**原行**，否则就没法在标签右侧切数字出来
    ——切错位置会取到完全无关的值。
    """
    chars: List[str] = []
    idx: List[int] = []
    for i, ch in enumerate(s):
        if not ch.isspace():
            chars.append(ch)
            idx.append(i)
    return "".join(chars), idx


def _normalize_line(line: str) -> str:
    """NFKC + 空白折叠。

    NFKC 解决的是全角问题：全角括号、全角逗号、全角负号，
    全角数字。PDF 抽取出来的文本里这些全都可能存在。
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", line)).strip()


#: 没有小数点时，一个数字要达到这么多位才算金额。
#:
#: 依据是中国会计准则的报表附注列：那一列是**会计报表项目编号**（对应附注
#: 里的「五、10」），形态固定是 1~3 位纯整数；而金额要么带小数点，要么量级足够大。
AMOUNT_MIN_DIGITS = 6


def _looks_like_amount(token: str) -> bool:
    """判断一个数字 token 是不是「金额形态」。

    真实年报的资产负债表行有四列，**不是**三列::

        存货 10 61,427,421,796.18 54,343,285,157.47
        科目名 ^附注编号  ^本期数       ^上期数

    那个 ``10`` 是会计报表项目编号，不是金额。旧实现「取行内第一个数字」
    于是把它当成了金额 —— 而且抽出来的不是 ``None``、``unit`` 也没报错，
    看上去「有数据」，实则完全是错的。而 fixed_assets / inventory /
    construction_in_progress / intangible_assets 在 DuckDB 里**没有对账口径**，
    这种错永远不会被发现。

    判据取「带小数点」或「位数 >= 6」：A 股年报的金额一律带两位小数，
    无小数的整数金额量级也远大于附注编号，判别很稳。
    """
    if "." in token:
        return True
    digits = re.sub(r"\D", "", token)
    return len(digits) >= AMOUNT_MIN_DIGITS


def _first_amount_token(tail: str) -> Optional[Tuple[str, float]]:
    """在行尾找出第一个「金额形态」的数字。找不到返回 ``None``。

    找不到时返回 ``None`` 而不是退回「取第一个数字」：那等于把附注编号
    写进库里。宁可判为抽不到（→ 标缺失），也不能造一个看起来正常的数。
    """
    for m in _NUMBER_RE.finditer(tail):
        token = m.group(0)
        value = parse_number(token)
        if value is None:
            continue
        if _looks_like_amount(token):
            return token, value
    return None


# ---------------------------------------------------------------------------
# 事实记录
# ---------------------------------------------------------------------------


@dataclass
class Fact:
    """一条抽取到的候选事实（还没落库）。

    ``value`` 始终是**折算到元（或人）之后的值**。原始单位保留在 ``unit``。
    这一点是整个模块的地基：比对和落库都只看 ``value``，量纲问题在入口
    就一次性解决掉。
    """

    field: str
    value: float
    unit: Optional[str]
    scope: str
    extract_by: str
    source_page: int
    raw_text: str
    confidence: float
    status: str = STATUS_PENDING

    def to_record(self) -> Dict[str, Any]:
        """转成可直接喂给 ``FactStore.upsert`` 的 dict（缺主键三列）。

        ``store.upsert`` 会忽略它不认识的键，所以这里带出来的
        ``alt_*`` 字段（人工裁决用的另一通道证据）不会破坏落库，
        由 pipeline 决定怎么呈现。
        """
        return {
            "field": self.field,
            "value": self.value,
            "unit": self.unit,
            "scope": self.scope,
            "extract_by": self.extract_by,
            "source_page": self.source_page,
            "raw_text": self.raw_text,
            "status": self.status,
            "confidence": self.confidence,
        }


#: 规则通道是确定性的，同样输入必然同样输出，置信度给满。
RULE_CONFIDENCE = 1.0

#: LLM 通道的默认置信度。它是概率性的，即便自报 1.0 也不代表确定性，
#: 所以只有**两通道互证**之后才允许升到 ``verified``。
LLM_DEFAULT_CONFIDENCE = 0.8


# ---------------------------------------------------------------------------
# 规则通道
# ---------------------------------------------------------------------------


def _match_field(line: str) -> Optional[Tuple[FieldSpec, re.Match]]:
    """在一行里定位科目名。返回 (字段定义, 命中位置)。"""
    for spec in FIELD_SPECS:
        for pat in spec.patterns:
            m = re.search(pat, line)
            if not m:
                continue
            if any(bad in line for bad in spec.exclude):
                continue
            return spec, m
    return None


def extract_by_rule(
    page: int,
    text: str,
    *,
    scope: str = SCOPE_CONSOLIDATED,
    unit: Optional[str] = None,
) -> List[Fact]:
    """规则通道：从单页文本里抽字段。

    是**逐行状态机**，两个状态随行推进：

    * ``scope``   —— 由表标题切换（合并 / 母公司）
    * ``unit``    —— 由「单位：」声明切换

    为什么不按页统一取单位：一张 PDF 页上如果并排了两张表（巨潮很常见），
    两张表的量纲可以不同。按「最近一次声明」而不是「本页第一个声明」，
    因为单位声明总是紧跟在它所属的表标题**下方**，就近声明更可信。

    Args:
        page: 页码，原样写进 ``source_page`` 供人工回原文裁决。
        text: 该页的纯文本。
        scope: **进入本页时的初始 scope**，用于跨页延续（见下）。
        unit: 进入本页时的初始单位，同上。

    跨页状态（重要）
    ----------------
    一张表会**跨多页**，而表标题只出现在第一页。真实例子：贵州茅台 2025 年报
    的母公司资产负债表标题在 p59，数据一直排到 p61；只看 p60 的话整页没有
    任何标题，scope 就会退回默认的 ``consolidated``，把母公司数字错标成
    合并口径 —— 而且不会报任何错。

    所以 ``scope``/``unit`` 必须由调用方**按页序**串起来。单页调用时用
    默认值即可（行为与之前一致）；批量处理请用 :func:`extract_pages`。

    Returns:
        ``Fact`` 列表。抽不到就是空列表——**绝不用 0 或估值填充**。
    """
    facts, _, _ = _scan_page(page, text, scope, unit)
    return facts


def _scan_page(
    page: int,
    text: str,
    scope: str,
    unit: Optional[str],
) -> Tuple[List[Fact], str, Optional[str]]:
    """单页扫描。返回 (事实, 末态 scope, 末态 unit)。

    末态是给 :func:`extract_pages` 串页用的。做成返回值而不是再扫一遍，
    是为了不把每页的行匹配做两次。
    """
    current_scope = scope
    current_unit = unit
    facts: List[Fact] = []

    for raw_line in text.splitlines():
        line = _normalize_line(raw_line)
        if not line:
            continue
        # 匹配在「去空白」的行上做，位置再换算回原行去切数字。
        compact, idx = _compact(line)

        # --- 状态更新：表标题切换 scope ---
        for pattern, new_scope in _SCOPE_MARKERS:
            if pattern.search(compact):
                current_scope = new_scope
                break

        # --- 状态更新：单位声明 ---
        m = _UNIT_RE.search(compact)
        if m:
            current_unit = _unit_from_match(m) or current_unit

        hit = _match_field(compact)
        if hit is None:
            continue
        spec, label_match = hit

        # 把匹配终点换算回原行下标，然后只在标签**右侧**找数字。标签左侧的
        # 数字是上一行的残留（PDF 换行错位），拿它会得到完全无关的值。
        tail_start = idx[label_match.end() - 1] + 1
        tail = line[tail_start:]

        if spec.kind == AMOUNT:
            # 金额只认「金额形态」的数字，跳过会计报表项目编号列。
            # 资产负债表/利润表每个科目有两列（本期数、上期数），取**第一列**。
            # 取最后一列会安静地拿到上一期的数 —— 这是本模块最隐蔽的错法。
            # 行内一个金额形态的数字都没有 => 判为抽不到，不硬取。
            found = _first_amount_token(tail)
            if found is None:
                continue
            value = found[1]
            resolved_unit = current_unit
            out_value = normalize_to_yuan(value, resolved_unit)
        else:
            # 人数不参与金额形态判定：「在职员工的数量合计 34,992」是 5 位纯
            # 整数，按金额判据（无小数点且 < 6 位）会被误判成「不是金额」。
            num_m = _NUMBER_RE.search(tail)
            if num_m is None:
                continue
            value = parse_number(num_m.group(0))
            if value is None:
                continue
            # 员工数没有金额单位，不折算。scope 也强制合并：员工数是上市公司
            # 层面的披露，年报里根本不存在「母公司员工数」，沿用当前表标题
            # 的 scope 反而会把它错标成 parent。
            resolved_unit = UNIT_PERSON
            out_value = value
            current_scope = SCOPE_CONSOLIDATED

        facts.append(
            Fact(
                field=spec.name,
                value=out_value,
                unit=resolved_unit,
                scope=current_scope,
                extract_by=EXTRACT_RULE,
                source_page=page,
                raw_text=raw_line.strip()[:500],
                confidence=RULE_CONFIDENCE,
            )
        )

    return facts, current_scope, current_unit


def extract_pages(
    pages: Sequence[Tuple[int, str]],
    *,
    scope: str = SCOPE_CONSOLIDATED,
    unit: Optional[str] = None,
) -> Tuple[List[Fact], str, Optional[str]]:
    """按页序批量跑规则通道，自动把 scope/unit 跨页串起来。

    这是处理「一张表跨多页」的正确入口：调用方只要把页按顺序丢进来，
    不用自己记得把上一页的 scope 传回来（忘了就静默错标口径）。

    Args:
        pages: ``[(页码, 文本), ...]``，**必须按页码升序**。乱序会让 scope
            在表之间乱跳，产出错误的口径且不报错。
        scope / unit: 起始状态，一般用默认值。

    Returns:
        ``(facts, 末页的 scope, 末页的 unit)``。后两个是**续接状态**，
        接着跑下一段（比如另一份报表）时传回去即可。
    """
    facts: List[Fact] = []
    for page, text in pages:
        page_facts, scope, unit = _scan_page(page, text, scope, unit)
        facts.extend(page_facts)
    return facts, scope, unit


# ---------------------------------------------------------------------------
# LLM 通道
# ---------------------------------------------------------------------------


class LLMExtractor(Protocol):
    """LLM 抽取通道的注入接口。

    刻意**不在本模块里实现**：python-service 目前是零依赖服务，
    引入 LLM SDK 会破坏这个性质（而且模型/重试/超时的策略应该由调用方
    按自己的模型配置决定，不该被一个抽取器绑死）。

    实现方只需返回形如::

        [{"field": "fixed_assets", "value": 2847653126.87, "unit": "CNY",
          "scope": "consolidated", "raw_text": "...", "confidence": 0.9}]

    的 dict 列表。不认识的 ``field`` 会被丢弃，缺 ``value`` 的条目会被丢弃。
    """

    async def extract(self, page: int, text: str) -> List[Dict[str, Any]]:  # pragma: no cover
        ...


class NullLLMExtractor:
    """未配置 LLM 时的降级实现。

    返回空列表而不是抛异常：双通道里缺一路只是让结果落到 ``pending``，
    而「LLM 没配」是一个**部署状态**而不是一次运行失败，让整条链路崩掉
    没有任何好处。
    """

    def __init__(self, reason: str = "未注入 LLM 抽取器") -> None:
        self.reason = reason

    async def extract(self, page: int, text: str) -> List[Dict[str, Any]]:
        logger.warning("LLM 通道不可用（%s），本页跳过 LLM 抽取", self.reason)
        return []


async def extract_by_llm(
    extractor: Optional[LLMExtractor],
    page: int,
    text: str,
) -> List[Fact]:
    """调用 LLM 通道并把返回规整成 :class:`Fact`。

    降级路径有两层，**都不抛异常**：

    1. ``extractor is None`` -> 用 :class:`NullLLMExtractor` 走空。
    2. 调用抛异常（超时、限流、格式错）-> 记日志、返回空。

    理由同上：LLM 通道的失败只应该让这条事实退化成 ``pending``，
    不应该让规则通道已经拿到的数据白抽一遍。
    """
    if extractor is None:
        extractor = NullLLMExtractor()

    try:
        raw_items = await extractor.extract(page, text)
    except Exception as exc:  # noqa: BLE001 —— 故意兜住所有异常，见 docstring
        logger.warning("LLM 抽取失败（page=%s）: %s，返回空结果", page, exc)
        return []

    facts: List[Fact] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        name = item.get("field")
        spec = _BY_NAME.get(name) if isinstance(name, str) else None
        if spec is None:
            # LLM 偶尔会「顺手多给」几个字段。只认 FIELD_SPECS 里的白名单，
            # 否则 store 里会出现没人定义语义的野字段。
            continue
        raw_value = item.get("value")
        if raw_value is None:
            continue

        unit = item.get("unit") or (UNIT_PERSON if spec.kind == COUNT else UNIT_CNY)
        value = normalize_to_yuan(raw_value, unit)
        if value is None:
            continue

        scope = item.get("scope") or SCOPE_CONSOLIDATED
        if scope not in (SCOPE_CONSOLIDATED, SCOPE_PARENT):
            logger.warning("LLM 返回了未知 scope %r，按合并口径处理", scope)
            scope = SCOPE_CONSOLIDATED

        try:
            conf = float(item.get("confidence", LLM_DEFAULT_CONFIDENCE))
        except (TypeError, ValueError):
            conf = LLM_DEFAULT_CONFIDENCE

        facts.append(
            Fact(
                field=spec.name,
                value=value,
                unit=unit,
                scope=scope,
                extract_by=EXTRACT_LLM,
                source_page=page,
                raw_text=str(item.get("raw_text") or "")[:500],
                confidence=conf,
            )
        )
    return facts


# ---------------------------------------------------------------------------
# 双通道比对
# ---------------------------------------------------------------------------

#: 双通道一致的相对误差阈值。定得很紧（1e-6）是因为两条通道读的是**同一页
#: 同一行**的同一个数字，容差理应只用来吸收浮点尾差。任何更大的差异都意味着
#: 有一边读错了列或读串了行，必须走人工裁决而不是悄悄放行。
CROSS_CHANNEL_REL_TOL = 1e-6


def _values_agree(a: float, b: float, rel_tol: float) -> bool:
    """相对误差判等。含 0 与极小量的边界处理。"""
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    if scale == 0.0:
        return True
    return abs(a - b) <= rel_tol * scale


def merge_channels(
    rule_facts: Sequence[Fact],
    llm_facts: Sequence[Fact],
    *,
    rel_tol: float = CROSS_CHANNEL_REL_TOL,
) -> List[Dict[str, Any]]:
    """比对两通道，产出待落库的记录列表。

    比对的键是 ``(field, scope)`` 而不是光 ``field``：合并口径和母公司口径
    的数字**本来就不该相等**，把它们放在一起比会制造假冲突。两个通道各自
    报了不同 scope 时宁可都留 ``pending``，也不要硬凑成一个结论。

    ``disputed`` 时**只输出一条记录**（主键 ``(thscode, period, report_type,
    field, scope)`` 唯一，输两条会被后一条覆盖）。被合并掉的另一通道证据放进
    ``alt_*`` 键——``store.upsert`` 会忽略它们，由 pipeline 决定怎么呈现给
    人工裁决。人工需要的是**两边都有据可查**，不是「系统替我选一个」。
    """
    rmap = {(f.field, f.scope): f for f in rule_facts}
    lmap = {(f.field, f.scope): f for f in llm_facts}

    out: List[Dict[str, Any]] = []
    for key in sorted(set(rmap) | set(lmap)):
        r = rmap.get(key)
        l = lmap.get(key)

        if r is not None and l is not None:
            # 关键：比的是**已经折算到元**的 value。Fact.value 的不变式就是
            # 「永远是元」（构造时由各通道的入口保证），所以这里直接比即可 ——
            # 绝不能再乘一次 unit 的乘数，否则会把已经归一化的值再放大 1e4~1e8 倍。
            # 少了入口那次归一化，「1.2万元」和「12000元」就会被判成不一致。
            if _values_agree(r.value, l.value, rel_tol):
                rec = replace(r, status=STATUS_VERIFIED, confidence=RULE_CONFIDENCE).to_record()
                rec["alt_value"] = l.value
                rec["alt_extract_by"] = l.extract_by
                rec["alt_source_page"] = l.source_page
                rec["alt_raw_text"] = l.raw_text
                out.append(rec)
            else:
                rec = replace(r, status=STATUS_DISPUTED).to_record()
                rec["alt_value"] = l.value
                rec["alt_extract_by"] = l.extract_by
                rec["alt_source_page"] = l.source_page
                rec["alt_raw_text"] = l.raw_text
                rec["diff"] = r.value - l.value
                out.append(rec)
        else:
            only = r if r is not None else l
            assert only is not None
            out.append(only.to_record())

    return out


async def extract_page(
    page: int,
    text: str,
    *,
    llm_extractor: Optional[LLMExtractor] = None,
    rel_tol: float = CROSS_CHANNEL_REL_TOL,
    scope: str = SCOPE_CONSOLIDATED,
    unit: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """一站式：单页文本 -> 待落库记录列表。

    两条通道**并发**跑。规则通道是纯 CPU 的正则，没有等待的理由；
    LLM 通道是网络调用，串行会让总耗时翻倍。

    ``scope`` / ``unit`` 会透传给规则通道，语义与 :func:`extract_by_rule`
    一致：处理跨页表格时由调用方按页序续接。
    """
    import asyncio

    rule_facts = extract_by_rule(page, text, scope=scope, unit=unit)
    llm_task = asyncio.create_task(extract_by_llm(llm_extractor, page, text))
    llm_facts = await llm_task
    return merge_channels(rule_facts, llm_facts, rel_tol=rel_tol)
