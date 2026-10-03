/**
 * Finance 模块对外唯一入口。
 *
 * 设计原则：
 *  - 主应用（`src/` 其它位置）只允许 `import ... from '@/finance'`，
 *    不允许直接 import 内部路径（如 `@/finance/composables/...`）。
 *    边界守护由 eslint `no-restricted-imports` 或 `import/no-internal-modules`
 *    在后续里程碑（4.6）里启用。
 *  - 所有金融专属的组件、composable、工具、样式都从这里 re-export。
 *  - 副作用（如 `.kline-ticker` 样式）通过入口 import 注入，确保只要
 *    金融能力接入主应用，样式就生效。
 */

// 副作用：金融专属样式（`.kline-ticker` 等）。Vite 会把 less 文件编译进主 CSS。
import './styles/kline-ticker.less';

// 副作用：注册金融专属的 markdown 预处理插件。
// 顶层 import 保证在任何 markdown 渲染发生之前执行。
import { registerMarkdownPreprocessor } from '@/utils/markdownPlugins';
import { injectKLineTickers } from '@/finance/utils/klineTickerInjector';
registerMarkdownPreprocessor({
  name: 'kline-ticker',
  transform: (text) => injectKLineTickers(text),
});

// 副作用：注册金融专属的「事件 → display_type」映射。
// `AgentStreamDisplay.vue` 的通用分发函数不再知道 `zettaranc.screener`，
// 而是在自己无法判定时查这个注册表。
import { registerToolDisplayTypeMapper } from '@/utils/toolDisplayTypeMappers';
registerToolDisplayTypeMapper((event) => {
  // 2026-10-01：kline_studio.show 随独立服务一起下线，只剩 screener。
  // display_type 从 'kline_studio' 改为 'kline_picks'（见 tool-results.ts）。
  if (event?.tool_name === 'zettaranc.screener' && event?.success !== false) {
    return 'kline_picks';
  }
  return undefined;
});

// 副作用：注册金融模块到主应用菜单和路由。
// `stores/menu.ts` 启动时读取注册表并插入菜单项；`router/index.ts` 创建时
// 读取注册表并通过 `router.addRoute('Platform', ...)` 动态加入路由。
// `insertAfter: 'toolbox'` 让 watchlist 出现在工具箱之后、organizations 之前。
import { registerModule } from '@/modules/registry';
registerModule({
  id: 'watchlist',
  path: 'watchlist',
  titleKey: 'menu.watchlist',
  icon: 'watchlist',
  insertAfter: 'toolbox',
  routeName: 'watchlist',
  routeComponent: () => import('@/finance/views/Watchlist.vue'),
});

// ─────────────────── 反代路由清单 ───────────────────
// 导出给 vite.config.ts 用的反代路径常量数组。vite 端已经从该数组动态构造
// proxy 规则，nginx 端仍靠正则 + 手动同步（见 nginx.conf 注释）。
export { FINANCE_PROXY_ROUTES, FINANCE_PROXY_ROUTES_REGEX } from './proxyRoutes';
export type { FinanceProxyRoute } from './proxyRoutes';

// ─────────────────── 组件（原地保留，从 barrel re-export）───────────────────
// 文件位置不变（仍在 src/components/...），但对外入口收敛到 @/finance。
// 4.6 目录重组时再物理搬迁到 src/finance/components/。
export { default as MentionedStocksBar } from '@/finance/components/MentionedStocksBar.vue';
export { default as StockCitationFloat } from '@/finance/components/kline/StockCitationFloat.vue';

// ─────────────────── Composables ───────────────────
export {
  provideChatKLinePanel,
  useChatKLinePanel,
} from '@/finance/composables/useChatKLinePanel';
export type { ChatKLinePanelContext, KLinePick } from '@/finance/composables/useChatKLinePanel';

export { useFinanceChatIntegration } from './composables/useFinanceChatIntegration';
export type { FinanceChatIntegration, FinanceChatIntegrationDeps } from './composables/useFinanceChatIntegration';

// ─────────────────── Utils（金融专属判定逻辑）───────────────────
export { KNOWN_STOCK_NAMES, pickPrimaryMention, extractMentionedStocks } from '@/finance/utils/stockMentions';
export type { MentionedStock, StockMention } from '@/finance/utils/stockMentions';
export { shouldAutoSwitchChart, isStreamedAnswer } from '@/finance/utils/chartAutoSwitch';
export type { AutoSwitchContext, ChatRowLike } from '@/finance/utils/chartAutoSwitch';
