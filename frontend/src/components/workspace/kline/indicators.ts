/**
 * 注册同花顺风格与 Zettaranc 专属量化指标到 KLineChart
 *
 * **元数据不在这里。** 名字、参数、默认周期、精度、主图/副图归属全部来自
 * config/indicators.yaml（经由生成的 indicator-meta.ts 读入）。改周期请改
 * config/indicators.yaml 再跑：
 *
 *   go test ./internal/indicators/ -run TestGeneratedFrontendModule -update
 *
 * 下面这些 calc* 函数是**公式实现**，按约定各自保留、不跨栈统一；统一的是参数。
 *
 * 指标体系：
 * 1. Z_MAIN (复合主图): DEMA10 白线 + 多空线 14/28/57/114 黄线 + BBI 牵牛绳
 * 2. Z_SIGNALS: 神奇九转 (TD 1..9) + K线形态气泡胶囊 + 最高/最低价引导线
 * 3. ZG_WHITE (白线): 10日 DEMA，主图叠加
 * 4. DG_YELLOW (黄线): 多空线 14/28/57/114，主图叠加
 * 5. Z_BBI (牵牛绳): 多空平衡均线 (3, 6, 12, 24)，主图叠加
 * 6. Z_VOL (经典同花顺成交量): 红绿量柱 + MA5 (黄) + MA10 (蓝) 均量线
 * 7. Z_MACD (同花顺风 MACD): DIFF + DEA + 柱状图 + [金叉]/[死叉] 实时胶囊徽章
 * 8. Z_KDJ (同花顺风 KDJ): K/D/J 三线走势 + [金叉]/[死叉] 实时胶囊徽章
 * 9. ZX_BRICK / Z_BRICK (砖型图): 同花顺知行砖型图
 * 10. Z_PCT_RET (区间涨跌幅): 3 日与 21 日**百分比涨跌幅**，副图曲线
 *     （2026-10-01 由 Z_RSL 改名 —— 它算的是涨跌幅；真正的 RSL 百分位排名
 *      在 DuckDB 的 zettaranc_rsl_rank_15 / _rank_105，两者不是同一个东西）
 *
 * **四块砖不在这个文件里。** 它不画在 K 线上，是个四项多空状态评分。
 * 2026-10-01 从此处删除（calcFourBricksDetails 当时零调用方，是死代码），
 * 计算实现在 python-service/zettaranc/four_bricks.py，agent 通过
 * zettaranc.four_bricks 工具读取，工作台顶部评分卡用的是另一套
 * （stock-score.ts 的 calcStockHoldingScore，基于砖型图 + 双线的 5 分制）。
 */

import { registerIndicator, LineType, PolygonType, IndicatorSeries, type KLineData } from 'klinecharts';
import { zettarancPalette as PAL } from './palette';
import { drawMainCanvasTongHuaShun, drawCrossBadge, globalOverlayConfig } from './overlay-drawer';
import { calcDEMA, calcLongBBI, calcZXBrick, type ZXBrickItem } from './stock-score';
import { ALL_INDICATORS, indicatorMeta } from './indicator-meta';

let registered = false;

/**
 * 把 indicators.yaml 里的颜色 token 翻译成实际颜色。
 *
 * 三个动态 token（volume_bar / macd_hist / brick）没有固定色：它们按当根 K 线的
 * 方向在 draw 时算，所以这里返回 null，由调用方自己定色。
 */
function paletteColor(token: string): string | null {
  const p = PAL() as unknown as Record<string, string>;
  return p[token] ?? null;
}

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

// 2. 简单移动平均线 (SMA)
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

