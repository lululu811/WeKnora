"""
四块砖 — 短线/趋势/多空/阴阳 四项多空状态合成

对应 TS: frontend/src/components/workspace/kline/indicators.ts:127 calcFourBricksDetails

**为什么搬到这里**（2026-10-01）:
  四块砖此前**只在工作台前端算**（5/10/14 三个周期还是硬编码，没进
  config/indicators.yaml），服务端取不到。于是 agent_system_prompt 只能命令
  agent 对"四块砖什么状态"一律回答"算不出来，请去看 K 线工作台"。搬过来之后
  agent 与工作台读同一份实现，那条禁令可以撤掉。

**只搬数值，不搬解释**:
  这里的 text（"四砖全红(+4)"）是**状态标签**，不是战法知识。"红2 = 黄金买点"
  这类解释留在知识库，不要在这里生成第二套说法 —— 两套解释必然会漂。

行序：rows[0] 是最新一根 K 线，与本包其余模块一致。
rows 需要能取到 close / open，以及由它们派生的 MA5 / EMA10 / EMA14 / BBI。
"""

from typing import Dict, List, Optional

MIN_BARS = 5

# 与 config/indicators.yaml 无关的说明：这三个周期此前硬编码在前端
# (indicators.ts:127)。搬到这里后它们由本模块定义；**若要改周期，改这里，
# 不要在别处再写一份**。known_gaps 里的 four_bricks_frontend_only_no_backend
# 条目描述的就是这段历史。
PERIOD_SHORT = 5    # 短线砖：close >= MA5
PERIOD_TREND_A = 10  # 趋势砖：EMA10 >= EMA14
PERIOD_TREND_B = 14
BBI_PERIODS = (3, 6, 12, 24)  # 多空砖：close >= BBI


def sma(closes: List[float], period: int) -> List[Optional[float]]:
    """简单移动平均，前 period-1 个为 None。"""
    out: List[Optional[float]] = []
    total = 0.0
    for i, c in enumerate(closes):
        total += c
        if i >= period:
            total -= closes[i - period]
        out.append(round(total / period, 2) if i >= period - 1 else None)
    return out


def ema(closes: List[float], period: int) -> List[Optional[float]]:
    """指数移动平均，与 TS 的 calcEMA 同种子：首个值取 SMA(period)。

    TS 侧 (indicators.ts calcEMA) 的初值是前 period 根的简单平均，不是收盘价。
    两者必须一致，否则第 period 根之后会有一条永不收敛的偏移。
    """
    out: List[Optional[float]] = []
    if len(closes) < period:
        return [None] * len(closes)
    seed = sum(closes[:period]) / period
    prev = seed
    k = 2.0 / (period + 1.0)
    for i, c in enumerate(closes):
        if i < period - 1:
            out.append(None)
            continue
        if i == period - 1:
            out.append(round(prev, 2))
            continue
        prev = c * k + prev * (1.0 - k)
        out.append(round(prev, 2))
    return out


def bbi(closes: List[float]) -> List[Optional[float]]:
    """牵牛绳 = (MA3+MA6+MA12+MA24)/4，与 TS 的 calcBBI 同样语义。

    **降级语义**：TS 有两个 calcBBI —— indicators.ts:94 读 YAML 参数、
    均线未成形的 bar 返回 null；stock-score.ts:133 硬编码 3/6/12/24 并在
    部分均线缺失时**用已有均线做降级平均**。本函数跟随 stock-score.ts 那一版
    （降级），因为 KLineWorkspace 顶部的评分卡用的就是它 —— 搬过去是为了
    消除分歧，不是引入分歧。若要改成 null 语义，两侧必须同时改。
    """
    mas = [sma(closes, p) for p in BBI_PERIODS]
    out: List[Optional[float]] = []
    for i in range(len(closes)):
        vals = [m[i] for m in mas if m[i] is not None]
        out.append(round(sum(vals) / len(vals), 2) if vals else None)
    return out


