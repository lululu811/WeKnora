/**
 * coreChart — 直接持 `klinecharts` 核心 `Chart` 实例，替代 `@klinecharts/pro`。
 *
 * 为什么弃用 Pro：Pro 把核心 `Chart` 封在闭包里，只对外暴露 12 个
 * theme/style/symbol/period 的 getter+setter，`createOverlay` / `scrollToTimestamp`
 * 一概拿不到（实测：`_chartApi` 对象的 own property 恰好就是那 12 个方法）。
 * 而「句子 ↔ 时间区间高亮」「水平位标注」「切票时不销毁重建」这三件事全部
 * 依赖那几个 API。同时丢掉的还有 Pro 内部的画线工具侧栏（约 35 个工具），
 * 这是本次替换的已知代价。
 *
 * 换来的能力：
 *  - `createOverlay` 画全宽水平位 / 区间带
 *  - `scrollToTimestamp` 把图滚到正文提到的日期
 *  - 切票/切周期走 `applyNewData` 就地换数据，保住用户的缩放、十字光标和已画标注
 *
 * 数据获取沿用既有的 `ZettarancDatafeed`（同一个 `/api/kline` 请求与错误语义），
 * 只是从「Pro 回调 datafeed」改成「我们自己取数后喂 `applyNewData`」——核心
 * `Chart` 本身不认识 datafeed，它只认 `loadData` 回调。
 */

import {
  init,
  dispose,
  registerOverlay,
  type Chart,
  type DeepPartial,
  type KLineData as CoreKLineData,
  type OverlayCreateFiguresCallbackParams,
  type Coordinate,
  type OverlayFigure,
  type Styles,
} from 'klinecharts'
import type { Period, SymbolInfo, KLineData } from './types'
import { ZettarancDatafeed } from './datafeed'

/** 区间带：横跨整个绘图区的半透明色块 + 上下两条虚线。 */
const RANGE_BAND = 'wkRangeBand'
/** 水平位：一条全宽虚线 + 右侧价格标签。 */
const PRICE_LEVEL = 'wkPriceLevel'
/** hover 聚焦：框外压暗 + 框内叠统计文字。 */
const ANCHOR_FOCUS = 'wkAnchorFocus'

/** 图表侧统一使用的 overlay 分组名。切标的时按组整体清掉。 */
const GROUP_RANGE = 'wk_range'
const GROUP_LEVEL = 'wk_levels'
const GROUP_FOCUS = 'wk_focus'

/** 这两个模板在模块加载时注册一次；重复注册同名模板会覆盖，无副作用。 */
let templatesRegistered = false

