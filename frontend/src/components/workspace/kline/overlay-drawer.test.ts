import assert from 'node:assert/strict'
import test from 'node:test'

import { detectKLinePatterns, selectPatternIndicesToDraw, type KLinePatternItem } from './overlay-drawer.ts'
import type { Annotation } from './annotate-api.ts'
import type { KLineData } from './types.ts'

const DAY = 86400000

/**
 * 一串**不会触发本地形态识别**的日线：全红、实体占比高、逐根抬高。
 *
 * 必须这样造数据，否则本地检测器（阳包阴/十字星/早晨之星…）会把数组先填满，
 * 测试就分不清某个位置上的形态到底来自后端标注还是本地检测——而那正是
 * 这个文件要守住的东西。
 */
function neutralSeries(n: number, startIso: string): KLineData[] {
  const start = Date.parse(`${startIso}T00:00:00Z`)
  return Array.from({ length: n }, (_, i) => {
    const open = 10 + i * 0.1
    const close = open + 0.08
    return {
      timestamp: start + i * DAY,
      open,
      close,
      high: close + 0.005,
      low: open - 0.005,
      volume: 1000,
    }
  })
}

function isoAt(startIso: string, offset: number): string {
  return new Date(Date.parse(`${startIso}T00:00:00Z`) + offset * DAY).toISOString().slice(0, 10)
}

function ann(onDate: string, extra: Partial<Annotation> = {}): Annotation {
  return {
    type: 'b1', date: onDate, price: 10, text: 'B1 建仓波',
    confidence: 0.8, metadata: {}, source: 'algorithm', ...extra,
  }
}

/** 被填充的下标。 */
function filledIndexes(list: Array<unknown | null>): number[] {
  return list.map((p, i) => (p ? i : -1)).filter((i) => i >= 0)
}

test('中性数据本身不触发任何本地形态（测试前提的自检）', () => {
  const found = detectKLinePatterns(neutralSeries(12, '2026-01-05'), [])
  assert.deepEqual(filledIndexes(found), [], '这串数据应当干净，否则下面的断言没有意义')
})

test('算法标注渲染为实心，标签保持原文', () => {
  const start = '2026-01-05'
  const found = detectKLinePatterns(neutralSeries(10, start), [ann(isoAt(start, 3))])
  assert.deepEqual(filledIndexes(found), [3], '标注应当只出现在第 4 根')
  const hit = found[3]
  assert.ok(hit)
  assert.equal(hit.text, 'B1 建仓波')
  assert.notEqual(hit.bgColor, 'transparent', '算法标注应为实心底')
})

test('模型主张的标注渲染为描边 + 带前缀，与算法标注可区分', () => {
  const start = '2026-02-02'
  const found = detectKLinePatterns(neutralSeries(10, start), [
    ann(isoAt(start, 3), { source: 'llm', text: '关键支撑' }),
  ])
  const hit = found[3]
  assert.ok(hit)
  assert.equal(hit.bgColor, 'transparent', '模型主张应为描边（不依赖色觉的区分）')
  assert.equal(hit.text, '观点·关键支撑')
})

test('同一根上算法与模型主张的渲染必然不同', () => {
  const start = '2026-07-06'
  const algo = detectKLinePatterns(neutralSeries(6, start), [ann(isoAt(start, 2))])[2]
  // 清缓存后换 llm 来源再算一次（同一个模块级缓存，靠内容变化触发重算）
  const llm = detectKLinePatterns(neutralSeries(6, start), [
    ann(isoAt(start, 2), { source: 'llm', text: 'B1 建仓波' }),
  ])[2]
  assert.ok(algo && llm)
  assert.notEqual(algo.bgColor, llm.bgColor)
  assert.notEqual(algo.text, llm.text)
})

test('换标的但根数相同：不得复用上一只票的形态', () => {
  // 这是本模块的真实缺陷：缓存键曾只有「根数」，两只票 K 线根数一样时，
  // 第二只票会拿到第一只票的形态——用户切了标的却看到上一只的标注。
  const first = neutralSeries(10, '2026-03-02')
  assert.deepEqual(filledIndexes(detectKLinePatterns(first, [ann(isoAt('2026-03-02', 3))])), [3])

  const second = neutralSeries(10, '2026-04-01')
  assert.deepEqual(
    filledIndexes(detectKLinePatterns(second, [])),
    [],
    '第二只票没有标注，结果里不该出现第一只票的形态',
  )
})

test('换标的且标注日期错位：按新数据重新定位', () => {
  const a = neutralSeries(12, '2026-05-04')
  detectKLinePatterns(a, [ann(isoAt('2026-05-04', 5))])

  const b = neutralSeries(12, '2026-09-07')
  const found = detectKLinePatterns(b, [ann(isoAt('2026-09-07', 1))])
  assert.deepEqual(filledIndexes(found), [1], '标注应落在第 2 根')
})

