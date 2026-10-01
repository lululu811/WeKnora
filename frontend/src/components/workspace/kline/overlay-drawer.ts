/**
 * 同花顺风格图表增强层 (TongHuaShun Style Overlay Engine)
 *
 * 核心功能：
 * 1. 神奇九转序列 (TD 9-Sequence: 1..8 紧凑标注，9 醒目方框预警变盘反弹/见顶)
 * 2. 经典 K 线形态气泡胶囊 (阳包阴、阴包阳、乌云压顶、十字星、早晨之星等)
 * 3. 极值标注引导线 (最高价与最低价箭头与价格标签 ← 1451.91)
 * 4. 副图金叉与死叉胶囊徽章 ([金叉] / [死叉])
 */

import type { KLineData } from './types';
import { zettarancPalette as PAL } from './palette';
import { isLlmAnnotation, annotationLabel, type Annotation } from './annotate-api';
import type { DrawableCandle, DrawablePattern } from './chart-patterns';

export interface OverlayConfig {
  showTD9: boolean;
  showPatterns: boolean;
  backendAnnotations: Annotation[];
  /** 几何形态与波浪的轮廓（下标已换算好，见 chart-patterns.ts）。 */
  patternGeometry: DrawablePattern[];
  /** 被用户关掉的气泡类型（战法标注 4 类 + 蜡烛形态若干类）。空 = 全显示。 */
  hiddenPatternTypes: string[];
  /**
   * 蜡烛形态标记（后端 TA-Lib 识别，下标已换算好）。
   *
   * 与 `patternGeometry` 分开：那是"画形状"（折线/虚线），这是"打标记"
   * （K 线上方的胶囊），用同一套间距避让和用户勾选逻辑。
   */
  candleMarks: DrawableCandle[];
  /**
   * 光标所在位置命中的形态名。**空数组 = 不做任何压暗**（常态）。
   *
   * 形态是按名字勾选的，所以这里也用名字而不是下标 —— 与用户看到的一致。
   */
  activePatternNames: string[];
}

export const globalOverlayConfig: OverlayConfig = {
  showTD9: true,
  showPatterns: true,
  backendAnnotations: [],
  patternGeometry: [],
  hiddenPatternTypes: [],
  candleMarks: [],
  activePatternNames: [],
};

export function setGlobalOverlayConfig(cfg: Partial<OverlayConfig>) {
  Object.assign(globalOverlayConfig, cfg);
}

// 1. 神奇九转数据结构与算法
export interface TD9Item {
  type: 'up' | 'down';
  count: number; // 1 ~ 9
}

let cachedTD9: Array<TD9Item | null> = [];
let cachedTD9DataLength = 0;

export function calcTD9(dataList: KLineData[]): Array<TD9Item | null> {
  if (cachedTD9DataLength === dataList.length && cachedTD9.length > 0) {
    return cachedTD9;
  }

  const result: Array<TD9Item | null> = new Array(dataList.length).fill(null);
  let upCount = 0;
  let downCount = 0;

  for (let i = 4; i < dataList.length; i++) {
    const curr = dataList[i]?.close;
    const ref = dataList[i - 4]?.close;
    if (typeof curr !== 'number' || typeof ref !== 'number') {
      upCount = 0;
      downCount = 0;
      continue;
    }

    if (curr > ref) {
      upCount++;
      downCount = 0;
      if (upCount >= 1 && upCount <= 9) {
        result[i] = { type: 'up', count: upCount };
      }
      if (upCount === 9) {
        upCount = 0; // 完成一轮九转后重置
      }
    } else if (curr < ref) {
      downCount++;
      upCount = 0;
      if (downCount >= 1 && downCount <= 9) {
        result[i] = { type: 'down', count: downCount };
      }
      if (downCount === 9) {
        downCount = 0;
      }
    } else {
      upCount = 0;
      downCount = 0;
    }
  }

  cachedTD9 = result;
  cachedTD9DataLength = dataList.length;
  return result;
}

