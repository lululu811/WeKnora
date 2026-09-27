import type { KLineData } from 'klinecharts';

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
  themeColor: string;        // 对应的主题色 (#ef4444, #f59e0b, #10b981)
  bulletPoints: string[];    // 3条核心战法要点
  whiteAboveYellow: boolean; // 白在黄上
  aboveBbi: boolean;         // 站上BBI
  aboveYellow: boolean;      // 站上大哥线
  zxBrickItem: ZXBrickItem;  // 最新砖型图状态
}

export function calcStockHoldingScore(dataList: KLineData[]): StockScoreResult | null {
  if (!dataList || dataList.length < 5) return null;

  const lastIdx = dataList.length - 1;
  const last = dataList[lastIdx];
  const close = last.close;

  // 1. 白黄线计算
  const dema10 = calcDEMA(dataList, 10);
  const longBbi = calcLongBBI(dataList);
  const bbi = calcBBI(dataList);
  const zxBricks = calcZXBrick(dataList);

  const white = dema10[lastIdx] ?? close;
  const yellow = longBbi[lastIdx] ?? close;
  const bbiVal = bbi[lastIdx] ?? close;
  const brickItem = zxBricks[lastIdx] || {
    brick: 0,
    prevBrick: 0,
    direction: 'flat',
    stepCount: 0,
    countText: '数据不足',
    isBuyPoint: false,
    isRiskPoint: false,
  };

  const whiteAboveYellow = white >= yellow;
  const aboveYellow = close >= yellow;
  const aboveBbi = close >= bbiVal;
  const isRedBrick = brickItem.direction === 'up';

  // 评分细则 (基于战法底线原则):
  // 基准分 1 分。
  // 规则1: 绝不抄底破黄线股票。跌破黄线大哥线必须立即止损！
  // 规则2: 白在黄上为顺大势金叉。
  // 规则3: ZX砖型图连续红砖(1~3)，红2为黄金买点，红4高抛。
  // 规则4: 站上短周期BBI牵牛绳。
  let score = 1;

  if (aboveYellow) {
    score += 1; // 站上多空大哥线 (+1)
    if (whiteAboveYellow) score += 1; // 顺大势金叉 (+1)
  }

  if (isRedBrick) {
    if (brickItem.stepCount <= 3) score += 1; // 红1~红3健康上行 (+1)
    else score += 0.5; // 红4已有滞涨高抛风险
  }

  if (aboveBbi) {
    score += 1; // 站上牵牛绳 (+1)
  }

  // 最终得分限制在 1..5
  const finalScore = Math.max(1, Math.min(5, Math.round(score)));

  let ratingText = '观望防守';
  let ratingTag = `${finalScore}分 · 弱势防守`;
  let themeColor = '#10b981';

  if (finalScore === 5) {
    ratingText = '多头共振 · 极度强势';
    ratingTag = '5星 · 强势进攻';
    themeColor = '#ef4444';
  } else if (finalScore === 4) {
    ratingText = '顺势多头 · 稳健持股';
    ratingTag = '4星 · 良好持股';
    themeColor = '#f97316';
  } else if (finalScore === 3) {
    ratingText = '多空博弈 · 控制仓位';
    ratingTag = '3星 · 震荡博弈';
    themeColor = '#eab308';
  } else if (finalScore === 2) {
    ratingText = '空头承压 · 谨慎防守';
    ratingTag = '2星 · 谨慎减仓';
    themeColor = '#14b8a6';
  } else {
    ratingText = '破位下行 · 严守止损';
    ratingTag = '1星 · 立即离场';
    themeColor = '#10b981';
  }

  // 生成 3 条简明核心战法要点
  const bulletPoints: string[] = [];

  // 要点1: 白黄线大势
  if (!aboveYellow) {
    bulletPoints.push('知行大哥线: 股价位于黄线下方，空头区间不可盲目抄底，触及止损纪律。');
  } else if (whiteAboveYellow) {
    bulletPoints.push(`知行双线: 白线(${white.toFixed(2)})金叉黄线(${yellow.toFixed(2)})，处于右侧顺大势通道。`);
  } else {
    bulletPoints.push(`知行双线: 股价回踩白黄线之间(碗内)，关注企稳支撑与缩量B1机会。`);
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
  if (aboveBbi) {
    bulletPoints.push(`多空牵牛绳: 站上BBI(${bbiVal.toFixed(2)})，短期多头占据主动。`);
  } else {
    bulletPoints.push(`多空牵牛绳: 运行于BBI(${bbiVal.toFixed(2)})下方，短期受制于成本均线压制。`);
  }

  return {
    score: finalScore,
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

// 9. 智能解析文本中所有提及的 A 股代码与名称
export interface MentionedStock {
  ticker: string;
  exchange: string;
  name: string;
  thscode: string;
}

// 常见股名映射字典，支持快速补全股票名称
export const COMMON_NAME_MAP: Record<string, { name: string; exchange: string }> = {
  '600487': { name: '亨通光电', exchange: 'SH' },
  '000833': { name: '粤桂股份', exchange: 'SZ' },
  '300055': { name: '万邦达', exchange: 'SZ' },
  '002594': { name: '比亚迪', exchange: 'SZ' },
  '600519': { name: '贵州茅台', exchange: 'SH' },
  '000001': { name: '平安银行', exchange: 'SZ' },
  '000592': { name: '平潭发展', exchange: 'SZ' },
  '601127': { name: '赛力斯', exchange: 'SH' },
  '300750': { name: '宁德时代', exchange: 'SZ' },
  '300059': { name: '东方财富', exchange: 'SZ' },
  '600036': { name: '招商银行', exchange: 'SH' },
  '601888': { name: '中国中免', exchange: 'SH' },
  '601318': { name: '中国平安', exchange: 'SH' },
  '002475': { name: '立讯精密', exchange: 'SZ' },
  '002415': { name: '海康威视', exchange: 'SZ' },
};

export function extractMentionedStocksFromText(text: string): MentionedStock[] {
  if (!text) return [];
  const map = new Map<string, MentionedStock>();

  // 1. 匹配带后缀的形式: 000833.SZ / 600519.SH / 830799.BJ
  const suffixedRegex = /\b(\d{6})\.(SH|SZ|BJ)\b/gi;
  let match: RegExpExecArray | null;
  while ((match = suffixedRegex.exec(text)) !== null) {
    const ticker = match[1];
    const exchange = match[2].toUpperCase();
    const thscode = `${ticker}.${exchange}`;
    const name = COMMON_NAME_MAP[ticker]?.name || ticker;
    map.set(thscode, { ticker, exchange, name, thscode });
  }

  // 2. 匹配中文/括号形式: 平潭发展(000592) / 粤桂股份（000833）
  const nameParenRegex = /([\u4e00-\u9fa5A-Za-z0-9]{2,8})[（(](\d{6})[)）]/g;
  while ((match = nameParenRegex.exec(text)) !== null) {
    const name = match[1];
    const ticker = match[2];
    const exchange = COMMON_NAME_MAP[ticker]?.exchange || (ticker.startsWith('6') ? 'SH' : 'SZ');
    const thscode = `${ticker}.${exchange}`;
    map.set(thscode, { ticker, exchange, name, thscode });
  }

  // 3. 匹配常见股名直接出现: 比如文本中出现 "粤桂股份"、"万邦达"、"比亚迪"
  for (const [code, info] of Object.entries(COMMON_NAME_MAP)) {
    if (text.includes(info.name)) {
      const thscode = `${code}.${info.exchange}`;
      if (!map.has(thscode)) {
        map.set(thscode, { ticker: code, exchange: info.exchange, name: info.name, thscode });
      }
    }
  }

  return Array.from(map.values());
}
