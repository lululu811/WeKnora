/**
 * anchor-stats — hover 某个时段锚点时叠在图上的一组数字。
 *
 * ## 为什么这组数字是联动的核心价值
 *
 * 正文说「这段最强」，用户悬停是想**验证**这句话。验证需要数字，不是一个框。
 * 而这些数字句子本身通常没写——所以它是联动唯一真正多给出来的信息。
 *
 * ## 口径都是刻意选的，不是随手写的
 *
 *  - **涨跌幅**取「区间第一根收盘 → 最后一根收盘」。不取前一根做基准：
 *    用户在图上看到的就是这两端，量出来应该和肉眼一致；用前一根会让区间
 *    起点的跳空被算进来，和"这段涨了多少"的直觉对不上。
 *  - **振幅**用 `(区间最高 - 区间最低) / 区间首根收盘`，与 A 股行情软件的
 *    「振幅」同口径（只是基准取首根而非前收）。
 *  - 全部指标在**数据不足时返回 null，绝不返回 0**：`0%` 会被读成"没涨没跌"，
 *    而 null 表达的是"算不出来"。这个仓库里已经因为"把 null 当 0"踩过坑
 *    （见 StockCitationFloat 的涨跌幅注释）。
 */

import type { LevelBar } from './levels'

export interface AnchorStats {
  /** 区间涨跌幅（%）。首根收盘为 0 或只有一根时为 null。 */
  pctChange: number | null
  /** 振幅（%）。区间首根收盘为 0 时为 null。 */
  amplitude: number | null
  /** 区间最高价。 */
  high: number
  /** 区间最低价。 */
  low: number
  /** 区间内的交易日数。 */
  bars: number
}

function round2(v: number): number {
  return Number(v.toFixed(2))
}

/**
 * 算一段区间的统计。区间为空或下标越界时返回 null。
 *
 * `startIndex`/`endIndex` 会被规范化（允许传反），与 `anchor-render` 的约定一致。
 */
export function computeAnchorStats(
  bars: readonly LevelBar[],
  startIndex: number,
  endIndex: number,
): AnchorStats | null {
  if (bars.length === 0) return null
  const lo = Math.max(0, Math.min(startIndex, endIndex))
  const hi = Math.min(bars.length - 1, Math.max(startIndex, endIndex))
  if (lo > hi) return null

  let high = -Infinity
  let low = Infinity
  for (let i = lo; i <= hi; i++) {
    if (bars[i].high > high) high = bars[i].high
    if (bars[i].low < low) low = bars[i].low
  }
  if (!Number.isFinite(high) || !Number.isFinite(low)) return null

  const count = hi - lo + 1
  const firstClose = bars[lo].close
  const lastClose = bars[hi].close

  // 只有一根时没有"涨跌幅"可言——它不是 0%，是算不出来。
  const pctChange =
    count > 1 && Number.isFinite(firstClose) && firstClose > 0
      ? round2(((lastClose - firstClose) / firstClose) * 100)
      : null

  const amplitude =
    Number.isFinite(firstClose) && firstClose > 0 ? round2(((high - low) / firstClose) * 100) : null

  return { pctChange, amplitude, high: round2(high), low: round2(low), bars: count }
}

/** 叠在图上的一行文字。数字缺失时用「—」占位，不写 0。 */
export function formatAnchorStats(stats: AnchorStats | null): string {
  if (!stats) return ''
  const pct =
    stats.pctChange === null ? '—' : `${stats.pctChange > 0 ? '+' : ''}${stats.pctChange.toFixed(2)}%`
  const amp = stats.amplitude === null ? '—' : `${stats.amplitude.toFixed(2)}%`
  return `${pct} · 振幅 ${amp} · 高 ${stats.high.toFixed(2)} 低 ${stats.low.toFixed(2)} · ${stats.bars} 个交易日`
}
