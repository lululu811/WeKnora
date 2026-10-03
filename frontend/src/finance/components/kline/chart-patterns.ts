/**
 * chart-patterns — 拉取几何形态与波浪，并换算成图上可画的几何。
 *
 * ## 数据来源
 *
 * `/api/chart-pattern`（python-service）。后端 `zettaranc/pattern.py` 早就会识别
 * 头肩/双顶底/三角/楔形/旗形，`waves.py` 是本次新加的艾略特波浪，但**前端从来没
 * 消费过**——所以这些形态一直只存在于文字里，没画到图上。
 *
 * ## 为什么换算放在前端
 *
 * 后端给的是**日期 + 价格**（人对得上的写法），图表用的是 bar 下标。换算成下标
 * 需要"当前图上加载了哪些 K 线"这个信息，只有图表侧有——和锚点那条链路同一个理由。
 *
 * ## 关于波浪的诚实说明
 *
 * 自动数浪本身有争议：同一段行情不同的人能数出不同的浪。后端已经把「通过了几条
 * 艾略特硬规则」换算成 confidence，这里**原样透传、不做美化**，图上也会标明
 * 它是「一种可能的数法」。
 */

import type { LevelBar } from './levels'

export interface RawPatternPoint {
  index?: number
  date?: string
  price?: number
  label?: string
}

export interface RawPatternLine {
  label?: string
  points?: RawPatternPoint[]
}

export interface RawPattern {
  name?: string
  type?: string
  direction?: string
  confidence?: number
  desc?: string
  points?: RawPatternPoint[]
  lines?: RawPatternLine[]
}

/** 后端返回的形态目录项（下拉用）。 */
export interface RawCandleCatalogItem {
  key?: string
  name?: string
  desc?: string
}

/** 后端返回的单条蜡烛形态信号。 */
export interface RawCandleSignal {
  date?: string
  type?: string
  name?: string
  direction?: string
  strength?: number
  desc?: string
}

export interface RawChartPatternResponse {
  code?: number
  symbol?: string
  chart_pattern?: {
    patterns?: RawPattern[]
    summary?: Record<string, unknown>
  } | null
  waves?: RawPattern | null
  candlesticks?: RawCandleSignal[]
  /** 指标背离（与形态同形状：带 points 与 lines，可直接画）。 */
  divergences?: RawPattern[]
  candle_summary?: Record<string, unknown> | null
  /** 形态目录：下拉的选项来源。 */
  candle_catalog?: RawCandleCatalogItem[]
}

/** 图上一个可画的点：下标 + 价格 + 标签。 */
export interface DrawablePoint {
  index: number
  price: number
  label: string
}

export interface DrawableLine {
  label: string
  points: [DrawablePoint, DrawablePoint]
}

export interface DrawablePattern {
  name: string
  direction: 'bullish' | 'bearish' | 'neutral'
  /** 几何形态 / 波浪 / 指标背离 —— 画法一致，标注措辞不同。 */
  kind: 'geometry' | 'wave' | 'divergence'
  confidence: number
  desc: string
  points: DrawablePoint[]
  lines: DrawableLine[]
}

/**
 * 一个形态在图上覆盖的下标区间。
 *
 * 取 points 与 lines 所有端点的最小/最大下标。**hugging** 语义：区间是"这个形态
 * 关心哪一段"，不是"这条线画在哪"—— 光标落在这段里就算命中它。
 *
 * 一个点都画不出来的形态返回 null（它本来就没画在图上，不该参与命中）。
 */
export function patternIndexRange(pattern: DrawablePattern): { min: number; max: number } | null {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  const consider = (i: number) => {
    if (i < min) min = i;
    if (i > max) max = i;
  };
  for (const p of pattern.points) consider(p.index);
  for (const ln of pattern.lines) for (const p of ln.points) consider(p.index);
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
  return { min, max };
}

/**
 * 光标落在 `index` 这根 K 线上时，**应该点亮**哪几个形态。
 *
 * 返回名字集合。用名字而不是下标：形态是按名字勾选的，用户看到的也是名字。
 * 落空时返回空集合 —— 调用方据此决定"是不是全部保持常态"。
 */
export function patternsAtBar(patterns: readonly DrawablePattern[], index: number): string[] {
  const out: string[] = [];
  for (const p of patterns) {
    const range = patternIndexRange(p);
    if (range && index >= range.min && index <= range.max) out.push(p.name);
  }
  return out;
}

