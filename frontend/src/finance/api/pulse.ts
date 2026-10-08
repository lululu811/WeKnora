// 「今天谁在动」量价异动（/platform/watch-pulse）。
//
// 链路：两个接口都走 **python-service**（`/api/finance/*`），与 `/api/kline`、
// `/api/quotes`、`/api/market/*` 同属一类：只读、无鉴权、浏览器直连。nginx 直通
// 正则的白名单在 `proxyRoutes.ts` 的 `FINANCE_PROXY_ROUTES`，新增路由必须在那里
// 登记 + 同步 `frontend/nginx.conf`。
//
// 自选清单**不走这里** —— 它走 Go `/api/v1/watchlist`（带鉴权、服务端按
// (user, tenant) 隔离），复用 `watchlist.ts` 的 `listWatchlist()`，本文件不重复实现。
//
// 两接口的容错等级是**不一样**的，调用方必须分开对待：
//   * pulse 是本页主角。挂了 → 整页错误态 + 重试，不能降级成空列表（空列表会
//     被读成「今天没人动」，是一个假结论）。
//   * calendar 是页脚辅助条。挂了 → 只少了这条横条，主面板照常渲染。
import { get, post } from '@/utils/request'

/**
 * 量能方向。
 *
 * `up` / `down` 是**后端按 window 窗口内的量能中位数判定的**，不是单日
 * `pct_change` 的同义词 —— 一只票可以放量上涨、放量下跌、缩量上涨、缩量下跌，
 * 四种都合法，所以这两个维度在本页是**正交**的，UI 必须分两列渲染，绝不能合并
 * 成一个「红=好、绿=坏」的信号。
 */
export type PulseVolumeState = 'up' | 'down' | 'normal'

/**
 * 后端给出的判定标签。**原样展示，不在本文件/组件里翻译一遍** —— 这些是服务端
 * 已定稿的人话，在前端再映射一遍只会让同一个结论在别处（对话流、观察日记）
 * 显示出另一种说法。与 `WatchDiary.reasons` 是同一条规矩。
 */
export type PulseVerdict = '推升' | '出货' | null

export interface PulseItem {
  /** **回显请求里的输入**（前端传的是裸 6 位码，如 '301190'），不是库内代码。 */
  thscode: string
  /** 库内代码（带交易所后缀，如 '301190.SZ'）。展示用这个。 */
  code: string
  /** 量能倍数（当日量 ÷ 窗口中位量）。1.0 = 中枢。 */
  multiple: number
  /** 当日涨跌幅（%）。红涨绿跌。 */
  pct_change: number
  volume_state: PulseVolumeState
  /** null = 后端读数不足，没给判定。**不能**把 null 渲染成「中性」以外的任何结论。 */
  verdict: PulseVerdict
}

export interface PulseMissing {
  /** 请求里给的那只。 */
  thscode: string
  /** 为什么没有（格式非法 / 库里没有 / 日线不足 …），服务端原话。 */
  reason: string
}

export interface PulseCoverage {
  /** 请求了几只。 */
  total: number
  /** 实际算出倍数的几只。 */
  included: number
  volume_up: number
  volume_down: number
}

export interface WatchPulseResponse {
  ok: boolean
  /** 本次口径的交易日 YYYY-MM-DD（收盘口径，不是今天）。 */
  trade_date: string
  coverage: PulseCoverage
  /** 已按 `abs(multiple - 1)` 降序 —— 最"异动"的在最前面，直接渲染即得目标顺序。 */
  items: PulseItem[]
  missing: PulseMissing[]
}

/** 可选覆盖参数。省略即由后端取最新交易日 + 默认窗口。 */
export interface WatchPulseParams {
  date?: string
  /** 量能对比窗口（交易日数）。 */
  window?: number
  /** 放量判定下沿（倍数）。 */
  low?: number
  /** 放量判定上沿（倍数）。 */
  high?: number
}

/**
 * 自选股量价异动。
 *
 * 一次最多若干只（后端有上限）；`codes` 为空时**不要调**这个函数 —— 空数组既拿
 * 不到东西，又会被前端渲染成「今天没人动」。调用方应在请求前判空并走空态。
 */
export function getWatchPulse(codes: string[], params: WatchPulseParams = {}) {
  // 只带显式给了的键：把 undefined 塞进 body 会被序列化成 null，而 null 与
  // 「未提供」在服务端不一定是同一件事。
  const body: { codes: string[] } & Partial<Record<keyof WatchPulseParams, string | number>> = {
    codes,
  }
  if (params.date !== undefined) body.date = params.date
  if (params.window !== undefined) body.window = params.window
  if (params.low !== undefined) body.low = params.low
  if (params.high !== undefined) body.high = params.high
  return post<WatchPulseResponse>('/api/finance/pulse', body)
}

