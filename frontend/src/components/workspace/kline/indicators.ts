/**
 * 注册同花顺风格与 Zettaranc 专属量化指标到 KLineChart
 *
 * 核心指标体系：
 * 1. Z_SIGNALS (同花顺主图增强): 神奇九转 (TD 1..9) + K线形态气泡胶囊 + 最高/最低价引导线
 * 2. ZG_WHITE (白线): 10日 EMA，主图叠加
 * 3. DG_YELLOW (黄线): 14日 EMA，主图叠加
 * 4. Z_BBI (牵牛绳): 多空平衡均线 (3, 6, 12, 24)，主图叠加
 * 5. Z_VOL (经典同花顺成交量): 红绿量柱 + MA5 (黄) + MA10 (蓝) 均量线
 * 6. Z_MACD (同花顺风 MACD): DIFF + DEA + 柱状图 + [金叉]/[死叉] 实时胶囊徽章
 * 7. Z_KDJ (同花顺风 KDJ): K/D/J 三线走势 + [金叉]/[死叉] 实时胶囊徽章
 * 8. Z_BRICK (四砖情绪): 势能/均线/BBI/阴阳四砖共振 [-4, +4]，副图柱状图
 * 9. Z_RSL (相对强度): 3 日短线 RSL 与 21 日长线 RSL，副图曲线
 */

import { registerIndicator, LineType, PolygonType, IndicatorSeries, type KLineData } from 'klinecharts';
import { zettarancPalette as PAL } from './palette';
import { drawMainCanvasTongHuaShun, drawCrossBadge, globalOverlayConfig } from './overlay-drawer';
import { calcDEMA, calcLongBBI, calcZXBrick, type ZXBrickItem } from './stock-score';

let registered = false;

// 1. 指数移动平均线 (EMA)
function calcEMA(dataList: KLineData[], period: number): Array<number | null> {
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

// 2. 简单移动平均线 (SMA)
function calcSMA(dataList: KLineData[], period: number): Array<number | null> {
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

// 3. 多空指标 (BBI = (MA3 + MA6 + MA12 + MA24) / 4)
function calcBBI(dataList: KLineData[]): Array<number | null> {
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
    return null;
  });
}

// 4. 四砖共振得分与多维度砖型结构 (Four Bricks: score from -4 to +4)
export interface FourBrickItem {
  score: number; // -4 to +4
  text: string;
  bull1: boolean; // 短线: close >= ma5
  bull2: boolean; // 趋势: ema10 >= ema14
  bull3: boolean; // 多空: close >= bbi
  bull4: boolean; // 阴阳: close >= open
}

export function calcFourBricksDetails(dataList: KLineData[]): FourBrickItem[] {
  const ma5 = calcSMA(dataList, 5);
  const ema10 = calcEMA(dataList, 10);
  const ema14 = calcEMA(dataList, 14);
  const bbi = calcBBI(dataList);

  return dataList.map((d, i) => {
    const close = d?.close ?? 0;
    const open = d?.open ?? close;
    const m5 = ma5[i];
    const e10 = ema10[i];
    const e14 = ema14[i];
    const bb = bbi[i];

    const bull1 = m5 !== null ? close >= m5 : true;
    const bull2 = (e10 !== null && e14 !== null) ? e10 >= e14 : true;
    const bull3 = bb !== null ? close >= bb : true;
    const bull4 = close >= open;

    let score = 0;
    score += bull1 ? 1 : -1;
    score += bull2 ? 1 : -1;
    score += bull3 ? 1 : -1;
    score += bull4 ? 1 : -1;

    let text = '中性震荡';
    if (score === 4) text = '四砖全红(+4)';
    else if (score >= 2) text = '多头共振(+' + score + ')';
    else if (score === -4) text = '四砖翻绿(-4)';
    else if (score <= -2) text = '空头承压(' + score + ')';
    else text = '多空博弈(' + (score >= 0 ? '+' : '') + score + ')';

    return {
      score,
      text,
      bull1,
      bull2,
      bull3,
      bull4,
    };
  });
}

function calcFourBricks(dataList: KLineData[]): Array<number | null> {
  const details = calcFourBricksDetails(dataList);
  return details.map((d) => d.score);
}

