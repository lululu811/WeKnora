// API client for the per-user watchlist ("个股追踪 / 持有股观察").
//
// 两条链路必须分清，这不是风格问题：
//   * 自选清单本身（增删改查）走 **Go** `/api/v1/watchlist`。后端从鉴权上下文
//     取当前 (user, tenant)，前端既不传、也不能传 user_id / tenant_id —— 所以
//     换空间就会换成另一份清单。
//   * 行情读数走 **python-service** `/api/quotes`，与 `/api/kline` 同属一类：
//     只读、无鉴权、浏览器直连（nginx.conf 里那条直通正则的白名单路径）。
//     标的搜索 `/api/symbols/search` 同理。
//
// 把清单也塞进 python-service 是行不通的：那边没有 user/tenant 概念，DuckDB
// 还是只读挂载，个人可写状态落到那里等于凭空造一套身份 + 鉴权 + 迁移。
import { get, post, put, del } from '@/utils/request'

/** 追踪池的一行（服务端 scoped 到当前 (user, tenant)）。 */
export interface WatchItem {
  user_id: string
  tenant_id: number
  thscode: string
  name: string
  exchange: string
  sort_order: number
  /** 该标的在池子里的状态，由用户手动推进（见 WatchState）。 */
  state: WatchState
  /** 用户把这只票放进池子的理由；'' 表示没写。 */
  note: string
  created_at: string
  updated_at: string
}

/**
 * 池子里的状态机。与服务端 types.StockWatchState* 一一对应，四个值都是
 * 「用户自己对这只票的立场」，不是持仓事实 —— 所以没有成本、数量、盈亏。
 *
 * 合法迁移（唯一真相源在服务端 types.stockWatchStateTransitions）：
 *   observing → holding | dropped
 *   triggered → holding | dropped | observing
 *   holding   → observing | dropped
 *   dropped   → observing
 * 前端只提供合法的那几条，但**不把这条规则当成保护** —— 服务端在同一个事务
 * 里再校验一次，过期页面发来的非法迁移会被 400 拒绝。
 */
export type WatchState = 'observing' | 'triggered' | 'holding' | 'dropped'

/**
 * 每个状态的合法下一步。**必须与后端 types.stockWatchStateTransitions 逐条一致**
 * —— 这里只负责「别把注定 400 的按钮画出来」，判定权威仍在服务端。
 */
export const WATCH_STATE_TRANSITIONS: Record<WatchState, WatchState[]> = {
  observing: ['holding', 'dropped'],
  triggered: ['holding', 'dropped', 'observing'],
  holding: ['observing', 'dropped'],
  dropped: ['observing'],
}

/**
 * 一只标的的最新行情。**所有数值字段都可为 null** —— 本地没有该标的的行情时
 * 后端给 null，而不是 0。0 在金融语义里是"真的等于零"，用它冒充缺失比留空危险。
 */
export interface Quote {
  thscode: string
  name: string
  exchange: string
  /** 最新交易日 YYYY-MM-DD。null = 连 K 线都没有。 */
  date: string | null
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  prev_close: number | null
  change: number | null
  change_pct: number | null
  volume: number | null
  turnover: number | null
}

export interface QuotesResponse {
  code: number
  /** 按 thscode 索引，前端逐行 O(1) 取用。 */
  data: Record<string, Quote>
  /** 格式合法但本地库里没有该标的（未上市 / 退市 / 改代码）。 */
  missing: string[]
  /** 连格式都不对，属于调用方 bug。 */
  invalid: string[]
}

export interface SymbolSuggestion {
  thscode: string
  ticker: string
  name: string
  exchange: string
  asset_type?: string
}

export function listWatchlist() {
  return get<{ success: boolean; data: WatchItem[] }>('/api/v1/watchlist')
}

export function addWatchItem(payload: { thscode: string; name?: string; exchange?: string }) {
  // created=false 表示这只票本来就在清单里（服务端据此刷新了名称）——
  // 让调用方能区分"已加入"和"已在自选中"，而不是两次都报同一句话。
  return post<{ success: boolean; data: WatchItem; created: boolean }>('/api/v1/watchlist', payload)
}

export function removeWatchItem(thscode: string) {
  return del<{ success: boolean; removed: boolean }>(
    `/api/v1/watchlist/${encodeURIComponent(thscode)}`,
  )
}

export function updateWatchItem(
  thscode: string,
  patch: { name?: string; sort_order?: number; state?: WatchState; note?: string },
) {
  return put<{ success: boolean; data: WatchItem }>(
    `/api/v1/watchlist/${encodeURIComponent(thscode)}`,
    patch,
  )
}

/** 事件种类 —— 后端 `stock_watch_events.kind` 的取值。 */
export type WatchEventKind = 'added' | 'state_changed' | 'note_changed'

/**
 * 批量行情（python-service，浏览器直连）。
 *
 * 一次最多 200 只；超出时后端返回 422，这里直接抛出而不是静默截断 ——
 * 少显示几只比显示一个悄悄被截断的列表更难排查。
 */
export function fetchQuotes(thscodes: string[]) {
  return get<QuotesResponse>(`/api/quotes?symbols=${encodeURIComponent(thscodes.join(','))}`)
}

export function searchSymbols(q: string, limit = 20) {
  return get<{ code: number; data: SymbolSuggestion[] }>(
    `/api/symbols/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  )
}
