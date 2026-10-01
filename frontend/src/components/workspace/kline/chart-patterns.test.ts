import assert from 'node:assert/strict'
import test from 'node:test'

import { resolvePatternGeometry } from './chart-patterns.ts'
import type { RawChartPatternResponse } from './chart-patterns.ts'
import type { LevelBar } from './levels.ts'

const DAY = 86400000

// 用 UTC 零点对齐，和后端返回的 "YYYY-MM-DD" 对得上。
function bar(i: number, high = 10, low = 5, close = 8): LevelBar {
  return { timestamp: Date.UTC(2026, 0, 1) + i * DAY, high, low, close }
}

function iso(i: number): string {
  return new Date(Date.UTC(2026, 0, 1) + i * DAY).toISOString().slice(0, 10)
}

const BARS: LevelBar[] = Array.from({ length: 10 }, (_, i) => bar(i))

// ---------------------------------------------------------------------------
// 换算的核心是**日期 -> 下标**。后端给日期（人对得上），图上要下标。
// 对不上的点必须丢掉，绝不能兜底到"最近的一根"——那会把形态画到错误的位置，
// 比不画更糟。
// ---------------------------------------------------------------------------

test('顶点按日期换算成下标', () => {
  const raw: RawChartPatternResponse = {
    chart_pattern: {
      patterns: [
        {
          name: '头肩顶',
          direction: 'bearish',
          confidence: 0.7,
          points: [
            { date: iso(2), price: 12, label: '左肩' },
            { date: iso(5), price: 16, label: '头' },
            { date: iso(8), price: 12, label: '右肩' },
          ],
        },
      ],
    },
  }
  const out = resolvePatternGeometry(raw, BARS)
  assert.equal(out.length, 1)
  assert.deepEqual(
    out[0].points.map((p) => [p.index, p.price, p.label]),
    [
      [2, 12, '左肩'],
      [5, 16, '头'],
      [8, 12, '右肩'],
    ],
  )
})

test('顶点按时间顺序输出，不受后端顺序影响', () => {
  const raw: RawChartPatternResponse = {
    chart_pattern: {
      patterns: [
        {
          name: '双顶',
          points: [
            { date: iso(7), price: 20, label: '顶二' },
            { date: iso(2), price: 20, label: '顶一' },
          ],
        },
      ],
    },
  }
  const out = resolvePatternGeometry(raw, BARS)
  assert.deepEqual(out[0].points.map((p) => p.index), [2, 7])
})

test('日期不在已加载的 K 线里时，该点被丢弃', () => {
  const raw: RawChartPatternResponse = {
    chart_pattern: {
      patterns: [
        {
          name: '头肩顶',
          points: [
            { date: iso(2), price: 12, label: '左肩' },
            { date: '2019-05-05', price: 16, label: '头' }, // 图外
          ],
        },
      ],
    },
  }
  const out = resolvePatternGeometry(raw, BARS)
  assert.deepEqual(out[0].points.map((p) => p.label), ['左肩'])
})

test('价格非法或缺失的点被丢弃', () => {
  const raw: RawChartPatternResponse = {
    chart_pattern: {
      patterns: [
        {
          name: '头肩顶',
          points: [
            { date: iso(2), price: 12, label: '好点' },
            { date: iso(3), price: 0, label: '零价' },
            { date: iso(4), label: '没价' },
            { date: iso(5), price: Number.NaN, label: 'NaN' },
          ],
        },
      ],
    },
  }
  const out = resolvePatternGeometry(raw, BARS)
  assert.deepEqual(out[0].points.map((p) => p.label), ['好点'])
})

test('参考线两端点都要能换算，否则整条丢弃', () => {
  const raw: RawChartPatternResponse = {
    chart_pattern: {
      patterns: [
        {
          name: '头肩顶',
          points: [{ date: iso(2), price: 12 }],
          lines: [
            {
              label: '颈线',
              points: [
                { date: iso(2), price: 10 },
                { date: iso(6), price: 10 },
              ],
            },
            {
              label: '目标',
              points: [
                { date: iso(2), price: 8 },
                { date: '2019-01-01', price: 8 }, // 图外 -> 整条丢弃
              ],
            },
          ],
        },
      ],
    },
  }
  const out = resolvePatternGeometry(raw, BARS)
  assert.deepEqual(out[0].lines.map((l) => l.label), ['颈线'])
})

test('一个点都画不出来的形态被整个丢弃', () => {
  const raw: RawChartPatternResponse = {
    chart_pattern: {
      patterns: [
        { name: '头肩顶', points: [{ date: '2019-01-01', price: 12 }] },
        { name: '双顶', points: [{ date: iso(3), price: 12 }] },
      ],
    },
  }
  const out = resolvePatternGeometry(raw, BARS)
  assert.deepEqual(out.map((p) => p.name), ['双顶'])
})

test('波浪作为 kind=wave 一并输出', () => {
  const raw: RawChartPatternResponse = {
    waves: {
      name: '推动浪 1-2-3-4-5',
      direction: 'bullish',
      confidence: 0.6,
      points: [
        { date: iso(1), price: 10, label: '1' },
        { date: iso(3), price: 12, label: '2' },
      ],
    },
  }
  const out = resolvePatternGeometry(raw, BARS)
  assert.equal(out.length, 1)
  assert.equal(out[0].kind, 'wave')
  assert.equal(out[0].name, '推动浪 1-2-3-4-5')
})

test('未知的 direction 归为 neutral，不猜方向', () => {
  const raw: RawChartPatternResponse = {
    chart_pattern: { patterns: [{ name: 'X', direction: 'sideways', points: [{ date: iso(1), price: 10 }] }] },
  }
  assert.equal(resolvePatternGeometry(raw, BARS)[0].direction, 'neutral')
})

test('空响应与空 K 线都返回空数组，不抛错', () => {
  assert.deepEqual(resolvePatternGeometry(null, BARS), [])
  assert.deepEqual(resolvePatternGeometry({}, BARS), [])
  assert.deepEqual(resolvePatternGeometry({ chart_pattern: { patterns: [{ name: 'A', points: [{ date: iso(1), price: 1 }] }] } }, []), [])
})