// 5. 相对强度指标 (RSL)
function calcRSL(dataList: KLineData[], period: number): Array<number | null> {
  const result: Array<number | null> = [];
  for (let i = 0; i < dataList.length; i++) {
    if (i < period) {
      result.push(null);
      continue;
    }
    const prev = dataList[i - period]?.close;
    const curr = dataList[i]?.close;
    if (typeof prev === 'number' && typeof curr === 'number' && prev > 0) {
      result.push(Number((((curr - prev) / prev) * 100).toFixed(2)));
    } else {
      result.push(null);
    }
  }
  return result;
}

// 6. 成交量均线计算 (VOL + MA5 + MA10)
function calcVOL(dataList: KLineData[]) {
  const result: Array<{ vol: number; ma5: number | null; ma10: number | null }> = [];
  let sum5 = 0;
  let sum10 = 0;
  for (let i = 0; i < dataList.length; i++) {
    const vol = dataList[i]?.volume ?? 0;
    sum5 += vol;
    sum10 += vol;
    if (i >= 5) sum5 -= (dataList[i - 5]?.volume ?? 0);
    if (i >= 10) sum10 -= (dataList[i - 10]?.volume ?? 0);
    result.push({
      vol,
      ma5: i >= 4 ? Number((sum5 / 5).toFixed(0)) : null,
      ma10: i >= 9 ? Number((sum10 / 10).toFixed(0)) : null,
    });
  }
  return result;
}

// 7. MACD 指标计算 (DIF, DEA, MACD)
function calcMACD(dataList: KLineData[], shortP = 12, longP = 26, m = 9) {
  const kShort = 2 / (shortP + 1);
  const kLong = 2 / (longP + 1);
  const kM = 2 / (m + 1);
  let emaShort = dataList[0]?.close ?? 0;
  let emaLong = dataList[0]?.close ?? 0;
  let dea = 0;
  const result: Array<{ dif: number; dea: number; macd: number }> = [];

  for (let i = 0; i < dataList.length; i++) {
    const c = dataList[i]?.close ?? 0;
    emaShort = c * kShort + emaShort * (1 - kShort);
    emaLong = c * kLong + emaLong * (1 - kLong);
    const dif = emaShort - emaLong;
    if (i === 0) dea = dif;
    else dea = dif * kM + dea * (1 - kM);
    const macd = (dif - dea) * 2;
    result.push({
      dif: Number(dif.toFixed(2)),
      dea: Number(dea.toFixed(2)),
      macd: Number(macd.toFixed(2)),
    });
  }
  return result;
}

// 8. KDJ 指标计算 (K, D, J)
function calcKDJ(dataList: KLineData[], n = 9) {
  const result: Array<{ k: number; d: number; j: number }> = [];
  let k = 50, d = 50;
  for (let i = 0; i < dataList.length; i++) {
    let low = Infinity, high = -Infinity;
    const start = Math.max(0, i - n + 1);
    for (let j = start; j <= i; j++) {
      low = Math.min(low, dataList[j]?.low ?? low);
      high = Math.max(high, dataList[j]?.high ?? high);
    }
    const close = dataList[i]?.close ?? 0;
    const rsv = high === low ? 50 : ((close - low) / (high - low)) * 100;
    k = (2 * k + rsv) / 3;
    d = (2 * d + k) / 3;
    const jVal = 3 * k - 2 * d;
    result.push({
      k: Number(k.toFixed(2)),
      d: Number(d.toFixed(2)),
      j: Number(jVal.toFixed(2)),
    });
  }
  return result;
}

