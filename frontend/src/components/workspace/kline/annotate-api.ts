export interface Annotation {
  type: string;
  date: string;
  price: number;
  text: string;
  confidence: number;
  metadata: Record<string, any>;
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