// 2. K 线形态数据结构与算法
export interface KLinePatternItem {
  type: string;
  text: string;
  color: string;
  bgColor: string;
  position: 'top' | 'bottom';
  /**
   * 是否来自服务端标注（B1/S1/关键K/暴力K）。
   *
   * 本地检测器在 2440 根里能产出 671 个形态（光 doji 就 181 个），而服务端
   * 只给 5 个。绘制时必须能区分两者——服务端的那些是**信号**（聊天里引用的
   * 就是它们），本地的绝大多数是噪声，需要按间距裁剪。
   */
  fromBackend?: boolean;
}

/**
 * 两个形态胶囊之间至少隔多少根 K 线。
 *
 * 10 是实测出来的：五粮液 120 根可见区间里原有 36 个胶囊（完全糊住），
 * 间距 5 -> 16 个、8 -> 12 个、10 -> 9 个、15 -> 7 个。取 10 能在「保留足够
 * 提示」与「看得清 K 线」之间站住，且服务端的 5 个标注始终保留。
 */
const PATTERN_MIN_GAP_BARS = 10;

let cachedPatterns: Array<KLinePatternItem | null> = [];
let cachedKey = '';

/**
 * 缓存键必须覆盖**内容**，不能只比长度。
 *
 * 这个 memo 是为了避免每次重绘都跑一遍 O(n) 的形态检测，但最早的实现只比
 * `dataList.length` 与标注条数，于是「切到另一只 K 线根数相同的票」会直接命中
 * 上一只票的结果——用户看到的是别的股票的标注，而且不报错。更隐蔽的是
 * 「同一只票、标注条数相同但日期不同」（换周期、重新拉标注）也会命中。
 *
 * 这里取三个维度：K 线的根数与首尾时间戳、标注的日期与来源。
 * 首尾时间戳足以区分不同标的/不同周期，标注签名足以区分同一份 K 线上的
 * 不同标注集合。代价是每次调用多做一次 O(标注数) 的字符串拼接，与
 * O(K线数) 的检测相比可以忽略。
 */
function patternsCacheKey(
  dataList: KLineData[],
  backendAnnotations: Annotation[],
  candleMarks: readonly DrawableCandle[],
): string {
  const first = dataList.length > 0 ? dataList[0].timestamp : 0;
  const last = dataList.length > 0 ? dataList[dataList.length - 1].timestamp : 0;
  const annSig = backendAnnotations
    .map((a) => `${a.date}:${a.type}:${a.source ?? ''}`)
    .join(',');
  // 蜡烛标记也必须进键：它是异步到的，不进键的话第一次（空）的结果会被缓存住，
  // 后面拿到真数据也不再重算 —— 表现为"气泡永远不出来"。
  const candleSig = candleMarks.map((c) => `${c.index}:${c.type}`).join(',');
  return `${dataList.length}|${first}|${last}|${annSig}|${candleSig}`;
}

/**
 * 从可见区间里挑出**真正要画**的形态下标。
 *
 * 为什么必须挑：本地检测器对 2440 根能产出 671 个形态，按截图那样 100 多根的
 * 可见区间里就有 36 个胶囊——它们互相叠在一起，把 K 线整个盖住，什么都读不出来。
 * 实测：加 10 根最小间距后降到 9 个，可读性恢复。
 *
 * 优先级是明确的，不是按时间顺序先到先得：
 *  1. **服务端标注全部保留**。它们是聊天里引用到的那些位，也是唯一经过确认的
 *     信号；为了排版把它们挤掉，等于把功能做没了。
 *  2. 本地检测按时间顺序，与已占位者距离小于 `minGap` 的直接丢弃。
 *
 * 返回排序后的下标数组。
 */
export function selectPatternIndicesToDraw(
  patterns: Array<KLinePatternItem | null>,
  from: number,
  to: number,
  minGap: number,
): number[] {
  const kept: number[] = [];
  const inWindow: number[] = [];
  for (let i = from; i <= to; i++) {
    if (patterns[i]) inWindow.push(i);
  }
  // 第一轮：服务端标注无条件占位
  for (const i of inWindow) {
    if (patterns[i]?.fromBackend) kept.push(i);
  }
  // 第二轮：本地检测按间距让位
  const gap = Math.max(0, minGap);
  for (const i of inWindow) {
    if (patterns[i]?.fromBackend) continue;
    if (kept.some((k) => Math.abs(k - i) < gap)) continue;
    kept.push(i);
  }
  return kept.sort((a, b) => a - b);
}

