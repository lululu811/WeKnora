/**
 * coreChart — 直接持 `klinecharts` 核心 `Chart` 实例，替代 `@klinecharts/pro`。
 *
 * 为什么弃用 Pro：Pro 把核心 `Chart` 封在闭包里，只对外暴露 12 个
 * theme/style/symbol/period 的 getter+setter，`createOverlay` 一概拿不到
 * （实测：`_chartApi` 对象的 own property 恰好就是那 12 个方法）。
 * 而「水平位标注」「切票时不销毁重建」这两件事全部
 * 依赖那几个 API。同时丢掉的还有 Pro 内部的画线工具侧栏（约 35 个工具），
 * 这是本次替换的已知代价。
 *
 * 换来的能力：
 *  - `createOverlay` 画全宽水平位
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
  type Styles,
} from 'klinecharts'
import type { Period, SymbolInfo, KLineData } from './types'
import { ZettarancDatafeed, type KLineErrorKind } from './datafeed'

/** 水平位：一条全宽虚线 + 右侧价格标签。 */
const PRICE_LEVEL = 'wkPriceLevel'

/** 预警位：醒目警戒色虚线 + 右侧预警标签。 */
export const ALERT_LEVEL = 'wkAlertLevel'

/** 持仓操盘位：成本线与止损线。 */
export const COST_LEVEL = 'wkCostLevel'
export const STOP_LEVEL = 'wkStopLevel'

/** 图表侧统一使用的 overlay 分组名。切标的时按组整体清掉。 */
const GROUP_LEVEL = 'wk_levels'
export const GROUP_ALERT = 'wk_alerts'
export const GROUP_TRADE_TARGETS = 'wk_trade_targets'

/** 模板在模块加载时注册一次；重复注册同名模板会覆盖，无副作用。 */
let templatesRegistered = false

