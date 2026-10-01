import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveAnchorsForChart, MAX_PERSISTENT_ANCHORS } from './anchor-render.ts'
import type { KLineAnchor } from '@/utils/klineAnchors'
import type { LevelBar } from './levels'

const DAY = 86_400_000
/** 2026-05-01 起的连续日线，便于按日期推下标。 */
const BASE = Date.parse('2026-05-01T00:00:00Z')

function bars(n: number): LevelBar[] {
  return Array.from({ length: n }, (_, i) => {
    const p = 100 + i
    return { timestamp: BASE + i * DAY, high: p + 2, low: p - 2, close: p }
  })
}

function iso(offset: number): string {
  return new Date(BASE + offset * DAY).toISOString().slice(0, 10)
}

function rangeAnchor(index: number, from: string, to: string, label = '段'): KLineAnchor {
  return { index, kind: 'range', from, to, label, valid: true }
}

function levelAnchor(index: number, value: number, label = '位'): KLineAnchor {
  return { index, kind: 'level', value, label, valid: true }
}

test('range：日期换算成下标并算出价格包络', () => {
  const out = resolveAnchorsForChart([rangeAnchor(1, iso(2), iso(5))], bars(20))
  assert.equal(out.length, 1)
  const a = out[0]
  assert.equal(a.resolvable, true)
  assert.equal(a.startIndex, 2)
  assert.equal(a.endIndex, 5)
  // 下标 2..5 的 high = 102+2..105+2 => 107；low = 100..103 => 100
  assert.equal(a.high, 107)
  assert.equal(a.low, 100)
})

test('range：单日区间也能画（起止同一天）', () => {
  const out = resolveAnchorsForChart([rangeAnchor(1, iso(3), iso(3))], bars(20))
  assert.equal(out[0].resolvable, true)
  assert.equal(out[0].startIndex, out[0].endIndex)
})

test('range：起止写反时自动纠正顺序', () => {
  const out = resolveAnchorsForChart([rangeAnchor(1, iso(8), iso(2))], bars(20))
  assert.equal(out[0].startIndex, 2)
  assert.equal(out[0].endIndex, 8)
})

test('range：日期落在数据之外 -> 不可解析（不硬贴到最近一根）', () => {
  const far = '2020-01-01'
  const out = resolveAnchorsForChart([rangeAnchor(1, far, iso(3))], bars(20))
  assert.equal(out[0].resolvable, false)
})

test('range：日期落在周末/停牌日，就近吸附', () => {
  // 取一个不在 bars 里的时间戳（中间偏 3 天），应吸附到最近的一根
  const shifted = new Date(BASE + 2 * DAY + 3 * 3600_000).toISOString().slice(0, 10)
  const out = resolveAnchorsForChart([rangeAnchor(1, shifted, iso(4))], bars(20))
  assert.equal(out[0].resolvable, true)
})

test('range：无效锚点 -> 不可解析', () => {
  const bad: KLineAnchor = { index: 1, kind: 'range', from: '2026-02-30', to: '2026-03-01', label: 'x', valid: false }
  assert.equal(resolveAnchorsForChart([bad], bars(20))[0].resolvable, false)
})

test('level：正数价格可画', () => {
  const out = resolveAnchorsForChart([levelAnchor(1, 105.5)], bars(20))
  assert.equal(out[0].resolvable, true)
  assert.equal(out[0].value, 105.5)
})

test('level：0 / 负数 / 缺失 -> 不可解析', () => {
  const cases: KLineAnchor[] = [
    { index: 1, kind: 'level', value: 0, label: 'x', valid: false },
    { index: 2, kind: 'level', value: -3, label: 'x', valid: false },
    { index: 3, kind: 'level', label: 'x', valid: false },
  ]
  for (const c of cases) {
    assert.equal(resolveAnchorsForChart([c], bars(20))[0].resolvable, false, JSON.stringify(c))
  }
})

test('上限：只保留前 N 个可解析的', () => {
  const list = Array.from({ length: 8 }, (_, i) => levelAnchor(i + 1, 100 + i))
  const out = resolveAnchorsForChart(list, bars(20), 3)
  assert.equal(out.filter((a) => a.resolvable).length, 3)
  assert.deepEqual(out.filter((a) => a.resolvable).map((a) => a.index), [1, 2, 3])
})

test('上限：先淘汰画不出来的，再按正文顺序取前 N 个', () => {
  // 前两个画不出来，若先截断就会白白浪费名额
  const list = [
    { index: 1, kind: 'level', value: 0, label: 'bad1', valid: false } as KLineAnchor,
    { index: 2, kind: 'level', value: -1, label: 'bad2', valid: false } as KLineAnchor,
    levelAnchor(3, 101),
    levelAnchor(4, 102),
  ]
  const out = resolveAnchorsForChart(list, bars(20), 2)
  assert.deepEqual(out.filter((a) => a.resolvable).map((a) => a.index), [3, 4])
  // 不可解析的仍然返回（正文里还要用它渲染文本）
  assert.deepEqual(out.filter((a) => !a.resolvable).map((a) => a.index), [1, 2])
})

test('上限：不可解析的锚点不计入名额', () => {
  const list = [
    levelAnchor(1, 101),
    { index: 2, kind: 'level', value: 0, label: 'bad', valid: false } as KLineAnchor,
    levelAnchor(3, 103),
  ]
  const out = resolveAnchorsForChart(list, bars(20), 2)
  assert.deepEqual(out.filter((a) => a.resolvable).map((a) => a.index), [1, 3])
})