export function registerZettarancIndicators() {
  if (registered) return;
  try {
    // 0. Z_MAIN (战法核心主图: 10日白线 + 14日黄线 + BBI多空牵牛绳 + 同花顺风形态装饰)
    registerIndicator({
      name: 'Z_MAIN',
      shortName: '战法主图',
      series: IndicatorSeries.Price,
      calcParams: [10, 14],
      precision: 2,
      figures: [
        {
          key: 'zg_white',
          title: '白线: ',
          type: 'line',
        },
        {
          key: 'dg_yellow',
          title: '黄线: ',
          type: 'line',
        },
        {
          key: 'bbi',
          title: 'BBI: ',
          type: 'line',
        },
      ],
      styles: {
        lines: [
          { color: PAL().white, size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
          { color: PAL().yellow, size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
          { color: PAL().orange, size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
        ],
      },
      calc: (dataList: any) => {
        const white = calcDEMA(dataList, 10);
        const yellow = calcLongBBI(dataList, [14, 28, 57, 114]);
        const bbi = calcBBI(dataList);
        return dataList.map((_: any, i: number) => ({
          zg_white: white[i],
          dg_yellow: yellow[i],
          bbi: bbi[i],
        }));
      },
      draw: ({ ctx, kLineDataList, visibleRange, xAxis, yAxis }: any) => {
        drawMainCanvasTongHuaShun(
          ctx,
          kLineDataList,
          visibleRange,
          xAxis,
          yAxis,
          globalOverlayConfig,
        );
        return false;
      },
    } as any);

    // 0-b. Z_SIGNALS (同花顺风格主图装饰增强：神奇九转 + K线形态胶囊 + 极值价格标记)
    registerIndicator({
      name: 'Z_SIGNALS',
      shortName: '信号层',
      series: IndicatorSeries.Price,
      calcParams: [],
      figures: [],
      calc: (dataList: any) => dataList.map(() => ({})),
      draw: ({ ctx, kLineDataList, visibleRange, xAxis, yAxis }: any) => {
        drawMainCanvasTongHuaShun(
          ctx,
          kLineDataList,
          visibleRange,
          xAxis,
          yAxis,
          globalOverlayConfig,
        );
        return false;
      },
    } as any);

    // 1. ZG_WHITE (白线: 二次平滑EMA10 - 主图)
    registerIndicator({
      name: 'ZG_WHITE',
      shortName: '白线',
      series: IndicatorSeries.Price,
      calcParams: [10],
      figures: [
        {
          key: 'zg_white',
          title: '白线(10): ',
          type: 'line',
        },
      ],
      styles: {
        lines: [{ color: PAL().white, size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2] }],
      },
      calc: (dataList: any) => {
        const dema = calcDEMA(dataList, 10);
        return dataList.map((_: any, i: number) => ({ zg_white: dema[i] }));
      },
      draw: ({ ctx, kLineDataList, visibleRange, xAxis, yAxis }: any) => {
        drawMainCanvasTongHuaShun(ctx, kLineDataList, visibleRange, xAxis, yAxis, globalOverlayConfig);
        return false;
      },
    } as any);

    // 2. DG_YELLOW (黄线: 多空线/大哥线 14/28/57/114 - 主图)
    registerIndicator({
      name: 'DG_YELLOW',
      shortName: '黄线',
      series: IndicatorSeries.Price,
      calcParams: [14, 28, 57, 114],
      figures: [
        {
          key: 'dg_yellow',
          title: '黄线(14/28/57/114): ',
          type: 'line',
        },
      ],
      styles: {
        lines: [{ color: PAL().yellow, size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2] }],
      },
      calc: (dataList: any) => {
        const yellow = calcLongBBI(dataList, [14, 28, 57, 114]);
        return dataList.map((_: any, i: number) => ({ dg_yellow: yellow[i] }));
      },
    } as any);

    // 3. Z_BBI (牵牛绳多空平衡线 - 主图叠加)
    registerIndicator({
      name: 'Z_BBI',
      shortName: 'BBI',
      series: IndicatorSeries.Price,
      calcParams: [3, 6, 12, 24],
      figures: [
        {
          key: 'bbi',
          title: 'BBI: ',
          type: 'line',
        },
      ],
      styles: {
        lines: [{ color: PAL().orange, size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2] }],
      },
      calc: (dataList: any) => {
        const bbi = calcBBI(dataList);
        return dataList.map((_: any, i: number) => ({ bbi: bbi[i] }));
      },
    } as any);

    // 4. Z_VOL (同花顺风成交量 - 副图: 红绿量柱 + MA5均量线 + MA10均量线)
    registerIndicator({
      name: 'Z_VOL',
      shortName: '成交量',
      series: IndicatorSeries.Normal,
      calcParams: [5, 10],
      precision: 0,
      figures: [
        {
          key: 'vol',
          title: '总量: ',
          type: 'bar',
          baseValue: 0,
          styles: (data: any) => {
            const kLine = data?.current?.kLineData;
            const isUp = kLine ? kLine.close >= kLine.open : true;
            const color = isUp ? PAL().up : PAL().down;
            return {
              style: PolygonType.Fill,
              color,
              borderColor: color,
            };
          },
        },
        {
          key: 'ma5',
          title: 'MA5: ',
          type: 'line',
        },
        {
          key: 'ma10',
          title: 'MA10: ',
          type: 'line',
        },
      ],
      styles: {
        lines: [
          { color: PAL().auxAmber, size: 1.2, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
          { color: PAL().sky, size: 1.2, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
        ],
      },
      calc: (dataList: any) => {
        return calcVOL(dataList);
      },
    } as any);

    // 5. Z_MACD (同花顺风 MACD - 副图: DIFF + DEA + 柱状图 + [金叉]/[死叉] 胶囊徽章)
    registerIndicator({
      name: 'Z_MACD',
      shortName: 'MACD',
      series: IndicatorSeries.Normal,
      calcParams: [12, 26, 9],
      figures: [
        {
          key: 'dif',
          title: 'DIFF: ',
          type: 'line',
        },
        {
          key: 'dea',
          title: 'DEA: ',
          type: 'line',
        },
        {
          key: 'macd',
          title: 'MACD: ',
          type: 'bar',
          baseValue: 0,
          styles: (data: any) => {
            const val = data?.current?.indicatorData?.macd ?? 0;
            const color = val > 0 ? PAL().up : val < 0 ? PAL().down : '#6b7280';
            return {
              style: PolygonType.Fill,
              color,
              borderColor: color,
            };
          },
        },
      ],
      styles: {
        lines: [
          { color: PAL().auxAmber, size: 1.3, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
          { color: PAL().auxSky, size: 1.3, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
        ],
      },
      calc: (dataList: any, indicator: any) => {
        const p1 = indicator.calcParams[0] || 12;
        const p2 = indicator.calcParams[1] || 26;
        const p3 = indicator.calcParams[2] || 9;
        return calcMACD(dataList, p1, p2, p3);
      },
      draw: ({ ctx, kLineDataList, visibleRange, xAxis, yAxis }: any) => {
        const macdList = calcMACD(kLineDataList);
        const from = Math.max(1, visibleRange.from);
        const to = Math.min(kLineDataList.length - 1, visibleRange.to);
        for (let i = from; i <= to; i++) {
          const prev = macdList[i - 1];
          const curr = macdList[i];
          if (!prev || !curr) continue;
          if (prev.dif <= prev.dea && curr.dif > curr.dea) {
            // 金叉
            const x = xAxis.convertToPixel(i);
            const y = yAxis.convertToPixel(curr.dea);
            drawCrossBadge(ctx, true, x, y);
          } else if (prev.dif >= prev.dea && curr.dif < curr.dea) {
            // 死叉
            const x = xAxis.convertToPixel(i);
            const y = yAxis.convertToPixel(curr.dea);
            drawCrossBadge(ctx, false, x, y);
          }
        }
        return false;
      },
    } as any);

    // 6. Z_KDJ (同花顺风 KDJ - 副图: K/D/J 三线走势 + [金叉]/[死叉] 胶囊徽章)
    registerIndicator({
      name: 'Z_KDJ',
      shortName: 'KDJ',
      series: IndicatorSeries.Normal,
      calcParams: [9, 3, 3],
      figures: [
        {
          key: 'k',
          title: 'K: ',
          type: 'line',
        },
        {
          key: 'd',
          title: 'D: ',
          type: 'line',
        },
        {
          key: 'j',
          title: 'J: ',
          type: 'line',
        },
      ],
      styles: {
        lines: [
          { color: PAL().auxOrange, size: 1.3, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
          { color: PAL().auxSky, size: 1.3, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
          { color: PAL().kdjJ, size: 1.3, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
        ],
      },
      calc: (dataList: any, indicator: any) => {
        const n = indicator.calcParams[0] || 9;
        return calcKDJ(dataList, n);
      },
      draw: ({ ctx, kLineDataList, visibleRange, xAxis, yAxis }: any) => {
        const kdjList = calcKDJ(kLineDataList);
        const from = Math.max(1, visibleRange.from);
        const to = Math.min(kLineDataList.length - 1, visibleRange.to);
        for (let i = from; i <= to; i++) {
          const prev = kdjList[i - 1];
          const curr = kdjList[i];
          if (!prev || !curr) continue;
          if (prev.k <= prev.d && curr.k > curr.d) {
            // 金叉
            const x = xAxis.convertToPixel(i);
            const y = yAxis.convertToPixel(curr.d);
            drawCrossBadge(ctx, true, x, y);
          } else if (prev.k >= prev.d && curr.k < curr.d) {
            // 死叉
            const x = xAxis.convertToPixel(i);
            const y = yAxis.convertToPixel(curr.d);
            drawCrossBadge(ctx, false, x, y);
          }
        }
        return false;
      },
    } as any);

    // 7. Z_BRICK (四砖情绪: 短线/趋势/多空/阴阳 4层堆叠彩色实体砖型阵列)
    // 7. ZX_BRICK (同花顺知行砖型图: 短期砖型图指标v2026 VAR1A..VAR6A 实体台阶砖块)
    const createZXBrickIndicator = (name: string, shortName: string) => ({
      name,
      shortName,
      series: IndicatorSeries.Normal,
      calcParams: [],
      precision: 2,
      figures: [
        {
          key: 'brick',
          title: '砖型图: ',
          type: 'line',
        },
      ],
      calc: (dataList: KLineData[]) => {
        const items = calcZXBrick(dataList);
        return items.map((it) => ({
          brick: it.brick,
          prevBrick: it.prevBrick,
          direction: it.direction,
          stepCount: it.stepCount,
          countText: it.countText,
          isBuyPoint: it.isBuyPoint,
          isRiskPoint: it.isRiskPoint,
        }));
      },
      createTooltipDataSource: ({ indicator, crosshair, kLineDataList }: any) => {
        const activeIdx = crosshair && crosshair.dataIndex >= 0 ? crosshair.dataIndex : kLineDataList.length - 1;
        const data = (indicator.result?.[activeIdx] || {}) as any;
        const val = typeof data.brick === 'number' ? data.brick.toFixed(2) : '0.00';
        const color = data.direction === 'up' ? PAL().up : data.direction === 'down' ? PAL().down : PAL().neutral;
        return {
          name: 'ZX砖型图',
          calcParamsText: '',
          values: [
            {
              title: { text: '砖型图: ', color: PAL().neutral },
              value: { text: `${val}  [${data.countText || '震荡'}]`, color },
            },
          ],
        };
      },
      draw: ({ ctx, kLineDataList, visibleRange, bounding, barSpace, xAxis, yAxis }: any) => {
        const items = calcZXBrick(kLineDataList);
        const from = Math.max(0, visibleRange.from);
        const to = Math.min(kLineDataList.length - 1, visibleRange.to);

        ctx.save();

        // 1. 绘制零轴基准跑道线
        const yZero = Math.round(yAxis.convertToPixel(0));
        if (yZero >= 0 && yZero <= bounding.height) {
          ctx.beginPath();
          ctx.strokeStyle = 'rgba(148, 163, 184, 0.2)';
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);
          ctx.moveTo(0, yZero);
          ctx.lineTo(bounding.width, yZero);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        // 2. 逐根绘制同花顺实体阶梯砖块 (STICKLINE)
        const barW = barSpace.gapBar;
        const brickW = Math.max(3, Math.min(22, Math.floor(barW * 0.75)));

        for (let i = from; i <= to; i++) {
          const item = items[i];
          if (!item) continue;

          const cx = xAxis.convertToPixel(i);
          const x = Math.round(cx - brickW / 2);

          const y1 = yAxis.convertToPixel(item.prevBrick);
          const y2 = yAxis.convertToPixel(item.brick);
          const topY = Math.round(Math.min(y1, y2));
          const bottomY = Math.round(Math.max(y1, y2));
          const h = Math.max(2.5, bottomY - topY);

          if (item.direction === 'up') {
            // 红色上升砖块
            ctx.fillStyle = PAL().up;
            ctx.strokeStyle = '#dc2626';
            ctx.lineWidth = 1;
            ctx.beginPath();
            if (typeof (ctx as any).roundRect === 'function') {
              (ctx as any).roundRect(x, topY, brickW, h, 1.5);
            } else {
              ctx.rect(x, topY, brickW, h);
            }
            ctx.fill();
            ctx.stroke();

            // 数砖数字标记 (1..4)
            if (item.stepCount > 0 && item.stepCount <= 9) {
              ctx.font = 'bold 9px -apple-system, sans-serif';
              ctx.textAlign = 'center';
              ctx.textBaseline = 'bottom';
              ctx.fillStyle = item.stepCount >= 4 ? PAL().auxAmber : PAL().up;
              ctx.fillText(String(item.stepCount), cx, topY - 1);

              // 红四清仓/减仓预警
              if (item.stepCount >= 4) {
                ctx.font = 'bold 8px -apple-system, sans-serif';
                ctx.fillStyle = PAL().auxAmber;
                ctx.fillText('减', cx, topY - 10);
              }
            }
          } else if (item.direction === 'down') {
            // 绿色下降砖块
            ctx.fillStyle = PAL().down;
            ctx.strokeStyle = '#059669';
            ctx.lineWidth = 1;
            ctx.beginPath();
            if (typeof (ctx as any).roundRect === 'function') {
              (ctx as any).roundRect(x, topY, brickW, h, 1.5);
            } else {
              ctx.rect(x, topY, brickW, h);
            }
            ctx.fill();
            ctx.stroke();

            // 绿砖数字
            if (item.stepCount > 0 && item.stepCount <= 9) {
              ctx.font = 'bold 9px -apple-system, sans-serif';
              ctx.textAlign = 'center';
              ctx.textBaseline = 'top';
              ctx.fillStyle = PAL().down;
              ctx.fillText(String(item.stepCount), cx, bottomY + 2);

              // 翻绿第一根: 止损提醒
              if (item.stepCount === 1) {
                ctx.font = 'bold 8px -apple-system, sans-serif';
                ctx.fillStyle = PAL().up;
                ctx.fillText('止', cx, bottomY + 11);
              }
            }
          } else {
            // 平局砖块
            if (item.brick > 0) {
              ctx.fillStyle = PAL().neutral;
              ctx.fillRect(x, Math.round(y2 - 1), brickW, 2);
            }
          }
        }

        ctx.restore();
        return true; // 拦截默认折线，呈现纯同花顺梯级砖块
      },
    });

    registerIndicator(createZXBrickIndicator('ZX_BRICK', 'ZX砖型图') as any);
    registerIndicator(createZXBrickIndicator('Z_BRICK', 'ZX砖型图') as any);

    // 8. Z_RSL (相对强度曲线 - 副图)
    registerIndicator({
      name: 'Z_RSL',
      shortName: 'RSL',
      series: IndicatorSeries.Normal,
      calcParams: [3, 21],
      figures: [
        {
          key: 'rsl_short',
          title: 'RSL短(3): ',
          type: 'line',
        },
        {
          key: 'rsl_long',
          title: 'RSL长(21): ',
          type: 'line',
        },
      ],
      styles: {
        lines: [
          { color: PAL().sky, size: 1.5, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
          { color: PAL().purple, size: 1.5, style: LineType.Solid, smooth: false, dashedValue: [2, 2] },
        ],
      },
      calc: (dataList: any, indicator: any) => {
        const p1 = indicator.calcParams[0] || 3;
        const p2 = indicator.calcParams[1] || 21;
        const rslShort = calcRSL(dataList, p1);
        const rslLong = calcRSL(dataList, p2);
        return dataList.map((_: any, i: number) => ({
          rsl_short: rslShort[i],
          rsl_long: rslLong[i],
        }));
      },
    } as any);

    registered = true;
    console.log('[zettaranc] indicators registered: Z_MAIN, Z_SIGNALS, ZG_WHITE, DG_YELLOW, Z_BBI, Z_VOL, Z_MACD, Z_KDJ, Z_BRICK, Z_RSL');
  } catch (err) {
    console.warn('[zettaranc] indicator register warning:', err);
  }
}