export function ensureTemplates(): void {
  if (templatesRegistered) return
  templatesRegistered = true

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
      ]
    },
  })

  // 预警位：醒目警戒色虚线 + 右侧预警标签
  registerOverlay({
    name: ALERT_LEVEL,
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    lock: true,
    createPointFigures: ({ coordinates, bounding, precision, overlay }: OverlayCreateFiguresCallbackParams) => {
      const y = coordinates[0]?.y ?? 0
      const value = overlay.points[0]?.value
      const label = (overlay.extendData as string) || '🔔 预警'
      const valText = typeof value === 'number' ? value.toFixed(precision?.price ?? 2) : ''
      const text = `${label} ${valText}`.trim()
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
          styles: { style: 'dashed', size: 1.5, color: 'rgba(235, 94, 40, 0.95)', dashedValue: [5, 3] },
        },
        {
          type: 'text',
          ignoreEvent: true,
          attrs: { x: bounding.width - 4, y: y - 4, text },
          styles: {
            style: 'fill',
            color: 'rgba(235, 94, 40, 0.95)',
            size: 11,
            align: 'right',
            baseline: 'bottom',
            textAlign: 'right',
          },
        },
      ]
    },
  })

  // 持仓成本位：青绿色虚线 + 右侧成本及浮盈标签
  registerOverlay({
    name: COST_LEVEL,
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    lock: true,
    createPointFigures: ({ coordinates, bounding, precision, overlay }: OverlayCreateFiguresCallbackParams) => {
      const y = coordinates[0]?.y ?? 0
      const value = overlay.points[0]?.value
      const label = (overlay.extendData as string) || '📈 成本'
      const valText = typeof value === 'number' ? value.toFixed(precision?.price ?? 2) : ''
      const text = `${label} ${valText}`.trim()
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
          styles: { style: 'dashed', size: 1.5, color: 'rgba(13, 148, 136, 0.95)', dashedValue: [6, 3] },
        },
        {
          type: 'text',
          ignoreEvent: true,
          attrs: { x: bounding.width - 4, y: y - 4, text },
          styles: {
            style: 'fill',
            color: 'rgba(13, 148, 136, 0.95)',
            size: 11,
            align: 'right',
            baseline: 'bottom',
            textAlign: 'right',
          },
        },
      ]
    },
  })

  // 防守止损位：鲜红色虚线 + 右侧止损标签
  registerOverlay({
    name: STOP_LEVEL,
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    lock: true,
    createPointFigures: ({ coordinates, bounding, precision, overlay }: OverlayCreateFiguresCallbackParams) => {
      const y = coordinates[0]?.y ?? 0
      const value = overlay.points[0]?.value
      const label = (overlay.extendData as string) || '🛑 止损'
      const valText = typeof value === 'number' ? value.toFixed(precision?.price ?? 2) : ''
      const text = `${label} ${valText}`.trim()
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
          styles: { style: 'dashed', size: 1.5, color: 'rgba(220, 38, 38, 0.95)', dashedValue: [5, 3] },
        },
        {
          type: 'text',
          ignoreEvent: true,
          attrs: { x: bounding.width - 4, y: y - 4, text },
          styles: {
            style: 'fill',
            color: 'rgba(220, 38, 38, 0.95)',
            size: 11,
            align: 'right',
            baseline: 'bottom',
            textAlign: 'right',
          },
        },
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
  onError?: (message: string, kind: KLineErrorKind) => void
}): { chart: Chart; datafeed: ZettarancDatafeed } {
  ensureTemplates()

  const datafeed = new ZettarancDatafeed({
    adjust: options.adjust,
    onDataLoaded: (data) => options.onDataLoaded?.(data),
    onNoData: () => options.onNoData?.(),
    onError: (_symbol, message, kind) => options.onError?.(message, kind),
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
  // 换标的先清掉上一只票的标注，否则水平位会挂在错误的价格上。
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
  chart.removeOverlay({ groupId: GROUP_LEVEL })
}

/**
 * 画一个价格水平位。同一 group 下可并存多条，各自按 id 覆盖。
 * `value` 必须是**图上真实存在过的价格**，否则线会画在 y 轴范围之外看不见。
 */
export function drawPriceLevel(
  chart: Chart | null,
  id: string,
  value: number,
): void {
  if (!chart || !Number.isFinite(value) || value <= 0) return
  chart.createOverlay({
    name: PRICE_LEVEL,
    id,
    groupId: GROUP_LEVEL,
    lock: true,
    points: [{ value }],
  })
}

/**
 * 画一个条件预警价格位。
 * 带明显的橙黄色预警虚线与标签，如 `🔔 预警 ≤ 18.50`。
 */
export function drawAlertLevel(
  chart: Chart | null,
  id: string,
  value: number,
  label = '🔔 预警',
): void {
  if (!chart || !Number.isFinite(value) || value <= 0) return
  ensureTemplates()
  chart.createOverlay({
    name: ALERT_LEVEL,
    id,
    groupId: GROUP_ALERT,
    lock: true,
    points: [{ value }],
    extendData: label,
  })
}

/** 清空当前图上的预警线。 */
export function clearAlertOverlays(chart: Chart | null): void {
  if (!chart) return
  chart.removeOverlay({ groupId: GROUP_ALERT })
}

/**
 * 画一个持仓成本线。
 * 带青绿色虚线与标签，如 `📈 成本 15.20 (+8.5%)`。
 */
export function drawCostLevel(
  chart: Chart | null,
  cost: number,
  currentPrice?: number,
): void {
  if (!chart || !Number.isFinite(cost) || cost <= 0) return
  ensureTemplates()
  let pnlText = ''
  if (currentPrice && Number.isFinite(currentPrice) && currentPrice > 0) {
    const diffPct = ((currentPrice - cost) / cost) * 100
    pnlText = ` (${diffPct >= 0 ? '+' : ''}${diffPct.toFixed(2)}%)`
  }
  chart.createOverlay({
    name: COST_LEVEL,
    id: 'target_cost',
    groupId: GROUP_TRADE_TARGETS,
    lock: true,
    points: [{ value: cost }],
    extendData: `📈 成本${pnlText}`,
  })
}

/**
 * 画一个防守止损线。
 * 带鲜红色虚线与标签，如 `🛑 止损 14.10 (-7.2%)`。
 */
export function drawStopLevel(
  chart: Chart | null,
  stopPrice: number,
  currentPrice?: number,
): void {
  if (!chart || !Number.isFinite(stopPrice) || stopPrice <= 0) return
  ensureTemplates()
  let diffText = ''
  if (currentPrice && Number.isFinite(currentPrice) && currentPrice > 0) {
    const diffPct = ((stopPrice - currentPrice) / currentPrice) * 100
    diffText = ` (${diffPct >= 0 ? '+' : ''}${diffPct.toFixed(2)}%)`
  }
  chart.createOverlay({
    name: STOP_LEVEL,
    id: 'target_stop',
    groupId: GROUP_TRADE_TARGETS,
    lock: true,
    points: [{ value: stopPrice }],
    extendData: `🛑 止损${diffText}`,
  })
}

/** 清空当前图上的持仓操盘线（成本与止损）。 */
export function clearTradeTargetOverlays(chart: Chart | null): void {
  if (!chart) return
  chart.removeOverlay({ groupId: GROUP_TRADE_TARGETS })
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