// 3. 多空指标 (BBI = (MA3 + MA6 + MA12 + MA24) / 4)
//
// 与 stock-score.ts 的 calcBBI **有意不同**：这里的版本在任一条均线还没成形时
// 返回 null（画布上就是断线），stock-score 的版本会用已有均线做降级平均。
// 两者服务的场景不同（画线 vs 评分），但这是一处真实存在的语义分叉，
// conformance 测试把它记录为已知差异，不在这里偷偷抹平。
export function calcBBI(dataList: KLineData[]): Array<number | null> {
  const [p3, p6, p12, p24] = indicatorMeta('Z_BBI').series[0].params;
  const ma3 = calcSMA(dataList, p3);
  const ma6 = calcSMA(dataList, p6);
  const ma12 = calcSMA(dataList, p12);
  const ma24 = calcSMA(dataList, p24);
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

// 4. 区间涨跌幅 (百分比) —— 2026-10-01 由 calcRSL 改名而来。
// 它算的是 (close[i]-close[i-n])/close[i-n]*100，就是涨跌幅，不是相对强弱排名。
// 真正的 RSL（滚动窗口百分位排名，值域 [0,100]）在 DuckDB 的
// zettaranc_rsl_rank_15 / _rank_105，两者是不同指标，此前却共用 RSL 这个名字。
export function calcPctRet(dataList: KLineData[], period: number): Array<number | null> {
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

// 5. 成交量均线计算 (VOL + MA5 + MA10)
export function calcVOL(dataList: KLineData[]) {
  const [maShort, maLong] = indicatorMeta('Z_VOL').params.map((p) => p.value);
  const result: Array<{ vol: number; ma5: number | null; ma10: number | null }> = [];
  let sum5 = 0;
  let sum10 = 0;
  for (let i = 0; i < dataList.length; i++) {
    const vol = dataList[i]?.volume ?? 0;
    sum5 += vol;
    sum10 += vol;
    if (i >= maShort) sum5 -= (dataList[i - maShort]?.volume ?? 0);
    if (i >= maLong) sum10 -= (dataList[i - maLong]?.volume ?? 0);
    result.push({
      vol,
      ma5: i >= maShort - 1 ? Number((sum5 / maShort).toFixed(0)) : null,
      ma10: i >= maLong - 1 ? Number((sum10 / maLong).toFixed(0)) : null,
    });
  }
  return result;
}

// 6. MACD 指标计算 (DIF, DEA, MACD)
//
// 周期全部来自 indicators.yaml 的 Z_MACD (12,26,9)；不传参数时用 yaml 里的默认值。
// 注意 hist = (dif - dea) * 2 —— DuckDB 侧的 momentum_macd_12_26_9_hist 也是乘过 2 的，
// 这是历史约定，两边必须同时改。
export function calcMACD(dataList: KLineData[], shortP?: number, longP?: number, m?: number) {
  const declared = indicatorMeta('Z_MACD').params.map((p) => p.value);
  const short = shortP || declared[0];
  const long = longP || declared[1];
  const signal = m || declared[2];
  const kShort = 2 / (short + 1);
  const kLong = 2 / (long + 1);
  const kM = 2 / (signal + 1);
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

// 7. KDJ 指标计算 (K, D, J)
//
// n / k_smooth / d_smooth 来自 indicators.yaml 的 Z_KDJ (9,3,3)。
export function calcKDJ(dataList: KLineData[], n?: number) {
  const declared = indicatorMeta('Z_KDJ').params;
  const nPeriod = n || declared.find((p) => p.name === 'n')!.value;
  const kSmooth = declared.find((p) => p.name === 'k_smooth')!.value;
  const dSmooth = declared.find((p) => p.name === 'd_smooth')!.value;
  const result: Array<{ k: number; d: number; j: number }> = [];
  let k = 50, d = 50;
  for (let i = 0; i < dataList.length; i++) {
    let low = Infinity, high = -Infinity;
    const start = Math.max(0, i - nPeriod + 1);
    for (let j = start; j <= i; j++) {
      low = Math.min(low, dataList[j]?.low ?? low);
      high = Math.max(high, dataList[j]?.high ?? high);
    }
    const close = dataList[i]?.close ?? 0;
    const rsv = high === low ? 50 : ((close - low) / (high - low)) * 100;
    k = ((kSmooth - 1) * k + rsv) / kSmooth;
    d = ((dSmooth - 1) * d + k) / dSmooth;
    const jVal = kSmooth * k - (dSmooth - 1) * d;
    result.push({
      k: Number(k.toFixed(2)),
      d: Number(d.toFixed(2)),
      j: Number(jVal.toFixed(2)),
    });
  }
  return result;
}

/** 主图上的高级 canvas 装饰层：神奇九转 / 形态气泡 / 极值引导线。 */
function mainCanvasOverlay() {
  return ({ ctx, kLineDataList, visibleRange, xAxis, yAxis }: any) => {
    drawMainCanvasTongHuaShun(
      ctx,
      kLineDataList,
      visibleRange,
      xAxis,
      yAxis,
      globalOverlayConfig,
    );
    return false;
  };
}

/**
 * 金叉/死叉胶囊徽章的通用 draw。
 *
 * MACD 比 DIF/DEA、KDJ 比 K/D，逻辑完全一样，只是取的字段不同，所以合成一个
 * 函数而不是写两遍。cross 在 slow 线上打点（跟原来的行为一致）。
 */
function crossBadgeOverlay(calc: (data: KLineData[]) => Array<{ fast: number; slow: number }>) {
  return ({ ctx, kLineDataList, visibleRange, xAxis, yAxis }: any) => {
    const list = calc(kLineDataList);
    const from = Math.max(1, visibleRange.from);
    const to = Math.min(kLineDataList.length - 1, visibleRange.to);
    for (let i = from; i <= to; i++) {
      const prev = list[i - 1];
      const curr = list[i];
      if (!prev || !curr) continue;
      if (prev.fast <= prev.slow && curr.fast > curr.slow) {
        drawCrossBadge(ctx, true, xAxis.convertToPixel(i), yAxis.convertToPixel(curr.slow));
      } else if (prev.fast >= prev.slow && curr.fast < curr.slow) {
        drawCrossBadge(ctx, false, xAxis.convertToPixel(i), yAxis.convertToPixel(curr.slow));
      }
    }
    return false;
  };
}

/**
 * 条形 figure 的按值着色。
 *
 * indicators.yaml 用 color token 声明"这条柱按什么规则上色"，这里把 token 翻成
 * 具体规则：volume_bar 看当根 K 线涨跌，macd_hist 看柱子正负。
 */
function barStyles(token: string) {
  return (data: any) => {
    let color: string;
    if (token === 'volume_bar') {
      const kLine = data?.current?.kLineData;
      const isUp = kLine ? kLine.close >= kLine.open : true;
      color = isUp ? PAL().up : PAL().down;
    } else {
      const val = data?.current?.indicatorData?.macd ?? 0;
      color = val > 0 ? PAL().up : val < 0 ? PAL().down : '#6b7280';
    }
    return { style: PolygonType.Fill, color, borderColor: color };
  };
}

/**
 * 把 indicators.yaml 的 series 条目铺成 klinecharts 的 figures 数组。
 *
 * 顺序 = yaml 里的声明顺序，styles.lines 也按同一个顺序取 line 型 series，
 * 所以两边永远对得上，不会出现"颜色串到别的线"的老问题。
 */
function figuresFrom(indicatorId: string): any[] {
  return indicatorMeta(indicatorId).series.map((s) => {
    const fig: any = { key: s.key, title: `${s.label}: `, type: s.type === 'bar' ? 'bar' : 'line' };
    if (s.baseValue !== undefined) fig.baseValue = s.baseValue;
    if (s.type === 'bar') fig.styles = barStyles(s.color);
    return fig;
  });
}

/** line 型 series 的描边样式，颜色与线宽都来自 indicators.yaml。 */
function lineStyles(indicatorId: string): any[] {
  return indicatorMeta(indicatorId)
    .series.filter((s) => s.type === 'line')
    .map((s) => ({
      color: paletteColor(s.color) ?? PAL().neutral,
      size: s.lineWidth ?? 1.5,
      style: LineType.Solid,
      smooth: false,
      dashedValue: [2, 2],
    }));
}

/** klinecharts 的 series 类型：主图挂在价格轴上，副图用自己的轴。 */
function seriesOf(panel: 'main' | 'sub') {
  return panel === 'main' ? IndicatorSeries.Price : IndicatorSeries.Normal;
}

/**
 * 砖型图（ZX_BRICK / Z_BRICK）的注册体。
 *
 * 两个 id 共用同一份实现：Z_BRICK 是旧 id 的兼容别名，参数必须与 ZX_BRICK 一致
 * （internal/indicators 的 Validate 会强制这一点）。
 */
function createZXBrickIndicator(id: string) {
  const meta = indicatorMeta(id);
  return {
    name: meta.id,
    shortName: meta.shortName,
    series: seriesOf(meta.panel),
    calcParams: meta.calcParams ?? [],
    precision: meta.precision,
    figures: figuresFrom(id),
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
      const val = typeof data.brick === 'number' ? data.brick.toFixed(meta.precision) : '0.00';
      const color = data.direction === 'up' ? PAL().up : data.direction === 'down' ? PAL().down : PAL().neutral;
      return {
        name: meta.shortName,
        calcParamsText: '',
        values: [
          {
            title: { text: `${meta.series[0].label}: `, color: PAL().neutral },
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
  };
}

export function registerZettarancIndicators() {
  if (registered) return;
  try {
    // 0. Z_MAIN —— 复合主图：三条**互相独立**的线（不是一条线的多段）。
    //    白线/黄线/BBI 各自带自己的 series.params，calcParams 里的 [10,14] 是
    //    klinecharts 设置面板的格子，calc 不读它们（见 config/indicators.yaml）。
    const zMain = indicatorMeta('Z_MAIN');
    const mainWhite = zMain.series.find((s) => s.formula === 'DEMA')!;
    const mainYellow = zMain.series.find((s) => s.formula === 'LONGBBI')!;
    const mainBbi = zMain.series.find((s) => s.formula === 'BBI')!;
    registerIndicator({
      name: zMain.id,
      shortName: zMain.shortName,
      series: seriesOf(zMain.panel),
      calcParams: zMain.calcParams ?? [],
      precision: zMain.precision,
      figures: figuresFrom('Z_MAIN'),
      styles: { lines: lineStyles('Z_MAIN') },
      calc: (dataList: any) => {
        const white = calcDEMA(dataList, mainWhite.params[0]);
        const yellow = calcLongBBI(dataList, mainYellow.params);
        const bbi = calcBBI(dataList);
        return dataList.map((_: any, i: number) => ({
          zg_white: white[i],
          dg_yellow: yellow[i],
          bbi: bbi[i],
        }));
      },
      draw: mainCanvasOverlay(),
    } as any);

    // 0-b. Z_SIGNALS —— 纯装饰层：没有 series、不产生数值，只在 draw 里画
    //      神奇九转 / 形态气泡 / 极值引导线。
    const zSignals = indicatorMeta('Z_SIGNALS');
    registerIndicator({
      name: zSignals.id,
      shortName: zSignals.shortName,
      series: seriesOf(zSignals.panel),
      calcParams: zSignals.calcParams ?? [],
      figures: [],
      calc: (dataList: any) => dataList.map(() => ({})),
      draw: mainCanvasOverlay(),
    } as any);

    // 1. ZG_WHITE —— 白线（单线，主图）
    const zWhite = indicatorMeta('ZG_WHITE');
    registerIndicator({
      name: zWhite.id,
      shortName: zWhite.shortName,
      series: seriesOf(zWhite.panel),
      calcParams: zWhite.calcParams ?? [],
      precision: zWhite.precision,
      figures: figuresFrom('ZG_WHITE'),
      styles: { lines: lineStyles('ZG_WHITE') },
      calc: (dataList: any) => {
        const dema = calcDEMA(dataList, zWhite.series[0].params[0]);
        return dataList.map((_: any, i: number) => ({ [zWhite.series[0].key]: dema[i] }));
      },
      draw: mainCanvasOverlay(),
    } as any);

    // 2. DG_YELLOW —— 黄线 / 多空线（单线，主图）
    const dYellow = indicatorMeta('DG_YELLOW');
    registerIndicator({
      name: dYellow.id,
      shortName: dYellow.shortName,
      series: seriesOf(dYellow.panel),
      calcParams: dYellow.calcParams ?? [],
      precision: dYellow.precision,
      figures: figuresFrom('DG_YELLOW'),
      styles: { lines: lineStyles('DG_YELLOW') },
      calc: (dataList: any) => {
        const yellow = calcLongBBI(dataList, dYellow.series[0].params);
        return dataList.map((_: any, i: number) => ({ [dYellow.series[0].key]: yellow[i] }));
      },
    } as any);

    // 3. Z_BBI —— 牵牛绳多空平衡线（单线，主图）
    const zBbi = indicatorMeta('Z_BBI');
    registerIndicator({
      name: zBbi.id,
      shortName: zBbi.shortName,
      series: seriesOf(zBbi.panel),
      calcParams: zBbi.calcParams ?? [],
      precision: zBbi.precision,
      figures: figuresFrom('Z_BBI'),
      styles: { lines: lineStyles('Z_BBI') },
      calc: (dataList: any) => {
        const bbi = calcBBI(dataList);
        return dataList.map((_: any, i: number) => ({ [zBbi.series[0].key]: bbi[i] }));
      },
    } as any);

    // 4. Z_VOL —— 副图：红绿量柱 + 均量线
    const zVol = indicatorMeta('Z_VOL');
    registerIndicator({
      name: zVol.id,
      shortName: zVol.shortName,
      series: seriesOf(zVol.panel),
      calcParams: zVol.calcParams ?? [],
      precision: zVol.precision,
      figures: figuresFrom('Z_VOL'),
      styles: { lines: lineStyles('Z_VOL') },
      calc: (dataList: any) => calcVOL(dataList),
    } as any);

    // 5. Z_MACD —— 副图：DIFF + DEA + 柱状图 + 金叉/死叉徽章
    const zMacd = indicatorMeta('Z_MACD');
    const macdDefaults = zMacd.params.map((p) => p.value);
    registerIndicator({
      name: zMacd.id,
      shortName: zMacd.shortName,
      series: seriesOf(zMacd.panel),
      calcParams: zMacd.calcParams ?? [],
      precision: zMacd.precision,
      figures: figuresFrom('Z_MACD'),
      styles: { lines: lineStyles('Z_MACD') },
      calc: (dataList: any, indicator: any) => {
        const p1 = indicator.calcParams[0] || macdDefaults[0];
        const p2 = indicator.calcParams[1] || macdDefaults[1];
        const p3 = indicator.calcParams[2] || macdDefaults[2];
        return calcMACD(dataList, p1, p2, p3);
      },
      draw: crossBadgeOverlay((d) => calcMACD(d).map((m) => ({ fast: m.dif, slow: m.dea }))),
    } as any);

    // 6. Z_KDJ —— 副图：K/D/J 三线 + 金叉/死叉徽章
    const zKdj = indicatorMeta('Z_KDJ');
    const kdjN = zKdj.params.find((p) => p.name === 'n')!.value;
    registerIndicator({
      name: zKdj.id,
      shortName: zKdj.shortName,
      series: seriesOf(zKdj.panel),
      calcParams: zKdj.calcParams ?? [],
      precision: zKdj.precision,
      figures: figuresFrom('Z_KDJ'),
      styles: { lines: lineStyles('Z_KDJ') },
      calc: (dataList: any, indicator: any) => {
        const n = indicator.calcParams[0] || kdjN;
        return calcKDJ(dataList, n);
      },
      draw: crossBadgeOverlay((d) => calcKDJ(d).map((m) => ({ fast: m.k, slow: m.d }))),
    } as any);

    // 7. 砖型图。ZX_BRICK 是当前 id，Z_BRICK 是旧 id 的兼容别名。
    for (const brickId of ALL_INDICATORS.filter((i) => i.series[0]?.formula === 'ZX_BRICK').map((i) => i.id)) {
      registerIndicator(createZXBrickIndicator(brickId) as any);
    }

    // 8. Z_PCT_RET —— 区间涨跌幅（%）。2026-10-01 由 Z_RSL 改名。
    const zPctRet = indicatorMeta('Z_PCT_RET');
    const pctRetDefaults = zPctRet.params.map((p) => p.value);
    registerIndicator({
      name: zPctRet.id,
      shortName: zPctRet.shortName,
      series: seriesOf(zPctRet.panel),
      calcParams: zPctRet.calcParams ?? [],
      precision: zPctRet.precision,
      figures: figuresFrom('Z_PCT_RET'),
      styles: { lines: lineStyles('Z_PCT_RET') },
      calc: (dataList: any, indicator: any) => {
        const p1 = indicator.calcParams[0] || pctRetDefaults[0];
        const p2 = indicator.calcParams[1] || pctRetDefaults[1];
        const shortRet = calcPctRet(dataList, p1);
        const longRet = calcPctRet(dataList, p2);
        const [shortKey, longKey] = zPctRet.series.map((s) => s.key);
        return dataList.map((_: any, i: number) => ({
          [shortKey]: shortRet[i],
          [longKey]: longRet[i],
        }));
      },
    } as any);

    registered = true;
    console.log(`[zettaranc] indicators registered from config/indicators.yaml: ${ALL_INDICATORS.map((i) => i.id).join(', ')}`);
  } catch (err) {
    console.warn('[zettaranc] indicator register warning:', err);
  }
}
