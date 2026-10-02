<template>
  <div class="wkc" :class="{ 'is-dark': isDark }" :style="paletteVars">
    <!-- 周期切换。侧栏只有图没有工具栏，周期是这张图唯一的高频操作，
         放在图上方而不是图内：画布顶部让给价格刻度更值钱。 -->
    <div class="wkc__toolbar">
      <span class="wkc__symbol" :title="thscode">
        <span class="wkc__name">{{ name || thscode }}</span>
        <span v-if="name" class="wkc__code">{{ thscode }}</span>
      </span>
      <div class="wkc__periods">
        <button
          v-for="opt in PERIODS"
          :key="opt.value"
          type="button"
          class="wkc__period-btn"
          :class="{ 'is-active': period === opt.value }"
          @click="period = opt.value"
        >
          {{ t(opt.labelKey) }}
        </button>
      </div>
    </div>

    <!-- canvas 与三层状态共用一个相对定位容器（而不是把状态画成兄弟节点）：
         状态层要精确盖住绘图区，absolute + inset:0 才能只遮图不遮工具栏，
         而且状态出现/消失不改变 canvas 尺寸，不会触发一次重排。 -->
    <div class="wkc__chart-wrap">
      <div ref="chartContainer" class="wkc__chart"></div>

      <!-- 三态必须各自可达、各自可辨。合并成一句「无数据」曾经把 nginx 502、
           DuckDB 锁、容器重启全显示成「本地没这只票」，而数据其实好好地躺在库里
           ——排查方向整个被带偏（去查代码表，而不是查代理层）。
           加载中/本地无行情/取数失败 指向的是三种完全不同的下一步。 -->
      <div v-if="state === 'loading'" class="wkc__overlay">
        <span class="wkc__spinner" aria-hidden="true"></span>
        <p class="wkc__title">{{ t('watchlist.klineLoadingSym', { code: thscode, period: currentText }) }}</p>
      </div>

      <div v-else-if="state === 'empty'" class="wkc__overlay">
        <p class="wkc__title">{{ t('watchlist.klineNoData') }}</p>
        <p class="wkc__hint">{{ t('watchlist.klineNoSymbolHint', { code: thscode }) }}</p>
      </div>

      <div v-else-if="state === 'error'" class="wkc__overlay is-error">
        <p class="wkc__title">{{ t('watchlist.klineFetchFailedTitle', { code: thscode }) }}</p>
        <p class="wkc__hint">
          {{ errorMessage }}<br />
          {{ t('watchlist.klineChainHint') }}
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { init, dispose, type Chart } from 'klinecharts'
import { useI18n } from 'vue-i18n'
import { useTheme } from '@/composables/useTheme'
import { getKlineChartTheme } from '@/components/workspace/kline/theme'
import { setZettarancPalette, zettarancPalette } from '@/components/workspace/kline/palette'
import type { KLineData } from '@/components/workspace/kline/types'

/**
 * 自选侧栏的单标的 K 线图：蜡烛 + 成交量 + MA。
 *
 * 为什么不复用 workspace/kline/core-chart.ts 的 `createCoreChart`：
 * 那个工厂把取数**内建**在实例里（自己 new 一个 ZettarancDatafeed、用它自己的
 * onDataLoaded/onNoData/onError 回调），没有注入点也**从不 abort**。本组件的
 * 硬要求是「切标的/切周期先 abort 上一发」「组件卸载时 abort 在飞请求」——
 * 那两条只能自己发 fetch 才做得到，所以按 core-chart 的做法直接持 klinecharts
 * 核心 `Chart`，并**复用**它的 styles（theme.ts）与调色板（palette.ts），
 * 保证红涨绿跌与画布观感跟工作区那套是同一份真相，而不是另起一套颜色。
 */

const props = defineProps<{
  /** `600519.SH` 形态。python-service 的 /api/kline 直接吃这个形态。 */
  thscode: string
  name?: string
}>()

const { t } = useI18n()
const { effectiveTheme } = useTheme()
const isDark = computed(() => effectiveTheme.value === 'dark')

