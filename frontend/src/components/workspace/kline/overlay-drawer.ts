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
import type { Annotation } from './annotate-api';

export interface OverlayConfig {
  showTD9: boolean;
  showPatterns: boolean;
  backendAnnotations: Annotation[];
}

export const globalOverlayConfig: OverlayConfig = {
  showTD9: true,
  showPatterns: true,
  backendAnnotations: [],
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
}

let cachedPatterns: Array<KLinePatternItem | null> = [];
let cachedPatternsLength = 0;
let cachedAnnLength = 0;

export function detectKLinePatterns(
  dataList: KLineData[],
  backendAnnotations: Annotation[] = [],
): Array<KLinePatternItem | null> {
  if (
    cachedPatternsLength === dataList.length &&
    cachedAnnLength === backendAnnotations.length &&
    cachedPatterns.length > 0
  ) {
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
        let color = '#3b82f6';
        let bgColor = 'rgba(59, 130, 246, 0.4)';
        let pos: 'top' | 'bottom' = 'bottom';
        if (ann.type === 's1') {
          color = '#ef4444';
          bgColor = 'rgba(239, 68, 68, 0.45)';
          pos = 'top';
        } else if (ann.type === 'b1') {
          color = '#10b981';
          bgColor = 'rgba(16, 185, 129, 0.45)';
          pos = 'bottom';
        } else if (ann.type === 'violent_k') {
          color = '#f59e0b';
          bgColor = 'rgba(245, 158, 11, 0.45)';
          pos = 'top';
        } else if (ann.type === 'key_k') {
          color = '#a855f7';
          bgColor = 'rgba(168, 85, 247, 0.45)';
          pos = 'bottom';
        }
        result[i] = {
          type: ann.type,
          text: ann.text,
          color,
          bgColor,
          position: pos,
        };
      }
    }
  }

  // 本地高性能检测经典 K 线组合形态
  for (let i = 2; i < dataList.length; i++) {
    if (result[i]) continue; // 优先保留模型已识别的形态

    const c = dataList[i];
    const p1 = dataList[i - 1];
    const p2 = dataList[i - 2];
    const range = c.high - c.low;
    if (range <= 0) continue;

    const isRed = c.close >= c.open;
    const isP1Red = p1.close >= p1.open;
    const isP2Red = p2.close >= p2.open;

    // 1. 阳包阴 (多头强势吞没)
    if (isRed && !isP1Red && c.close > p1.open && c.open <= p1.close) {
      result[i] = {
        type: 'yang_bao_yin',
        text: '阳包阴',
        color: '#ef4444',
        bgColor: 'rgba(239, 68, 68, 0.4)',
        position: 'bottom',
      };
      continue;
    }

    // 2. 阴包阳 (空头吞没防守)
    if (!isRed && isP1Red && c.close < p1.open && c.open >= p1.close) {
      result[i] = {
        type: 'yin_bao_yang',
        text: '阴包阳',
        color: '#10b981',
        bgColor: 'rgba(16, 185, 129, 0.4)',
        position: 'top',
      };
      continue;
    }

    // 3. 乌云压顶 (高位空头压制)
    if (!isRed && isP1Red && c.open > p1.high && c.close < (p1.open + p1.close) / 2) {
      result[i] = {
        type: 'dark_cloud',
        text: '乌云压顶',
        color: '#06b6d4',
        bgColor: 'rgba(6, 182, 212, 0.4)',
        position: 'top',
      };
      continue;
    }

    // 4. 曙光初现 (低位多头反攻)
    if (isRed && !isP1Red && c.open < p1.low && c.close > (p1.open + p1.close) / 2) {
      result[i] = {
        type: 'piercing_line',
        text: '曙光初现',
        color: '#f43f5e',
        bgColor: 'rgba(244, 63, 94, 0.4)',
        position: 'bottom',
      };
      continue;
    }

    // 5. 十字星 (关键转折点)
    if (Math.abs(c.close - c.open) / range < 0.1 && range / c.open > 0.015) {
      result[i] = {
        type: 'doji',
        text: '十字星',
        color: '#38bdf8',
        bgColor: 'rgba(56, 189, 248, 0.4)',
        position: isRed ? 'bottom' : 'top',
      };
      continue;
    }

    // 6. 早晨之星 (经典见底反转 3-K线组合)
    if (!isP2Red && (p1.high - p1.low) > 0 && Math.abs(p1.close - p1.open) / (p1.high - p1.low) < 0.3 && isRed && c.close > (p2.open + p2.close) / 2) {
      result[i] = {
        type: 'morning_star',
        text: '早晨之星',
        color: '#e11d48',
        bgColor: 'rgba(225, 29, 72, 0.4)',
        position: 'bottom',
      };
      continue;
    }

    // 7. 黄昏之星 (经典见顶回落 3-K线组合)
    if (isP2Red && (p1.high - p1.low) > 0 && Math.abs(p1.close - p1.open) / (p1.high - p1.low) < 0.3 && !isRed && c.close < (p2.open + p2.close) / 2) {
      result[i] = {
        type: 'evening_star',
        text: '黄昏之星',
        color: '#10b981',
        bgColor: 'rgba(16, 185, 129, 0.4)',
        position: 'top',
      };
      continue;
    }
  }

  cachedPatterns = result;
  cachedPatternsLength = dataList.length;
  cachedAnnLength = backendAnnotations.length;
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
  const color = isUp ? '#ef4444' : '#10b981';
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
    ctx.fillStyle = isUp ? '#fca5a5' : '#86efac';
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

    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(hx, hy);
    ctx.lineTo(elbowX, markY);
    ctx.lineTo(endX, markY);
    ctx.stroke();

    ctx.fillStyle = '#ef4444';
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

    ctx.strokeStyle = '#10b981';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(lx, ly);
    ctx.lineTo(elbowX, markY);
    ctx.lineTo(endX, markY);
    ctx.stroke();

    ctx.fillStyle = '#10b981';
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
  } = {},
) {
  const { showTD9 = true, showPatterns = true, backendAnnotations = [] } = options;
  if (!kLineDataList || kLineDataList.length === 0) return;

  const from = Math.max(0, visibleRange.from);
  const to = Math.min(kLineDataList.length - 1, visibleRange.to);

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
    const patterns = detectKLinePatterns(kLineDataList, backendAnnotations);
    for (let i = from; i <= to; i++) {
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
        drawCapsuleBadge(ctx, pat.text, x, y, pat.color, pat.bgColor, pat.position, candleY, 10);
      }
    }
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
  const color = isGolden ? '#ef4444' : '#10b981';
  const bgColor = isGolden ? 'rgba(239, 68, 68, 0.45)' : 'rgba(16, 185, 129, 0.45)';
  const badgeY = isGolden ? y - 10 : y + 10;
  drawCapsuleBadge(ctx, text, x, badgeY, color, bgColor, undefined, undefined, 9);
}
