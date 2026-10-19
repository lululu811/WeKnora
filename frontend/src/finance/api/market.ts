// 大盘预览（/dashboard）的行情读数。
//
// 两条链路，和 `watchlist.ts` 是同一个道理，必须分清：
//   * 这里的接口全部走 **python-service**（`/api/market/*`），与 `/api/kline`、
//     `/api/quotes` 同属一类：只读、无鉴权、浏览器直连。nginx 直通正则的白名单位于
//     `proxyRoutes.ts` 的 `FINANCE_PROXY_ROUTES`，新增路由必须在那里登记 +
//     同步 `frontend/nginx.conf`。
//   * 自选清单本身走 **Go** `/api/v1/watchlist`（带鉴权、服务端按 (user, tenant) 隔离），
//     行情读数再走 python-service `/api/quotes`。这里不碰自选，见 `watchlist.ts`。
//
// **空值约定（贯穿本文件）**：所有数值字段都可能是 `null`，而且必须尊重它。
// 后端把「查不到」和「真的等于 0」分成两件事（0 在金融语义里是"真的等于零"，
// 用它顶替缺失会读出一个假结论）。前端拿到 null 就该显示「无数据」，
// 不能 `?? 0`、不能 `toFixed()` —— 那会把"没有数据"变成"涨跌 0.00%"。
import { get, post } from '@/utils/request'

/** 一个指数的快照 + 收盘序列（四条曲线共用一个形状）。 */
export interface MarketIndex {
  thscode: string
  name: string
  /** 最新交易日 YYYY-MM-DD。null = 本地没有该指数的行情。 */
  trade_date: string | null
  /** 最新（盘中为最新价，盘后为收盘价）。 */
  last: number | null
  /** 前收盘。快照条的虚线与曲线上的昨收水平线都用它。只有一根 K 线时为 null。 */
  prev_close: number | null
  open: number | null
  high: number | null
  low: number | null
  /** 涨跌幅（%）。只有一根 K 线时为 null —— 新股首日没有"前一天"。 */
  change_pct: number | null
  /**
   * 收盘序列，**按时间正序**（最早在前）。后端已经排好序，前端直接画，
   * 不要再 reverse —— 反了图就是倒的，而那种 bug 在静态图上很难一眼看出来。
   */
  series: Array<{ date: string | null; close: number | null }>
}

/** 近 N 个**有数据**的交易日（不是自然日 —— 节假日会让"最近 5 天"里少 2 天）。 */
export interface LimitUpTrendPoint {
  trade_date: string | null
  count: number
}

export interface MarketSentiment {
  /** 情绪口径统一到这一天。四块若各自取最新日会拼出不同日期加减的数。 */
  trade_date: string | null
  limit_up: number | null
  limit_down: number | null
  /** 炸板家数。 */
  broken: number | null
  /**
   * 炸板率（%），分母是 涨停 + 炸板。
   * 涨停与炸板都是 0 时为 null 而不是 0 —— "0% 的炸板率"是一个结论，
   * "算不出来"是另一回事。
   */
  broken_rate: number | null
  /** 最高连板数。 */
  max_streak: number | null
  /** 最高连板那只票的名字。 */
  streak_name: string | null
  limit_up_trend: LimitUpTrendPoint[]
  /** 全市场涨跌家数。任一项为 null = 算不出来（market 库不可用）。 */
  breadth: { up: number | null; flat: number | null; down: number | null }
}

export interface MarketEtf {
  thscode: string
  name: string
  trade_date: string | null
  last: number | null
  change_pct: number | null
}

/**
 * 市场状态评估（`zettaranc.market_state` 的输出）。
 *
 * ⚠️ **`tradable` 恒为 false，别把它当买/卖信号。** 后端逐年 IC 回测显示
 * composite 的方向反复反号（2022 -0.52 / 2023 +0.07 / 2024 -0.22 /
 * 2025 +0.33 / 2026 -0.15），无法作为方向信号。这是后端在返回值里明说的，
 * 前端若要据此做仓位决策必须先过这一关。
 */
