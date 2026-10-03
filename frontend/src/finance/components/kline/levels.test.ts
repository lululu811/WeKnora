import assert from 'node:assert/strict'
import test from 'node:test'

import {
  calcPivotPoints,
  calcFibonacci,
  roundNumbers,
  findSwings,
  findNearest,
  computeLevels,
  pickChartLevels,
} from './levels.ts'
import type { KLineData } from './types.ts'

const DAY = 86400000

function bar(i: number, high: number, low: number, close: number, open = close): KLineData {
  return { timestamp: i * DAY, open, high, low, close, volume: 1000 }
}

// ---------------------------------------------------------------------------
// 这些期望值是**手算**的，不是从实现里抄回来的——抄回来的测试只能证明
// 「代码没变」，证明不了「公式对」。每个值下面写了算式。
// ---------------------------------------------------------------------------

test('枢轴点：经典公式，逐项手算核对', () => {
  // h=110 l=90 c=100
  // pp=(110+90+100)/3=100, r=110-90=20
  const p = calcPivotPoints(110, 90, 100)
  assert.equal(p.pp, 100)
  assert.equal(p.r1, 2 * 100 - 90) // 110
  assert.equal(p.r1, 110)
  assert.equal(p.r2, 100 + 20) // 120
  assert.equal(p.r2, 120)
  assert.equal(p.r3, 110 + 2 * (100 - 90)) // 130
  assert.equal(p.r3, 130)
  assert.equal(p.s1, 2 * 100 - 110) // 90
  assert.equal(p.s1, 90)
  assert.equal(p.s2, 100 - 20) // 80
  assert.equal(p.s2, 80)
  assert.equal(p.s3, 90 - 2 * (110 - 100)) // 70
  assert.equal(p.s3, 70)
})

test('枢轴点：R 在上、S 在下，且围绕 PP 对称', () => {
  const p = calcPivotPoints(110, 90, 100)
  assert.ok(p.r1 > p.pp && p.r2 > p.r1 && p.r3 > p.r2, 'R1<R2<R3 应递增')
  assert.ok(p.s1 < p.pp && p.s2 < p.s1 && p.s3 < p.s2, 'S1>S2>S3 应递减')
  // R1 与 S1 关于 PP 对称：PP-S1 = R1-PP
  assert.equal(p.pp - p.s1, p.r1 - p.pp)
})

test('斐波那契：下跌趋势从高点向下排', () => {
  // high=110 low=90 span=20
  const f = calcFibonacci(110, 90, true)
  assert.equal(f.high, 110)
  assert.equal(f.low, 90)
  assert.equal(Number(f.levels['23.6'].toFixed(2)), 110 - 20 * 0.236) // 105.28
  assert.equal(Number(f.levels['23.6'].toFixed(2)), 105.28)
  assert.equal(Number(f.levels['50.0'].toFixed(2)), 100) // 110-10
  assert.equal(Number(f.levels['78.6'].toFixed(2)), 94.28) // 110-15.72
})

test('斐波那契：非下跌趋势从低点向上排', () => {
  const f = calcFibonacci(110, 90, false)
  assert.equal(Number(f.levels['23.6'].toFixed(2)), 94.72) // 90+4.72
  assert.equal(Number(f.levels['50.0'].toFixed(2)), 100) // 90+10
  assert.equal(Number(f.levels['78.6'].toFixed(2)), 105.72) // 90+15.72
})

test('斐波那契：两种方向在 50% 处重合，其余分列两侧', () => {
  const down = calcFibonacci(110, 90, true)
  const up = calcFibonacci(110, 90, false)
  assert.equal(down.levels['50.0'], up.levels['50.0'])
  assert.ok(down.levels['23.6'] > up.levels['23.6'], '下跌方向应更靠近高点')
  assert.ok(down.levels['78.6'] < up.levels['78.6'], '下跌方向应更靠近低点')
})

test('斐波那契：五档齐全', () => {
  const f = calcFibonacci(110, 90, true)
  assert.deepEqual(Object.keys(f.levels).sort(), ['23.6', '38.2', '50.0', '61.8', '78.6'])
})

test('整数关口：按量级选步长', () => {
  // >1000 -> step 50。1060 不在关口上（1060/50=21.2），两侧各取到 2 个。
  assert.deepEqual(roundNumbers(1060), { below: [1050, 1000], above: [1100, 1150] })
  // >100 -> step 10
  assert.deepEqual(roundNumbers(105), { below: [100, 90], above: [110, 120] })
  // >10 -> step 5
  assert.deepEqual(roundNumbers(53), { below: [50, 45], above: [55, 60] })
  // <=10 -> step 1
  assert.deepEqual(roundNumbers(8.5), { below: [8, 7], above: [9, 10] })
})