function ensureTemplates(): void {
  if (templatesRegistered) return
  templatesRegistered = true

  // 区间带：坐标 y 由两个 value 决定，x 铺满 bounding.width。
  registerOverlay({
    name: RANGE_BAND,
    totalStep: 3,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    lock: true,
    createPointFigures: ({ coordinates, overlay }: OverlayCreateFiguresCallbackParams) => {
      const x1 = coordinates[0]?.x ?? 0
      const x2 = coordinates[1]?.x ?? x1
      const y1 = coordinates[0]?.y ?? 0
      const y2 = coordinates[1]?.y ?? y1
      // 左右边界由两个点的 dataIndex 决定，**不是铺满整个画布**。
      //
      // 第一版画的是满宽横带（x:0..bounding.width），只在文字上写了日期——
      // 结果是 hover「2026-02-25 ~ 2026-05-26」时，图上出现一条横贯全时间轴的
      // 价格带，位置信息完全是错的。区间带的语义就是「这一段」，x 必须跟着日期走。
      const left = Math.min(x1, x2)
      const width = Math.abs(x2 - x1)
      const top = Math.min(y1, y2)
      const height = Math.abs(y2 - y1)
      // 区间文字由调用方通过 extendData 传入。画在带子上沿，让「正文里的那句话」
      // 和「图上这一段」看到的是同一串日期——联动的可读性主要靠这个。
      const extra = overlay.extendData as { label?: string; badge?: number } | undefined
      const label = extra?.label
      const badge = extra?.badge
      const figures: OverlayFigure[] = [
        {
          type: 'rect',
          attrs: { x: left, y: top, width, height },
          styles: { style: 'fill', color: 'rgba(201, 146, 8, 0.14)' },
        },
        // 用一圈细实线边框代替原来的上下两条虚线：两条横线叠在价格线上会
        // 进一步加剧"横线太多"的观感，边框只勾出范围、不额外制造横线。
        {
          type: 'rect',
          ignoreEvent: true,
          attrs: { x: left, y: top, width, height },
          styles: { style: 'stroke', color: 'rgba(201, 146, 8, 0.5)', size: 1 },
        },
      ]
      // 编号徽章画在框内左上角。它是「正文里那个 ①」和「图上这块」的对应载体，
      // 比任何 tooltip 都直接：两处印同一个符号，眼睛自己就接上了。
      if (badge !== undefined) {
        const bx = left + 12
        const by = top + 12
        figures.push({
          type: 'circle',
          ignoreEvent: true,
          attrs: { x: bx, y: by, r: 8 },
          styles: { style: 'fill', color: 'rgba(201, 146, 8, 0.9)' },
        })
        figures.push({
          type: 'text',
          ignoreEvent: true,
          attrs: { x: bx, y: by, text: String(badge) },
          styles: {
            style: 'fill',
            color: '#1a1408',
            size: 11,
            align: 'center',
            baseline: 'middle',
            textAlign: 'center',
          },
        })
      }
      if (label) {
        figures.push({
          type: 'text',
          ignoreEvent: true,
          attrs: { x: left + (badge !== undefined ? 26 : 6), y: top + (badge !== undefined ? 17 : -4), text: label },
          styles: {
            style: 'fill',
            color: 'rgba(246, 217, 138, 0.95)',
            size: 11,
            baseline: badge !== undefined ? 'middle' : 'bottom',
            textAlign: 'left',
          },
        })
      }
      return figures
    },
  })

  // 聚焦：框外四块半透明遮罩 + 框内一行统计文字。
  //
  // 遮罩只压到 58% 亮度（rgba 0.42），不压死：压太狠会让人以为图坏了，
  // 而这里要的是「把注意力拉过去」，不是「把其它内容藏起来」。
  // 而且它只在 hover 期间存在——常驻压暗会把整张图长期灰掉一半，最毁观感。
  registerOverlay({
    name: ANCHOR_FOCUS,
    totalStep: 3,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    // 聚焦层是纯装饰，绝不能吃掉底下那个框的 hover 事件——它的每个 figure
    // 都标了 ignoreEvent，锁不锁都不影响它自己不需要事件这件事。
    lock: true,
    createPointFigures: ({ coordinates, bounding, overlay }: OverlayCreateFiguresCallbackParams) => {
      const x1 = coordinates[0]?.x ?? 0
      const x2 = coordinates[1]?.x ?? x1
      const y1 = coordinates[0]?.y ?? 0
      const y2 = coordinates[1]?.y ?? y1
      const left = Math.min(x1, x2)
      const right = Math.max(x1, x2)
      const top = Math.min(y1, y2)
      const bottom = Math.max(y1, y2)
      const W = bounding.width
      const H = bounding.height
      const DIM = 'rgba(0, 0, 0, 0.42)'
      const boxW = Math.max(0, right - left)
      const figures: OverlayFigure[] = [
        { type: 'rect', ignoreEvent: true, attrs: { x: 0, y: 0, width: Math.max(0, left), height: H }, styles: { style: 'fill', color: DIM } },
        { type: 'rect', ignoreEvent: true, attrs: { x: right, y: 0, width: Math.max(0, W - right), height: H }, styles: { style: 'fill', color: DIM } },
        { type: 'rect', ignoreEvent: true, attrs: { x: left, y: 0, width: boxW, height: Math.max(0, top) }, styles: { style: 'fill', color: DIM } },
        { type: 'rect', ignoreEvent: true, attrs: { x: left, y: bottom, width: boxW, height: Math.max(0, H - bottom) }, styles: { style: 'fill', color: DIM } },
        // 框边描一圈，让"亮"的那块边界清楚
        { type: 'rect', ignoreEvent: true, attrs: { x: left, y: top, width: boxW, height: Math.max(0, bottom - top) }, styles: { style: 'stroke', color: 'rgba(246, 217, 138, 0.85)', size: 1 } },
      ]
      const text = (overlay.extendData as { text?: string } | undefined)?.text
      if (text) {
        figures.push({
          type: 'text',
          ignoreEvent: true,
          attrs: { x: left + 8, y: top + 34, text },
          styles: {
            style: 'fill',
            color: '#f6d98a',
            size: 11,
            baseline: 'middle',
            textAlign: 'left',
          },
        })
      }
      return figures
    },
  })

  // 水平位：全宽虚线，右侧一个价格文字。文字位置跟着 bounding 走，
  // 不用库的默认 lockText —— 那个会把标签甩到绘图区最右边、与线脱节。
  registerOverlay({
    name: PRICE_LEVEL,
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    lock: true,
    createPointFigures: ({ coordinates, bounding, precision, overlay }: OverlayCreateFiguresCallbackParams) => {
      const y = coordinates[0]?.y ?? 0
      // Coordinate 只有 x/y，拿不到价格；价格要从 overlay 自己的 points 读。
      const value = overlay.points[0]?.value
      const text = typeof value === 'number' ? value.toFixed(precision?.price ?? 2) : ''
      const badge = (overlay.extendData as { badge?: number } | undefined)?.badge
      return [
        {
          type: 'line',
          ignoreEvent: true,
          attrs: {
            coordinates: [
              { x: 0, y },
              { x: bounding.width, y },
            ],
          },
          styles: { style: 'dashed', size: 1, color: 'rgba(201, 146, 8, 0.9)', dashedValue: [6, 4] },
        },
        {
          type: 'text',
          ignoreEvent: true,
          attrs: { x: bounding.width - 4, y: y - 4, text },
          styles: {
            style: 'fill',
            color: 'rgba(201, 146, 8, 0.95)',
            size: 11,
            align: 'right',
            baseline: 'bottom',
            textAlign: 'right',
          },
        },
        // 编号徽章紧挨着价格文字，让「② 72.40」读成一个整体。
        //
        // 第一版把它贴在线的左端，结果几个价位接近时徽章在左边缘竖直叠成一串，
        // 既分不清谁属于哪条线，又贴着画布边容易被裁。放到价格旁边之后，
        // 徽章天然跟着自己的线走，也不会和别的线的徽章撞在一起。
        ...(badge !== undefined
          ? [
              {
                type: 'circle',
                ignoreEvent: true,
                attrs: { x: bounding.width - 58, y: y - 4, r: 8 },
                styles: { style: 'fill', color: 'rgba(201, 146, 8, 0.9)' },
              },
              {
                type: 'text',
                ignoreEvent: true,
                attrs: { x: bounding.width - 58, y: y - 4, text: String(badge) },
                styles: {
                  style: 'fill',
                  color: '#1a1408',
                  size: 11,
                  align: 'center',
                  baseline: 'middle',
                  textAlign: 'center',
                },
              },
            ]
          : []),
      ]
    },
  })
}

