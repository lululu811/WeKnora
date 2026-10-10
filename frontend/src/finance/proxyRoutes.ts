/**
 * 金融模块反代路由清单。
 *
 * 背景：
 *   `vite.config.ts` 曾散着 7 条几乎完全一样的 python-service 反代规则
 *  （/api/kline、/api/annotate、/api/chart-pattern、/api/indicators、
 *   /api/symbols、/api/quotes、/api/stock-profile），每条都是同样的
 *   `{ target: ..., changeOrigin: true }`。`frontend/nginx.conf` 又用一条
 *   正则 location 兜上面所有路径。两份配置必须手动保持一致 ——
 *   vite.config.ts:185-186 注释明说"这份列表与 nginx.conf 里 ... 必须一致"。
 *
 * 本模块把这份清单收敛成一个常量数组：
 *   - `vite.config.ts` import 它，用 `Object.fromEntries` 动态构造 proxy 对象
 *   - `nginx.conf` 仍是一条正则（静态配置，无法 import JS），但本模块同时
 *     导出 `FINANCE_PROXY_ROUTES_REGEX` 常量作为"应该同步到的目标形态"，
 *     并在 nginx.conf 注释里指向本文件。任何新增路由都先在本文件加一条，
 *     再手动同步 nginx 正则。
 *
 * 后续演进：
 *   如果想彻底消除手动同步，可以让 Docker 构建时跑一个小脚本读这个文件
 *   输出 `nginx-finance-routes.conf` 片段被主 nginx.conf include。当前先做
 *   "单一真相源 + vite 端自动收敛"，nginx 端仍靠注释约定。
 */

export const FINANCE_PROXY_ROUTES = [
  // K 线行情
  '/api/kline',
  // 图表标注
  '/api/annotate',
  // 形态识别（几何形态 + 波浪）
  '/api/chart-pattern',
  // 技术指标
  '/api/indicators',
  // 代码表 / 标的解析
  '/api/symbols',
  // 自选批量行情快照
  '/api/quotes',
  // 悬浮卡个股速览
  '/api/stock-profile',
  // 大盘预览：聚合快照（指数 + 情绪 + ETF）
  '/api/market/snapshot',
  // 大盘预览：龙虎榜净买入榜
  '/api/market/dragon-tiger',
  // 大盘工作台：板块指数今日榜（848 个板块，按 涨停家数→放量→涨跌幅 排）
  '/api/market/sectors',
  // 大盘工作台：期股联动 —— 股指期货基差 + 5 年分位
  '/api/market/basis',
  // 大盘工作台：连板梯队（近 N 日 × 2/3/4/5/6/7+ 板）
  '/api/market/limit-up-ladder',
  // 批量行业归属（前端 finance/api/watchlist.ts 的 fetchIndustryMap 一直在调，后端此前 404）
  '/api/market/industry-map',
  // 大盘工作台：批量技术指标读数（indicators.duckdb，264 列 / 1036 万行）
  '/api/market/technicals',
  // 大盘工作台：集合竞价（高开低开分布 + 短线风向标 + 异动榜）
  '/api/market/auction',
  // 「今天谁在动」：量价异动 /pulse + 财经日历 /calendar
  // 前缀级白名单（见文件头与 FINANCE_PROXY_ROUTES_REGEX 的说明）。
  '/api/finance',
] as const;

export type FinanceProxyRoute = (typeof FINANCE_PROXY_ROUTES)[number];

/**
 * 给 `nginx.conf` 用的正则片段（手动同步！）。
 *
 * 与 `FINANCE_PROXY_ROUTES` 表达的是同一份清单，但 nginx 的正则语法要求
 * 把所有路径写进一个 alternation。本常量只作为"应该同步到的目标形态"的
 * 文档，不被任何 JS 代码消费 —— nginx.conf 是静态配置，无法 import JS。
 *
 * 注意 `/api/market/(snapshot|dragon-tiger|...)` 这段：多条路径共享 `market`
 * 前缀，但**不能**写成 `/api/market/.*` —— 那会把将来任何挂在 /api/market
 * 下的新端点（包括可能出现的写端点）一并直通到 python-service。
 * 反代白名单要显式枚举，漏一条是"页面少一块数据"（还能发现），
 * 多一条是"把不该公开的端点暴露出去"（发现不了）。
 *
 * `finance` 是前缀级的一条（`/api/finance/pulse`、`/api/finance/calendar`），
 * 与 `FINANCE_PROXY_ROUTES` 里的 `/api/finance` 一一对应：`(/.*)?$` 让子路径
 * 一并命中。它目前只挂只读端点，若将来挂上写端点，应把这里也降级成
 * `(pulse|calendar)` 这样的显式枚举。
 *
 * 2026-10-09：market 分支新增 sectors / basis / limit-up-ladder / industry-map。
 * 少同步一条的表现是**前端静默少一块数据**（该请求 404、组件按空态渲染），
 * 不会报错 —— 所以改这里之后要顺手核对 nginx.conf 那一行。
 */
export const FINANCE_PROXY_ROUTES_REGEX =
  '^/api/(kline|annotate|indicators|symbols|stock-profile|quotes|chart-pattern|market/(snapshot|dragon-tiger|sectors|basis|limit-up-ladder|industry-map|technicals|auction)|finance)(/.*)?$';