def four_bricks(rows: List[dict]) -> List[Dict]:
    """逐根 K 线算出四块砖状态。返回列表与 rows **同序**（rows[0] 是最新一根）。

    每项：
      score   -4..+4，四项之和
      text    状态标签（不含战法解释）
      bull1   短线砖：close >= MA5
      bull2   趋势砖：EMA10 >= EMA14
      bull3   多空砖：close >= BBI
      bull4   阴阳砖：close >= open

    均线还没成形的 bar 记为**看多**（True），与 TS 侧一致
    (indicators.ts:139-141)：数据不足时偏向多头而不是凭空给空头判断。
    """
    closes = [float(r.get("close") or 0.0) for r in rows]
    opens = [float(r.get("open") or c) for r, c in
             ((r, float(r.get("close") or 0.0)) for r in rows)]

    # ⚠️ **均线必须按 oldest-first 算。**
    #
    # rows 是 newest-first（data_loader 的 ORDER BY date DESC，rows[0] 是最新
    # 一根），而 SMA/EMA/BBI 都是"回看"型：MA5[i] 需要 i..i+4 根。
    # 直接在 newest-first 序列上算，第 0 根拿到的是"它自己和后面 4 根**更旧的**"，
    # 也就是"截至 5 天前的均值"—— 结果整个均线序列错位，最新那根必然是 None。
    #
    # 2026-10-01 首版直接按 rows 顺序算，且端到端验证用的是上涨的 601398.SH：
    # 两种顺序在涨势里恰好都偏多，零差异，缺陷被掩盖。是 test_four_bricks.py
    # 里"持续下跌应得 -4"这条用例抓出来的 —— 那种票上错位的均线给出的
    # 是多头读数，与肉眼相反。
    closes_old_first = list(reversed(closes))
    ma5 = sma(closes_old_first, PERIOD_SHORT)
    e10 = ema(closes_old_first, PERIOD_TREND_A)
    e14 = ema(closes_old_first, PERIOD_TREND_B)
    bb = bbi(closes_old_first)
    # 算完翻回 newest-first，与 rows 对齐
    ma5.reverse()
    e10.reverse()
    e14.reverse()
    bb.reverse()

    out: List[Dict] = []
    for i in range(len(rows)):
        c = closes[i]
        o = opens[i]
        m5, p10, p14, pbbi = ma5[i], e10[i], e14[i], bb[i]

        bull1 = (c >= m5) if m5 is not None else True
        bull2 = (p10 >= p14) if (p10 is not None and p14 is not None) else True
        bull3 = (c >= pbbi) if pbbi is not None else True
        bull4 = c >= o

        score = (1 if bull1 else -1) + (1 if bull2 else -1) + \
                (1 if bull3 else -1) + (1 if bull4 else -1)

        if score == 4:
            text = "四砖全红(+4)"
        elif score >= 2:
            text = f"多头共振(+{score})"
        elif score == -4:
            text = "四砖翻绿(-4)"
        elif score <= -2:
            text = f"空头承压({score})"
        else:
            text = f"多空博弈({score:+d})"

        out.append({
            "date": rows[i].get("date"),
            "score": score,
            "text": text,
            "bull1": bull1,
            "bull2": bull2,
            "bull3": bull3,
            "bull4": bull4,
        })
    return out


def analyze_four_bricks(rows: List[dict]) -> Dict:
    """给 agent 用的单只票摘要。

    刻意**只给数值与状态标签**，不给买卖建议 —— 那是知识库与用户的领域。
    """
    if len(rows) < MIN_BARS:
        return {
            "insufficient": True,
            "required_bars": MIN_BARS,
            "actual_bars": len(rows),
            "note": f"四块砖需要至少 {MIN_BARS} 根 K 线，仅 {len(rows)} 根",
        }
    items = four_bricks(rows)
    latest = items[0]
    return {
        "insufficient": False,
        "date": latest["date"],
        "score": latest["score"],
        "text": latest["text"],
        "bull1": latest["bull1"],
        "bull2": latest["bull2"],
        "bull3": latest["bull3"],
        "bull4": latest["bull4"],
        "bull_count": sum(1 for k in ("bull1", "bull2", "bull3", "bull4")
                          if latest[k]),
        "periods": {
            "short": PERIOD_SHORT,
            "trend": [PERIOD_TREND_A, PERIOD_TREND_B],
            "bbi": list(BBI_PERIODS),
        },
        "note": "这是数值状态，不含战法解释；'红2 = 黄金买点'这类解读请查知识库。",
    }