export interface MarketState {
  composite: number
  regime: 'strong' | 'neutral' | 'weak'
  /** 恒为 false。composite 不能作方向信号。 */
  tradable: false
  tradable_note: string
  /**
   * 仓位暴露参考（0 / 0.5 / 1.0），**不是方向建议**。
   *
   * 回测：基准满仓 +1.13% / 回撤 -29.73%，方向自适应 +9.47% / -16.96%。
   * 但 2024 年把 +12.82% 的涨幅压到 +0.01% —— 收益主要来自降暴露。
   */
  exposure_hint: {
    neutral: number
    follow: number
    contrarian: number
    note: string
  }
  /**
   * 短期温度。**`heat_score` 是反向分：高 = 冷、低 = 热**，
   * 与 `raw_heat`（原始热度，越高越热）方向相反，别看混。
   */
  short_term_signal: {
    heat_score: number
    raw_heat: number
    regime: 'cold' | 'hot' | 'neutral'
    interpretation: string
    /** 恒为 'weak'：60日 IC 仅 +0.123，是弱信号，需人工确认。 */
    confidence: 'weak'
    confidence_note: string
  }
  dimensions: Record<
    'trend' | 'breadth' | 'volume' | 'volatility' | 'short_term_heat',
    { score: number; weight: number; reason: string }
  >
  /** 计算所用数据源的日期 YYYY-MM-DD。 */
  as_of: string
  /**
   * 宽度缓存新鲜度。缓存是 `build_breadth_cache.py` 生成的**静态文件，没有任何
   * 定时任务会重建它** —— 数据同步后宽度指标不会自动跟新。
   *
   * 两种 stale：
   *  * `lag_trading_days` > 阈值 —— 缓存落后于指数数据
   *  * `days_behind_today` > 阈值 —— 缓存与指数**同步**滞后（同步任务挂了）
   *
   * `stale: true` 时 composite 的宽度/量能维度基于过期数据，`note` 说明怎么修。
   */
  breadth_freshness: {
    source: string
    last_date: string | null
    /** 缓存落后指数数据的自然日数。 */
    lag_trading_days: number | null
    /** 缓存最后一天距今的自然日数。 */
    days_behind_today: number | null
    stale: boolean
    note: string | null
  }
  /** 计算失败时才有，值为错误说明。 */
  error?: string
}

export interface MarketSnapshot {
  code: number
  /** 情绪口径的交易日。 */
  trade_date: string | null
  /** 四大指数（带 60 日曲线）。 */
  indices: MarketIndex[]
  /** 指数快照条（上证50 / 科创50 / 北证50 / 中证500 / 中证1000）。 */
  tickers: MarketIndex[]
  sentiment: MarketSentiment
  etfs: MarketEtf[]
  /** 市场状态评估。计算失败时后端返回 `{ error }`，其余部分照常。 */
  market_state: MarketState
  /**
   * 降级的数据源名（`index` / `special` / `fund` / `market`）。
   * 非空表示对应格子会是空的 —— 前端可以据此提示"数据未同步"，
   * 而不是让用户对着空白格猜。
   */
  unavailable: string[]
}

export interface DragonTigerRow {
  thscode: string
  name: string
  /**
   * 一句人能读的来源说明。**只由本地确实有的字段拼成**（榜单口径 + 统计区间），
   * 不是交易所的上榜原因 —— 上榜原因本地库里没有，要接别的源才有。
   */
  tag: string
  /** 净买入额（元）。 */
  net_value: number | null
  /** 机构净买额（元）。null = 当日无机构席位数据，不等于 0。 */
  org_net_value: number | null
}