/** 记录当前挂在图上的主/副图指标名，供 swapIndicators 做差集。 */
const installedIndicators = new WeakMap<Chart, { main: string[]; sub: string[] }>()

export interface CoreChartHandle {
  chart: Chart;
}

export interface LoadOutcome {
  dataList: KLineData[];
}

/**
 * 在给定容器上创建一个核心图表实例，并返回操作句柄。
 *
 * 取数由 `ZettarancDatafeed` 完成（保留它对 404 / 非 JSON / code!=0 的三态区分），
 * 拿到数据后交给 `applyNewData`。`loadData` 回调只在「向前加载更多」时触发，
 * 本项目是纯复盘模式，5000 根一次取完，因此该分支直接回调空数组终止。
 */
export function createCoreChart(options: {
  container: HTMLElement
  symbol: SymbolInfo
  period: Period
  adjust: ZettarancDatafeed['adjust'] extends infer _ ? 'none' | 'forward' | 'backward' : never
  styles: DeepPartial<Styles>
  mainIndicators: string[]
  subIndicators: string[]
  onDataLoaded?: (data: KLineData[]) => void
  onNoData?: () => void
  onError?: (message: string) => void
}): { chart: Chart; datafeed: ZettarancDatafeed } {
  ensureTemplates()

  const datafeed = new ZettarancDatafeed({
    adjust: options.adjust,
    onDataLoaded: (data) => options.onDataLoaded?.(data),
    onNoData: () => options.onNoData?.(),
    onError: (_symbol, message) => options.onError?.(message),
  })

  const chart = init(options.container, {
    locale: 'zh-CN',
    timezone: 'Asia/Shanghai',
    styles: options.styles,
    customApi: {
      // 与 Pro 的 customApi.formatDate 保持一致：X 轴按粒度显示，
      // 不用默认的完整时间戳（否则周K/月K 的 X 轴会精确到分钟）。
      // 签名是 (dateTimeFormat, timestamp, format, type)，type 见 FormatDateType。
      formatDate: (_dateTimeFormat: Intl.DateTimeFormat, timestamp: number, _format: string, type: number) => {
        const d = new Date(timestamp)
        const y = d.getUTCFullYear()
        const m = String(d.getUTCMonth() + 1).padStart(2, '0')
        const day = String(d.getUTCDate()).padStart(2, '0')
        return type === 2 /* XAxis */ ? `${y}-${m}-${day}` : `${y}-${m}-${day}`
      },
    },
  })

  if (!chart) {
    throw new Error('K 线图表初始化失败：容器不可用')
  }

  chart.createIndicator(options.mainIndicators[0] ?? 'MA', false, { id: 'candle_pane' })
  for (const name of options.mainIndicators.slice(1)) {
    chart.createIndicator(name, false, { id: 'candle_pane' })
  }
  for (const name of options.subIndicators) {
    chart.createIndicator(name, true)
  }
  // 记下初始配置，swapIndicators 才能算出差集。
  installedIndicators.set(chart, {
    main: [...options.mainIndicators],
    sub: [...options.subIndicators],
  })

  void loadInto(chart, datafeed, options.symbol, options.period)

  return { chart, datafeed }
}

