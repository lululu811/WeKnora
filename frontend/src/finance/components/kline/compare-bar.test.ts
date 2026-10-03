import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSparklinePath,
  changeDirection,
  summarizeCompare,
  formatSummary,
  type CompareRow,
} from './compare-bar.ts'

function row(name: string, pctChange: number | null, closes: number[] = [1, 2]): CompareRow {
  return { thscode: `${name}.SH`, name, close: 10, pctChange, closes }
}

test('sparkline：少于两个点不画', () => {
  assert.equal(buildSparklinePath([], 100, 20), '')
  assert.equal(buildSparklinePath([5], 100, 20), '')
})

test('sparkline：宽度或高度非正时不画', () => {
  assert.equal(buildSparklinePath([1, 2], 0, 20), '')
  assert.equal(buildSparklinePath([1, 2], 100, 0), '')
})

test('sparkline：上升序列从左下到右上', () => {
  const d = buildSparklinePath([1, 2, 3], 100, 20)
  // 解析出各点 y 坐标，应当单调递减（SVG 的 y 向下）
  const ys = [...d.matchAll(/,([\d.]+)/g)].map((m) => Number(m[1]))
  assert.equal(ys.length, 3)
  assert.ok(ys[0] > ys[1] && ys[1] > ys[2], `y 应递减，实际 ${ys.join(',')}`)
})

test('sparkline：所有价格相等时画水平中线，不产生 NaN', () => {
  const d = buildSparklinePath([5, 5, 5], 100, 20)
  assert.ok(!d.includes('NaN'), `不应出现 NaN：${d}`)
  const ys = [...d.matchAll(/,([\d.]+)/g)].map((m) => Number(m[1]))
  assert.equal(new Set(ys).size, 1, '所有 y 应相同')
})

test('sparkline：x 均匀铺满给定宽度', () => {
  const d = buildSparklinePath([1, 2, 3, 4, 5], 80, 20)
  const xs = [...d.matchAll(/[ML]([\d.]+),/g)].map((m) => Number(m[1]))
  assert.equal(xs[0], 0)
  assert.equal(xs[xs.length - 1], 80)
  // 步长应一致
  const step = xs[1] - xs[0]
  for (let i = 1; i < xs.length; i++) {
    assert.ok(Math.abs(xs[i] - xs[i - 1] - step) < 0.01, `x 步长应均匀：${xs.join(',')}`)
  }
})

test('sparkline：端点不被裁到画布之外', () => {
  const d = buildSparklinePath([1, 100], 100, 20)
  const ys = [...d.matchAll(/,([\d.]+)/g)].map((m) => Number(m[1]))
  for (const y of ys) {
    assert.ok(y >= 0 && y <= 20, `y 应落在 [0,20]：${y}`)
  }
})

test('changeDirection：null 是 unknown，不是 flat', () => {
  assert.equal(changeDirection(null), 'unknown')
  assert.equal(changeDirection(NaN), 'unknown')
  assert.equal(changeDirection(0), 'flat')
  assert.equal(changeDirection(+1.2), 'up')
  assert.equal(changeDirection(-1.2), 'down')
})

test('summarizeCompare：挑出最强与最弱', () => {
  const s = summarizeCompare([row('A', 1), row('B', 5), row('C', -3)])
  assert.equal(s.count, 3)
  assert.equal(s.strongest?.name, 'B')
  assert.equal(s.weakest?.name, 'C')
})

test('summarizeCompare：null 不参与比较，也不被当成 0', () => {
  const s = summarizeCompare([row('A', null), row('B', -2)])
  // A 的数据不足不该让它成为"最强"（0 > -2 会把 A 选出来）
  assert.equal(s.strongest?.name, 'B', 'null 项不应胜出')
  assert.equal(s.weakest?.name, 'B')
  assert.equal(s.count, 2, 'count 仍应包含数据不足的项')
})

test('summarizeCompare：全部为 null 时不编造一个最强', () => {
  const s = summarizeCompare([row('A', null), row('B', null)])
  assert.equal(s.strongest, null)
  assert.equal(s.weakest, null)
  assert.equal(s.count, 2)
})

test('summarizeCompare：并列时保留先出现的', () => {
  const s = summarizeCompare([row('A', 5), row('B', 5)])
  assert.equal(s.strongest?.name, 'A')
})

test('formatSummary：零项返回空串', () => {
  assert.equal(formatSummary({ count: 0, strongest: null, weakest: null }), '')
})

test('formatSummary：无涨跌幅时只说数量，不编最强', () => {
  const text = formatSummary({ count: 3, strongest: null, weakest: null })
  assert.equal(text, '对比 3 只')
  assert.ok(!text.includes('最强'))
})

test('formatSummary：最强带正负号', () => {
  const up = formatSummary(summarizeCompare([row('A', 3.456)]))
  assert.match(up, /最强 A \+3\.46%/)
  const down = formatSummary(summarizeCompare([row('A', -2.1)]))
  assert.match(down, /最强 A -2\.10%/)
})