export interface DragonTigerResponse {
  code: number
  trade_date: string | null
  count: number
  /** 已按 thscode 去重（同一只票在 all/org 两个榜单口径下各有一条）。 */
  data: DragonTigerRow[]
}

/**
 * 大盘聚合快照（指数 + 情绪 + 权重 ETF）。
 *
 * 入口横幅和大屏共用这一个接口：横幅只取其中三个数，但它们来自同一批表、
 * 同一批交易日，拆成多接口等于让两个页面各打一轮。
 */
export function getMarketSnapshot(days = 60) {
  return get<MarketSnapshot>('/api/market/snapshot', { params: { days } })
}

/** 龙虎榜净买入榜。大盘工作台传 50（= 后端上限）。 */
export function getDragonTiger(limit = 5) {
  return get<DragonTigerResponse>('/api/market/dragon-tiger', { params: { limit } })
}

// ══════════════════════════════════════════════════════════════════════════
// 板块 / 情绪 / 期股联动
//
// 这一组对应库里三块此前**没有任何端点在读**的数据：index 库的 848 个板块
// 指数与五年日线、special 库的连板梯队、futures 库的四个股指期货主力连续。
// ══════════════════════════════════════════════════════════════════════════

export interface SectorRow {
  thscode: string
  name: string
  /** industry 行业 / concept 概念 / tszs 特色 / region 地域 */
  tag: string
  close: number | null
  change_pct: number | null
  turnover: number | null
  /** 成交额 / 近 20 日中位额。null = 没有足够历史，**不是 1.0**。 */
  volume_multiple: number | null
  /** 该板块内今日涨停家数。null = 反查失败，与「0 只」不同。 */
  limit_up_count: number | null
}

export interface SectorResponse {
  ok: boolean
  trade_date: string | null
  items: SectorRow[]
  /** 该分类下的板块总数（items 已被 limit 截断）。 */
  total: number
  unavailable: string[]
  /** 排序规则，字符串形式透传给用户看：涨停家数 → 放量倍数 → |涨跌幅|。 */
  sort_rule: string
  reason?: string
}

/** 板块指数今日榜。排序在后端做（跨库合并涨停家数后才有得排）。 */
export function getSectors(params: { tag?: string; limit?: number } = {}) {
  return get<SectorResponse>('/api/market/sectors', { params })
}

export interface LadderLevel {
  board_level: string
  board_num: number
  sign_level: number
}

export interface LadderResponse {
  ok: boolean
  days: Array<{ trade_date: string; levels: LadderLevel[] }>
  unavailable: string[]
  reason?: string
}

/** 连板梯队：近 N 日 × 2/3/4/5/6/7+ 板家数矩阵。 */
export function getLimitUpLadder(days = 30) {
  return get<LadderResponse>('/api/market/limit-up-ladder', { params: { days } })
}

export interface BasisPoint {
  trade_date: string
  basis_pct: number
  /** 现货指数当日涨跌（%）。用于与基差率画背离对照。 */
  spot_pct: number | null
}

export interface BasisItem {
  variety: string
  name: string
  futures_thscode: string
  spot_thscode: string
  futures_close: number
  spot_close: number
  /** 期货主力连续 − 现货指数。负 = 贴水。 */
  basis: number
  basis_pct: number | null
  /** 当前基差率在历史中的百分位（0~100）。低于 20 = 贴水偏深。 */
  percentile: number | null
  history_size: number
  series: BasisPoint[]
}

export interface BasisResponse {
  ok: boolean
  /**
   * 基差日 = 期货与现货**共同的**最新交易日。
   * 实测期货滞后一天，所以它通常比 `spot_latest` 早一天。
   * 前端**不要**拿 `spot_latest` 的指数去配 `basis_date` 的期货。
   */
  basis_date: string | null
  futures_latest: string | null
  spot_latest: string | null
  items: BasisItem[]
  unavailable: string[]
  note?: string
  reason?: string
}

