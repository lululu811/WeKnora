/**
 * 多标的对比条的纯逻辑：sparkline 路径与摘要文案。
 *
 * 抽出来是为了可测——`.vue` 里在 node:test 下没法挂载（没有 jsdom），而
 * 「折线画在哪儿」「谁的涨跌幅最大」恰好是这个组件里唯二会算错的地方。
 */

export interface CompareRow {
  thscode: string;
  name: string;
  /** 最新收盘价；null 表示还没取到数据，界面显示「—」。 */
  close: number | null;
  /** 涨跌幅百分比；null 表示数据只有一根或缺失，**不是 0**。 */
  pctChange: number | null;
  /** 收盘价序列（旧→新），用于画 sparkline。 */
  closes: number[];
}

export interface CompareSummary {
  count: number;
  strongest: CompareRow | null;
  weakest: CompareRow | null;
}

/**
 * 把收盘价序列映射成 SVG path 的 `d`。
 *
 * 几个刻意的取舍：
 *  - **归一化到各自的 min/max**，而不是共用坐标轴。对比条里每行只有几十像素高，
 *    共用轴会让波动小的票变成一条直线，失去"形态"信息——而这一栏要回答的是
 *    「谁在往上走」，不是「谁绝对价格高」。绝对价格由旁边的现价列负责。
 *  - 全部相等时画一条水平中线，而不是除以 0 得到 NaN（NaN 会让 SVG 整个不显示）。
 *  - 少于两个点时返回空串，调用方据此不渲染 sparkline。
 */
export function buildSparklinePath(closes: number[], width: number, height: number): string {
  if (closes.length < 2 || width <= 0 || height <= 0) return ''

  let min = Infinity
  let max = -Infinity
  for (const v of closes) {
    if (v < min) min = v
    if (v > max) max = v
  }

  const span = max - min
  const stepX = width / (closes.length - 1)
  // 上下各留 1px，避免线被裁在边线上。
  const pad = 1
  const usable = Math.max(height - pad * 2, 1)

  const points = closes.map((v, i) => {
    const x = i * stepX
    // span === 0 时贴中线：所有价格相同，画一条水平线是正确的表达。
    const ratio = span === 0 ? 0.5 : (v - min) / span
    const y = pad + (1 - ratio) * usable
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })

  return `M${points.join(' L')}`
}

/** 涨跌幅的方向。null（数据不足）既不算涨也不算跌——它没有方向。 */
export function changeDirection(pctChange: number | null): 'up' | 'down' | 'flat' | 'unknown' {
  if (pctChange === null || !Number.isFinite(pctChange)) return 'unknown'
  if (pctChange > 0) return 'up'
  if (pctChange < 0) return 'down'
  return 'flat'
}

/**
 * 汇总一组对比项，挑出最强/最弱。
 *
 * 只在**有明确涨跌幅**的项里挑：null 的项不参与，也不被当作 0——
 * 把「数据不足」当成「不涨不跌」会让它意外胜出或挤掉真实的最强者。
 * 并列时保留先出现的那只（数组顺序来自 picks，即模型的提及顺序）。
 */
export function summarizeCompare(rows: CompareRow[]): CompareSummary {
  const known = rows.filter((r) => r.pctChange !== null && Number.isFinite(r.pctChange))
  if (known.length === 0) {
    return { count: rows.length, strongest: null, weakest: null }
  }
  let strongest = known[0]
  let weakest = known[0]
  for (const r of known) {
    if ((r.pctChange as number) > (strongest.pctChange as number)) strongest = r
    if ((r.pctChange as number) < (weakest.pctChange as number)) weakest = r
  }
  return { count: rows.length, strongest, weakest }
}

/** 收起态的摘要文案。 */
export function formatSummary(summary: CompareSummary): string {
  if (summary.count === 0) return ''
  if (!summary.strongest) {
    // 一只票都没算出涨跌幅时不要编一个"最强"出来。
    return `对比 ${summary.count} 只`
  }
  const pct = summary.strongest.pctChange as number
  const sign = pct > 0 ? '+' : ''
  return `对比 ${summary.count} 只 · 最强 ${summary.strongest.name} ${sign}${pct.toFixed(2)}%`
}
