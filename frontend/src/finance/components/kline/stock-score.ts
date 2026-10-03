import type { KLineData } from 'klinecharts';
import { zettarancPalette } from './palette';

/**
 * 知行战法核心量化计算与股票评分体系
 *
 * 严格依据知识库文档：
 * 1. [202511151725]策略篇六知行趋势线白黄线的本质与应用.md
 *    - 白线 (W, 知行短期趋势线): W := EMA(EMA(CLOSE, 10), 10);
 *    - 黄线 (Y, 知行多空线 / 大哥线): Y := (MA(CLOSE, 14) + MA(CLOSE, 28) + MA(CLOSE, 57) + MA(CLOSE, 114)) / 4;
 *    - BBI (短周期牵牛绳): BBI := (MA(CLOSE, 3) + MA(CLOSE, 6) + MA(CLOSE, 12) + MA(CLOSE, 24)) / 4;
 * 2. 短期砖型图指标v2026.docx (同花顺 ZX砖型图移动端同款):
 *    - VAR1A := (HHV(HIGH, 4) - CLOSE) / (HHV(HIGH, 4) - LLV(LOW, 4)) * 100 - 90;
 *    - VAR2A := SMA(VAR1A, 4, 1) + 100;
 *    - VAR3A := (CLOSE - LLV(LOW, 4)) / (HHV(HIGH, 4) - LLV(LOW, 4)) * 100;
 *    - VAR4A := SMA(VAR3A, 6, 1);
 *    - VAR5A := SMA(VAR4A, 6, 1) + 100;
 *    - VAR6A := VAR5A - VAR2A;
 *    - 砖型图 := IF(VAR6A > 4, VAR6A - 4, 0);
 *    - STICKLINE(REF(砖型图,1) < 砖型图, 砖型图, REF(砖型图,1), 3, 0), COLORRED;
 *    - STICKLINE(REF(砖型图,1) > 砖型图, 砖型图, REF(砖型图,1), 3, 0), COLOR00FF00;
 * 3. 20260318z哥直播学习笔记砖型图焚诀周期数砖战法.md:
 *    - 核心4数砖法则：红1启动，红2建仓点，红3持股，连续4红砖必须减仓/清仓；翻绿必须立即止损！
 */

// 1. 指数移动平均线 (EMA)
export function calcEMA(dataList: KLineData[], period: number): Array<number | null> {
  const result: Array<number | null> = [];
  const k = 2 / (period + 1);
  let ema: number | null = null;
  for (let i = 0; i < dataList.length; i++) {
    const close = dataList[i]?.close;
    if (typeof close !== 'number' || !Number.isFinite(close)) {
      result.push(null);
      continue;
    }
    if (ema === null) {
      ema = close;
    } else {
      ema = close * k + ema * (1 - k);
    }
    result.push(Number(ema.toFixed(2)));
  }
  return result;
}

// 2. 双重平滑指数移动平均线 (二次平滑EMA10 -> 知行白线)
export function calcDEMA(dataList: KLineData[], period = 10): Array<number | null> {
  const ema1 = calcEMA(dataList, period);
  const k = 2 / (period + 1);
  const result: Array<number | null> = [];
  let ema2: number | null = null;
  for (let i = 0; i < ema1.length; i++) {
    const val = ema1[i];
    if (val === null || typeof val !== 'number') {
      result.push(null);
      continue;
    }
    if (ema2 === null) {
      ema2 = val;
    } else {
      ema2 = val * k + ema2 * (1 - k);
    }
    result.push(Number(ema2.toFixed(2)));
  }
  return result;
}

// 3. 简单移动平均线 (SMA / MA)
export function calcSMA(dataList: KLineData[], period: number): Array<number | null> {
  const result: Array<number | null> = [];
  let sum = 0;
  for (let i = 0; i < dataList.length; i++) {
    const close = dataList[i]?.close;
    if (typeof close !== 'number' || !Number.isFinite(close)) {
      result.push(null);
      continue;
    }
    sum += close;
    if (i >= period) {
      sum -= (dataList[i - period]?.close ?? 0);
    }
    if (i >= period - 1) {
      result.push(Number((sum / period).toFixed(2)));
    } else {
      result.push(null);
    }
  }
  return result;
}