/** 期股联动：股指期货基差 + 5 年分位数。 */
export function getFuturesBasis() {
  return get<BasisResponse>('/api/market/basis')
}

export interface TechnicalItem {
  thscode: string
  date: string | null
  close: number | null
  sma20: number | null
  sma60: number | null
  sma250: number | null
  rsi14: number | null
  macd_hist: number | null
  bb_upper: number | null
  bb_lower: number | null
  /** 收盘是否在均线之上。null = 没有该均线（次新股等），**不是 false**。 */
  above_sma20: boolean | null
  above_sma60: boolean | null
  above_sma250: boolean | null
  /** bull 多头排列 / bear 空头 / mixed 纠缠 / null 判不出。 */
  ma_alignment: 'bull' | 'bear' | 'mixed' | null
  /** oversold / neutral / overbought / null。 */
  rsi_zone: 'oversold' | 'neutral' | 'overbought' | null
  /** positive / negative / flat / null。 */
  macd_state: 'positive' | 'negative' | 'flat' | null
  /** 0 = 贴下轨，1 = 贴上轨；**可超出 [0,1]**（跌破下轨为负、突破上轨 >1）。 */
  bb_position: number | null
}

export interface TechnicalsResponse {
  ok: boolean
  as_of: string | null
  /** 计算后端。2024-05-06 前后是**两套实现**，做长回看时会混用。 */
  backend: string | null
  items: Record<string, TechnicalItem>
  /** 请求了但库里没有的（未上市 / 当日没算）。 */
  missing: string[]
  unavailable: string[]
  note?: string
  reason?: string
}

/**
 * 批量技术指标读数（最新日）。
 *
 * POST 而非 GET：thscode 是列表，走 query 会长到几百个字符。
 */
export function fetchTechnicals(thscodes: string[]) {
  return post<TechnicalsResponse>('/api/market/technicals', { thscodes })
}

export interface CommodityItem {
  variety: string
  name: string
  category: string
  a_share_hint: string
  linked_stocks?: string[]
  thscode: string
  contract_name: string
  close_price: number | null
  prev_close?: number | null
  change_pct?: number | null
  volume: number | null
  turnover: number | null
  trade_date: string | null
}

export interface CommoditiesResponse {
  ok: boolean
  trade_date: string | null
  items: CommodityItem[]
  unavailable: string[]
  reason?: string
}

/** 核心大宗商品期货最新行情与关联 A 股板块提示。 */
export function getCommodities() {
  return get<CommoditiesResponse>('/api/market/commodities')
}

export interface FundHoldingsSummary {
  total_funds: number
  total_hold_ratio: number | null
  avg_nav_ratio: number | null
  increase_count: number
  decrease_count: number
  unchanged_count: number
}

export interface FundHoldingItem {
  fund_thscode: string
  fund_name: string
  fund_category?: string
  manager_name: string | null
  hold_ratio: number | null
  position_count: number | null
  security_market_value_rate_pct: number | null
  period_increase_rate_pct: number | null
  investment_rank: number | null
}

export interface FundHoldingsResponse {
  ok: boolean
  stock_thscode: string
  summary?: FundHoldingsSummary
  items: FundHoldingItem[]
  count: number
  unavailable: string[]
  reason?: string
}

/** 穿透查询持有指定股票的公募基金与 ETF 重仓明细。 */
export function getStockFundHoldings(thscode: string, limit = 20) {
  return get<FundHoldingsResponse>('/api/market/fund-holdings', { params: { thscode, limit } })
}

export interface LadderStockItem {
  continue_day_cnt: number
  thscode: string
  name: string
  last_price: number | null
  limit_up_time: string | null
  seal_money: number | null
}

export interface LadderDetailResponse {
  ok: boolean
  trade_date: string | null
  max_board: number
  total_count: number
  items: LadderStockItem[]
  by_level: Record<string, LadderStockItem[]>
  unavailable: string[]
  reason?: string
}