/** 只开放日/周/月三档。分钟级在这个宽度下没有任何可读性。 */
type PeriodValue = 'day' | 'week' | 'month'
const PERIODS: ReadonlyArray<{ value: PeriodValue; labelKey: string }> = [
  { value: 'day', labelKey: 'watchlist.klinePeriodDay' },
  { value: 'week', labelKey: 'watchlist.klinePeriodWeek' },
  { value: 'month', labelKey: 'watchlist.klinePeriodMonth' },
]
/** 当前周期的本地化名，供「正在加载 xxx 的周行情」这句话用。 */
const currentText = computed(
  () => t(PERIODS.find((p) => p.value === period.value)?.labelKey ?? 'watchlist.klinePeriodDay'),
)

/** 侧栏宽度有限，120 根日线 ≈ 半年；再多蜡烛就压成 1px 宽，看不出实体。 */
const BAR_LIMIT = 120

/** 三态。`null` = 有数据，图正常显示；其余各自对应上面三层 overlay。 */
type LoadState = 'loading' | 'empty' | 'error' | null
const state = ref<LoadState>('loading')
const errorMessage = ref('')
const period = ref<PeriodValue>('day')

const chartContainer = ref<HTMLElement | null>(null)
// shallowRef：ref 会对值做深层响应式代理，而 klinecharts 的 Chart 内部是
// 画布 / 观察者 / 指标实例组成的对象图，被 Proxy 包一层只会让库内部的
// instanceof 与 WeakMap 键全部失配（workspace 里同一个坑踩过一次）。
const chart = shallowRef<Chart | null>(null)

let resizeObserver: ResizeObserver | null = null
/** 当前在飞的请求。切标的/切周期先 abort 它——不 abort 的话迟到的响应会盖掉
 *  新标的的图，而用户完全看不出中间发生过一次竞态。 */
let inflight: AbortController | null = null
/** 与 inflight 配对的序号。abort 只保证「请求被取消」，不保证「回调没跑」——
 *  fetch 已经 resolve 之后再 abort 是无效的，所以落图前还要比对一次序号。 */
let requestSeq = 0

/** 红涨绿跌取自 palette.ts 的同一份真相，不在本文件里另写一组颜色。 */
const paletteVars = computed<Record<string, string>>(() => {
  const p = zettarancPalette()
  return { '--wkc-up': p.up, '--wkc-down': p.down }
})

/** 把网络边界上的 unknown 收成可按 key 取值的对象；不是对象就返回 null。 */
function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

/** 从「未校验的对象」里取一个字符串字段。响应体是网络边界，
 *  不能让它以 any 的身份渗进后面每一行。 */
function readString(source: unknown, key: string): string | undefined {
  const value = asRecord(source)?.[key]
  return typeof value === 'string' ? value : undefined
}

/**
 * 取出服务端错误体里的一句人话。形状见 python-service 的 `fail()`：
 * 成功体是 `{ code: 0, data: [...] }`，错误体是 `{ success: false, error, detail }`，
 * 且 detail 既可能是对象（FastAPI 原始形状）也可能是字符串。
 */
function extractErrorMessage(body: unknown): string {
  const detailText = readString(body, 'detail')
  const detailObj = asRecord(asRecord(body)?.['detail'])
  const candidates = [
    readString(body, 'error'),
    readString(detailObj, 'error'),
    readString(detailObj, 'message'),
    readString(body, 'message'),
    detailText,
  ]
  return candidates.find((c) => c !== undefined && c.trim())?.trim() ?? ''
}