test('默认上限就是 5，与提示词里的数量约束同数', () => {
  assert.equal(MAX_PERSISTENT_ANCHORS, 5)
  const list = Array.from({ length: 10 }, (_, i) => levelAnchor(i + 1, 100 + i))
  assert.equal(resolveAnchorsForChart(list, bars(20)).filter((a) => a.resolvable).length, 5)
})

test('空数据：range 不可解析，level 不受影响', () => {
  const out = resolveAnchorsForChart([rangeAnchor(1, iso(1), iso(2)), levelAnchor(2, 100)], [])
  const byIndex = (i: number) => out.find((a) => a.index === i)!
  // range 要时间轴定位，没有 K 线就定位不了
  assert.equal(byIndex(1).resolvable, false)
  // level 是横贯全图的线，不依赖时间轴
  assert.equal(byIndex(2).resolvable, true)
})

test('返回顺序契约：可画的在前、不可画的后', () => {
  // 这个顺序是刻意的——绘制层按序画前 N 个即可，不必自己再过滤一遍。
  // 编号不因排序而改变，正文里的 ①②③ 与图上一致。
  const out = resolveAnchorsForChart(
    [rangeAnchor(1, '2020-01-01', '2020-01-02'), levelAnchor(2, 100), levelAnchor(3, 101)],
    bars(20),
  )
  assert.deepEqual(out.map((a) => a.resolvable), [true, true, false])
  assert.deepEqual(out.map((a) => a.index), [2, 3, 1])
})

test('编号原样保留，不因截断而重排', () => {
  const list = [levelAnchor(1, 101), levelAnchor(2, 102), levelAnchor(3, 103)]
  const out = resolveAnchorsForChart(list, bars(20), 2)
  // 图上只画 1、2，但它们的编号仍是 1、2（不是重排成 1、2 之外的东西）
  assert.deepEqual(out.filter((a) => a.resolvable).map((a) => a.index), [1, 2])
})

// ---------------------------------------------------------------------------
// hitTestAnchor：光标命中判定。
// 自己算而不是用库的 overlay 事件——库的 figure 事件在这套配置下不触发，
// 而这个判定是纯函数，可以确定性地测。
// ---------------------------------------------------------------------------

import { hitTestAnchor, LEVEL_HIT_TOLERANCE, type AnchorHitBox } from './anchor-render.ts'

const RANGE_BOX: AnchorHitBox = { index: 1, kind: 'range', startIndex: 10, endIndex: 20, top: 100, bottom: 200 }
const LEVEL_BOX: AnchorHitBox = { index: 2, kind: 'level', levelY: 300 }

test('命中：落在框内', () => {
  assert.equal(hitTestAnchor([RANGE_BOX], 15, 150), 1)
})

test('命中：框的边界算在内', () => {
  assert.equal(hitTestAnchor([RANGE_BOX], 10, 100), 1)
  assert.equal(hitTestAnchor([RANGE_BOX], 20, 200), 1)
})

test('不命中：时间在框外', () => {
  assert.equal(hitTestAnchor([RANGE_BOX], 9, 150), null)
  assert.equal(hitTestAnchor([RANGE_BOX], 21, 150), null)
})

test('不命中：纵向在框外', () => {
  assert.equal(hitTestAnchor([RANGE_BOX], 15, 99), null)
  assert.equal(hitTestAnchor([RANGE_BOX], 15, 201), null)
})

test('命中：价格线在容差内', () => {
  assert.equal(hitTestAnchor([LEVEL_BOX], 0, 300), 2)
  assert.equal(hitTestAnchor([LEVEL_BOX], 0, 300 + LEVEL_HIT_TOLERANCE), 2)
  assert.equal(hitTestAnchor([LEVEL_BOX], 0, 300 - LEVEL_HIT_TOLERANCE), 2)
})

test('不命中：价格线超出容差', () => {
  assert.equal(hitTestAnchor([LEVEL_BOX], 0, 300 + LEVEL_HIT_TOLERANCE + 1), null)
})

test('优先级：框压过线（线横贯全图、命中带宽，否则会抢走框内所有位置）', () => {
  const boxes = [RANGE_BOX, { index: 9, kind: 'level' as const, levelY: 150 }]
  assert.equal(hitTestAnchor(boxes, 15, 150), 1, '框内应命中框而不是那条线')
})

test('多个框重叠时取先出现的', () => {
  const a: AnchorHitBox = { index: 1, kind: 'range', startIndex: 10, endIndex: 30, top: 100, bottom: 200 }
  const b: AnchorHitBox = { index: 2, kind: 'range', startIndex: 15, endIndex: 20, top: 120, bottom: 180 }
  assert.equal(hitTestAnchor([a, b], 17, 150), 1)
})

test('空输入 / 非法坐标返回 null', () => {
  assert.equal(hitTestAnchor([], 1, 1), null)
  assert.equal(hitTestAnchor([RANGE_BOX], NaN, 150), null)
  assert.equal(hitTestAnchor([RANGE_BOX], 15, NaN), null)
})

test('缺几何信息的框被跳过，不误判', () => {
  const incomplete: AnchorHitBox = { index: 5, kind: 'range', startIndex: 10, endIndex: 20 }
  assert.equal(hitTestAnchor([incomplete], 15, 150), null)
})
