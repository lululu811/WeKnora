/**
 * 标注 API 客户端
 */

const API_BASE = '/api';

export interface Annotation {
  type: string;
  date: string;
  price: number;
  text: string;
  confidence: number;
  metadata: Record<string, number | string | boolean>;
}

export interface AnnotateResponse {
  symbol: string;
  days: number;
  has_today: boolean;
  data_source: string;
  pattern_types: string[];
  annotation_count: number;
  annotations: Annotation[];
}

export interface Plugin {
  name: string;
  description: string;
  version: string;
  annotate: boolean;
}

export interface PluginsResponse {
  plugins: Plugin[];
  count: number;
}

/**
 * 获取形态标注
 */
export async function fetchAnnotations(
  symbol: string,
  days: number = 200,
  patterns: string[] = ['b1', 'key_k', 's1', 'violent_k']
): Promise<AnnotateResponse> {
  const params = new URLSearchParams({
    symbol,
    days: String(days),
    patterns: patterns.join(','),
  });

  const res = await fetch(`${API_BASE}/annotate?${params}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/**
 * 获取插件列表
 */
export async function fetchPlugins(): Promise<PluginsResponse> {
  const res = await fetch(`${API_BASE}/plugins`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/**
 * 标注类型配置（颜色、图标等）
 */
export const PATTERN_CONFIG: Record<string, { color: string; label: string; icon: string }> = {
  b1: { color: '#FF6B00', label: 'B1 建仓波', icon: '▲' },
  b2: { color: '#FF6B00', label: 'B2 突破', icon: '▲' },
  b3: { color: '#FF6B00', label: 'B3 买点', icon: '▲' },
  s1: { color: '#9900FF', label: 'S1 信号', icon: '▼' },
  s2: { color: '#9900FF', label: 'S2 信号', icon: '▼' },
  s3: { color: '#9900FF', label: 'S3 信号', icon: '▼' },
  key_k: { color: '#FFD700', label: '关键K', icon: '★' },
  violent_k: { color: '#FF0066', label: '暴力K', icon: '◆' },
};