/** 一根 K 线的五个必填数字字段。任一缺失/非有限数就返回 null。 */
function finite(row: Record<string, unknown>, key: string): number | null {
  const value = row[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 单行归一化。字段名与 `KLineData` 一一对应，不多不少。 */
function toBar(row: Record<string, unknown> | null): KLineData | null {
  if (!row) return null
  const ts = finite(row, 'ts')
  const open = finite(row, 'open')
  const high = finite(row, 'high')
  const low = finite(row, 'low')
  const close = finite(row, 'close')
  const volume = finite(row, 'volume')
  if (ts === null || open === null || high === null || low === null || close === null || volume === null) {
    return null
  }
  const turnover = finite(row, 'turnover')
  return {
    timestamp: ts * 1000,
    open,
    high,
    low,
    close,
    volume,
    ...(turnover === null ? {} : { turnover }),
  }
}

/** 把 fetch 抛出的异常压成一句能直接展示的话。 */
function describeNetworkError(err: unknown): string {
  if (err instanceof DOMException && err.name === 'AbortError') return t('watchlist.klineAborted')
  if (err instanceof TypeError) return t('watchlist.klineNoServer')
  const msg = err instanceof Error ? err.message : String(err)
  return msg || t('watchlist.klineUnknownError')
}

/**
 * 建图。切主题/切周期都不重建实例——重建会丢用户的缩放级别和十字光标位置，
 * 而这两个状态没有任何理由因为「换个皮肤」而丢。
 */
function ensureChart(): Chart | null {
  if (chart.value) return chart.value
  const container = chartContainer.value
  if (!container) return null

  // 调色板必须在建实例**之前**切换：theme.ts 内部直接读模块级 current 来取色，
  // 事后改 current 不会回溯已经算好的 styles（workspace 里记着这条注释）。
  setZettarancPalette(isDark.value)

  const instance = init(container, {
    locale: 'zh-CN',
    timezone: 'Asia/Shanghai',
    styles: getKlineChartTheme(isDark.value),
    customApi: {
      // X 轴按粒度显示日期，不显示默认的完整时间戳：周K/月K 的时间戳精确到
      // 某一天的 00:00，默认格式会把同一周的 7 根 K 线标成同一个完整时刻，
      // 在这个宽度下必然重叠糊成一片。
      formatDate: (_f: Intl.DateTimeFormat, timestamp: number, _fmt: string, _type: number) => {
        const d = new Date(timestamp)
        const y = d.getUTCFullYear()
        const m = String(d.getUTCMonth() + 1).padStart(2, '0')
        const day = String(d.getUTCDate()).padStart(2, '0')
        return `${y}-${m}-${day}`
      },
    },
  })

  if (!instance) {
    // 容器还没挂上（理论上只有卸载竞态能走到）。不建图，交给下一次 ensureChart。
    return null
  }

  // MA 画在蜡烛窗格里：它是「价格相对中期成本」的读数，放进副图会和价格轴
  // 脱节。默认参数是 5/10/30/60，在这个宽度下四条线互相压着看不出差异，
  // 换成日线上真正会被盯的 5/10/20。
  instance.createIndicator({ name: 'MA', calcParams: [5, 10, 20] }, false, { id: 'candle_pane' })
  // 成交量单独开一个矮窗格（isStack=true）。限死高度：副图一旦参与 flex 伸缩，
  // 主图就会被挤到只剩一条缝。
  instance.createIndicator('VOL', true, { height: 72, minHeight: 48 })

  chart.value = instance
  return instance
}

/** 清到干净状态。切标的时必须先调它，否则上一只票的蜡烛会留在图上。 */
function resetChart(): void {
  const instance = chart.value
  if (!instance) return
  instance.clearData()
  // clearData 只把数据仓清空，**不重绘画布**（klinecharts 里它只碰
  // _dataList / _visibleDataList / timeScale / tooltip）。新数据回来时
  // applyNewData 会顺带重画，于是正常路径看不出问题；但「切到一只本地没有
  // 行情的票」这条路径上永远不会有新数据，旧蜡烛就会一直挂在新标的的图上——
  // 那是本组件最容易犯且最难发现的错。
  // resize() 走 adjustPaneViewport(UpdateLevel.All)，强制整图重画，正好是这里
  // 需要的语义；顺带还把容器尺寸重测一遍，副作用无害。
  instance.resize()
}

/** 清空三态之外的一切，然后发请求。 */
async function load(): Promise<void> {
  const code = props.thscode.trim().toUpperCase()
  if (!code) {
    state.value = 'empty'
    resetChart()
    return
  }

  // 先 abort 上一发，再把本轮序号记下来。序号而不是「controller 相等」做判据：
  // resolve 之后再 abort 是空操作，那时 controller 仍然相等，挡不住迟到的落图。
  inflight?.abort()
  const controller = new AbortController()
  inflight = controller
  const seq = ++requestSeq

  state.value = 'loading'
  errorMessage.value = ''
  resetChart()

  const url =
    `/api/kline?symbol=${encodeURIComponent(code)}` +
    `&period=${period.value}&adjust=forward&limit=${BAR_LIMIT}`

  let resp: Response
  try {
    resp = await fetch(url, { signal: controller.signal })
  } catch (err) {
    // 取消是本组件自己发起的（切标的/卸载），不是故障——不写错误态，
    // 否则用户切一下周期就会闪一次红色报错。
    if (err instanceof DOMException && err.name === 'AbortError') return
    if (seq !== requestSeq) return
    errorMessage.value = describeNetworkError(err)
    state.value = 'error'
    return
  }

  // 解析失败说明拿到的不是 JSON：nginx 502 错误页、登录页 HTML、容器没起来返回的
  // 纯文本都会走到这里。这一步必须在读 status **之前**分流出错。
  let body: unknown = null
  try {
    body = await resp.json()
  } catch {
    if (seq !== requestSeq) return
    errorMessage.value = t('watchlist.klineNotJSON', { status: resp.status })
    state.value = 'error'
    return
  }

  if (seq !== requestSeq) return

  if (!resp.ok) {
    // 只有 404 是「服务端确认这只票没行情」；400 参数非法 / 500 / 502 网关 /
    // 503 数据源未就绪都是**链路故障**，绝不能报成「本地无此标的行情」。
    errorMessage.value = extractErrorMessage(body) || t('watchlist.klineHttpError', { status: resp.status })
    state.value = resp.status === 404 ? 'empty' : 'error'
    return
  }

  const envelope = asRecord(body)

  // 业务层错误：HTTP 200 但 code !== 0。
  if (envelope?.['code'] !== 0) {
    errorMessage.value = extractErrorMessage(body) || t('watchlist.klineCodeError', { code: String(envelope?.['code']) })
    state.value = 'error'
    return
  }

  const rows = Array.isArray(envelope?.['data']) ? (envelope['data'] as unknown[]) : []
  // ts 是 epoch **秒**，klinecharts 要毫秒。少乘这一下会得到 1970 年的 K 线，
  // 而且因为时间戳仍然单调递增，图不会报错、只会安静地画错。
  //
  // 逐根校验而不是直接 map：任一字段不是有限数，klinecharts 会用 NaN 参与
  // 坐标换算，整张图变成空白且不报任何错——宁可少画一根，也不要给出一张
  // 看起来正常的空图。
  const bars: KLineData[] = []
  for (const row of rows) {
    const bar = toBar(asRecord(row))
    if (bar) bars.push(bar)
  }

  if (!bars.length) {
    // rows 为空 = 服务端明确回答「这只票没有行情」；rows 有内容但一根都过不了
    // 校验 = 服务端**给了**数据但形状不对（字段改名、数值被污染）。后者是故障，
    // 报成「本地无该标的行情」等于把排查指向代码表，而真因在服务端——
    // 正是这个组件存在的理由：这两种情况绝不能合成一句话。
    if (rows.length === 0) {
      state.value = 'empty'
    } else {
      errorMessage.value = t('watchlist.klineBadBars', { n: rows.length })
      state.value = 'error'
    }
    return
  }

  const instance = ensureChart()
  if (!instance) {
    state.value = 'error'
    errorMessage.value = '图表初始化失败（容器不可用）'
    return
  }

  instance.applyNewData(bars)
  state.value = null
}

onMounted(() => {
  void load()

  // 侧栏宽度由父面板的拖拽滑块决定，图表必须跟着容器实时重排。
  // 核心 Chart 有真正的 resize()，不必派发 window resize 兜底。
  if (chartContainer.value) {
    resizeObserver = new ResizeObserver(() => chart.value?.resize())
    resizeObserver.observe(chartContainer.value)
  }
})

// 切标的 / 切周期都走同一条重载路径：abort + 清空 + 重新取数。
// load 内部已经做了 abort 与序号判据，这里不再重复判断。
watch([() => props.thscode, period], () => {
  void load()
})

// 换皮肤只改 styles，不重建实例（重建会丢缩放与十字光标）。
watch(isDark, () => {
  setZettarancPalette(isDark.value)
  chart.value?.setStyles(getKlineChartTheme(isDark.value))
})

onBeforeUnmount(() => {
  // 卸载时先掐掉在飞请求：组件没了而 fetch 还在，回来时 setState 会打到已卸载的
  // 组件上，在 Vue 里是一句警告，在日志里则是一条永远查不到来源的噪音。
  inflight?.abort()
  inflight = null
  // 让任何已经 resolve、正准备落图的回调就地作废。
  requestSeq++

  resizeObserver?.disconnect()
  resizeObserver = null

  if (chart.value) {
    // 容器随后会被 Vue 移除，dispose 只在极端情况下才可能抛；吞掉比在卸载钩子里
    // 抛出一个用户完全无法处理的异常好。
    try {
      dispose(chart.value)
    } catch {
      /* 容器已被移除 */
    }
    chart.value = null
  }
})
</script>

<style lang="less" scoped>
.wkc {
  /* 画布底色必须与 getKlineChartTheme 的浅色底 (#FAF7F0) 逐字一致，
     否则图四周会露出一圈与画布不同的边框。 */
  background: #FAF7F0;
  border: 0.5px solid var(--td-component-stroke);
  border-radius: var(--app-radius-md);
  overflow: hidden;
  user-select: none;
}

.is-dark.wkc {
  background: #11141a;
}

.wkc__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--app-space-2);
  padding: var(--app-space-1) var(--app-space-2);
  border-bottom: 0.5px solid var(--td-component-stroke);
}