export function detectKLinePatterns(
  dataList: KLineData[],
  backendAnnotations: Annotation[] = [],
  candleMarks: readonly DrawableCandle[] = [],
): Array<KLinePatternItem | null> {
  const key = patternsCacheKey(dataList, backendAnnotations, candleMarks);
  if (key === cachedKey && cachedPatterns.length > 0) {
    return cachedPatterns;
  }

  const result: Array<KLinePatternItem | null> = new Array(dataList.length).fill(null);

  // 首先合并后端 AI 标注（精确匹配日期）
  if (backendAnnotations && backendAnnotations.length > 0) {
    const annMap = new Map<string, Annotation>();
    backendAnnotations.forEach((ann) => {
      annMap.set(ann.date, ann);
    });

    for (let i = 0; i < dataList.length; i++) {
      const d = dataList[i];
      const dt = new Date(d.timestamp);
      const isoDate = dt.toISOString().split('T')[0];
      const y = dt.getFullYear();
      const m = String(dt.getMonth() + 1).padStart(2, '0');
      const day = String(dt.getDate()).padStart(2, '0');
      const localDate = `${y}-${m}-${day}`;
      const ann = annMap.get(isoDate) || annMap.get(localDate);
      if (ann) {
        let color = PAL().keyBlue;
        let bgColor = 'rgba(59, 130, 246, 0.4)';
        let pos: 'top' | 'bottom' = 'bottom';
        if (ann.type === 's1') {
          color = PAL().up;
          bgColor = 'rgba(239, 68, 68, 0.45)';
          pos = 'top';
        } else if (ann.type === 'b1') {
          color = PAL().down;
          bgColor = 'rgba(16, 185, 129, 0.45)';
          pos = 'bottom';
        } else if (ann.type === 'violent_k') {
          color = PAL().auxAmber;
          bgColor = 'rgba(245, 158, 11, 0.45)';
          pos = 'top';
        } else if (ann.type === 'key_k') {
          color = PAL().purple;
          bgColor = 'rgba(168, 85, 247, 0.45)';
          pos = 'bottom';
        }

        // 模型主张的位与算法识别用**同一套颜色 + 不同形状**。
        //
        // 不用"换一种颜色"来区分：深色画布上可选色本来就少，金色/琥珀/橙色
        // 挤在一起分不清，红绿色觉障碍的用户更看不出差别。改用实心 vs 描边
        // 这个不依赖色觉的维度，加上标签前缀，三重区分。
        if (isLlmAnnotation(ann)) {
          bgColor = 'transparent';
        }

        result[i] = {
          type: ann.type,
          text: annotationLabel(ann),
          color,
          bgColor,
          position: pos,
          fromBackend: true,
        };
      }
    }
  }

  // 本地高性能检测经典 K 线组合形态
  // 蜡烛形态：由后端用 TA-Lib 识别（`candles_cdl_*` 列），这里只负责摆放。
  //
  // 这里**原来有一套手写的检测**（阳包阴/十字星/早晨之星…）。已删除，因为
  // 两套代码检测同一件事是最糟的状态：判定标准不同，同一根 K 线在两边可能
  // 结论相反，用户看到的是随机的。现在统一走 TA-Lib。
  for (const mark of candleMarks) {
    if (mark.index < 0 || mark.index >= dataList.length) continue;
    // 一根 K 线上只放一个胶囊：叠三个谁也读不出来，后到的不覆盖先到的。
    if (result[mark.index]) continue;
    const bar = dataList[mark.index];
    const isBull = mark.direction === 'bullish';
    const isBear = mark.direction === 'bearish';
    result[mark.index] = {
      type: mark.type,
      text: mark.name,
      color: isBull ? PAL().up : isBear ? PAL().down : PAL().patternDoji,
      bgColor: isBull
        ? 'rgba(239, 68, 68, 0.4)'
        : isBear
          ? 'rgba(16, 185, 129, 0.4)'
          : 'rgba(56, 189, 248, 0.35)',
      // 阳线标在下方、阴线标在上方，尽量不压住实体。
      position: bar.close >= bar.open ? 'bottom' : 'top',
    };
  }

  cachedPatterns = result;
  cachedKey = key;
  return result;
}

