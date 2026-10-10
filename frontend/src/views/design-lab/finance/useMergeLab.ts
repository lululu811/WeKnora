/**
 * design-lab / finance：菜单合并方案对比页的数据层。
 *
 * **只用真实接口，不用 mock。** 三个候选方案（?variant=a|b|c）看到的数据
 * 必须是同一批，否则比的是"数据好不好看"而不是"方案好不好用"。
 *
 * 与大盘预览的差别只有一处，且是本轮明确要改的那处：龙虎榜在这里按 **50** 请求
 * （MarketDashboard.vue:652 写死 5），后端上限正好 50。这样对比页才能诚实地
 * 回答"5 条 vs 50 条到底差多少"。
 */

import { onMounted, ref } from 'vue'
import {
  getMarketSnapshot,
  getDragonTiger,
  type DragonTigerRow,
  type MarketSnapshot,
} from '@/finance/api/market'
import { getEtfFlow, type EtfFlowItem, type EtfFlowResponse } from '@/finance/api/pulse'

/** 候选方案的 id。与路由上的 ?variant= 对应。 */
export type VariantId = 'a' | 'b' | 'c'

export function useMergeLab() {
  const snapshot = ref<MarketSnapshot | null>(null)
  const dragon = ref<DragonTigerRow[]>([])
  const dragonDate = ref<string | null>(null)
  const flow = ref<EtfFlowResponse | null>(null)
  const loading = ref(true)
  const failed = ref<string[]>([])

  async function load() {
    loading.value = true
    failed.value = []
    const [s, d, f] = await Promise.allSettled([
      getMarketSnapshot(60),
      // 大屏只请求 5 条；这里要 50 条，让候选方案看到全量。
      getDragonTiger(50),
      getEtfFlow(),
    ])
    if (s.status === 'fulfilled') snapshot.value = s.value
    else failed.value.push('指数/情绪快照')
    if (d.status === 'fulfilled') {
      dragon.value = d.value.data ?? []
      dragonDate.value = d.value.trade_date ?? null
    } else failed.value.push('龙虎榜')
    if (f.status === 'fulfilled') flow.value = f.value
    else failed.value.push('ETF 份额')
    loading.value = false
  }

  onMounted(load)

  return { snapshot, dragon, dragonDate, flow, loading, failed, reload: load }
}

// ─────────────────────────── 展示格式化 ───────────────────────────

/** 元 → 亿/万，带正负号。A 股行情惯例：正数带 +。 */
export function fmtMoney(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : v < 0 ? '−' : ''
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(digits)} 亿`
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(digits)} 万`
  return `${sign}${abs.toFixed(0)}`
}

/** 百分比。null 一律渲染成「—」，绝不用 0 顶替 —— 0 会被读成"没动"。 */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(digits)}%`
}

export function fmtNum(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

/** 涨红跌绿。跟随本卡的 --md-up / --md-down，深色模式自动切。 */
export function trendClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return 'is-flat'
  return v > 0 ? 'is-up' : 'is-down'
}

/**
 * ETF 行的"变动"用于着色与排序的字段。
 *
 * 份额变动优先（季频，代表资金真实进出），没有份额观测点时退回近 5 日涨跌
 * —— 但这两者**不能混在一个语义里**，所以调用方要同时拿到用了哪个字段。
 */
export function etfPrimaryChange(it: EtfFlowItem): { value: number | null; basis: 'share' | 'price' } {
  if (it.share_change_pct !== null && it.share_change_pct !== undefined) {
    return { value: it.share_change_pct, basis: 'share' }
  }
  if (it.change_5d_pct !== null && it.change_5d_pct !== undefined) {
    return { value: it.change_5d_pct, basis: 'price' }
  }
  return { value: null, basis: 'price' }
}

/** 份额观测日里最晚的那天，用于 caption「份额截至 {date}」。 */
export function latestShareDate(items: EtfFlowItem[]): string | null {
  const dates = items.map((i) => i.trade_date).filter((d): d is string => !!d).sort()
  return dates.length ? dates[dates.length - 1] : null
}

export type { DragonTigerRow, EtfFlowItem }