test('整数关口：恰好落在关口上时不含自身，且该侧只剩 1 个', () => {
  // 这是后端循环上界带来的不对称，前端刻意保持一致（见 levels.ts 的说明）。
  // 若哪天后端改成"各取两个"，这里必须同时改——所以把它钉住而不是放过。
  assert.ok(!roundNumbers(105).below.includes(105))
  assert.ok(!roundNumbers(1500).below.includes(1500))
  assert.ok(!roundNumbers(1500).above.includes(1500))

  // 1500 恰在 50 的关口上（1500/50=30）-> below 只有 1 个；1060 不在 -> 2 个
  assert.deepEqual(roundNumbers(1500).below, [1450])
  assert.deepEqual(roundNumbers(1060).below, [1050, 1000])
  // 55 恰在 5 的关口上 -> below 只有 1 个
  assert.deepEqual(roundNumbers(55).below, [50])
  assert.deepEqual(roundNumbers(53).below, [50, 45])
})

test('摆动点：找出唯一的高点与低点', () => {
  // 20 根，bars[10] 是最高点，bars[5] 是最低点
  const bars: KLineData[] = []
  for (let i = 0; i < 20; i++) {
    const high = i === 10 ? 130 : 100
    const low = i === 5 ? 60 : 90
    bars.push(bar(i, high, low, 100))
  }
  const { highs, lows } = findSwings(bars, 5)
  assert.deepEqual(highs, [9], '新到旧序列里，bars[10] 的下标是 9')
  assert.deepEqual(lows, [14], '新到旧序列里，bars[5] 的下标是 14')
})

test('摆动点：窗口内的高点不构成摆动点', () => {
  // 把最高点放到最末尾（新到旧下标 0），落在 window 之前，不应被识别
  const bars: KLineData[] = []
  for (let i = 0; i < 20; i++) bars.push(bar(i, i === 19 ? 130 : 100, 90, 100))
  const { highs } = findSwings(bars, 5)
  assert.deepEqual(highs, [], '边界上的极值没有左右各 5 根可比，不该报出来')
})

test('摆动点：全平数据不产生任何摆动点', () => {
  const bars = Array.from({ length: 20 }, (_, i) => bar(i, 100, 90, 95))
  const { highs, lows } = findSwings(bars, 5)
  assert.deepEqual(highs, [])
  assert.deepEqual(lows, [])
})

test('最近支撑/阻力：各取价格两侧最近的一个', () => {
  const levels = [
    { name: 'a', price: 90 },
    { name: 'b', price: 95 },
    { name: 'c', price: 105 },
    { name: 'd', price: 120 },
  ]
  const { support, resistance } = findNearest(levels, 100)
  assert.equal(support?.price, 95, '下方最近的是 95')
  assert.equal(support?.source, 'b')
  assert.equal(resistance?.price, 105, '上方最近的是 105')
  assert.equal(resistance?.source, 'c')
})

test('最近支撑/阻力：价格恰好等于某个位时，该位既不算支撑也不算阻力', () => {
  const { support, resistance } = findNearest([{ name: 'x', price: 100 }], 100)
  assert.equal(support, null)
  assert.equal(resistance, null)
})

test('computeLevels：数据不足时返回 null，不编造位', () => {
  assert.equal(computeLevels([]), null)
  assert.equal(computeLevels([bar(0, 10, 9, 10)]), null)
})

test('computeLevels：最新价为 0 时返回 null', () => {
  const bars = [bar(0, 10, 9, 10), bar(1, 10, 9, 0)]
  assert.equal(computeLevels(bars), null)
})

test('computeLevels：以最新一根为基准算枢轴点', () => {
  const bars = [bar(0, 50, 40, 45), bar(1, 110, 90, 100)]
  const lv = computeLevels(bars)
  assert.ok(lv)
  assert.equal(lv.price, 100, '基准价应是最新收盘')
  assert.equal(lv.pivot.pp, 100)
})

test('computeLevels：全部候选位都是正数，且带来源名', () => {
  const bars: KLineData[] = []
  for (let i = 0; i < 40; i++) {
    const base = 100 + Math.sin(i / 5) * 8
    bars.push(bar(i, base + 2, base - 2, base))
  }
  const lv = computeLevels(bars)
  assert.ok(lv)
  assert.ok(lv.all.length > 0)
  for (const l of lv.all) {
    assert.ok(l.price > 0, `价位应为正: ${l.name}=${l.price}`)
    assert.ok(Number.isFinite(l.price))
    assert.ok(l.name.length > 0)
  }
})