// 3. 基础画布绘制辅助函数
export function drawRoundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  ctx.beginPath();
  if (typeof (ctx as any).roundRect === 'function') {
    (ctx as any).roundRect(x, y, w, h, r);
  } else {
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r);
    ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    ctx.lineTo(x + r, y + h);
    ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r);
    ctx.arcTo(x, y, x + r, y, r);
  }
  ctx.closePath();
}

// 绘制同花顺胶囊气泡徽章（带有精致虚线引导针）
export function drawCapsuleBadge(
  ctx: CanvasRenderingContext2D,
  text: string,
  cx: number,
  cy: number,
  color: string,
  bgColor: string,
  position?: 'top' | 'bottom',
  candleY?: number,
  fontSize = 10,
) {
  ctx.save();
  ctx.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", sans-serif`;
  const textWidth = ctx.measureText(text).width;
  const padX = 6;
  const h = fontSize + 7;
  const w = textWidth + padX * 2;
  const x = cx - w / 2;
  const y = cy - h / 2;

  // 1. 引导针微线
  if (typeof candleY === 'number' && position) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.setLineDash([1, 1]);
    ctx.beginPath();
    if (position === 'top') {
      ctx.moveTo(cx, cy + h / 2);
      ctx.lineTo(cx, candleY);
    } else {
      ctx.moveTo(cx, cy - h / 2);
      ctx.lineTo(cx, candleY);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // 2. 深色背景与外边框
  drawRoundRect(ctx, x, y, w, h, 3);
  ctx.fillStyle = '#0f172a';
  ctx.fill();

  ctx.fillStyle = bgColor;
  ctx.fill();

  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  ctx.stroke();

  // 3. 徽章文本
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, cx, cy);
  ctx.restore();
}

// 绘制九转序列 (1~8 紧凑标注，9 号加粗高亮预警框)
export function drawTD9Badge(
  ctx: CanvasRenderingContext2D,
  item: TD9Item,
  x: number,
  candleY: number,
) {
  ctx.save();
  const isUp = item.type === 'up';
  // 上涨序列在 K 线上方（红系变盘），下跌序列在下方（绿系反弹）
  const color = isUp ? PAL().up : PAL().down;
  const y = isUp ? candleY - 13 : candleY + 13;

  if (item.count === 9) {
    const boxSize = 16;
    drawRoundRect(ctx, x - boxSize / 2, y - boxSize / 2, boxSize, boxSize, 3);
    ctx.fillStyle = isUp ? 'rgba(239, 68, 68, 0.9)' : 'rgba(16, 185, 129, 0.9)';
    ctx.fill();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1.2;
    ctx.stroke();

    ctx.font = 'bold 11px monospace';
    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('9', x, y);
  } else {
    ctx.font = 'bold 10px monospace';
    ctx.fillStyle = isUp ? PAL().td9Up : PAL().td9Down;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(item.count), x, y);
  }
  ctx.restore();
}