// 4. 通达信/同花顺 SMA(X, N, M): Y = (M * X + (N - M) * Y') / N
export function calcTongHuaShunSMA(values: number[], n: number, m: number): number[] {
  const result: number[] = [];
  let prevY: number | null = null;
  for (let i = 0; i < values.length; i++) {
    const x = values[i];
    if (prevY === null) {
      prevY = x;
    } else {
      prevY = (m * x + (n - m) * prevY) / n;
    }
    result.push(prevY);
  }
  return result;
}

// 5. 知行多空线 / 大哥线 (长周期BBI结构: 14, 28, 57, 114)
export function calcLongBBI(dataList: KLineData[], periods = [14, 28, 57, 114]): Array<number | null> {
  const ma1 = calcSMA(dataList, periods[0]);
  const ma2 = calcSMA(dataList, periods[1]);
  const ma3 = calcSMA(dataList, periods[2]);
  const ma4 = calcSMA(dataList, periods[3]);
  return dataList.map((_, i) => {
    const m1 = ma1[i];
    const m2 = ma2[i];
    const m3 = ma3[i];
    const m4 = ma4[i];
    if (m1 !== null && m2 !== null && m3 !== null && m4 !== null) {
      return Number(((m1 + m2 + m3 + m4) / 4).toFixed(2));
    }
    // 数据量不足 114 天时平滑退化
    const valid = [m1, m2, m3, m4].filter((v): v is number => v !== null);
    if (valid.length > 0) {
      return Number((valid.reduce((a, b) => a + b, 0) / valid.length).toFixed(2));
    }
    return null;
  });
}

// 6. 短周期 BBI (牵牛绳: 3, 6, 12, 24)
export function calcBBI(dataList: KLineData[]): Array<number | null> {
  const ma3 = calcSMA(dataList, 3);
  const ma6 = calcSMA(dataList, 6);
  const ma12 = calcSMA(dataList, 12);
  const ma24 = calcSMA(dataList, 24);
  return dataList.map((_, i) => {
    const m3 = ma3[i];
    const m6 = ma6[i];
    const m12 = ma12[i];
    const m24 = ma24[i];
    if (m3 !== null && m6 !== null && m12 !== null && m24 !== null) {
      return Number(((m3 + m6 + m12 + m24) / 4).toFixed(2));
    }
    const valid = [m3, m6, m12, m24].filter((v): v is number => v !== null);
    if (valid.length > 0) {
      return Number((valid.reduce((a, b) => a + b, 0) / valid.length).toFixed(2));
    }
    return null;
  });
}

// 7. 知行ZX砖型图详细计算结构
export interface ZXBrickItem {
  brick: number;          // 当前砖型数值
  prevBrick: number;      // 前一日砖型数值
  direction: 'up' | 'down' | 'flat'; // up=红砖, down=绿砖, flat=平
  stepCount: number;      // 连续红/绿砖计数 (1..4..)
  countText: string;      // 状态标签: 如 "红2 [建仓]", "红4 [减仓]"
  isBuyPoint: boolean;    // 是否为红2或红1买点
  isRiskPoint: boolean;   // 是否为红4或翻绿高危点
}

