/**
 * 标注的来源。
 *
 * `algorithm` = 后端形态识别算出来的（B1/S1/关键K/暴力K），有确定的判据。
 * `llm`       = 模型在回答里主张的位（「1400 是关键支撑」这类）。
 *
 * 两者会画在同一张 K 线上，但**必须长得不一样**：一条是数据，一条是观点。
 * 混成同一种墨迹，用户就无从判断哪条可以当依据。
 */
export type AnnotationSource = 'algorithm' | 'llm';

export interface Annotation {
  type: string;
  date: string;
  price: number;
  text: string;
  confidence: number;
  metadata: Record<string, any>;
  /**
   * 缺省视为 `algorithm`：这个字段是后加的，历史响应里没有它。
   * 不设默认值会让所有旧数据在渲染时被判成未知来源而静默不画。
   */
  source?: AnnotationSource;
}

/** 该标注是否来自模型主张（而非算法识别）。 */
export function isLlmAnnotation(ann: Annotation): boolean {
  return ann.source === 'llm';
}

/**
 * 画在 K 线上的短标签。
 *
 * 模型主张的位加一个前缀标记，因为**颜色之外还需要一个不依赖色觉的区分**——
 * 深色画布上金色与琥珀色很接近，红绿色觉障碍用户更分不出。
 */
export function annotationLabel(ann: Annotation): string {
  return isLlmAnnotation(ann) ? `观点·${ann.text}` : ann.text;
}

export interface AnnotationResponse {
  code: number;
  symbol: string;
  days: number;
  data_source: string;
  pattern_types: string[];
  annotation_count: number;
  annotations: Annotation[];
}

// color 字段是**历史残留**：全仓只有 Object.keys() 读过这个对象，形态气泡真正
// 上色在 overlay-drawer.ts（走 palette）。这里保留字面量只为类型完整——改它
// 不会有任何视觉变化，要调色请改 palette。
export const PATTERN_CONFIG: Record<string, { label: string; color: string; desc: string }> = {
  b1: { label: 'B1 建仓波', color: '#10b981', desc: '建仓波后第一次回调缩量，J<13' },
  key_k: { label: '关键K', color: '#3b82f6', desc: '十字星 + 缩量转折' },
  s1: { label: 'S1 预警', color: '#ef4444', desc: '高位放量长上影线' },
  violent_k: { label: '暴力K', color: '#f59e0b', desc: '低位倍量突破长阳/长阴' },
};

export async function fetchAnnotations(symbol: string, days = 120): Promise<AnnotationResponse> {
  const res = await fetch(`/api/annotate?symbol=${encodeURIComponent(symbol)}&days=${days}`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: Failed to fetch annotations`);
  }
  return res.json();
}