export interface CalendarEvent {
  /** YYYY-MM-DD，按日期升序（后端已排）。 */
  date: string
  title: string
  /** 后端给的分类，可能出现 '其他' —— 不要假设它属于某个已知枚举。 */
  category: string
}

export interface FinanceCalendarResponse {
  ok: boolean
  count: number
  events: CalendarEvent[]
}

/** 未来若干天的财经日历。`days` 默认 7。 */
export function getFinanceCalendar(days = 7) {
  return get<FinanceCalendarResponse>('/api/finance/calendar', { params: { days } })
}

// ── 权重 ETF 大资金动向（/api/finance/etf-flow） ──────────────────────────
//
// 回答的是「国家队是不是在进出宽基 ETF、动的哪只、力度多大」：份额变动才是
// 「进出」的证据，价格涨跌不是。份额口径是**季频**（见下），所以这张卡的
// caption 必须写清"份额截至哪一天"，否则读者会把一个季度的变化读成今天的。
//
// 容错等级与本文件上方两个接口都不同：**etf-flow 挂了不报整卡错误**，大盘预览
// 回退到快照里的纯价格行（见 MarketDashboard.vue）—— 一张辅助卡不该拖垮整页。

/**
 * 份额口径粒度。
 *
 * 目前后端**恒为 `'quarterly'`**：份额来自季报披露，一个观测点与上一个观测点之间
 * 是三个月，把 `share_change_pct` 读成"这周动了多少"是错的。若后端将来接入日频
 * 份额，这里与 caption 的"（季频）"文案要一起改，不要只改其中一处。
 */
export type EtfFlowGranularity = 'quarterly';

export interface EtfFlowItem {
  /** 带交易所后缀，如 '510300.SH'。与 `MarketEtf.thscode` 同构，可直接 join。 */
  thscode: string
  name: string
  /** 收盘价（元）。 */
  close: number | null
  /** 成交额（元）。 */
  turnover: number | null
  /** 份额（股）。null = 该观测点没有份额披露，**不能**当 0 处理。 */
  shares_outstanding: number | null
  /**
   * 份额相对**上一观测点**的变动（%），不是相对上日。季频口径下这一列是
   * "这个季度国家队进了/出了多少"。
   *
   * null = 拿不到上一个观测点（例如首次入库），UI 显示「—」，**绝不显示 0%** ——
   * 0% 会被读成"没动"，那是另一个结论。
   */
  share_change_pct: number | null
  /** 近 5 日涨跌幅（%）。 */
  change_5d_pct: number | null
  /** 成交放大倍数（当日量 ÷ 窗口中位量）。1.0 = 中枢。 */
  turnover_multiple: number | null
  /** 后端判定的异动标记。本卡只做展示，不在前端复算、不翻译。 */
  signal: boolean
  granularity: EtfFlowGranularity
  /**
   * 本行**份额观测日** YYYY-MM-DD（季频口径下是季报披露日，如 2026-06-30）。
   * 与响应级 `trade_date`（每日行情日）不是同一个日期，caption 要用这个。
   */
  trade_date: string | null
}

/** 请求了但没拿到的标的（代码非法 / 库里没有 / 观测点不足 …）。 */
export interface EtfFlowMissing {
  thscode: string
  /** 服务端原话。 */
  reason: string;
}

export interface EtfFlowCounts {
  /** 请求了几只。 */
  total: number;
  /** 实际返回几只。 */
  included: number;
  /** 落进 `missing` 的几只。 */
  missing: number;
}

export interface EtfFlowResponse {
  ok: boolean
  /**
   * 响应级 `trade_date` 是**行情交易日**（与行情库一致，如 2026-10-08），
   * 不是份额观测日。要"份额截至哪天"请取 item 级 `trade_date` 的最大值 ——
   * 行情每天都有，份额一个季度才披露一次，两个日期混用会把季频伪装成日频。
   */
  trade_date: string | null
  items: EtfFlowItem[]
  missing: EtfFlowMissing[]
  counts: EtfFlowCounts;
}

/** 权重 ETF 大资金动向。无可选参数：口径由后端固定，前端不传 date 去猜。 */
export function getEtfFlow() {
  return get<EtfFlowResponse>('/api/finance/etf-flow')
}