async function loadInto(
  chart: Chart,
  datafeed: ZettarancDatafeed,
  symbol: SymbolInfo,
  period: Period,
): Promise<LoadOutcome> {
  const dataList = await datafeed.getHistoryKLineData(symbol, period, 0, Date.now())
  // datafeed 产出的是本仓库的 KLineData（多了 turnover 等字段），
  // 核心库只要求 timestamp/OHLCV，结构上兼容；这里做一次边界 cast。
  chart.applyNewData(dataList)
  return { dataList }
}

/**
 * 换标的：就地替换数据，不重建实例。保住缩放级别、十字光标位置与已画标注。
 */
export async function swapSymbol(
  chart: Chart,
  datafeed: ZettarancDatafeed,
  symbol: SymbolInfo,
  period: Period,
): Promise<void> {
  // 换标的先清掉上一只票的标注，否则水平位/区间带会挂在错误的价格上。
  clearAllOverlays(chart)
  await loadInto(chart, datafeed, symbol, period)
}

/** 销毁实例。 */
export function destroyChart(chart: Chart | null, container: HTMLElement | null): void {
  if (chart) {
    try {
      dispose(chart)
    } catch {
      /* 容器已被 Vue 移除时忽略 */
    }
  }
  if (container) container.innerHTML = ''
}

/** 清掉本模块管理的全部标注（切标的时用）。 */
export function clearAllOverlays(chart: Chart | null): void {
  if (!chart) return
  chart.removeOverlay({ groupId: GROUP_RANGE })
  chart.removeOverlay({ groupId: GROUP_LEVEL })
  // 聚焦层也要清：换标的时上一次 hover 的压暗必须跟着走，
  // 否则新标的图上会残留一块属于旧标的的暗区。
  chart.removeOverlay({ groupId: GROUP_FOCUS })
}

