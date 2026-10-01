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

/**
 * 事件种类 —— 后端 `stock_watch_events.kind` 的取值。
 *
 * `condition_triggered` 是用户自设条件的「不满足 → 满足」那一刻留下的证据，
 * 也是本页「今日触发」徽标的唯一来源。刻意不复用 `state_changed`：状态是
 * 用户自己的立场，条件里的读数是系统报的事实，两者混在一个 kind 里就没法
 * 只筛出"机器报的"那一类。
 */
export type WatchEventKind = 'added' | 'state_changed' | 'note_changed' | 'condition_triggered'

/**
 * 追踪池的一条事件（append-only，后端按 created_at 倒序返回）。
 *
 * `from_state` / `to_state` 只对 `state_changed` 有意义；`note` 对
 * `note_changed` 与 `condition_triggered` 有意义（后者的 note 是服务端
 * 生成的人话，如「价格跌破 1235.00」——原样展示，不在这里翻译一遍，
 * 否则事件流和飞书里收到的消息会对同一件事说两种话）。
 */
export interface WatchEvent {
  id: number
  user_id: string
  tenant_id: number
  kind: WatchEventKind
  thscode: string
  from_state: string
  to_state: string
  note: string
  /**
   * 事件所属的**交易日**（状态/理由类事件为 null）。
   *
   * 不能拿 created_at 当这个用：任务在 D+1 早上 08:30 报告 D 日收盘，created_at 是
   * 落库那一刻（D+1），与它关于的交易日天然差一天。
   */
  eval_date: string | null
  created_at: string
}

/**
 * 池子活动流（`GET /watchlist/events`）。
 *
 * 这是唯一一处能回答「今天有没有触发过」的地方：条件行上的 `last_satisfied`
 * 只说明"此刻满不满足"，无法区分"今天刚跨过"和"早就一直满足"。传 limit 要
 * 克制 —— 后端上限 200，默认 50。
 */
export function listEvents(params: { thscode?: string; limit?: number } = {}) {
  const query = new URLSearchParams()
  if (params.thscode) query.set('thscode', params.thscode)
  if (params.limit) query.set('limit', String(params.limit))
  const qs = query.toString()
  return get<{ success: boolean; data: WatchEvent[] }>(
    `/api/v1/watchlist/events${qs ? `?${qs}` : ''}`,
  )
}

/**
 * 条件可用的字段。与后端 `types.StockWatchConditionField*` 一一对应，
 * 每一个都映射到 `/api/quotes` 上的一个读数：
 *   price         → close
 *   pct_change    → change_pct（本身就是百分数，-3.2 表示 -3.2%）
 *   volume_ratio  → volume_ratio（当日量 ÷ 前 5 根均量）
 *   close_vs_ma20 → (close / ma20 - 1) * 100（距 MA20 的百分比偏离）
 */
export type ConditionField = 'price' | 'pct_change' | 'volume_ratio' | 'close_vs_ma20'

/** 比较方向。above = 读数 > value，below = 读数 < value。 */
export type ConditionOp = 'above' | 'below'

/**
 * 用户自设的一条触发条件。
 *
 * `last_satisfied` 是三态，**null 与 false 是两回事**：
 *   null  —— 还没判定过（本地缺该标的的历史，或刚添加还没跑过一轮）；
 *   false —— 判定过，当时不满足；
 *   true  —— 判定过，当时满足。
 * 把 null 折叠成 false 会让「尚无法判定」被读成「已确认不满足」，所以 UI 必须
 * 分别渲染。`last_eval_date` 是判定水位：它没往前走就不会再判（周末/节假日/
 * 数据未就绪天然静默）。
 */
export interface WatchCondition {
  /** 服务端生成的 uuid（varchar(36)）—— 不是自增整数，别当数字用。 */
  id: string
  thscode: string
  field: ConditionField
  op: ConditionOp
  value: number
  last_satisfied: boolean | null
  last_eval_date: string | null
  created_at: string
  updated_at: string
}

export function listConditions(thscode: string) {
  return get<{ success: boolean; data: WatchCondition[] }>(
    `/api/v1/watchlist/${encodeURIComponent(thscode)}/conditions`,
  )
}

export function addCondition(
  thscode: string,
  payload: { field: ConditionField; op: ConditionOp; value: number },
) {
  // 新条件第一轮只记录状态、不触发：一个"加进去的那一刻就已经满足"的条件
  // 是用户本来就知道的事，推给他只是噪音。
  // created=false 表示同一条 (field, op, value) 已经在库里（服务端按这个三元组
  // 去重）—— 不是错误，但也不能报「已添加」，那样用户会以为自己多加了一条。
  return post<{ success: boolean; data: WatchCondition; created: boolean }>(
    `/api/v1/watchlist/${encodeURIComponent(thscode)}/conditions`,
    payload,
  )
}

export function removeCondition(thscode: string, id: string) {
  return del<{ success: boolean; removed: boolean }>(
    `/api/v1/watchlist/${encodeURIComponent(thscode)}/conditions/${encodeURIComponent(id)}`,
  )
}

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
