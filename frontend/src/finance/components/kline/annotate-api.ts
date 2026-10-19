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
/**
 * 形态的准入档位。
 *
 * `descriptive` = 描述层：只说明「这里长成了某个样子」，**不构成买卖建议**。
 * `signal` = 信号层：判据矩阵里有 edge 证据，可以进通知/建议通道。
 *
 * 档位不是随手标的：每个形态都要有一条判据，判据结论是 0/N 的就只能是
 * 描述层。清单与判据出处见 docs/design/signal-admission.md。
 */
export type AdmissionKind = 'descriptive' | 'signal';

export interface PatternMeta {
  /** 展示名的 i18n 键（文案要走 t()，不能写死，否则切不了语言）。 */
  labelKey: string;
  /** 悬浮说明的 i18n 键。 */
  descKey: string;
  /** 颜色：全仓只有 Object.keys() 读过这个对象，形态气泡真正上色在
   *  overlay-drawer.ts（走 palette）。要调色请改 palette。 */
  color: string;
  /** 准入档位。 */
  admission: AdmissionKind;
  /**
   * 确认延迟（bar 数）：形态要 N 根 bar 之后才可判定。
   *
   * B1 是 3 —— 判据循环留着最后 3 根 bar 不参与判定，也就是形态在出现当天
   * 还不成立。图上看到它的人若不知道这一层，会当成当天就能依据它做判断。
   */
  confirmDelayBars?: number;
  /** 判据结论的 i18n 键（有 edge 证据的形态才需要）。 */
  basisKey?: string;
}

export const PATTERN_CONFIG: Record<string, PatternMeta> = {
  b1: {
    labelKey: 'kline.patterns.b1.label',
    descKey: 'kline.patterns.b1.desc',
    color: '#10b981',
    admission: 'descriptive',
    confirmDelayBars: 3,
  },
  key_k: {
    labelKey: 'kline.patterns.keyK.label',
    descKey: 'kline.patterns.keyK.desc',
    color: '#3b82f6',
    admission: 'descriptive',
  },
  s1: {
    labelKey: 'kline.patterns.s1.label',
    descKey: 'kline.patterns.s1.desc',
    color: '#ef4444',
    admission: 'descriptive',
  },
  violent_k: {
    labelKey: 'kline.patterns.violentK.label',
    descKey: 'kline.patterns.violentK.desc',
    color: '#f59e0b',
    admission: 'descriptive',
  },
  pole_yang: {
    labelKey: 'kline.patterns.poleYang.label',
    descKey: 'kline.patterns.poleYang.desc',
    color: '#e11d48',
    admission: 'descriptive',
  },
  pile_yang: {
    labelKey: 'kline.patterns.pileYang.label',
    descKey: 'kline.patterns.pileYang.desc',
    color: '#059669',
    admission: 'descriptive',
  },
  capping_yin: {
    labelKey: 'kline.patterns.cappingYin.label',
    descKey: 'kline.patterns.cappingYin.desc',
    color: '#dc2626',
    admission: 'descriptive',
  },
  fake_yin: {
    labelKey: 'kline.patterns.fakeYin.label',
    descKey: 'kline.patterns.fakeYin.desc',
    color: '#8b5cf6',
    admission: 'descriptive',
  },
  rebound_trap: {
    labelKey: 'kline.patterns.reboundTrap.label',
    descKey: 'kline.patterns.reboundTrap.desc',
    color: '#d97706',
    admission: 'descriptive',
  },
};

export async function fetchAnnotations(symbol: string, days = 120): Promise<AnnotationResponse> {
  const res = await fetch(`/api/annotate?symbol=${encodeURIComponent(symbol)}&days=${days}`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: Failed to fetch annotations`);
  }
  return res.json();
}