/**
 * 画一个价格水平位。同一 group 下可并存多条，各自按 id 覆盖。
 * `value` 必须是**图上真实存在过的价格**，否则线会画在 y 轴范围之外看不见。
 */
export function drawPriceLevel(
  chart: Chart | null,
  id: string,
  value: number,
  /** 编号徽章（正文里的锚点编号）。不传则只画线。 */
  badge?: number,
): void {
  if (!chart || !Number.isFinite(value) || value <= 0) return
  chart.createOverlay({
    name: PRICE_LEVEL,
    id,
    groupId: GROUP_LEVEL,
    lock: true,
    points: [{ value }],
    extendData: { badge },
  })
}

/**
 * 把锚点换算成画布像素上的命中区域，供 crosshair 命中判定使用。
 *
 * 用 `convertToPixel` 而不是自己按 barSpace 推算：可见区间、缩放、右移偏移
 * 全在库内部，自己算必然漂移。
 */
export function anchorCanvasBoxes(
  chart: Chart | null,
  anchors: readonly { index: number; kind: 'range' | 'level'; startIndex?: number; endIndex?: number; low?: number; high?: number; value?: number }[],
): Array<{ index: number; kind: 'range' | 'level'; startIndex?: number; endIndex?: number; top?: number; bottom?: number; levelY?: number }> {
  if (!chart) return []
  const out: ReturnType<typeof anchorCanvasBoxes> = []
  for (const a of anchors) {
    if (a.kind === 'range') {
      if (a.startIndex === undefined || a.endIndex === undefined || a.low === undefined || a.high === undefined) continue
      try {
        // convertToPixel 的返回类型是重载的联合，传数组时实际返回数组。
        const pts = chart.convertToPixel(
          [
            { dataIndex: a.startIndex, value: a.low },
            { dataIndex: a.endIndex, value: a.high },
          ],
          { paneId: 'candle_pane' },
        ) as Array<Partial<Coordinate>>
        if (pts.length < 2) continue
        out.push({ index: a.index, kind: 'range', startIndex: a.startIndex, endIndex: a.endIndex, top: pts[0].y, bottom: pts[1].y })
      } catch {
        continue
      }
      continue
    }
    if (a.value === undefined) continue
    try {
      const pts = chart.convertToPixel([{ dataIndex: 0, value: a.value }], {
        paneId: 'candle_pane',
      }) as Array<Partial<Coordinate>>
      if (pts.length === 0) continue
      out.push({ index: a.index, kind: 'level', levelY: pts[0].y })
    } catch {
      continue
    }
  }
  return out
}

/**
 * 画 hover 聚焦层：框外压暗 + 框内统计文字。
 *
 * 用固定的 id，重复调用即替换——hover 会在多个锚点之间快速切换，
 * 每次先清再建会让画面闪一下。
 */
export function drawAnchorFocus(
  chart: Chart | null,
  startIndex: number,
  endIndex: number,
  low: number,
  high: number,
  text: string,
): void {
  if (!chart) return
  if (!Number.isFinite(low) || !Number.isFinite(high) || low === high) return
  if (startIndex === endIndex) return
  chart.createOverlay({
    name: ANCHOR_FOCUS,
    id: 'anchor_focus',
    groupId: GROUP_FOCUS,
    lock: true,
    points: [
      { dataIndex: startIndex, value: low },
      { dataIndex: endIndex, value: high },
    ],
    extendData: { text },
  })
}

/** 撤掉聚焦层。hover 离开时调用——压暗只在 hover 期间存在。 */
export function clearAnchorFocus(chart: Chart | null): void {
  if (!chart) return
  chart.removeOverlay({ groupId: GROUP_FOCUS })
}

