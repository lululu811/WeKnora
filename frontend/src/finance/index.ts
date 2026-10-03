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
import { injectKLineTickers } from '@/utils/klineTickerInjector';
registerMarkdownPreprocessor({
  name: 'kline-ticker',
  transform: (text) => injectKLineTickers(text),
});

// ─────────────────── 组件（原地保留，从 barrel re-export）───────────────────
// 文件位置不变（仍在 src/components/...），但对外入口收敛到 @/finance。
// 4.6 目录重组时再物理搬迁到 src/finance/components/。
export { default as MentionedStocksBar } from '@/components/chat/MentionedStocksBar.vue';
export { default as StockCitationFloat } from '@/components/workspace/kline/StockCitationFloat.vue';

// ─────────────────── Composables ───────────────────
export {
  provideChatKLinePanel,
  useChatKLinePanel,
} from '@/composables/useChatKLinePanel';
export type { ChatKLinePanelContext, KLinePick } from '@/composables/useChatKLinePanel';

export { useFinanceChatIntegration } from './composables/useFinanceChatIntegration';
export type { FinanceChatIntegration, FinanceChatIntegrationDeps } from './composables/useFinanceChatIntegration';

// ─────────────────── Utils（金融专属判定逻辑）───────────────────
export { KNOWN_STOCK_NAMES, pickPrimaryMention, extractMentionedStocks } from '@/utils/stockMentions';
export type { MentionedStock, StockMention } from '@/utils/stockMentions';
export { shouldAutoSwitchChart, isStreamedAnswer } from '@/utils/chartAutoSwitch';
export type { AutoSwitchContext, ChatRowLike } from '@/utils/chartAutoSwitch';
