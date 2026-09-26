/**
 * 标注叠加层 - 在 K 线图上画 Zettaranc 形态标注
 *
 * 实现原理：
 * 1. 监听 K 线图容器大小变化
 * 2. 根据 datafeed 返回的 K 线数据，计算每根 K 线的 x 坐标
 * 3. 在对应位置画标注图标
 * 4. 监听 K 线图缩放（如果有 API），重新计算
 */
import type { KLineData } from '@/types/klinecharts-pro';
import type { Annotation } from '@/lib/annotate-api';
import { PATTERN_CONFIG } from '@/lib/annotate-api';

const OVERLAY_ID = 'zettaranc-overlay';

/**
 * 过滤近一年标注
 */
export function filterRecentYear(annotations: Annotation[]): Annotation[] {
  const oneYearAgo = Date.now() - 365 * 24 * 60 * 60 * 1000;
  return annotations.filter((a) => new Date(a.date).getTime() >= oneYearAgo);
}

/**
 * 创建并维护 K 线图标注叠加层
 */
export class AnnotationOverlay {
  private container: HTMLElement;
  private annotations: Annotation[] = [];
  private enabledPatterns: Set<string> = new Set();
  private overlay: HTMLElement | null = null;
  private bars: KLineData[] = [];
  private resizeObserver: ResizeObserver | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    this.setupResizeObserver();
  }

  private setupResizeObserver() {
    this.resizeObserver = new ResizeObserver(() => this.render());
    this.resizeObserver.observe(this.container);
  }

  setData(bars: KLineData[], annotations: Annotation[], enabledPatterns: Set<string>) {
    this.bars = bars;
    this.annotations = annotations;
    this.enabledPatterns = enabledPatterns;
    this.render();
  }

  /**
   * 渲染所有标注
   */
  render() {
    if (!this.container) return;

    // 清除旧覆盖层
    this.clear();

    if (this.bars.length === 0) {
      console.log('[annotate-overlay] no bars');
      return;
    }
    if (this.annotations.length === 0) {
      console.log('[annotate-overlay] no annotations');
      return;
    }

    // 创建覆盖层
    this.overlay = document.createElement('div');
    this.overlay.id = OVERLAY_ID;
    this.overlay.style.cssText = `
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
      z-index: 100;
    `;

    // 过滤近一年 + 启用类型
    const oneYearAgo = Date.now() - 365 * 24 * 60 * 60 * 1000;
    const filtered = this.annotations.filter((a) => {
      if (!this.enabledPatterns.has(a.type)) return false;
      return new Date(a.date).getTime() >= oneYearAgo;
    });

    console.log('[annotate-overlay] rendering', filtered.length, 'annotations on', this.bars.length, 'bars');

    // 计算主图区域（上方 70%）
    const mainTop = 0;
    const mainHeight = this.container.clientHeight * 0.7;

    // 绘制每个标注
    for (const ann of filtered) {
      const cfg = PATTERN_CONFIG[ann.type];
      if (!cfg) continue;

      const pos = this.calcPosition(ann, mainTop, mainHeight);
      if (!pos) continue;

      const isUp = ann.type.startsWith('b');
      const marker = document.createElement('div');
      marker.className = `zettaranc-marker zettaranc-${ann.type}`;
      marker.dataset.date = ann.date;
      marker.dataset.type = ann.type;
      marker.title = `${cfg.label} | ${ann.date} | ¥${ann.price.toFixed(2)} | ${(ann.confidence * 100).toFixed(0)}%`;

      marker.style.cssText = `
        position: absolute;
        left: ${pos.x}px;
        top: ${pos.y}px;
        transform: translate(-50%, ${isUp ? '-130%' : '30%'});
        width: 24px;
        height: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 18px;
        font-weight: bold;
        color: ${cfg.color};
        background: rgba(0,0,0,0.85);
        border: 2px solid ${cfg.color};
        border-radius: 50%;
        text-shadow: 0 0 2px rgba(0,0,0,1);
        pointer-events: auto;
        cursor: pointer;
        z-index: 101;
        box-shadow: 0 0 8px ${cfg.color}66;
      `;
      marker.textContent = cfg.icon;

      this.overlay.appendChild(marker);
    }

    // 确保容器是 relative 定位
    if (getComputedStyle(this.container).position === 'static') {
      this.container.style.position = 'relative';
    }

    this.container.appendChild(this.overlay);
    console.log('[annotate-overlay] rendered', filtered.length, 'markers');
  }

  /**
   * 计算标注在图表上的像素坐标
   *
   * 基于 K 线数据计算，不依赖 KLineChart API
   */
  private calcPosition(ann: Annotation, mainTop: number = 0, mainHeight: number = 400): { x: number; y: number } | null {
    if (this.bars.length === 0) return null;

    const containerRect = this.container.getBoundingClientRect();
    const width = containerRect.width;

    // 找到标注日期对应的 K 线索引
    const annDate = new Date(ann.date + 'T00:00:00+08:00').getTime();
    let barIndex = -1;
    for (let i = 0; i < this.bars.length; i++) {
      const barTime = typeof this.bars[i].timestamp === 'number'
        ? this.bars[i].timestamp
        : new Date(this.bars[i].timestamp as any).getTime();
      if (barTime >= annDate) {
        barIndex = i;
        break;
      }
    }

    if (barIndex < 0) barIndex = this.bars.length - 1;

    // 计算 x 坐标：K 线在容器内的比例
    const xRatio = (barIndex + 0.5) / this.bars.length;
    const x = xRatio * width;

    // 计算 y 坐标：价格在主图 y 范围（基于 high/low）的比例
    // 取标注前后 30 根 K 线的数据范围作为可视范围
    const viewStart = Math.max(0, barIndex - 30);
    const viewEnd = Math.min(this.bars.length - 1, barIndex + 30);
    let minPrice = Infinity;
    let maxPrice = -Infinity;
    for (let i = viewStart; i <= viewEnd; i++) {
      minPrice = Math.min(minPrice, this.bars[i].low);
      maxPrice = Math.max(maxPrice, this.bars[i].high);
    }

    if (maxPrice === minPrice) return null;
    const yRatio = (maxPrice - ann.price) / (maxPrice - minPrice);
    const y = mainTop + yRatio * mainHeight;

    return { x, y };
  }

  clear() {
    if (this.overlay) {
      this.overlay.remove();
      this.overlay = null;
    }
  }

  destroy() {
    this.clear();
    this.resizeObserver?.disconnect();
  }
}