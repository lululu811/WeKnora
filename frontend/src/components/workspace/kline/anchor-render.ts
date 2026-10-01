/**
 * anchor-render — 把正文里的锚点换算成图上能画的东西。
 *
 * 分两层：这个模块负责**纯计算**（日期→下标、算价格包络、截断到上限），
 * `core-chart.ts` 负责真正落到 overlay 上。分开是为了可测——换算里的边界
 * （日期在数据范围外、区间只有一根、超过上限）全是容易写错又不容易发现的地方，
 * 而 `.vue` 里挂不起来。
 */

import type { KLineAnchor } from '@/utils/klineAnchors'
import type { LevelBar } from './levels'

/**
 * 常驻锚点的上限。
 *
 * 5 是「一张图上还能一眼数清」的数目，也大致等于一条回答里真正被强调的重点数。
 * 超出部分**不丢**——正文里照样可 hover，只是不在图上常驻，避免重演之前
 * 「密密麻麻什么都看不出来」的那一幕。
 *
 * 提示词里给模型的数量上限也是这个数，两边必须一致，否则模型写 8 个、
 * 界面只画 5 个，用户会觉得「标记丢了」。
 */
export const MAX_PERSISTENT_ANCHORS = 5

export interface RenderableAnchor {
  /** 正文里的编号，正文与图上共用。 */
  index: number
  kind: 'range' | 'level'
  label: string
  /** `range`：起止 K 线下标。 */
  startIndex?: number
  endIndex?: number
  /** `range`：该段价格包络。 */
  low?: number
  high?: number
  /** `level`：价格。 */
  value?: number
  /**
   * 是否能在图上定位。
   *
   * false 有两种情况：锚点本身就无效（模型写错），或日期落在已加载数据之外
   * （讨论的是很久以前、或还没发生的时段）。两种都不画，但**正文里仍然保留**
   * ——语法不露出来、内容按文本渲染。
   */
  resolvable: boolean
}

function toTimestamp(iso: string | undefined): number | null {
  if (!iso) return null
  const t = Date.parse(`${iso}T00:00:00Z`)
  return Number.isFinite(t) ? t : null
}

/**
 * 找到最接近给定时间戳的 K 线下标。
 *
 * 用「最近一根」而不是「精确相等」：正文写的日期可能是周末/节假日，或者那天
 * 停牌，精确相等会直接定位失败。距离上限设成 10 个自然日——再远就说明这个日期
 * 根本不在这段数据里，硬贴到最近一根会画错位置。
 */
const MAX_SNAP_DAYS = 10

function nearestIndex(bars: readonly LevelBar[], ts: number): number | null {
  if (bars.length === 0) return null
  let best = 0
  for (let i = 1; i < bars.length; i++) {
    if (Math.abs(bars[i].timestamp - ts) < Math.abs(bars[best].timestamp - ts)) best = i
  }
  const distance = Math.abs(bars[best].timestamp - ts)
  return distance <= MAX_SNAP_DAYS * 86_400_000 ? best : null
}

/**
 * 把一组锚点换算成可绘制项，并按上限截断。
 *
 * 截断发生在**换算成功之后**：先淘汰画不出来的，再按正文顺序取前 N 个。
 * 反过来先截断会让「前 5 个里 3 个画不出来」白白浪费名额。
 */
