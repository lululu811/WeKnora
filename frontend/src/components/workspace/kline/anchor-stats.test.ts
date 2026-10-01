import assert from 'node:assert/strict'
import test from 'node:test'

import { computeAnchorStats, formatAnchorStats } from './anchor-stats.ts'
import type { LevelBar } from './levels'

const DAY = 86_400_000

function bar(i: number, close: number, high = close + 1, low = close - 1): LevelBar {
  return { timestamp: i * DAY, close, high, low }
}

test('正常区间：涨跌幅取首尾收盘，振幅取高低差', () => {
  const bars = [bar(0, 100), bar(1, 110), bar(2, 120)]
  const s = computeAnchorStats(bars, 0, 2)
  assert.ok(s)
  // (120 - 100) / 100 = 20%
  assert.equal(s.pctChange, 20)
  // high 最大 121，low 最小 99 => (121-99)/100 = 22%
  assert.equal(s.amplitude, 22)
  assert.equal(s.high, 121)
  assert.equal(s.low, 99)
  assert.equal(s.bars, 3)
})

test('下跌区间：涨跌幅为负', () => {
  const bars = [bar(0, 100), bar(1, 90)]
  const s = computeAnchorStats(bars, 0, 1)
  assert.equal(s?.pctChange, -10)
})

test('只有一根：涨跌幅是 null 而不是 0', () => {
  const s = computeAnchorStats([bar(0, 100), bar(1, 105)], 1, 1)
  assert.ok(s)
  // 0 会被读成"没涨没跌"，而这里表达的是"算不出来"
  assert.equal(s.pctChange, null)
  assert.equal(s.bars, 1)
  // 振幅仍然算得出：那一根自己 (106-104)/105 = 1.9%
  assert.equal(s.amplitude, 1.9)
})

test('首根收盘为 0：两个比率都是 null，但不崩', () => {
  const bars = [bar(0, 0, 5, 0), bar(1, 10)]
  const s = computeAnchorStats(bars, 0, 1)
  assert.ok(s)
  assert.equal(s.pctChange, null)
  assert.equal(s.amplitude, null)
  assert.equal(s.high, 11)
  assert.equal(s.low, 0)
})

test('空数据返回 null', () => {
  assert.equal(computeAnchorStats([], 0, 1), null)
})

test('下标传反时自动规范化', () => {
  const bars = [bar(0, 100), bar(1, 110), bar(2, 120)]
  const a = computeAnchorStats(bars, 2, 0)
  const b = computeAnchorStats(bars, 0, 2)
  assert.deepEqual(a, b)
})

test('下标越界时夹到有效范围，不返回 null', () => {
  const bars = [bar(0, 100), bar(1, 110)]
  const s = computeAnchorStats(bars, -5, 99)
  assert.ok(s)
  assert.equal(s.bars, 2)
  assert.equal(s.pctChange, 10)
})

test('单根区间的振幅就是那一根的高低幅', () => {
  const s = computeAnchorStats([bar(0, 50, 55, 45)], 0, 0)
  assert.equal(s?.amplitude, 20) // (55-45)/50
})

test('全平数据：涨跌幅 0（真的没涨没跌），振幅 0', () => {
  const bars = [bar(0, 100, 100, 100), bar(1, 100, 100, 100)]
  const s = computeAnchorStats(bars, 0, 1)
  // 这里 0 是**真的** 0，与"算不出来"的 null 是两回事
  assert.equal(s?.pctChange, 0)
  assert.equal(s?.amplitude, 0)
})

test('数值保留两位，不出现浮点长尾', () => {
  const bars = [bar(0, 3), bar(1, 7)]
  const s = computeAnchorStats(bars, 0, 1)
  assert.equal(s?.pctChange, 133.33)
})

test('formatAnchorStats：完整一行', () => {
  const bars = [bar(0, 100), bar(1, 110), bar(2, 120)]
  const text = formatAnchorStats(computeAnchorStats(bars, 0, 2))
  assert.match(text, /\+20\.00%/)
  assert.match(text, /振幅 22\.00%/)
  assert.match(text, /高 121\.00/)
  assert.match(text, /低 99\.00/)
  assert.match(text, /3 个交易日/)
})

test('formatAnchorStats：缺失的数字写成「—」，不写 0', () => {
  const text = formatAnchorStats(computeAnchorStats([bar(0, 100)], 0, 0))
  assert.match(text, /^—/)
  assert.ok(!text.includes('+0.00%'), `不该把算不出来的涨跌幅写成 0：${text}`)
})

test('formatAnchorStats：null 返回空串', () => {
  assert.equal(formatAnchorStats(null), '')
})

test('负涨跌幅不带多余的正号', () => {
  const text = formatAnchorStats(computeAnchorStats([bar(0, 100), bar(1, 90)], 0, 1))
  assert.match(text, /-10\.00%/)
  assert.ok(!text.includes('+-'), text)
})
