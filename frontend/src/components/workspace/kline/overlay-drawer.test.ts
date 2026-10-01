import assert from 'node:assert/strict'
import test from 'node:test'

import { detectKLinePatterns, selectPatternIndicesToDraw, drawPatternGeometry, type KLinePatternItem } from './overlay-drawer.ts'
import type { DrawablePattern } from './chart-patterns.ts'
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

// ---------------------------------------------------------------------------
// 形态轮廓绘制
//
// 这层只做**像素映射**（下标/价格 -> 屏幕坐标），换算已经在 chart-patterns.ts
// 里验过了。这里守的是：参考线必须是虚线、顶点必须打点、换算不出坐标的点不能
// 被画到 (0,0) 这种假位置上。
// ---------------------------------------------------------------------------

interface Recorded {
  moveTo: Array<[number, number]>
  lineTo: Array<[number, number]>
  arcs: Array<[number, number]>
  texts: string[]
  dashes: number[][]
  strokes: number
  fills: number
  /** 每次 stroke 时的线宽，用来确认光晕层比主线宽。 */
  widths: number[]
  /** 圆角矩形的弧角调用次数（胶囊徽章会用它描底色）。 */
  arcTos: number
}

function recordingCtx(): { ctx: CanvasRenderingContext2D; rec: Recorded } {
  const rec: Recorded = {
    moveTo: [], lineTo: [], arcs: [], texts: [], dashes: [],
    strokes: 0, fills: 0, widths: [], arcTos: 0,
  }
  const ctx = {
    save() {}, restore() {},
    beginPath() {},
    stroke(this: { lineWidth: number }) { rec.strokes++; rec.widths.push(this.lineWidth) },
    fill() { rec.fills++ },
    arcTo() { rec.arcTos++ },
    measureText(t: string) { return { width: String(t).length * 6 } },
    // 画布 API 的其余部分：测试只关心上面记录的那几类调用，
    // 其余按 no-op 补全，免得实现换个画法就报 "not a function"。
    closePath() {}, rect() {}, ellipse() {}, clip() {},
    translate() {}, rotate() {}, scale() {}, setTransform() {}, resetTransform() {},
    quadraticCurveTo() {}, bezierCurveTo() {},
    createLinearGradient() { return { addColorStop() {} } },
    createRadialGradient() { return { addColorStop() {} } },
    moveTo(x: number, y: number) { rec.moveTo.push([x, y]) },
    lineTo(x: number, y: number) { rec.lineTo.push([x, y]) },
    arc(x: number, y: number) { rec.arcs.push([x, y]) },
    fillText(t: string) { rec.texts.push(t) },
    setLineDash(d: number[]) { rec.dashes.push(d) },
    strokeStyle: '', fillStyle: '', globalAlpha: 1, lineWidth: 1, font: '', textAlign: '',
  } as unknown as CanvasRenderingContext2D
  return { ctx, rec }
}

/** 轴换算：下标 -> 10*i，价格 -> 1000 - price（够用即可，只验映射发生）。 */
const fakeAxes = {
  xAxis: { convertToPixel: (i: number) => i * 10 },
  yAxis: { convertToPixel: (p: number) => 1000 - p },
}

function mkPattern(over: Partial<DrawablePattern> = {}): DrawablePattern {
  return {
    name: '头肩顶', direction: 'bearish', kind: 'geometry', confidence: 0.7, desc: '',
    points: [
      { index: 1, price: 12, label: '左肩' },
      { index: 3, price: 16, label: '头' },
      { index: 5, price: 12, label: '右肩' },
    ],
    lines: [{ label: '颈线', points: [{ index: 1, price: 10, label: '' }, { index: 5, price: 10, label: '' }] }],
    ...over,
  }
}