export function calcZXBrick(dataList: KLineData[]): ZXBrickItem[] {
  const len = dataList.length;
  if (len === 0) return [];

  // 计算 VAR1A 与 VAR3A
  // HHV(HIGH, 4) 与 LLV(LOW, 4)
  const var1aList: number[] = [];
  const var3aList: number[] = [];

  for (let i = 0; i < len; i++) {
    const start = Math.max(0, i - 3);
    let hhv = -Infinity;
    let llv = Infinity;
    for (let j = start; j <= i; j++) {
      const h = dataList[j]?.high ?? 0;
      const l = dataList[j]?.low ?? 0;
      if (h > hhv) hhv = h;
      if (l < llv) llv = l;
    }
    const c = dataList[i]?.close ?? 0;
    const range = Math.max(hhv - llv, 0.0001);

    const v1 = ((hhv - c) / range) * 100 - 90;
    const v3 = ((c - llv) / range) * 100;
    var1aList.push(v1);
    var3aList.push(v3);
  }

  // VAR2A := SMA(VAR1A, 4, 1) + 100
  const smaVar1a = calcTongHuaShunSMA(var1aList, 4, 1);
  const var2aList = smaVar1a.map((v) => v + 100);

  // VAR4A := SMA(VAR3A, 6, 1)
  const var4aList = calcTongHuaShunSMA(var3aList, 6, 1);

  // VAR5A := SMA(VAR4A, 6, 1) + 100
  const smaVar4a = calcTongHuaShunSMA(var4aList, 6, 1);
  const var5aList = smaVar4a.map((v) => v + 100);

  // VAR6A := VAR5A - VAR2A
  // 砖型图 := IF(VAR6A > 4, VAR6A - 4, 0)
  const rawBricks: number[] = [];
  for (let i = 0; i < len; i++) {
    const var6a = var5aList[i] - var2aList[i];
    const val = var6a > 4 ? var6a - 4 : 0;
    rawBricks.push(Number(val.toFixed(2)));
  }

  // 计算红绿步长与数砖战法计数 (1..4)
  const result: ZXBrickItem[] = [];
  let currDir: 'up' | 'down' | 'flat' = 'flat';
  let count = 0;

  for (let i = 0; i < len; i++) {
    const curr = rawBricks[i];
    const prev = i > 0 ? rawBricks[i - 1] : 0;

    let dir: 'up' | 'down' | 'flat' = 'flat';
    if (curr > prev) {
      dir = 'up';
    } else if (curr < prev) {
      dir = 'down';
    } else {
      dir = 'flat';
    }

    if (dir === currDir && dir !== 'flat') {
      count += 1;
    } else if (dir !== 'flat') {
      currDir = dir;
      count = 1;
    } else {
      // flat 保持原方向计数或重置
      currDir = 'flat';
      count = 0;
    }

    let countText = '震荡观望';
    let isBuyPoint = false;
    let isRiskPoint = false;

    if (dir === 'up') {
      if (count === 1) countText = '红1 · 企稳信号';
      else if (count === 2) {
        countText = '红2 · 进攻买点';
        isBuyPoint = true;
      } else if (count === 3) countText = '红3 · 顺势持股';
      else if (count >= 4) {
        countText = `红${count} · 注意减仓!`;
        isRiskPoint = true;
      }
    } else if (dir === 'down') {
      if (count === 1) {
        countText = '翻绿 · 离场止损';
        isRiskPoint = true;
      } else {
        countText = `绿${count} · 空仓等待`;
      }
    } else {
      countText = curr > 0 ? `持平(${curr})` : '零轴空仓';
    }

    result.push({
      brick: curr,
      prevBrick: prev,
      direction: dir,
      stepCount: count,
      countText,
      isBuyPoint,
      isRiskPoint,
    });
  }

  return result;
}

// 8. 五分制持股打分评级 (1 ~ 5 分)
export interface StockScoreResult {
  score: number;             // 1 到 5 分
  ratingText: string;        // 评级名称: 如 "强力看多 (5分)", "良好持股 (4分)"
  ratingTag: string;         // 短标签: "5星", "4星", etc.
  themeColor: string;        // 来自 palette.scoreStars，随主题深浅切换
  bulletPoints: string[];    // 核心战法要点
  whiteAboveYellow: boolean; // 快线在大哥线上
  aboveBbi: boolean;         // 站上BBI
  aboveYellow: boolean;      // 站上大哥线
  zxBrickItem: ZXBrickItem;  // 最新砖型图状态
  quality: DataQuality;      // 数据可信度（见下）
  range: RangePerformance;   // 区间表现（见下）
  /** 每个可展示数值的来源说明，用于「不捏造」的溯源展示 */
  sources: Record<string, string>;
}

/** 计算「大哥线」四均线（14/28/57/114）所需的最少 K 线根数。 */
export const LONG_BBI_MIN_BARS = 114;
/** 计算「牵牛绳」四均线（3/6/12/24）所需的最少 K 线根数。 */
export const BBI_MIN_BARS = 24;

