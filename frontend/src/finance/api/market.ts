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
import { get } from '@/utils/request'

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

/** 龙虎榜净买入榜，默认前五。 */
export function getDragonTiger(limit = 5) {
  return get<DragonTigerResponse>('/api/market/dragon-tiger', { params: { limit } })
}
