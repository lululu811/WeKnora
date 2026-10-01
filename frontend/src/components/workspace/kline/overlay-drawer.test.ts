import assert from 'node:assert/strict'
import test from 'node:test'

import { detectKLinePatterns, selectPatternIndicesToDraw, drawPatternGeometry, reserveLabel, type KLinePatternItem, type PlacedLabel } from './overlay-drawer.ts'
import type { DrawablePattern } from './chart-patterns.ts'
import type { Annotation } from './annotate-api.ts'
import type { KLineData } from './types.ts'

const DAY = 86400000

/**
 * 一串规整的日线：全红、实体占比高、逐根抬高。
 *
 * 这个文件守的是**摆放**（谁占位、谁让位），不关心形态怎么识别 —— 识别要么来自
 * 后端标注，要么来自后端的蜡烛形态序列，两者都由测试显式喂进来。
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
// 起因：形态信号对 2440 根产出 600+ 个，100 多根的可见区间里就有 36 个
// 胶囊叠在一起，K 线被完全盖住。实测加 10 根间距后降到 9 个。
// ---------------------------------------------------------------------------

function pat(type: string, fromBackend = false): KLinePatternItem {
  return { type, text: type, color: '#fff', bgColor: 'rgba(0,0,0,.4)', position: 'top', fromBackend }
}

test('间距裁剪：相邻的非服务端形态只保留第一个', () => {
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

test('间距裁剪：非服务端形态要给服务端标注让位', () => {
  const patterns: Array<KLinePatternItem | null> = new Array(30).fill(null)
  patterns[9] = pat('doji')          // 本地，距后端标注 2 根
  patterns[11] = pat('key_k', true)  // 后端
  const kept = selectPatternIndicesToDraw(patterns, 0, 29, 10)
  assert.deepEqual(kept, [11], '非服务端形态应被挤掉，后端标注留下')
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
  /** 每次 stroke 时的 globalAlpha（含 halo 的固有值，不能直接用来判压暗）。 */
  alphas: number[]
  /** 每次 fill 时的 globalAlpha：顶点圆点与徽章底，常态为 1，压暗时 < 1。 */
  fillAlphas: number[]
}