test('pickChartLevels：每侧不超过上限', () => {
  const bars: KLineData[] = []
  for (let i = 0; i < 40; i++) {
    const base = 100 + Math.sin(i / 5) * 8
    bars.push(bar(i, base + 2, base - 2, base))
  }
  const lv = computeLevels(bars)
  assert.ok(lv)
  const picked = pickChartLevels(lv, 3)
  assert.ok(picked.filter((p) => p.side === 'support').length <= 3)
  assert.ok(picked.filter((p) => p.side === 'resistance').length <= 3)
})

test('pickChartLevels：支撑在价格下方，阻力在上方', () => {
  const bars: KLineData[] = []
  for (let i = 0; i < 40; i++) {
    const base = 100 + Math.sin(i / 5) * 8
    bars.push(bar(i, base + 2, base - 2, base))
  }
  const lv = computeLevels(bars)
  assert.ok(lv)
  for (const p of pickChartLevels(lv, 3)) {
    if (p.side === 'support') assert.ok(p.price < lv.price, `支撑应低于现价: ${p.price} vs ${lv.price}`)
    else assert.ok(p.price > lv.price, `阻力应高于现价: ${p.price} vs ${lv.price}`)
  }
})

test('pickChartLevels：重合的位合并成一个，并记下全部来源', () => {
  const bars = [bar(0, 10, 9, 10), bar(1, 110, 90, 100)]
  const lv = computeLevels(bars)
  assert.ok(lv)
  // 手工塞两个几乎重合的位（差 0.1%，小于默认 0.5% 容差）
  const merged = pickChartLevels(
    { ...lv, all: [
      { name: 'A', price: 90 },
      { name: 'B', price: 90.1 },
      { name: 'C', price: 120 },
    ] },
    3,
  )
  const sup = merged.find((m) => m.side === 'support')
  assert.ok(sup)
  assert.deepEqual(sup.sources.sort(), ['A', 'B'], '重合的位应合并为一条并保留两个来源')
  // 支撑按价位从高到低遍历（先遇到离现价最近的），所以代表价是 90.1。
  assert.equal(sup.price, 90.1, '合并后取离现价最近的那个价位')
})

test('pickChartLevels：结果按价位从低到高排序', () => {
  const bars: KLineData[] = []
  for (let i = 0; i < 40; i++) {
    const base = 100 + Math.sin(i / 5) * 8
    bars.push(bar(i, base + 2, base - 2, base))
  }
  const lv = computeLevels(bars)
  assert.ok(lv)
  const prices = pickChartLevels(lv, 3).map((p) => p.price)
  const sorted = [...prices].sort((a, b) => a - b)
  assert.deepEqual(prices, sorted)
})

test('pickChartLevels：没有候选位时返回空数组', () => {
  const bars = [bar(0, 10, 9, 10), bar(1, 110, 90, 100)]
  const lv = computeLevels(bars)
  assert.ok(lv)
  assert.deepEqual(pickChartLevels({ ...lv, all: [] }, 3), [])
})

test('pickChartLevels：合并容差决定多近才算"同一个价位"', () => {
  // 现价 100，下方两个位相距 0.8：默认容差 0.5%（=0.5）不合并，
  // 画线用的 1.5%（=1.5）合并成一个。
  const bars = [bar(0, 10, 9, 10), bar(1, 110, 90, 100)]
  const lv = computeLevels(bars)
  assert.ok(lv)
  const clustered = {
    ...lv,
    price: 100,
    all: [
      { name: 'A', price: 98.0 },
      { name: 'B', price: 98.8 },
      { name: 'C', price: 101.5 },
    ],
  }
  const tight = pickChartLevels(clustered, 2)
  assert.equal(tight.filter((p) => p.side === 'support').length, 2, '默认容差下两个支撑各自成条')

  const loose = pickChartLevels(clustered, 2, 0.015)
  const looseSup = loose.filter((p) => p.side === 'support')
  assert.equal(looseSup.length, 1, '放大容差后应收成一条')
  assert.deepEqual(looseSup[0].sources.sort(), ['A', 'B'], '合并后保留全部来源')
})

test('pickChartLevels：每侧上限为 1 时只给最近的那个', () => {
  const bars = [bar(0, 10, 9, 10), bar(1, 110, 90, 70.06)]
  const lv = computeLevels(bars)
  assert.ok(lv)
  const picked = pickChartLevels(
    { ...lv, price: 70.06, all: [
      { name: 'S1', price: 68.87 },
      { name: 'S2', price: 70.0 },
      { name: 'R1', price: 70.79 },
      { name: 'R2', price: 72.45 },
    ] },
    1,
  )
  assert.equal(picked.filter((p) => p.side === 'support').length, 1)
  assert.equal(picked.filter((p) => p.side === 'resistance').length, 1)
  // 必须是最近的那个
  assert.equal(picked.find((p) => p.side === 'support')?.price, 70.0)
  assert.equal(picked.find((p) => p.side === 'resistance')?.price, 70.79)
})