/**
 * 数据可信度。
 *
 * 存在的理由：评分公式依赖 114 根 K 线才能算出完整的「大哥线」，但旧实现只要求
 * 5 根就出评级，并用 `?? close` 把算不出的均线**用收盘价顶替**后当成真实线值
 * 打印进文案——新股因此能拿到一份看似笃定的结论。这个结构体的作用是把
 * 「算得出多少」如实告诉 UI，由 UI 决定要不要降级展示，而不是悄悄糊过去。
 */
export interface DataQuality {
  bars: number;
  /** 最新交易日 YYYY-MM-DD */
  asOf: string;
  firstAvailable: string;
  /** 不足 114 根时大哥线退化为「还剩哪几条 MA 平均哪几条」，口径已变 */
  yellowDegraded: boolean;
  /** 不足 24 根时牵牛绳同样退化 */
  bbiDegraded: boolean;
  /** 是否够算出完整评分（要求大哥线不退化） */
  sufficient: boolean;
  /** 人类可读的提示，逐条对应上面的标记 */
  notes: string[];
}

/** 区间表现：全部由已持有的 OHLCV 算出，不产生任何新增网络请求。 */
export interface RangePerformance {
  ret5: number | null;
  ret20: number | null;
  ret60: number | null;
  /** 当日振幅 % =(high-low)/prevClose */
  amplitude: number | null;
  /** 量比 = 当日量 / 前 5 日均量 */
  volRatio: number | null;
  /** 当日成交额 */
  turnover: number | null;
}