.wkc__symbol {
  display: flex;
  align-items: baseline;
  gap: var(--app-space-1);
  min-width: 0;
  font-size: var(--app-text-sm);
}

.wkc__name {
  font-weight: 600;
  color: var(--td-text-color-primary);
  /* 代码可能很长，名字必须能被压掉而不是把周期按钮挤出容器。 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wkc__code {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  font-family: var(--app-font-family-mono);
  flex: none;
}

.wkc__periods {
  display: flex;
  gap: 2px;
  flex: none;
}

.wkc__period-btn {
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 2px var(--app-space-2);
  border-radius: var(--app-radius-xs);
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
  transition: color var(--app-motion-fast) ease, background-color var(--app-motion-fast) ease;

  &:hover {
    background: var(--td-bg-color-container-hover);
    color: var(--td-text-color-primary);
  }
}

/* 选中态用品牌色实底而不是下划线：这个面板窄，下划线在视觉上会被 K 线的
   坐标轴线盖住，实底在任何底色下都认得出来。 */
.wkc__period-btn.is-active {
  background: var(--td-brand-color);
  color: #fff;
}

.wkc__chart-wrap {
  position: relative;
  height: 280px;
}

.wkc__chart {
  width: 100%;
  height: 100%;
}

/* 状态层：absolute + inset:0，只盖绘图区（工具栏必须仍可点），
   出现/消失都不改变 canvas 尺寸，因此不会触发重排。 */
.wkc__overlay {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--app-space-2);
  padding: var(--app-space-4);
  text-align: center;
  /* 半透明而非实心：下面的 canvas 还在，能看出「图在这」，只是没有数据。 */
  background: rgba(250, 247, 240, 0.88);
}