// 绘制最高价与最低价引导标签 (同花顺折线引出风格，规避与K线形态气泡和九转重叠)
export function drawHighLowPriceMarks(
  ctx: CanvasRenderingContext2D,
  kLineDataList: KLineData[],
  from: number,
  to: number,
  xAxis: any,
  yAxis: any,
) {
  if (kLineDataList.length === 0 || from < 0) return;

  let maxHigh = -Infinity;
  let maxIdx = -1;
  let minLow = Infinity;
  let minIdx = -1;

  const start = Math.max(0, from);
  const end = Math.min(kLineDataList.length - 1, to);

  for (let i = start; i <= end; i++) {
    const d = kLineDataList[i];
    if (d.high > maxHigh) {
      maxHigh = d.high;
      maxIdx = i;
    }
    if (d.low < minLow) {
      minLow = d.low;
      minIdx = i;
    }
  }

  ctx.save();
  ctx.font = 'bold 10px monospace';

  // 最高价
  if (maxIdx >= 0) {
    const hx = xAxis.convertToPixel(maxIdx);
    const hy = yAxis.convertToPixel(maxHigh);
    const text = `${maxHigh.toFixed(2)}`;
    const isRightSide = maxIdx > (from + to) / 2;
    const dir = isRightSide ? -1 : 1;
    const elbowX = hx + dir * 14;
    const endX = hx + dir * 32;
    const markY = Math.max(16, hy - 14);

    ctx.strokeStyle = PAL().up;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(hx, hy);
    ctx.lineTo(elbowX, markY);
    ctx.lineTo(endX, markY);
    ctx.stroke();

    ctx.fillStyle = PAL().up;
    ctx.textAlign = isRightSide ? 'right' : 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, endX + dir * 4, markY);
  }

  // 最低价
  if (minIdx >= 0) {
    const lx = xAxis.convertToPixel(minIdx);
    const ly = yAxis.convertToPixel(minLow);
    const text = `${minLow.toFixed(2)}`;
    const isRightSide = minIdx > (from + to) / 2;
    const dir = isRightSide ? -1 : 1;
    const elbowX = lx + dir * 14;
    const endX = lx + dir * 32;
    const markY = ly + 14;

    ctx.strokeStyle = PAL().down;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(lx, ly);
    ctx.lineTo(elbowX, markY);
    ctx.lineTo(endX, markY);
    ctx.stroke();

    ctx.fillStyle = PAL().down;
    ctx.textAlign = isRightSide ? 'right' : 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, endX + dir * 4, markY);
  }

  ctx.restore();
}