function recordingCtx(): { ctx: CanvasRenderingContext2D; rec: Recorded } {
  const rec: Recorded = {
    moveTo: [], lineTo: [], arcs: [], texts: [], dashes: [],
    strokes: 0, fills: 0, widths: [], arcTos: 0, alphas: [], fillAlphas: [],
  }
  // save/restore 必须真的实现（只存 globalAlpha 就够）：它们在产品代码里被用来
  // 把"压暗"限制在一段绘制内。假 ctx 把这两个做成 no-op 的话，alpha 会从
  // strokePatternLine 泄漏到后面所有 fill，测试就会因为替身不忠实而误报。
  const alphaStack: number[] = []
  const ctx = {
    save(this: { globalAlpha: number }) { alphaStack.push(this.globalAlpha) },
    restore(this: { globalAlpha: number }) { this.globalAlpha = alphaStack.pop() ?? 1 },
    beginPath() {},
    stroke(this: { lineWidth: number; globalAlpha: number }) { rec.strokes++; rec.widths.push(this.lineWidth); rec.alphas.push(this.globalAlpha) },
    fill(this: { globalAlpha: number }) { rec.fills++; rec.fillAlphas.push(this.globalAlpha) },
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

// ---------------------------------------------------------------------------
// 光标联动（压暗）与两层共享的标签占位
// ---------------------------------------------------------------------------

test('非当前形态被压暗，当前形态保持常态', () => {
  const { ctx, rec } = recordingCtx()
  const a = mkPattern({ name: '双顶' })
  const b = mkPattern({ name: '熊市旗形' })
  drawPatternGeometry(ctx, [a, b], fakeAxes.xAxis, fakeAxes.yAxis, ['双顶'])
  assert.ok(rec.fillAlphas.some((x) => x === 1), '命中的那个应保持不透明')
  assert.ok(rec.fillAlphas.some((x) => x > 0 && x < 0.5),
    `未命中的应被压暗，实际: ${[...new Set(rec.fillAlphas)]}`)
  // 线也必须跟着压暗。这里单独守一道：strokePatternLine 曾经是**覆盖**
  // globalAlpha 而不是相乘，于是压暗对参考线完全无效、只有徽章在变淡。
  assert.ok(rec.alphas.some((x) => x > 0 && x < 0.15),
    `参考线也应被压暗，实际 stroke alpha: ${[...new Set(rec.alphas)]}`)
})

test('activeNames 为空时全都不压暗（没悬停/悬停处没形态）', () => {
  const { ctx, rec } = recordingCtx()
  drawPatternGeometry(ctx, [mkPattern({ name: '双顶' })], fakeAxes.xAxis, fakeAxes.yAxis, [])
  assert.ok(rec.fillAlphas.every((x) => x === 1),
    `空集合 = 常态，不该有任何压暗，实际: ${[...new Set(rec.fillAlphas)]}`)
})

test('压暗是按名字匹配，不是按下标', () => {
  const { ctx, rec } = recordingCtx()
  // 两个同名形态：命中名字时两个都亮（名字是用户勾选的单位）
  drawPatternGeometry(ctx, [mkPattern({ name: 'X' }), mkPattern({ name: 'X' })],
    fakeAxes.xAxis, fakeAxes.yAxis, ['X'])
  assert.ok(rec.fillAlphas.every((x) => x === 1), '同名都命中，不该有压暗')
})

test('气泡先占位时不平移（它锚定在某根 K 线上）', () => {
  const { ctx } = recordingCtx()
  const placed: PlacedLabel[] = []
  const y = reserveLabel(ctx, '十字星', 100, 200, 10, placed)
  assert.equal(y, 200, '占位不应改变 y —— 挪走了气泡就指向了别的 K 线')
  assert.equal(placed.length, 1)
  assert.ok(placed[0].w > 0 && placed[0].h > 0)
})

test('传给形态的共享占位表里有气泡时，形态名会让位', () => {
  const { ctx, rec } = recordingCtx()
  const placed: PlacedLabel[] = []
  // 先在形态名会出现的位置放一个气泡
  const patX = 10 + 34;   // mkPattern 最左顶点 x=10，名字锚在 +34
  reserveLabel(ctx, '占位气泡', patX, 988 - 14, 10, placed)
  const before = placed.length
  drawPatternGeometry(ctx, [mkPattern()], fakeAxes.xAxis, fakeAxes.yAxis, [], placed)
  assert.ok(placed.length > before, '形态名应登记进同一张表')
  // 形态名的 rect 不能和气泡的 rect 完全重合（说明避让发生了）
  const bubble = placed[0]
  const name = placed[placed.length - 1]
  const overlap = !(name.x + name.w < bubble.x || bubble.x + bubble.w < name.x ||
                    name.y + name.h < bubble.y || bubble.y + bubble.h < name.y)
  assert.equal(overlap, false, `形态名应避开气泡：bubble=${JSON.stringify(bubble)} name=${JSON.stringify(name)}`)
})

test('形态名之间的避让仍然生效（同一张表内）', () => {
  const { ctx } = recordingCtx()
  const placed: PlacedLabel[] = []
  drawPatternGeometry(ctx, [mkPattern({ name: 'A' }), mkPattern({ name: 'B' })],
    fakeAxes.xAxis, fakeAxes.yAxis, [], placed)
  // 每个形态先登记 3 个顶点标签再登记形态名 -> 名字在第 4 个和第 8 个
  assert.equal(placed.length, 8, `期望 4 个标签 x 2 个形态，实际 ${placed.length}`)
  const a = placed[3]
  const b = placed[7]
  const overlap = !(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y)
  assert.equal(overlap, false,
    `两个同位置形态的名字应错开：A=${JSON.stringify(a)} B=${JSON.stringify(b)}`)
})