/**
 * 时间戳 -> 最接近的 K 线下标。找不到（数据为空）时返回 null。
 *
 * 导出给调用方：日期换算成下标这件事必须只做一次。绘制层再算一遍会出现
 * 「判断能不能画」和「实际画在哪」用了两套换算结果的分裂。
 */
export function indexOfTimestamp(chart: Chart | null, ts: number): number | null {
  if (!chart || !Number.isFinite(ts)) return null
  const dataList = chart.getDataList() || []
  if (dataList.length === 0) return null
  let best = 0
  for (let i = 1; i < dataList.length; i++) {
    if (Math.abs(dataList[i].timestamp - ts) < Math.abs(dataList[best].timestamp - ts)) best = i
  }
  return best
}

/**
 * 画一个「时间段 × 价格区间」的矩形带。
 *
 * 收**下标**而不是时间戳：换算由调用方做一次（见 indexOfTimestamp / anchor-render），
 * 这里只负责画。
 *
 * `label` 画在框内上沿、`badge` 是左上角的编号圆徽章——两者都是为了让
 * 「正文里那句话」和「图上这一块」看到同一个符号。
 */
export function drawRangeBand(
  chart: Chart | null,
  id: string,
  startIndex: number,
  endIndex: number,
  low: number,
  high: number,
  label?: string,
  badge?: number,
): void {
  if (!chart) return
  if (!Number.isFinite(low) || !Number.isFinite(high) || low === high) return
  if (startIndex === endIndex) return

  chart.createOverlay({
    name: RANGE_BAND,
    id,
    groupId: GROUP_RANGE,
    lock: true,
    points: [
      { dataIndex: startIndex, value: low },
      { dataIndex: endIndex, value: high },
    ],
    extendData: { label, badge },
  })
}

/**
 * 把图滚动到最接近给定时间戳的那根 K 线。
 *
 * 越界与非交易日都安全（实测：库不抛错，只是滚到边界）。找不到任何可定位的
 * K 线时返回 false，让调用方降级为「只标两端文字」而不是画错位置。
 */
export function scrollToTimestamp(chart: Chart | null, timestamp: number): boolean {
  if (!chart || !Number.isFinite(timestamp)) return false
  const dataList = chart.getDataList()
  if (!dataList || dataList.length === 0) return false
  let nearest: CoreKLineData | null = null
  for (const bar of dataList) {
    if (!nearest || Math.abs(bar.timestamp - timestamp) < Math.abs(nearest.timestamp - timestamp)) {
      nearest = bar
    }
  }
  if (!nearest) return false
  chart.scrollToTimestamp(timestamp, 0)
  return true
}

/** 供测试与外部读取当前图上的数据（核心库原样类型）。 */
export function getChartData(chart: Chart | null): CoreKLineData[] {
  return chart ? chart.getDataList() || [] : []
}

/**
 * 就地换主图/副图指标，不重建图表。
 *
 * 重建会丢掉用户的缩放级别、十字光标与已画标注，切一次指标就清零一次代价太大。
 * 库提供了 createIndicator / removeIndicator，这里按「主图全量重建、副图按差集增删」
 * 来做：主图指标共享 candle_pane，重复 add 会出现两条同名线，所以先清空再按
 * 新配置加；副图各自独立 pane，只增删差集即可。
 */
export function swapIndicators(
  chart: Chart | null,
  mainIndicators: string[],
  subIndicators: string[],
): void {
  if (!chart) return
  const current = installedIndicators.get(chart) ?? { main: [], sub: [] }

  // 主图：全量重建
  for (const name of current.main) {
    chart.removeIndicator('candle_pane', name)
  }
  for (const name of mainIndicators) {
    chart.createIndicator(name, false, { id: 'candle_pane' })
  }

  // 副图：差集增删
  const nextSub = new Set(subIndicators)
  const prevSub = new Set(current.sub)
  for (const name of current.sub) {
    if (!nextSub.has(name)) chart.removeIndicator(name)
  }
  for (const name of subIndicators) {
    if (!prevSub.has(name)) chart.createIndicator(name, true)
  }

  installedIndicators.set(chart, { main: [...mainIndicators], sub: [...subIndicators] })
}