export function resolveAnchorsForChart(
  anchors: readonly KLineAnchor[],
  bars: readonly LevelBar[],
  maxPersistent: number = MAX_PERSISTENT_ANCHORS,
): RenderableAnchor[] {
  const resolved: RenderableAnchor[] = []

  for (const anchor of anchors) {
    const base = { index: anchor.index, kind: anchor.kind, label: anchor.label }

    if (anchor.kind === 'level') {
      const value = anchor.value
      // 价格锚点不需要在时间轴上定位——它是一条横贯全图的线。只要价格是正数
      // 且落在数据的可见价格区间附近就画，否则线会画到画面外看不见。
      const resolvable = Boolean(anchor.valid && value !== undefined && value > 0)
      resolved.push({ ...base, value, resolvable })
      continue
    }

    const fromTs = toTimestamp(anchor.from)
    const toTs = toTimestamp(anchor.to) ?? fromTs
    if (!anchor.valid || fromTs === null || toTs === null) {
      resolved.push({ ...base, resolvable: false })
      continue
    }

    const startIndex = nearestIndex(bars, fromTs)
    const endIndex = nearestIndex(bars, toTs)
    if (startIndex === null || endIndex === null) {
      resolved.push({ ...base, resolvable: false })
      continue
    }

    const lo = Math.min(startIndex, endIndex)
    const hi = Math.max(startIndex, endIndex)
    let low = Infinity
    let high = -Infinity
    for (let i = lo; i <= hi; i++) {
      if (bars[i].low < low) low = bars[i].low
      if (bars[i].high > high) high = bars[i].high
    }
    // 只有一根且价格退化时画不出有面积的框，但仍然可以定位——用一个极薄的
    // 区间表示，而不是判为不可解析。
    if (!Number.isFinite(low) || !Number.isFinite(high)) {
      resolved.push({ ...base, resolvable: false })
      continue
    }

    resolved.push({ ...base, startIndex: lo, endIndex: hi, low, high, resolvable: true })
  }

  const usable = resolved.filter((a) => a.resolvable)
  const unusable = resolved.filter((a) => !a.resolvable)
  // 不可解析的排在后面，但它们仍然占正文里的编号——图上不画而已。
  return [...usable.slice(0, maxPersistent), ...unusable]
}

/** 一个锚点在画布上的命中区域（像素坐标，由调用方用 convertToPixel 算好）。 */
export interface AnchorHitBox {
  index: number;
  kind: 'range' | 'level';
  /** `range`：时间轴上的下标范围。 */
  startIndex?: number;
  endIndex?: number;
  /** `range`：画布上的纵向范围。 */
  top?: number;
  bottom?: number;
  /** `level`：画布上的横线 y。 */
  levelY?: number;
}

/** 价格线的命中容差（像素）。1px 的线几乎点不中，给一条看不见的宽带。 */
export const LEVEL_HIT_TOLERANCE = 14;

/**
 * 判断光标落在哪个锚点上。
 *
 * 为什么自己算而不用库的 overlay 事件：实测 klinecharts 9.8.5 的 overlay
 * figure 事件在这套配置下不触发（`lock` 并非原因，figure 事件本就不看 lock），
 * 而排障要钻进库内部的命中判定。crosshair 事件稳定给出 `dataIndex` 与画布 `y`，
 * 拿它自己算命中完全可控，而且这个判定是**纯函数**——能单测，不必靠"手动点点看"。
 *
 * 优先级：`range` 先于 `level`。时段框是有面积的、用户更容易瞄准它；
 * 价格线横贯全图、命中带又宽，若让它优先，框里几乎任何位置都会被线抢走。
 */
export function hitTestAnchor(
  boxes: readonly AnchorHitBox[],
  dataIndex: number,
  y: number,
  levelTolerance: number = LEVEL_HIT_TOLERANCE,
): number | null {
  if (!Number.isFinite(dataIndex) || !Number.isFinite(y)) return null

  for (const box of boxes) {
    if (box.kind !== 'range') continue
    if (box.startIndex === undefined || box.endIndex === undefined) continue
    if (box.top === undefined || box.bottom === undefined) continue
    const lo = Math.min(box.startIndex, box.endIndex)
    const hi = Math.max(box.startIndex, box.endIndex)
    if (dataIndex < lo || dataIndex > hi) continue
    const top = Math.min(box.top, box.bottom)
    const bottom = Math.max(box.top, box.bottom)
    if (y < top || y > bottom) continue
    return box.index
  }

  for (const box of boxes) {
    if (box.kind !== 'level') continue
    if (box.levelY === undefined) continue
    if (Math.abs(y - box.levelY) <= levelTolerance) return box.index
  }

  return null
}