// 4. 主画布综合装饰绘制主入口
export function drawMainCanvasTongHuaShun(
  ctx: CanvasRenderingContext2D,
  kLineDataList: KLineData[],
  visibleRange: { from: number; to: number },
  xAxis: any,
  yAxis: any,
  options: {
    showTD9?: boolean;
    showPatterns?: boolean;
    backendAnnotations?: Annotation[];
    /** 几何形态与波浪的轮廓（下标已换算好，见 chart-patterns.ts）。 */
    patternGeometry?: DrawablePattern[];
    /** 被用户关掉的气泡类型。 */
    hiddenPatternTypes?: string[];
    /** 蜡烛形态标记（后端识别）。 */
    candleMarks?: DrawableCandle[];
    /** 光标命中的形态名（空 = 不压暗）。 */
    activePatternNames?: string[];
  } = {},
) {
  const {
    showTD9 = true,
    showPatterns = true,
    backendAnnotations = [],
    patternGeometry = [],
    hiddenPatternTypes = [],
    candleMarks = [],
    activePatternNames = [],
  } = options;
  if (!kLineDataList || kLineDataList.length === 0) return;

  const from = Math.max(0, visibleRange.from);
  const to = Math.min(kLineDataList.length - 1, visibleRange.to);

  // 两层标签共用一张占位表：气泡先登记（它锚定在某根 K 线上，不能挪），
  // 形态名后放（它是浮动的，可以往上让）。以前两层各排各的，于是
  // 「顶1 / 双顶 / 矩形整理」会叠在同一片区域上。
  const placedLabels: PlacedLabel[] = [];

  // 1. 绘制最高价与最低价引导标签
  drawHighLowPriceMarks(ctx, kLineDataList, from, to, xAxis, yAxis);

  // 2. 绘制神奇九转
  const td9List = showTD9 ? calcTD9(kLineDataList) : [];
  if (showTD9) {
    for (let i = from; i <= to; i++) {
      const td = td9List[i];
      if (td) {
        const x = xAxis.convertToPixel(i);
        const candleY = td.type === 'up'
          ? yAxis.convertToPixel(kLineDataList[i].high)
          : yAxis.convertToPixel(kLineDataList[i].low);
        drawTD9Badge(ctx, td, x, candleY);
      }
    }
  }

  // 3. 绘制形态胶囊徽章 (避让同柱九转标记)
  if (showPatterns) {
    const detected = detectKLinePatterns(kLineDataList, backendAnnotations, candleMarks);
    // 过滤必须发生在挑点之前：先挑再过滤的话，被关掉的类型仍然占着间距名额，
    // 结果是「关掉了 A 类，B 类的气泡反而更稀」。
    const patterns = hiddenPatternTypes.length > 0
      ? detected.map((p) => (p && hiddenPatternTypes.includes(p.type) ? null : p))
      : detected;
    // 先按间距+优先级挑出要画的，再逐个绘制。直接遍历可见区间会把 30 多个
    // 胶囊叠在同一片区域上，K 线被盖住就什么都读不出来了。
    const toDraw = selectPatternIndicesToDraw(patterns, from, to, PATTERN_MIN_GAP_BARS);
    for (const i of toDraw) {
      const pat = patterns[i];
      if (pat) {
        const x = xAxis.convertToPixel(i);
        const d = kLineDataList[i];
        const hasTD9 = Boolean(td9List[i]);
        const candleY = pat.position === 'top'
          ? yAxis.convertToPixel(d.high)
          : yAxis.convertToPixel(d.low);
        const y = pat.position === 'top'
          ? (hasTD9 ? candleY - 34 : candleY - 20)
          : (hasTD9 ? candleY + 34 : candleY + 20);
        // 先登记再画：登记时**不平移**，气泡必须留在它自己那根 K 线上。
        reserveLabel(ctx, pat.text, x, y, 10, placedLabels);
        drawCapsuleBadge(ctx, pat.text, x, y, pat.color, pat.bgColor, pat.position, candleY, 10);
      }
    }
  }

  // 4. 几何形态与波浪的轮廓画在**最上层**。
  //
  // 第一版画在最底层（避免盖住 K 线），结果是蜡烛把它们压得几乎看不见 ——
  // 「形态有了但看不清」。形态线本身是半透明带光晕的细线，压在 K 线上不会
  // 遮住价格读法，反而因为始终可见才起到「说」与「看」对上的作用。
  if (patternGeometry.length > 0) {
    drawPatternGeometry(ctx, patternGeometry, xAxis, yAxis, activePatternNames, placedLabels);
  }
}

// 5. 副图金叉与死叉胶囊徽章绘制
export function drawCrossBadge(
  ctx: CanvasRenderingContext2D,
  isGolden: boolean,
  x: number,
  y: number,
) {
  const text = isGolden ? '金叉' : '死叉';
  const color = isGolden ? PAL().up : PAL().down;
  const bgColor = isGolden ? 'rgba(239, 68, 68, 0.45)' : 'rgba(16, 185, 129, 0.45)';
  const badgeY = isGolden ? y - 10 : y + 10;
  drawCapsuleBadge(ctx, text, x, badgeY, color, bgColor, undefined, undefined, 9);
}

// ---------------------------------------------------------------------------
// 6. 几何形态与波浪的轮廓绘制
//
// 上面第 3 项的胶囊徽章只标注「这里有个形态」，不表达形状。这里把后端识别出的
// 关键点画成折线轮廓、把颈线/目标位/趋势边界画成虚线 —— 这样「说」和「看」
// 才真正对得上，而不是两件并排的事。
//
// 日期 -> 下标的换算已经在 chart-patterns.ts 里做完了，这里只负责像素映射。
// ---------------------------------------------------------------------------

function patternDirectionColor(direction: DrawablePattern['direction']): string {
  if (direction === 'bullish') return PAL().up;
  if (direction === 'bearish') return PAL().down;
  return '#94a3b8';
}

/**
 * 画一条**压在 K 线上也读得出来**的形态线。
 *
 * 先铺一层更宽的半透明底（halo）再压主线。只画主线的话，红绿 K 线一密，
 * 1px 的虚线就淹进去了 —— 这正是第一版「形态有了但看不清」的原因。
 */
