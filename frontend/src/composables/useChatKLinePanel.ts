import { computed, inject, provide, ref, type InjectionKey, type Ref } from 'vue'

// 右侧栏宽度可拖拽调整，持久化到 localStorage。
export const KLINE_PANEL_MIN_WIDTH = 360
export const KLINE_PANEL_MAX_WIDTH = 1200
export const KLINE_PANEL_DEFAULT_WIDTH = 420

export interface KLinePick {
  ticker: string
  exchange: string
}

function clampPanelWidth(width: number): number {
  if (typeof window === 'undefined') return KLINE_PANEL_DEFAULT_WIDTH
  const viewportCap = Math.max(KLINE_PANEL_MIN_WIDTH, window.innerWidth - 480)
  return Math.min(
    KLINE_PANEL_MAX_WIDTH,
    viewportCap,
    Math.max(KLINE_PANEL_MIN_WIDTH, Math.round(width)),
  )
}

function initialPanelWidth(): number {
  if (typeof localStorage === 'undefined') return KLINE_PANEL_DEFAULT_WIDTH
  const raw = Number(localStorage.getItem('kline_panel_width'))
  return Number.isFinite(raw) && raw > 0 ? clampPanelWidth(raw) : KLINE_PANEL_DEFAULT_WIDTH
}

export type ChatKLinePanelContext = {
  visible: Ref<boolean>
  picks: Ref<KLinePick[]>
  activeIndex: Ref<number>
  width: Ref<number>
  setWidth: (width: number) => void
  // 打开面板并加载一组 picks。默认 active=0。
  open: (tickers: KLinePick[], activeIndex?: number) => void
  // 仅切换 active ticker，不关闭面板。
  setActive: (index: number) => void
  close: () => void
  // 当前 active ticker 的完整 thscode（如 "600519.SH"）。
  activeThscode: import('vue').ComputedRef<string | null>
}

const CHAT_KLINE_PANEL_KEY: InjectionKey<ChatKLinePanelContext> = Symbol(
  'chatKLinePanel',
)

export function provideChatKLinePanel(): ChatKLinePanelContext {
  const visible = ref(false)
  const picks = ref<KLinePick[]>([])
  const activeIndex = ref(0)
  const width = ref(initialPanelWidth())

  const setWidth = (next: number) => {
    width.value = clampPanelWidth(next)
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('kline_panel_width', String(width.value))
    }
  }

  const open = (tickers: KLinePick[], activeIndexArg?: number) => {
    picks.value = Array.isArray(tickers) ? tickers.slice() : []
    activeIndex.value =
      typeof activeIndexArg === 'number' &&
      activeIndexArg >= 0 &&
      activeIndexArg < picks.value.length
        ? activeIndexArg
        : 0
    visible.value = picks.value.length > 0
  }

  const setActive = (index: number) => {
    if (index < 0 || index >= picks.value.length) return
    activeIndex.value = index
  }

  const close = () => {
    visible.value = false
  }

  const activeThscode = computed(() => {
    const p = picks.value[activeIndex.value]
    if (!p) return null
    return `${p.ticker}.${p.exchange}`
  })

  const ctx: ChatKLinePanelContext = {
    visible,
    picks,
    activeIndex,
    width,
    setWidth,
    open,
    setActive,
    close,
    activeThscode,
  }

  provide(CHAT_KLINE_PANEL_KEY, ctx)
  return ctx
}

export function useChatKLinePanel(): ChatKLinePanelContext | null {
  return inject(CHAT_KLINE_PANEL_KEY, null)
}