test('形态轮廓：顶点连成折线、打点并标字', () => {
  const { ctx, rec } = recordingCtx()
  drawPatternGeometry(ctx, [mkPattern()], fakeAxes.xAxis, fakeAxes.yAxis)
  // 折线：3 个点 -> moveTo(首点) + 2 次 lineTo。
  // 不能用 deepEqual 断言整个数组——颈线同样会调 moveTo/lineTo。
  assert.ok(rec.moveTo.some(([x, y]) => x === 10 && y === 988), `折线首点应为 moveTo: ${JSON.stringify(rec.moveTo)}`)
  assert.ok(rec.lineTo.some(([x, y]) => x === 30 && y === 984), '第二个顶点应连到 x=30')
  assert.ok(rec.lineTo.some(([x, y]) => x === 50 && y === 988), '第三个顶点应连到 x=50')
  // 每个顶点两笔：深色外环 + 彩色实心点（压在 K 线上才分得清）
  assert.equal(rec.arcs.length, 6, '3 个顶点 x (外环 + 实心)')
  assert.deepEqual(rec.arcs[0], [10, 988])
  // 顶点标签，最后再补一个形态名徽章
  assert.deepEqual(rec.texts, ['左肩', '头', '右肩', '头肩顶'])
})

test('参考线画成虚线，不是实线', () => {
  const { ctx, rec } = recordingCtx()
  drawPatternGeometry(ctx, [mkPattern()], fakeAxes.xAxis, fakeAxes.yAxis)
  assert.ok(rec.dashes.some((d) => d.length > 0), '颈线必须以虚线画出')
  // 颈线两端：index 1 -> x=10，index 5 -> x=50，价格 10 -> y=990
  assert.ok(rec.moveTo.some(([x, y]) => x === 10 && y === 990), `缺颈线左端: ${JSON.stringify(rec.moveTo)}`)
  assert.ok(rec.lineTo.some(([x, y]) => x === 50 && y === 990), '缺颈线右端')
})

test('换算不出坐标的点被跳过，不会画到 (0,0)', () => {
  const { ctx, rec } = recordingCtx()
  const badAxes = {
    xAxis: { convertToPixel: (i: number) => (i === 3 ? Number.NaN : i * 10) },
    yAxis: { convertToPixel: (p: number) => 1000 - p },
  }
  drawPatternGeometry(ctx, [mkPattern()], badAxes.xAxis, badAxes.yAxis)
  // 中间那个顶点被跳过，只剩两个可画的点 -> 各画两笔
  assert.equal(rec.arcs.length, 4, 'NaN 坐标的顶点不应打点')
  assert.ok(rec.arcs.every(([x]) => x !== 0), '不能兜底到 0')
  assert.deepEqual(rec.texts, ['左肩', '右肩', '头肩顶'], '被跳过的顶点不画标签')
})

test('空输入不画任何东西', () => {
  const { ctx, rec } = recordingCtx()
  drawPatternGeometry(ctx, [], fakeAxes.xAxis, fakeAxes.yAxis)
  assert.equal(rec.strokes + rec.fills, 0)
  assert.deepEqual(rec.arcs, [])
})

test('只有参考线、没有顶点的形态也能画', () => {
  const { ctx, rec } = recordingCtx()
  drawPatternGeometry(ctx, [mkPattern({ points: [] })], fakeAxes.xAxis, fakeAxes.yAxis)
  assert.ok(rec.dashes.length > 0, '颈线仍然要画')
  assert.deepEqual(rec.arcs, [], '没有顶点就不打点')
})

test('形态线带光晕：同一条线画两遍，底下一遍更宽', () => {
  const { ctx, rec } = recordingCtx()
  drawPatternGeometry(ctx, [mkPattern()], fakeAxes.xAxis, fakeAxes.yAxis)
  // 只画 1px 主线的版本在密集 K 线里会淹掉，必须有一层更宽的半透明底。
  // 记录里同一线段应出现两次：先宽后窄。
  const neck = rec.moveTo.filter(([x]) => x === 10)
  assert.ok(neck.length >= 2, `颈线应画两遍（光晕 + 主线），实际 ${neck.length} 遍`)
  assert.ok(rec.widths.some((w) => w > 3), `应有比主线更宽的描边，宽度记录: ${rec.widths}`)
})

test('顶点标签用胶囊徽章，不是裸文字', () => {
  const { ctx, rec } = recordingCtx()
  drawPatternGeometry(ctx, [mkPattern()], fakeAxes.xAxis, fakeAxes.yAxis)
  // 胶囊徽章会先描一个圆角矩形再填色；裸 fillText 在 K 线上读不出来。
  assert.ok(rec.arcTos > 0, '标签必须走胶囊徽章（深色底 + 描边）')
})
