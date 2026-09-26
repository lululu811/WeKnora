/**
 * 标注绘制服务 - 使用 HTML 覆盖层在 K 线图上显示标注
 *
 * 方案：在 KLineChart 容器上层叠加一个绝对定位的 div，
 * 通过图表 API 将数据坐标转换为屏幕坐标，然后放置标注点。
 */
import type { KLineChartPro } from '@klinecharts/pro';
import type { Annotation } from '@/lib/annotate-api';
import { PATTERN_CONFIG } from '@/lib/annotate-api';

const OVERLAY_ID = 'zottaranc-annotation-overlay';

/**
 * 在 KLineChart 上绘制标注（HTML 覆盖层方案）
 */
export function drawAnnotations(
  chartPro: KLineChartPro,
  annotations: Annotation[],
  enabledPatterns: Set<string>,
  container?: HTMLElement
): void {
  // 清除旧的覆盖层
  clearAnnotations(container);

  // 获取底层 chart 实例
  const chart = getChartInstance(chartPro);
  if (!chart) {
    console.warn('[annotate] cannot get chart instance');
    return;
  }

  // 获取容器
  const el = container ?? (chartPro as any)._container ?? (chartPro as any).container;
  if (!el) {
    console.warn('[annotate] cannot get container');
    return;
  }

  // 创建覆盖层
  const overlay = document.createElement('div');
  overlay.id = OVERLAY_ID;
  overlay.style.cssText = `
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    z-index: 10;
  `;

  // 过滤标注
  const filtered = annotations.filter((a) => enabledPatterns.has(a.type));

  // 绘制每个标注
  for (const ann of filtered) {
    const cfg = PATTERN_CONFIG[ann.type];
    if (!cfg) continue;

    // 尝试将数据坐标转换为像素坐标
    const pos = dataToPixel(chart, ann.date, ann.price);
    if (!pos) continue;

    // 创建标注元素
    const marker = document.createElement('div');
    marker.className = 'zottaranc-marker';
    marker.dataset.type = ann.type;
    marker.dataset.date = ann.date;
    marker.title = `${cfg.label}\n${ann.date}\n价格: ${ann.price.toFixed(2)}\n置信度: ${(ann.confidence * 100).toFixed(0)}%`;

    // 根据类型选择样式
    const isUp = ann.type.startsWith('b'); // B1/B2/B3 向上

    marker.style.cssText = `
      position: absolute;
      left: ${pos.x}px;
      top: ${pos.y}px;
      transform: translate(-50%, ${isUp ? '-100%' : '0%'});
      width: 20px;
      height: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      font-weight: bold;
      color: ${cfg.color};
      text-shadow: 0 0 3px rgba(0,0,0,0.8);
      pointer-events: auto;
      cursor: pointer;
      filter: drop-shadow(0 0 2px rgba(0,0,0,0.5));
    `;
    marker.textContent = cfg.icon;

    overlay.appendChild(marker);
  }

  el.style.position = 'relative';
  el.appendChild(overlay);
}

/**
 * 清除标注
 */
export function clearAnnotations(container?: HTMLElement): void {
  if (!container) return;
  const existing = container.querySelector(`#${OVERLAY_ID}`);
  if (existing) existing.remove();
}

/**
 * 获取底层 Chart 实例
 */
function getChartInstance(chartPro: KLineChartPro): any {
  // 尝试多种获取方式
  if (typeof (chartPro as any).getChartInstance === 'function') {
    return (chartPro as any).getChartInstance();
  }
  if ((chartPro as any).chart) {
    return (chartPro as any).chart;
  }
  if ((chartPro as any)._chart) {
    return (chartPro as any)._chart;
  }
  // 直接返回 chartPro 本身（也许它就是 Chart）
  if (typeof (chartPro as any).convertToPixel === 'function') {
    return chartPro;
  }
  return null;
}

/**
 * 数据坐标转像素坐标
 */
function dataToPixel(
  chart: any,
  dateStr: string,
  price: number
): { x: number; y: number } | null {
  const timestamp = Date.parse(dateStr + 'T00:00:00+08:00') / 1000;

  // 尝试使用 chart 的坐标转换 API
  try {
    // KLineChart 可能有 convertToPixel 方法
    if (typeof chart.convertToPixel === 'function') {
      const pixel = chart.convertToPixel('main', { timestamp, value: price });
      if (pixel) return { x: pixel.x ?? pixel[0], y: pixel.y ?? pixel[1] };
    }

    // 尝试 getDataSpace 和 getXAxis
    if (typeof chart.getXAxis === 'function') {
      const xAxis = chart.getXAxis();
      const yAxis = chart.getYAxis?.('main') ?? chart.getYAxis?.();
      if (xAxis && yAxis) {
        // 使用内部方法
        const x = xAxis.valueToPixel?.(timestamp) ?? xAxis.getDataToPixel?.(timestamp);
        const y = yAxis.valueToPixel?.(price) ?? yAxis.getDataToPixel?.(price);
        if (x !== undefined && y !== undefined) {
          return { x, y };
        }
      }
    }
  } catch (e) {
    // 忽略
  }

  return null;
}