function strokePatternLine(
  ctx: CanvasRenderingContext2D,
  ax: number, ay: number, bx: number, by: number,
  color: string,
  width: number,
  dash: number[],
  baseAlpha = 1,
) {
  for (const pass of [
    { w: width + 3, alpha: 0.16 },
    { w: width, alpha: 0.9 },
  ]) {
    ctx.save();
    ctx.strokeStyle = color;
    // **相乘**而不是覆盖：覆盖会把外层的"压暗"整个吃掉，于是光标联动对参考线
    // 完全失效 —— 线不动、只有徽章在变淡，看起来像"压暗只压了一半"。
    ctx.globalAlpha = pass.alpha * baseAlpha;
    ctx.lineWidth = pass.w;
    ctx.lineCap = 'round';
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
    ctx.restore();
  }
}

/** 取像素坐标；轴换算不出有限值时返回 null（缩放中途可能拿到 NaN）。 */
function safePx(axis: any, value: number): number | null {
  const px = axis?.convertToPixel?.(value);
  return Number.isFinite(px) ? px : null;
}

/** 顶点圆点：外面一圈深色，压在 K 线上不会和红绿蜡烛糊成一片。 */
function drawVertexDot(ctx: CanvasRenderingContext2D, x: number, y: number, color: string, r = 3.2) {
  ctx.save();
  ctx.fillStyle = '#0f172a';
  ctx.beginPath();
  ctx.arc(x, y, r + 1.6, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

/**
 * 胶囊徽章的尺寸（和 drawCapsuleBadge 里那条算法保持一致）。
 * 避让要在画之前算出矩形，所以这里必须自己量一次。
 */
function badgeSize(ctx: CanvasRenderingContext2D, text: string, fontSize: number) {
  ctx.save();
  ctx.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", sans-serif`;
  const w = ctx.measureText(text).width + 12;
  ctx.restore();
  return { w, h: fontSize + 7 };
}

export type PlacedLabel = { x: number; y: number; w: number; h: number };

/**
 * 只把位置**登记**下来，不平移。
 *
 * 气泡用这个：它的 x 锚定在某根 K 线上，挪走就等于指向了另一根 K 线 ——
 * 那比标签重叠更糟（重叠只是难看，错位是错误信息）。
 */
export function reserveLabel(
  ctx: CanvasRenderingContext2D,
  text: string,
  cx: number,
  cy: number,
  fontSize: number,
  placed: PlacedLabel[],
): number {
  const { w, h } = badgeSize(ctx, text, fontSize);
  placed.push({ x: cx - w / 2, y: cy - h / 2, w, h });
  return cy;
}

/**
 * 给标签找一个不压住别人的位置。
 *
 * 形态一多，顶点标签和形态名就会叠在一起 —— 叠住的标签比不画还难读，
 * 所以这里逐个记下已占的矩形，撞上了就往上让一行。
 */
function placeLabel(
  ctx: CanvasRenderingContext2D,
  text: string,
  cx: number,
  cy: number,
  fontSize: number,
  placed: PlacedLabel[],
): number {
  const { w, h } = badgeSize(ctx, text, fontSize);
  let y = cy;
  for (let guard = 0; guard < 24; guard++) {
    const left = cx - w / 2;
    const top = y - h / 2;
    const hit = placed.find(
      (r) => !(left + w < r.x || r.x + r.w < left || top + h < r.y || r.y + r.h < top),
    );
    if (!hit) break;
    y = hit.y - h - 3;
  }
  placed.push({ x: cx - w / 2, y: y - h / 2, w, h });
  return y;
}

export function drawPatternGeometry(
  ctx: CanvasRenderingContext2D,
  patterns: DrawablePattern[],
  xAxis: any,
  yAxis: any,
  activeNames: readonly string[] = [],
  sharedPlaced: PlacedLabel[] = [],
) {
  if (!patterns || patterns.length === 0) return;

  // 占位表由调用方传进来，气泡已经登记过了 —— 形态名要连同**气泡**一起避让，
  // 不能只管形态之间。不传时自己建一张（单测与独立调用走这条）。
  const placed: PlacedLabel[] = sharedPlaced;
  // 光标落在某根 K 线时，只把**那一段所属的**形态画亮，其余压暗。
  // 空集合 = 没悬停 / 悬停处没有任何形态 -> 全部常态，不压暗。
  const dimming = activeNames.length > 0;

  for (const pat of patterns) {
    const color = patternDirectionColor(pat.direction);
    // 压暗具体值取 0.18：再低就完全看不见（用户会以为形态消失了，而不是被压暗）；
    // 再高则和常态区分不开。标签也跟着淡，否则"压暗"只剩下线、名字还在抢注意力。
    const alpha = dimming && !activeNames.includes(pat.name) ? 0.18 : 1;

    // 参考线（颈线 / 目标位 / 上下边界）。长划线：和关键位的短虚线区分开，
    // 免得两种「虚线水平位」混在一起分不清谁是谁。
    for (const ln of pat.lines) {
      const [a, b] = ln.points;
      const ax = safePx(xAxis, a.index), ay = safePx(yAxis, a.price);
      const bx = safePx(xAxis, b.index), by = safePx(yAxis, b.price);
      if (ax === null || ay === null || bx === null || by === null) continue;
      strokePatternLine(ctx, ax, ay, bx, by, color, 1.8, [6, 4], alpha);
    }

    // 顶点轮廓：折线 + 顶点圆点 + 标签
    const px: Array<{ x: number; y: number; label: string }> = [];
    for (const p of pat.points) {
      const x = safePx(xAxis, p.index);
      const y = safePx(yAxis, p.price);
      if (x === null || y === null) continue;
      px.push({ x, y, label: p.label });
    }

    ctx.save();
    // drawVertexDot 不碰 globalAlpha，所以这里设一次就能盖住折线与顶点圆点。
    ctx.globalAlpha = alpha;
    for (let i = 1; i < px.length; i++) {
      strokePatternLine(ctx, px[i - 1].x, px[i - 1].y, px[i].x, px[i].y, color, 2.2, [], alpha);
    }
    for (const p of px) drawVertexDot(ctx, p.x, p.y, color);
    ctx.restore();

    // 顶点标签走胶囊徽章（深色底 + 彩色描边）：直接 fillText 的字在 K 线上
    // 基本读不出来，这也是第一版看不清的一部分。
    for (const p of px) {
      if (!p.label) continue;
      const ly = placeLabel(ctx, p.label, p.x, p.y - 17, 10, placed);
      ctx.save();
      ctx.globalAlpha = alpha;
      drawCapsuleBadge(ctx, p.label, p.x, ly, color, color, 'top', p.y, 10);
      ctx.restore();
    }

    // 形态名：锚在整个形态的左上角，让人一眼知道画的是什么。
    // 楔形/旗形没有顶点（只有参考线），所以锚点要连同参考线端点一起算。
    if (pat.name) {
      // 锚在最左端点**自己的 y** 上（不是全局最高点）。用最高点会让所有形态名
      // 都挤到图的上沿互相压住，反而更难读。
      // 候选点先收齐再取最左，避免在闭包里改可选值（TS 收窄会退化成 never）。
      const candidates: Array<{ x: number; y: number }> = [];
      for (const p of px) candidates.push({ x: p.x, y: p.y });
      for (const ln of pat.lines) {
        for (const pt of ln.points) {
          const x = safePx(xAxis, pt.index), y = safePx(yAxis, pt.price);
          if (x !== null && y !== null) candidates.push({ x, y });
        }
      }
      let anchor: { x: number; y: number } | null = null;
      for (const c of candidates) {
        if (anchor === null || c.x < anchor.x) anchor = c;
      }
      if (anchor !== null) {
        // 名字放端点右上角，避开端点圆点与顶点标签。
        const cx = anchor.x + 34;
        const ny = placeLabel(ctx, pat.name, cx, anchor.y - 14, 10, placed);
        ctx.save();
        ctx.globalAlpha = alpha;
        drawCapsuleBadge(ctx, pat.name, cx, ny, color, color, undefined, undefined, 10);
        ctx.restore();
      }
    }
  }
}