.is-dark .wkc__overlay {
  background: rgba(17, 20, 26, 0.86);
}

.wkc__title {
  margin: 0;
  font-size: var(--app-text-base);
  font-weight: 600;
  color: #2A2520;
}

.is-dark .wkc__title {
  color: #e5e7eb;
}

.wkc__hint {
  margin: 0;
  max-width: 320px;
  font-size: var(--app-text-sm);
  line-height: 1.7;
  color: #6B6259;

  b {
    color: #4A4239;
    font-weight: 600;
  }
}

.is-dark .wkc__hint {
  color: #8b93a3;

  b {
    color: #d1d5db;
  }
}

/* 失败态用错误色描一下边框，和「本地无此票」的纯灰态在余光里就能分开——
   两者共用标题/正文样式，只有图标和语气不同是不够的。 */
.wkc__overlay.is-error {
  box-shadow: inset 0 0 0 1px var(--td-error-color-3);
}

.wkc__spinner {
  width: 18px;
  height: 18px;
  border-radius: var(--app-radius-pill);
  border: 2px solid var(--td-component-stroke);
  border-top-color: var(--td-brand-color);
  animation: wkc-spin 800ms linear infinite;
}

@keyframes wkc-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .wkc__spinner {
    animation-duration: 2400ms;
  }
}
</style>