/** 连板梯队详情（最高板、连板个股清单与封单金额）。 */
export function getLadderDetail(params: { min_board?: number; limit?: number } = {}) {
  return get<LadderDetailResponse>('/api/market/ladder-detail', { params })
}

export interface AnomalyReasonItem {
  capture_date: string | null
  thscode: string
  stock_name: string
  tag_name: string
  analysis_content: string
  keywords: string[]
}

export interface AnomalyReasonsResponse {
  ok: boolean
  thscode?: string | null
  items: AnomalyReasonItem[]
  count: number
  unavailable: string[]
  reason?: string
}

/** 异动归因分析与关键词列表。 */
export function getStockAnomalyReasons(thscode?: string, limit = 10) {
  return get<AnomalyReasonsResponse>('/api/market/anomaly-reasons', { params: { thscode, limit } })
}

export interface LimitBreakItem {
  thscode: string
  name: string
  last_price: number | null
  open_times: number
  price_change_ratio: number | null
}

export interface LimitDownItem {
  thscode: string
  name: string
  last_price: number | null
  price_change_ratio: number | null
  first_limit_time: string | null
  last_limit_time: string | null
}

export interface LimitBreakDownResponse {
  ok: boolean
  trade_date: string | null
  break_pool: LimitBreakItem[]
  down_pool: LimitDownItem[]
  break_count: number
  down_count: number
  unavailable: string[]
  reason?: string
}

/** 炸板池与跌停池监控。 */
export function getLimitBreakDown(limit = 20) {
  return get<LimitBreakDownResponse>('/api/market/limit-break-down', { params: { limit } })
}

export interface DividendEventItem {
  thscode: string
  name: string
  ex_date: string | null
  dividend_per_share: number | null
  per_share_bonus: number | null
  allotment_ratio: number | null
  close: number | null
  dividend_yield: number | null
}

export interface DividendCalendarResponse {
  ok: boolean
  items: DividendEventItem[]
  count: number
  unavailable: string[]
  reason?: string
}

export interface DividendCalendarParams {
  thscode?: string
  start_date?: string
  end_date?: string
  min_dividend?: number
  min_bonus?: number
  sort_by?: 'date' | 'yield' | 'bonus' | 'dividend'
  limit?: number
}

/** 除权除息与分红送转事件日历。 */
export function getDividendCalendar(params: DividendCalendarParams = {}) {
  return get<DividendCalendarResponse>('/api/market/dividend-calendar', { params })
}

export interface HotStockItem {
  thscode: string
  ticker?: string
  name: string
  rank: number
  heat: number | null
  rank_change: number
  rank_trend: string | null
}

export interface HotStocksResponse {
  ok: boolean
  mode: 'skyrocket' | 'rank'
  trade_date: string | null
  items: HotStockItem[]
  count: number
  unavailable: string[]
  reason?: string
}

export interface HotStockTrendPoint {
  date: string | null
  rank: number
}

export interface HotStockTrendResponse {
  ok: boolean
  thscode: string
  items: HotStockTrendPoint[]
  count: number
  unavailable: string[]
  reason?: string
}

/** 全市场热度榜与飙升黑马雷达。 */
export function getHotStocks(params: { mode?: 'skyrocket' | 'rank'; limit?: number } = {}) {
  return get<HotStocksResponse>('/api/market/hot-stocks', { params })
}

/** 个股历史人气排名走势。 */
export function getHotStockTrend(thscode: string, limit = 30) {
  return get<HotStockTrendResponse>('/api/market/hot-stock-trend', { params: { thscode, limit } })
}

export interface FuturesWarehouseItem {
  thscode: string
  trade_date: string | null
  amount: number | null
  amount_change: number | null
  equivalent_lots: number | null
}

export interface FuturesWarehouseResponse {
  ok: boolean
  trade_date: string | null
  items: FuturesWarehouseItem[]
  count: number
  unavailable: string[]
  reason?: string
}

