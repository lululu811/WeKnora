/**
 * 大盘预览大屏的图形几何计算（纯函数，无 DOM、无 Vue）。
 *
 * 为什么要单独抽出来：这几段是"看起来对但其实悄悄错了"的重灾区 ——
 * 曲线路径的 y 轴方向、昨收虚线的位置、快照条上点位百分比的分母，
 * 任何一个搞反了，图形仍然会正常渲染，只是**含义**全错（涨的画成跌的、
 * 点位跑到轨道外）。这类 bug 在截图上很难一眼看出来，所以抽成纯函数 +
 * 配 `market-geometry.test.ts`，让它们跑在 CI 里。
 *
 * 坐标系约定：SVG 的 y 轴向下增长，所以归一化值 1（最高）对应 y = pad，
 * 而不是 y = height - pad。搞反的话整条线会上下颠倒。
 */

/** 折线图的几何常量，与设计稿样稿的 viewBox 一致。 */
export const CHART = {
  width: 260,
  height: 64,
  /** 留白：折线不贴边。 */
  pad: 3,
} as const

/** 自选股行内迷你走势条。 */
export const SPARK = {
  width: 58,
  height: 22,
  pad: 2,
} as const

/**
 * 丢弃无法参与绘制的点，而不是让它们污染整条曲线。
 *
 * 序列里只要有一个 `close: null`（本地没那天的行情），就不能画 ——
 * 跳过它会让横轴不再是等距交易日，把"缺一天"画成"那天没涨没跌"。
 * 整段返回空数组，由调用方显示「暂无数据」。
 */
export function usableCloses(
  series: ReadonlyArray<{ close: number | null }>,
): number[] {
  const out: number[] = []
  for (const p of series) {
    if (typeof p.close === 'number' && Number.isFinite(p.close)) out.push(p.close)
  }
  return out
}

/** 把一段收盘价归一化到 [0,1]（0 = 最低，1 = 最高）。全等时返回全 0.5。 */
export function normalize(closes: ReadonlyArray<number>): number[] {
  if (closes.length === 0) return []
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min
  // 全平的一段（停牌、指数不动）：min === max，除零会得到 NaN 让整条线消失。
  if (span === 0) return closes.map(() => 0.5)
  return closes.map((c) => (c - min) / span)
}

/** 归一化值 → SVG 坐标（y 轴向下，故 1 → pad）。 */
function toY(value: number, height: number, pad: number): number {
  return pad + (1 - value) * (height - pad * 2)
}

/** 折线 path。`normalize` 的输出按时间正序。 */
export function linePath(
  norm: ReadonlyArray<number>,
  width: number,
  height: number,
  pad: number,
): string {
  if (norm.length === 0) return ''
  if (norm.length === 1) {
    // 只有一个点：画不出线。给一个零长度 path（不渲染）比给一条横线诚实 ——
    // 横线会被读成"这段时间没波动"，而真相是"只有一个读数"。
    return `M${(pad).toFixed(1)},${toY(norm[0], height, pad).toFixed(1)}`
  }
  return norm
    .map((v, i) => {
      const x = pad + (i / (norm.length - 1)) * (width - pad * 2)
      return `${i ? 'L' : 'M'}${x.toFixed(1)},${toY(v, height, pad).toFixed(1)}`
    })
    .join(' ')
}

/** 面积 path（折线 + 封底），用作渐变填充。 */
export function areaPath(
  norm: ReadonlyArray<number>,
  width: number,
  height: number,
  pad: number,
): string {
  const line = linePath(norm, width, height, pad)
  if (!line || norm.length < 2) return ''
  return `${line} L${(width - pad).toFixed(1)},${height} L${pad},${height} Z`
}

/**
 * 昨收水平虚线的 y 坐标。
 *
 * 用序列倒数第二根收盘（而不是后端给的 `prev_close`）：两者本来同源，
 * 但曲线是"能画出来的那些点"，虚线必须和曲线用**同一套**归一化，
 * 否则缺一天时虚线会落在曲线"应该"在的位置之外，看着像画错了。
 * 只有一个点时返回 null（没有前收可画）。
 */
export function prevCloseY(
  norm: ReadonlyArray<number>,
  height: number,
  pad: number,
): number | null {
  if (norm.length < 2) return null
  return Number(toY(norm[norm.length - 2], height, pad).toFixed(1))
}

/**
 * 快照条上一个点位在「低—高」轨道上的位置（百分比 0~100）。
 *
 * `value` 超出 [low, high] 时**夹到边界**而不是让它跑出轨道外 ——
 * 轨道只有一条线，跑出去就等于把读数画到了看不见的地方。
 * low === high（当日一字板）时返回 50：没有区间就没有位置可言，
 * 放在中点比放在 0%（看起来像"开在最低"）诚实。
 */
export function rangePosition(
  value: number | null,
  low: number | null,
  high: number | null,
): number | null {
  if (value == null || low == null || high == null) return null
  if (!Number.isFinite(value) || !Number.isFinite(low) || !Number.isFinite(high)) return null
  if (high === low) return 50
  const ratio = ((value - low) / (high - low)) * 100
  return Number(Math.min(100, Math.max(0, ratio)).toFixed(1))
}

/**
 * 近 5 日涨停柱状图的高度百分比。
 *
 * 高度按 **max(柱值)** 归一（而不是写死 80），否则数据波动一大柱子就顶出格、
 * 一小又全贴地。最大值本身给 100 会让最高那根顶死，所以留 12% 余量 ——
 * 与样稿 `Math.max(...) * 1.12` 一致。
 */
export function barHeights(values: ReadonlyArray<number>): number[] {
  if (values.length === 0) return []
  const peak = Math.max(...values)
  if (peak <= 0) return values.map(() => 0)
  return values.map((v) => Number(((v / (peak * 1.12)) * 100).toFixed(0)))
}

/** 涨跌家数条三段的宽度百分比，三者之和为 100（除非全为 null）。 */
export function breadthWidths(breadth: {
  up: number | null
  flat: number | null
  down: number | null
}): { up: number; flat: number; down: number } {
  const { up, flat, down } = breadth
  if (up == null && flat == null && down == null) {
    return { up: 0, flat: 0, down: 0 }
  }
  const u = up ?? 0
  const f = flat ?? 0
  const d = down ?? 0
  const total = u + f + d
  if (total <= 0) return { up: 0, flat: 0, down: 0 }
  return {
    up: Number(((u / total) * 100).toFixed(1)),
    flat: Number(((f / total) * 100).toFixed(1)),
    down: Number(((d / total) * 100).toFixed(1)),
  }
}

/** 涨跌方向：null 表示没有读数（不染色），不能当 0 当"平"。 */
export function trendOf(value: number | null): 'up' | 'down' | null {
  if (value == null || !Number.isFinite(value)) return null
  return value >= 0 ? 'up' : 'down'
}

/** 元 → 亿元，带符号。null 原样返回（调用方决定显示什么）。 */
export function toYi(value: number | null, digits = 2): string | null {
  if (value == null || !Number.isFinite(value)) return null
  const yi = value / 1e8
  return `${yi >= 0 ? '+' : '−'}${Math.abs(yi).toFixed(digits)}`
}
