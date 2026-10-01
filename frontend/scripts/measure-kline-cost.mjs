/**
 * 多标的 K 线的加载成本实测。
 *
 * 触发这个测量的原因：图表侧每次取 5000 根（`/api/kline?...&limit=5000`），
 * 而多标的对比条要一次拉 N 只票。如果对比条也按 5000 根取，10 只票就是
 * 5 万根——所以对比条刻意只取 60 根。这里把两条路径的真实成本量出来，
 * 而不是靠估算。
 *
 * 跑法：node --import tsx scripts/measure-kline-cost.mjs
 * 或者：npx tsx scripts/measure-kline-cost.mjs
 */

import { computeLevels, pickChartLevels } from '../src/components/workspace/kline/levels.ts'
import { buildSparklinePath, summarizeCompare } from '../src/components/workspace/kline/compare-bar.ts'
import { computeChange } from '../src/components/workspace/kline/kline-cache.ts'

const DAY = 86400000

/** 造一份与 /api/kline 形状一致的日线（ts 为秒，与后端一致）。 */
function makeRows(n, seed = 1) {
  const base = Math.floor(Date.UTC(2010, 0, 4) / 1000)
  const rows = []
  let px = 50 + seed * 7
  for (let i = 0; i < n; i++) {
    px += Math.sin(i / 23 + seed) * 1.7 + Math.cos(i / 7) * 0.4
    const o = px
    const c = px + Math.sin(i / 3) * 0.7
    rows.push({
      ts: base + i * (DAY / 1000),
      open: +o.toFixed(3),
      high: +(Math.max(o, c) + 0.6).toFixed(3),
      low: +(Math.min(o, c) - 0.6).toFixed(3),
      close: +c.toFixed(3),
      volume: 1_000_000 + (i % 97) * 13_337,
      turnover: 1_000_000 + (i % 97) * 13_337,
    })
  }
  return rows
}

/** 复刻 kline-cache 的 normalize（ts 秒 -> 毫秒）。 */
function normalize(raw) {
  return raw.map((r) => ({
    timestamp: r.ts * 1000,
    open: r.open,
    high: r.high,
    low: r.low,
    close: r.close,
    volume: r.volume,
    turnover: r.turnover,
  }))
}

function heapMB() {
  global.gc?.()
  return process.memoryUsage().heapUsed / 1024 / 1024
}

function timeIt(label, fn, iterations = 1) {
  // 预热，避免把 JIT 编译时间算进去
  fn()
  const t0 = performance.now()
  let out
  for (let i = 0; i < iterations; i++) out = fn()
  const dt = (performance.now() - t0) / iterations
  console.log(`  ${label.padEnd(46)} ${dt.toFixed(2)} ms`)
  return { dt, out }
}

function bytesOf(value) {
  return Buffer.byteLength(JSON.stringify(value), 'utf8')
}

function fmtKB(bytes) {
  return `${(bytes / 1024).toFixed(1)} KB`
}

console.log('\n=== 单标的：主图路径（5000 根）===')
const raw5000 = makeRows(5000, 1)
console.log(`  ${'网络载荷（JSON）'.padEnd(46)} ${fmtKB(bytesOf(raw5000))}`)

const base = heapMB()
const parsed5000 = normalize(raw5000)
const afterParse = heapMB()
console.log(`  ${'解析后常驻内存'.padEnd(46)} ${(afterParse - base).toFixed(2)} MB`)

timeIt('computeLevels(5000 根)', () => computeLevels(parsed5000), 5)
timeIt('pickChartLevels', () => pickChartLevels(computeLevels(parsed5000), 3), 5)

const lv = computeLevels(parsed5000)
console.log(`  ${'算出的候选关键位数量'.padEnd(46)} ${lv.all.length}`)
console.log(`  ${'画到图上的关键位数量'.padEnd(46)} ${pickChartLevels(lv, 3).length}`)

console.log('\n=== 多标的：对比条路径（每只 60 根）===')
for (const n of [3, 6, 10]) {
  const raws = Array.from({ length: n }, (_, i) => makeRows(60, i + 1))
  const payload = raws.reduce((s, r) => s + bytesOf(r), 0)
  const heapBefore = heapMB()
  const parsed = raws.map(normalize)
  const heapAfter = heapMB()
  const { dt } = timeIt(`${n} 只 x 60 根：解析 + 现价 + sparkline + 汇总`, () => {
    const rows = parsed.map((p) => {
      const change = computeChange(p)
      const closes = p.map((r) => r.close)
      return {
        thscode: `x${n}`,
        name: 'n',
        close: change.close,
        pctChange: change.pctChange,
        closes,
        path: buildSparklinePath(closes, 72, 18),
      }
    })
    return summarizeCompare(rows)
  }, 20)
  console.log(
    `  ${`  -> ${n} 只的网络载荷`.padEnd(46)} ${fmtKB(payload)}（主图的 ${((payload / bytesOf(raw5000)) * 100).toFixed(1)}%）`,
  )
  console.log(`  ${`  -> ${n} 只的常驻内存`.padEnd(46)} ${(heapAfter - heapBefore).toFixed(2)} MB`)
  void dt
}

console.log('\n=== 结论 ===')
const mainPayload = bytesOf(raw5000)
const compare10 = 10 * bytesOf(makeRows(60, 1))
console.log(`  主图（1 只 x 5000 根）      ${fmtKB(mainPayload)}`)
console.log(`  对比条（10 只 x 60 根）     ${fmtKB(compare10)}`)
console.log(
  `  对比条 10 只的总量是主图的  ${((compare10 / mainPayload) * 100).toFixed(1)}%（同一只票不重复取，走共享缓存）`,
)