/** 两个名字集合是否相同（用于「集合没变就跳过重绘」的判断）。 */
export function samePatternSet(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** 日期 -> 下标的索引，建一次复用。 */
function buildDateIndex(bars: readonly LevelBar[]): Map<string, number> {
  const map = new Map<string, number>()
  for (let i = 0; i < bars.length; i++) {
    const iso = new Date(bars[i].timestamp).toISOString().slice(0, 10)
    // 同一天多根（分钟级）时保留第一根，图上按天对齐就够了。
    if (!map.has(iso)) map.set(iso, i)
  }
  return map
}

function normalizeDirection(raw: string | undefined): DrawablePattern['direction'] {
  if (raw === 'bullish' || raw === 'bearish' || raw === 'neutral') return raw
  return 'neutral'
}

/**
 * 把后端返回换算成图上可画的几何。
 *
 * **画不出来的部分直接丢掉**：日期不在已加载的 K 线里（比如形态落在更早的历史上）、
 * 或者价格不是正数。宁可少画一个形态，也不要把它画到错误的位置上。
 */
export function resolvePatternGeometry(
  raw: RawChartPatternResponse | null | undefined,
  bars: readonly LevelBar[],
): DrawablePattern[] {
  if (!raw || bars.length === 0) return []
  const dateIndex = buildDateIndex(bars)

  const toPoint = (p: RawPatternPoint): DrawablePoint | null => {
    const iso = typeof p?.date === 'string' ? p.date : ''
    const idx = iso ? dateIndex.get(iso) : undefined
    if (idx === undefined) return null
    const price = Number(p?.price)
    if (!Number.isFinite(price) || price <= 0) return null
    return { index: idx, price, label: String(p?.label || '') }
  }

  const convert = (
    pattern: RawPattern | null | undefined,
    kind: DrawablePattern['kind'],
  ): DrawablePattern | null => {
    if (!pattern || typeof pattern.name !== 'string' || !pattern.name) return null

    const points = (pattern.points || [])
      .map(toPoint)
      .filter((p): p is DrawablePoint => p !== null)
      .sort((a, b) => a.index - b.index)

    const lines: DrawableLine[] = []
    for (const ln of pattern.lines || []) {
      const pts = (ln?.points || []).map(toPoint).filter((p): p is DrawablePoint => p !== null)
      if (pts.length !== 2) continue
      lines.push({ label: String(ln?.label || ''), points: [pts[0], pts[1]] })
    }

    // 一个点都画不出来、也没有参考线 -> 这个形态在图上没有表达，丢掉。
    if (points.length === 0 && lines.length === 0) return null

    return {
      name: pattern.name,
      direction: normalizeDirection(pattern.direction),
      kind,
      confidence: Number.isFinite(Number(pattern.confidence)) ? Number(pattern.confidence) : 0,
      desc: String(pattern.desc || ''),
      points,
      lines,
    }
  }

  const out: DrawablePattern[] = []
  for (const p of raw.chart_pattern?.patterns || []) {
    const converted = convert(p, 'geometry')
    if (converted) out.push(converted)
  }
  const wave = convert(raw.waves, 'wave')
  if (wave) out.push(wave)
  // 背离与几何形态同形状（两点 + 一条连线），所以走同一套换算与绘制，
  // 不需要为它写新的画法。差别只在 kind，供 UI 决定措辞。
  for (const d of raw.divergences || []) {
    const converted = convert(d, 'divergence')
    if (converted) out.push(converted)
  }
  return out
}

/** 图上的一根蜡烛形态标记。 */
export interface DrawableCandle {
  index: number
  type: string
  name: string
  direction: 'bullish' | 'bearish' | 'neutral'
  desc: string
}

/**
 * 把后端的蜡烛形态序列换算成图上可画的标记。
 *
 * 和几何形态同口径：日期对不上已加载 K 线的**直接丢**，不兜底到"最近的一根" ——
 * 那会把标记画到错误的 K 线上，而蜡烛形态是**单根 K 线**的判定，错一根就完全错了。
 */
export function resolveCandleMarks(
  raw: RawChartPatternResponse | null | undefined,
  bars: readonly LevelBar[],
): DrawableCandle[] {
  if (!raw || bars.length === 0) return []
  const dateIndex = buildDateIndex(bars)

  const out: DrawableCandle[] = []
  for (const c of raw.candlesticks || []) {
    const iso = typeof c?.date === 'string' ? c.date : ''
    const idx = iso ? dateIndex.get(iso) : undefined
    if (idx === undefined) continue
    if (typeof c?.type !== 'string' || !c.type) continue
    out.push({
      index: idx,
      type: c.type,
      name: String(c.name || c.type),
      direction: normalizeDirection(c.direction),
      desc: String(c.desc || ''),
    })
  }
  // 同一根上可能命中多个形态，按 (下标, 类型) 去重，避免后端重复给时画两遍。
  const seen = new Set<string>()
  return out.filter((c) => {
    const k = `${c.index}:${c.type}`
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
}

/** 拉取形态数据。失败时返回 null（形态是增强信息，缺了不该让图表报错）。 */
export async function fetchChartPatterns(
  symbol: string,
  days = 250,
): Promise<RawChartPatternResponse | null> {
  try {
    const res = await fetch(
      `/api/chart-pattern?symbol=${encodeURIComponent(symbol)}&days=${days}`,
    )
    if (!res.ok) return null
    return (await res.json()) as RawChartPatternResponse
  } catch {
    return null
  }
}