function fmtDate(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 由 dataList 直接计算区间表现；任一项数据不足时返回 null，绝不用 0 顶替。 */
export function calcRangePerformance(dataList: KLineData[]): RangePerformance {
  const n = dataList.length;
  const last = n > 0 ? dataList[n - 1] : null;
  const ret = (bars: number): number | null => {
    if (n < bars + 1) return null;
    const base = dataList[n - 1 - bars];
    if (!base || base.close <= 0) return null;
    return Number((((last as KLineData).close - base.close) / base.close * 100).toFixed(2));
  };

  let amplitude: number | null = null;
  let volRatio: number | null = null;
  if (last) {
    const prev = n > 1 ? dataList[n - 2] : null;
    if (prev && prev.close > 0) {
      amplitude = Number((((last.high - last.low) / prev.close) * 100).toFixed(2));
    }
    if (n >= 6) {
      let sum = 0;
      let ok = true;
      for (let i = n - 6; i < n - 1; i += 1) {
        const v = dataList[i]?.volume;
        if (typeof v !== 'number' || v <= 0) { ok = false; break; }
        sum += v;
      }
      const lastVol = (last as KLineData).volume;
      if (ok && sum > 0 && typeof lastVol === 'number' && lastVol > 0) {
        volRatio = Number((lastVol / (sum / 5)).toFixed(2));
      }
    }
  }

  return {
    ret5: ret(5),
    ret20: ret(20),
    ret60: ret(60),
    amplitude,
    volRatio,
    turnover: typeof last?.turnover === 'number' ? last.turnover : null,
  };
}

/** 由 dataList 直接计算数据可信度。 */
export function calcDataQuality(dataList: KLineData[]): DataQuality {
  const n = dataList.length;
  const yellowDegraded = n < LONG_BBI_MIN_BARS;
  const bbiDegraded = n < BBI_MIN_BARS;
  const notes: string[] = [];

  if (n === 0) {
    notes.push('无行情数据');
  } else {
    if (yellowDegraded) {
      notes.push(`大哥线按不足 ${LONG_BBI_MIN_BARS} 根的可用均线折算（当前 ${n} 根），口径与标准四均线不同`);
    }
    if (bbiDegraded) {
      notes.push(`牵牛绳按不足 ${BBI_MIN_BARS} 根的可用均线折算（当前 ${n} 根），口径与标准四均线不同`);
    }
    if (n < 5) {
      notes.push(`仅 ${n} 根 K 线，样本不足以评估砖型形态`);
    }
  }

  return {
    bars: n,
    asOf: n > 0 ? fmtDate(dataList[n - 1].timestamp) : '',
    firstAvailable: n > 0 ? fmtDate(dataList[0].timestamp) : '',
    yellowDegraded,
    bbiDegraded,
    // 大哥线是评分的核心基准，它退化时结论就不可信 —— 宁可不出分也不给个
    // 基于不同口径硬算出来的分数。
    sufficient: n >= LONG_BBI_MIN_BARS,
    notes,
  };
}

export function calcStockHoldingScore(dataList: KLineData[]): StockScoreResult | null {
  if (!dataList || dataList.length < 5) return null;

  const quality = calcDataQuality(dataList);
  const range = calcRangePerformance(dataList);

  const lastIdx = dataList.length - 1;
  const last = dataList[lastIdx];
  const close = last.close;

  // 1. 白黄线计算
  const dema10 = calcDEMA(dataList, 10);
  const longBbi = calcLongBBI(dataList);
  const bbi = calcBBI(dataList);
  const zxBricks = calcZXBrick(dataList);

  // 均线算不出来就是算不出来。此前这里写 `?? close`，等于拿收盘价冒充均线值，
  // 再把这个数当真实线值打印进「知行双线: 白线(XX)金叉黄线(XX)」—— 三根线
  // 全部退化成收盘价时会凭空造出「金叉」结论。数据不足时宁可不给结论。
  const white = dema10[lastIdx] ?? null;
  const yellow = longBbi[lastIdx] ?? null;
  const bbiVal = bbi[lastIdx] ?? null;
  const brickItem = zxBricks[lastIdx] || {
    brick: 0,
    prevBrick: 0,
    direction: 'flat',
    stepCount: 0,
    countText: '数据不足',
    isBuyPoint: false,
    isRiskPoint: false,
  };

  // 大哥线与牵牛绳任一算不出，就无法判断多空位置，也不给分。
  const linesUsable = white !== null && yellow !== null && bbiVal !== null;

  const whiteAboveYellow = linesUsable ? white >= yellow : false;
  const aboveYellow = linesUsable ? close >= yellow : false;
  const aboveBbi = linesUsable ? close >= bbiVal : false;
  const isRedBrick = brickItem.direction === 'up';

  // 评分细则 (基于战法底线原则):
  // 基准分 1 分。
  // 规则1: 绝不抄底破黄线股票。跌破黄线大哥线必须立即止损！
  // 规则2: 白在黄上为顺大势金叉。
  // 规则3: ZX砖型图连续红砖(1~3)，红2为黄金买点，红4高抛。
  // 规则4: 站上短周期BBI牵牛绳。
  let score = 1;

  if (linesUsable) {
    if (aboveYellow) {
      score += 1; // 站上多空大哥线 (+1)
      if (whiteAboveYellow) score += 1; // 顺大势金叉 (+1)
    }
  } else {
    // 没有均线基准就没有多空判断，此时把基准分也标成不可信，
    // 由 UI 决定怎么显示，而不是硬凑一个 1 分出来。
    return null;
  }

  if (isRedBrick) {
    if (brickItem.stepCount <= 3) score += 1; // 红1~红3健康上行 (+1)
    // 红4 原本给 +0.5，但最终会 round(3.5)=4，+0.5 与不加完全等价 ——
    // 是条无效分支，保留只会让人误以为红4有额外加成。直接不加。
  }

  if (aboveBbi) {
    score += 1; // 站上牵牛绳 (+1)
  }

  // 上限 5。**不再有 Math.max(1, ...)**：老实现无论数据多差都保底给 1 分，
  // 于是「查不到」和「真的很差」在界面上长得一模一样。1 分本来就由基准分覆盖，
  // 去掉下限不会让任何正常场景少给分。
  const finalScore = Math.min(5, Math.round(score));

  let ratingText = '观望防守';
  let ratingTag = `${finalScore}分 · 弱势防守`;
  // 评分色取自共享调色板：这张卡片会跟随平台深浅色，色值必须跟着换。
  // 写死时白底上 3 分黄只有 1.9:1、4 分橙 2.8:1，星星几乎看不见。
  const stars = zettarancPalette().scoreStars;
  const themeColor = stars[finalScore - 1] ?? stars[stars.length - 1];

  if (finalScore === 5) {
    ratingText = '多头共振 · 极度强势';
    ratingTag = '5星 · 强势进攻';
  } else if (finalScore === 4) {
    ratingText = '顺势多头 · 稳健持股';
    ratingTag = '4星 · 良好持股';
  } else if (finalScore === 3) {
    ratingText = '多空博弈 · 控制仓位';
    ratingTag = '3星 · 震荡博弈';
  } else if (finalScore === 2) {
    ratingText = '空头承压 · 谨慎防守';
    ratingTag = '2星 · 谨慎减仓';
  } else {
    ratingText = '破位下行 · 严守止损';
    ratingTag = '1星 · 立即离场';
  }

  // 生成核心战法要点。措辞跟着数据可信度走：数据不足时只陈述事实，
  // 不断言「金叉/站上/跌破」这类需要完整基准才能下的结论。
  const bulletPoints: string[] = [];
  const degradedNote = quality.yellowDegraded
    ? `（按 ${quality.bars} 根可用均线折算，非标准四均线）`
    : '';

  // 要点1: 双线大势
  if (!aboveYellow) {
    bulletPoints.push(`知行大哥线: 股价位于大哥线(${yellow.toFixed(2)})下方，空头区间不可盲目抄底，触及止损纪律。${degradedNote}`);
  } else if (whiteAboveYellow) {
    bulletPoints.push(`知行双线: 快线(${white.toFixed(2)})在大哥线(${yellow.toFixed(2)})之上，处于右侧顺大势通道。${degradedNote}`);
  } else {
    bulletPoints.push(`知行双线: 股价回踩快线(${white.toFixed(2)})与大哥线(${yellow.toFixed(2)})之间(碗内)，关注企稳支撑与缩量B1机会。${degradedNote}`);
  }

  // 要点2: 砖型图数砖
  if (brickItem.direction === 'up') {
    bulletPoints.push(`ZX砖型图: 处于连续${brickItem.countText}，${brickItem.stepCount === 2 ? '符合第二块红砖建仓定式。' : brickItem.stepCount >= 4 ? '已满4砖，切记主动分批止盈高抛！' : '多头势能延续。'}`);
  } else if (brickItem.direction === 'down') {
    bulletPoints.push(`ZX砖型图: ${brickItem.countText}，趋势转折向下，不可恋战。`);
  } else {
    bulletPoints.push('ZX砖型图: 零轴水平休整，等待放量红砖企稳启动。');
  }

  // 要点3: BBI与多空位置
  const bbiNote = quality.bbiDegraded
    ? `（按 ${quality.bars} 根可用均线折算，非标准四均线）`
    : '';
  if (aboveBbi) {
    bulletPoints.push(`多空牵牛绳: 站上BBI(${bbiVal.toFixed(2)})，短期多头占据主动。${bbiNote}`);
  } else {
    bulletPoints.push(`多空牵牛绳: 运行于BBI(${bbiVal.toFixed(2)})下方，短期受制于成本均线压制。${bbiNote}`);
  }

  // 每个展示数值的来源。前端算出来的指标与库内直接取的字段要分清楚——
  // 「来源」在这里指的是数据链条的上游，UI 上会显示给用户。
  const sources: Record<string, string> = {
    '收盘价': 'market.v_daily_qfq（后复权→前复权行情，由 /api/kline 返回）',
    '快线 DEMA10': '前端基于收盘价计算 EMA(EMA(·,10),10)',
    '大哥线 LongBBI': '前端计算 MA14/28/57/114 的均值',
    '牵牛绳 BBI': '前端计算 MA3/6/12/24 的均值',
    'ZX砖型图': '前端按通达信 SMA(·,4,1)/SMA(·,6,1) 折算',
    'K线根数': `GET /api/kline?limit=300（截至 ${quality.asOf}）`,
  };

  return {
    score: finalScore,
    quality,
    range,
    sources,
    ratingText,
    ratingTag,
    themeColor,
    bulletPoints,
    whiteAboveYellow,
    aboveBbi,
    aboveYellow,
    zxBrickItem: brickItem,
  };
}