export interface FuturesBasisRateItem {
  thscode: string
  trade_date: string | null
  spot_price: number | null
  close_price: number | null
  close_basis: number | null
  close_basis_rate: number | null
}

export interface FuturesBasisRateResponse {
  ok: boolean
  trade_date: string | null
  items: FuturesBasisRateItem[]
  count: number
  unavailable: string[]
  reason?: string
}

/** 期货交易所注册仓单库存与日增减。 */
export function getFuturesWarehouse(params: { thscode?: string; limit?: number } = {}) {
  return get<FuturesWarehouseResponse>('/api/market/futures-warehouse', { params })
}

/** 期货基差与升贴水率。 */
export function getFuturesBasisRate(params: { thscode?: string; limit?: number } = {}) {
  return get<FuturesBasisRateResponse>('/api/market/futures-basis-rate', { params })
}

export interface EtfHolderStructure {
  fund_thscode: string
  fund_name: string | null
  report_date: string | null
  ins_position: number | null
  psnl_rate: number | null
  holder_amount: number | null
  avg_holder_share: number | null
  mgmt_staff_hold_rate: number | null
}

export interface EtfTopHolderItem {
  fund_thscode: string
  holder_name: string
  holder_type: string
  holder_code?: string | null
  rank: number
  hold_share: number | null
  hold_rate_pct: number | null
  report_date: string | null
}

export interface EtfHoldersResponse {
  ok: boolean
  fund_thscode: string
  structure: EtfHolderStructure | null
  top_holders: EtfTopHolderItem[]
  count: number
  unavailable: string[]
  reason?: string
}

export interface EtfInstitutionalRankingItem {
  fund_thscode: string
  fund_name: string | null
  report_date: string | null
  ins_position: number | null
  psnl_rate: number | null
  holder_amount: number | null
  avg_holder_share: number | null
}

export interface EtfInstitutionalRankingResponse {
  ok: boolean
  order_by: 'institution' | 'retail'
  items: EtfInstitutionalRankingItem[]
  count: number
  unavailable: string[]
  reason?: string
}

/** 查询 ETF/基金持有人结构（机构vs散户）与前十大持有人穿透。 */
export function getEtfHoldersDetail(thscode: string, limit = 10) {
  return get<EtfHoldersResponse>('/api/market/etf-holders', { params: { thscode, limit } })
}

/** 全市场基金/ETF 机构投资者占比排行与散户聚集度排行。 */
export function getEtfInstitutionalRanking(params: { order_by?: 'institution' | 'retail'; limit?: number } = {}) {
  return get<EtfInstitutionalRankingResponse>('/api/market/etf-institutional-ranking', { params })
}

export interface SqueezePoint {
  date: string | null
  bb_upper: number | null
  bb_lower: number | null
  bb_middle: number | null
  kc_upper: number | null
  kc_lower: number | null
  kc_middle: number | null
  is_squeezing: boolean | null
  momentum_hist: number | null
  supertrend_trend: number | null
  supertrend_direction: number | null
  supertrend_long: number | null
  supertrend_short: number | null
  beta: number | null
}

export interface SqueezeStatusResponse {
  ok: boolean
  thscode: string
  is_squeezing: boolean
  squeeze_status: 'on' | 'off' | 'fired' | 'no_data' | 'unavailable' | 'error'
  latest_date: string | null
  supertrend_direction: number | null
  supertrend_level: number | null
  momentum_hist: number | null
  beta: number | null
  items: SqueezePoint[]
  count: number
  unavailable: string[]
  reason?: string
}

/** 查询个股 TTM Squeeze 波动挤压指标与 Supertrend 顺势追踪状态。 */
export function getSqueezeStatus(thscode: string, limit = 30) {
  return get<SqueezeStatusResponse>('/api/market/squeeze-status', { params: { thscode, limit } })
}