test('根数相同但标注数量变化时必须重算', () => {
  const start = '2026-06-01'
  const data = neutralSeries(8, start)
  assert.equal(filledIndexes(detectKLinePatterns(data, [ann(isoAt(start, 2))])).length, 1)
  assert.equal(
    filledIndexes(
      detectKLinePatterns(data, [ann(isoAt(start, 2)), ann(isoAt(start, 5))]),
    ).length,
    2,
    '标注数量变了必须重算',
  )
})

test('根数相同、数量相同，但落在不同日期：必须重算', () => {
  // 只比长度和数量挡不住这一种：两个标的都是 10 根、都只有 1 个标注，
  // 但标注日期不同，缓存会把前一个的位置原样返回。
  const start = '2026-08-03'
  const data = neutralSeries(10, start)
  assert.deepEqual(filledIndexes(detectKLinePatterns(data, [ann(isoAt(start, 2))])), [2])
  assert.deepEqual(
    filledIndexes(detectKLinePatterns(data, [ann(isoAt(start, 7))])),
    [7],
    '标注日期变了必须按新日期定位',
  )
})

test('同一份输入重复调用结果一致（缓存不能改变答案）', () => {
  const start = '2026-10-05'
  const data = neutralSeries(9, start)
  const annotations = [ann(isoAt(start, 4))]
  const first = detectKLinePatterns(data, annotations)
  const second = detectKLinePatterns(data, annotations)
  assert.deepEqual(filledIndexes(second), filledIndexes(first))
  assert.deepEqual(filledIndexes(first), [4])
})

// ---------------------------------------------------------------------------
// selectPatternIndicesToDraw：形态胶囊的间距裁剪。
//
// 起因：本地检测器对 2440 根产出 671 个形态，100 多根的可见区间里就有 36 个
// 胶囊叠在一起，K 线被完全盖住。实测加 10 根间距后降到 9 个。
// ---------------------------------------------------------------------------

function pat(type: string, fromBackend = false): KLinePatternItem {
  return { type, text: type, color: '#fff', bgColor: 'rgba(0,0,0,.4)', position: 'top', fromBackend }
}

test('间距裁剪：相邻的本地形态只保留第一个', () => {
  const patterns: Array<KLinePatternItem | null> = new Array(30).fill(null)
  patterns[5] = pat('doji')
  patterns[6] = pat('doji')
  patterns[7] = pat('doji')
  const kept = selectPatternIndicesToDraw(patterns, 0, 29, 10)
  assert.deepEqual(kept, [5])
})

test('间距裁剪：服务端标注永远保留，即使彼此很近', () => {
  const patterns: Array<KLinePatternItem | null> = new Array(30).fill(null)
  patterns[10] = pat('key_k', true)
  patterns[11] = pat('key_k', true)
  patterns[12] = pat('key_k', true)
  const kept = selectPatternIndicesToDraw(patterns, 0, 29, 10)
  // 三个都是后端标注 -> 全部保留（信号优先于排版）
  assert.deepEqual(kept, [10, 11, 12])
})

test('间距裁剪：本地形态要给服务端标注让位', () => {
  const patterns: Array<KLinePatternItem | null> = new Array(30).fill(null)
  patterns[9] = pat('doji')          // 本地，距后端标注 2 根
  patterns[11] = pat('key_k', true)  // 后端
  const kept = selectPatternIndicesToDraw(patterns, 0, 29, 10)
  assert.deepEqual(kept, [11], '本地形态应被挤掉，后端标注留下')
})

test('间距裁剪：只在可见区间内挑选', () => {
  const patterns: Array<KLinePatternItem | null> = new Array(100).fill(null)
  patterns[3] = pat('doji')
  patterns[50] = pat('doji')
  patterns[90] = pat('doji')
  const kept = selectPatternIndicesToDraw(patterns, 40, 95, 10)
  assert.deepEqual(kept, [50, 90], '区间外的下标不应出现')
})

test('间距裁剪：返回下标升序，便于稳定绘制', () => {
  const patterns: Array<KLinePatternItem | null> = new Array(60).fill(null)
  patterns[50] = pat('doji')
  patterns[5] = pat('doji')
  patterns[30] = pat('key_k', true)
  const kept = selectPatternIndicesToDraw(patterns, 0, 59, 10)
  assert.deepEqual(kept, [...kept].sort((a, b) => a - b))
})

test('间距裁剪：空输入与零间距', () => {
  assert.deepEqual(selectPatternIndicesToDraw([], 0, 0, 10), [])
  const patterns: Array<KLinePatternItem | null> = new Array(5).fill(null)
  patterns[1] = pat('doji')
  patterns[2] = pat('doji')
  // 间距 0 表示不去重，两个都留下
  assert.deepEqual(selectPatternIndicesToDraw(patterns, 0, 4, 0), [1, 2])
})
