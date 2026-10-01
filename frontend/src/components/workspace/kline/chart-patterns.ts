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

export interface RawChartPatternResponse {
  code?: number
  symbol?: string
  chart_pattern?: {
    patterns?: RawPattern[]
    summary?: Record<string, unknown>
  } | null
  waves?: RawPattern | null
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
  /** 几何形态还是波浪 —— 两者在图上的画法一致，但标注措辞不同。 */
  kind: 'geometry' | 'wave'
  confidence: number
  desc: string
  points: DrawablePoint[]
  lines: DrawableLine[]
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
  return out
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
