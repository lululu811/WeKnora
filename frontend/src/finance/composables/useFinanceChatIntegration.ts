/**
 * useFinanceChatIntegration — 把 chat/index.vue 里所有金融专属的状态与逻辑
 * 封装到一个 composable，让聊天视图还原成纯聊天容器。
 *
 * 包含：
 *  - 股票悬浮卡（StockCitationFloat）的状态与关闭调度
 *  - 正文 ticker 的 hover / leave / click 绑定
 *  - 打开 K 线工作台前的服务端候选池校验（resolveStocks）
 *  - 回答完成后自动切图到主标的（maybeAutoSwitchChart）
 *  - 全局调试 API `window.__openKLineWorkspace` 的注入
 *  - 工作台→输入栏的 sendToChat 回调桥接
 *
 * 设计原则：
 *  - chat/index.vue 只挂这一个 composable，不再直接 import 任何金融细节。
 *  - 通过解构暴露 template 需要的一切（stockFloat、handleOpenStockWorkspace 等），
 *    template 零改动。
 *  - `useAgentWorkspace` 仍由 chat/index.vue 调用并 provide：工作台抽象是通用的，
 *    本 composable 通过参数接收它，而不是重复 provide。
 *  - `provideChatKLinePanel` 也仍由 chat/index.vue 调用：它 provide 的 inject key
 *    被 KLineStudioResult 等下游消费，必须在 chat 层就位。
 *
 * 回归风险点：
 *  - `window.__openKLineWorkspace` 是全局调试 API，chat/index.vue 没在 onUnmounted
 *    里清理，这里保留相同语义（不清理）。多 chat 实例场景下，最后 mounted 的那个
 *    会覆盖前面的，与旧行为一致。
 *  - `autoSwitchedForMessageId` 必须在新一轮 sendMsg 时清空，否则跨会话残留会让
 *    新会话的首条回答永远不切图。`resetFinanceForNewTurn()` 暴露给 chat/index.vue
 *    在 sendMsg 入口处调用。
 */
import { ref, onMounted, type Ref } from 'vue';
import { useKLineTickerObserver } from '@/composables/useKLineTickerObserver';
import { KNOWN_STOCK_NAMES, pickPrimaryMention } from '@/utils/stockMentions';
import { shouldAutoSwitchChart } from '@/utils/chartAutoSwitch';
import type { AgentWorkspaceContext } from '@/composables/useAgentWorkspace';

declare global {
  interface Window {
    /** 全局调试 API：外部脚本直接打开 K 线工作台。金融模块挂载。 */
    __openKLineWorkspace?: (ticker?: string, exchange?: string, name?: string) => void;
  }
}

export interface FinanceChatIntegrationDeps {
  /** 当前正在生成的 assistant message ID，用于 `isStreamedAnswer` 之外的辅助判定。 */
  currentAssistantMessageId: Ref<string>;
  /** 输入栏 ref，用于工作台→聊天的 `sendToChat` 桥接。 */
  inputFieldRef: Ref<{ triggerSend?: (text: string) => void } | undefined>;
}

export interface FinanceChatIntegration {
  /** 股票悬浮卡的响应式状态（visible/top/left/thscode/name）。 */
  stockFloat: Ref<{
    visible: boolean;
    top: number;
    left: number;
    thscode: string;
    name: string;
  }>;
  /** 取消挂起的悬浮卡关闭定时器。 */
  cancelStockFloatClose: () => void;
  /** 调度一次悬浮卡关闭（默认 200ms）。 */
  scheduleStockFloatClose: (delay?: number) => void;
  /**
   * 打开 K 线工作台。先让服务端过一遍候选池（`/api/symbols/resolve`），
   * 失败则退回原始候选池，不阻断用户操作。
   */
  handleOpenStockWorkspace: (
    stock: { ticker: string; exchange: string; name?: string },
    allStocks?: Array<{ ticker: string; exchange: string; name?: string }>,
  ) => Promise<void>;
  /**
   * 回答完成后，按 `shouldAutoSwitchChart` 的判定把 K 线切到这条回答的主标的。
   * chat/index.vue 在 `onAfterMsgList` 里调：
   *   `finance.maybeAutoSwitchChart(findLastMessage(isStreamedAnswer(...)))`
   */
  maybeAutoSwitchChart: (message: {
    role?: string;
    id?: string | null;
    is_completed?: boolean;
    answer?: string;
    content?: string;
    message?: string;
  } | null | undefined) => void;
  /** 新一轮 sendMsg 入口调用：清空"已自动切过"标记，让本轮联动重新有机会发生。 */
  resetFinanceForNewTurn: () => void;
}

export function useFinanceChatIntegration(
  agentWorkspace: AgentWorkspaceContext,
  scrollContainer: Ref<HTMLElement | null | undefined>,
  deps: FinanceChatIntegrationDeps,
): FinanceChatIntegration {
  // ─────────────────── 股票悬浮卡 ───────────────────
  const stockFloat = ref({
    visible: false,
    top: 0,
    left: 0,
    thscode: '',
    name: '',
  });
  let stockFloatCloseTimer: ReturnType<typeof setTimeout> | null = null;

  const cancelStockFloatClose = () => {
    if (stockFloatCloseTimer) {
      clearTimeout(stockFloatCloseTimer);
      stockFloatCloseTimer = null;
    }
  };

  const scheduleStockFloatClose = (delay = 200) => {
    cancelStockFloatClose();
    stockFloatCloseTimer = setTimeout(() => {
      stockFloat.value.visible = false;
    }, delay);
  };

  const handleStockHover = (thscode: string, el: Element) => {
    cancelStockFloatClose();
    const rect = el.getBoundingClientRect();
    const ticker = thscode.split('.')[0];
    const matchedName = KNOWN_STOCK_NAMES[ticker] || '';
    stockFloat.value = {
      visible: true,
      top: rect.top,
      left: rect.left + rect.width / 2,
      thscode,
      name: matchedName,
    };
  };

  // ─────────────────── 候选池校验 ───────────────────
  /**
   * 打开 K 线工作台前，先让服务端把候选池过一遍。
   *
   * 候选池来自模型回答里的自由文本抽取，未经任何校验，两类坏东西会混进来：
   *   - 幻觉代码：模型把 600487（亨通光电）写成 688487。后者本地从未发行，
   *     进了池子点下去就是一片黑——图表取不到任何行情。
   *   - name 是代码：抽取正则会把 `600105（600101.SH）` 里的纯数字当股票名，
   *     于是池子出现 "600105 600101.SH" 这种 name 与 code 相同的条目。
   *
   * /api/symbols/resolve 一次查询同时解决两件事：剔掉本地不存在的代码，并用
   * v_symbol 的权威名称覆盖抽取阶段猜出来的名字。
   *
   * 这一步是**增强**不是依赖：接口挂了或超时都退回原始候选池，不阻断用户点开图表。
   */
  const resolveStocks = async (
    stocks: Array<{ ticker: string; exchange: string; name?: string }>,
  ): Promise<Array<{ ticker: string; exchange: string; name: string }> | null> => {
    const symbols = stocks.map((s) => `${s.ticker}.${s.exchange}`);
    try {
      const res = await fetch('/api/symbols/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols }),
      });
      if (!res.ok) return null;
      const body = await res.json();
      const rows = body?.data;
      if (!Array.isArray(rows) || rows.length !== stocks.length) return null;

      const kept: Array<{ ticker: string; exchange: string; name: string }> = [];
      stocks.forEach((s, i) => {
        const hit = rows[i];
        if (!hit?.valid) return;
        kept.push({
          ticker: hit.ticker || s.ticker,
          exchange: hit.exchange || s.exchange,
          name: hit.name || s.ticker,
        });
      });
      // 全部无效时保留原列表：与其什么都不显示，不如让用户点开看到空状态提示。
      return kept.length > 0 ? kept : null;
    } catch {
      return null;
    }
  };

  const handleOpenStockWorkspace = async (
    stock: { ticker: string; exchange: string; name?: string },
    allStocks?: Array<{ ticker: string; exchange: string; name?: string }>,
  ) => {
    stockFloat.value.visible = false;
    // 票签点击与正文 ticker 点击共用这个入口，都是用户的显式选择：
    // 标记之后本轮不再自动切图。
    agentWorkspace.markUserPick(`${stock.ticker}.${stock.exchange}`);
    const picks =
      allStocks && allStocks.length > 0
        ? allStocks.map((s) => ({ ticker: s.ticker, exchange: s.exchange, name: s.name }))
        : [{ ticker: stock.ticker, exchange: stock.exchange, name: stock.name }];
    const activeIdx = Math.max(0, picks.findIndex((p) => p.ticker === stock.ticker));

    // 先按过滤后的列表开面板（不阻塞交互），解析回来后再用权威名称刷新一次。
    agentWorkspace.open('kline', picks as any, activeIdx);

    const resolved = await resolveStocks(picks);
    if (!resolved) return;
    const stillThere = resolved.findIndex((p) => p.ticker === stock.ticker);
    agentWorkspace.open(
      'kline',
      resolved as any,
      stillThere >= 0 ? stillThere : Math.min(activeIdx, resolved.length - 1),
    );
  };

  // ─────────────────── 自动切图 ───────────────────
  /** 已经为哪条回答自动切过图，避免同一轮重复触发。 */
  const autoSwitchedForMessageId = ref<string | null>(null);

  /**
   * 回答完成后，把右侧 K 线切到这条回答的「主标的」。
   *
   * 三重克制，缺一不可：
   *  1. **只在面板已经打开时切**。面板关着还去开，就是把「看K线」这个决定
   *     替用户做了——那正是刚修掉的 KLineStudioResult 自动开图缺陷。
   *  2. **用户手动选过就不切**。用户的显式选择永远优先于模型的暗示。
   *  3. **每条回答只切一次**。判不出主标的（`pickPrimaryMention` 返回 null）时
   *     什么都不做，宁可空着也不猜。
   */
  const maybeAutoSwitchChart = (
    message:
      | {
          role?: string;
          id?: string | null;
          is_completed?: boolean;
          answer?: string;
          content?: string;
          message?: string;
        }
      | null
      | undefined,
  ) => {
    if (!message || message.role !== 'assistant') return;
    // 判定逻辑在 utils/chartAutoSwitch.ts 里，是纯函数、有单测覆盖——
    // 这几条守卫写错的表现是静默的（要么抢图位，要么永远不联动）。
    if (
      !shouldAutoSwitchChart({
        messageCompleted: Boolean(message.is_completed),
        panelOpen: agentWorkspace.isOpen.value,
        userPickedThscode: agentWorkspace.userPickedThscode.value,
        alreadySwitchedForId: autoSwitchedForMessageId.value,
        messageId: message.id,
      })
    )
      return;

    const text = message.answer || message.content || message.message || '';
    const primary = pickPrimaryMention(text);
    // 先记账再判定：即使这条回答判不出主标的，也不该在后续刷新里反复尝试。
    autoSwitchedForMessageId.value = message.id ?? null;
    if (!primary) return;
    if (primary.thscode === agentWorkspace.activeThscode.value) return;

    agentWorkspace.setActiveThscode(primary.thscode);
  };

  const resetFinanceForNewTurn = () => {
    autoSwitchedForMessageId.value = null;
  };

  // ─────────────────── 正文 ticker 监听 ───────────────────
  // 监听 chat 文本中的股票代码：hover 唤出轻量 5 星持股评分卡片，点击打开完整右侧工作台
  useKLineTickerObserver(scrollContainer, {
    onHover: (thscode, el) => {
      handleStockHover(thscode, el);
    },
    onLeave: () => {
      scheduleStockFloatClose(200);
    },
    onClick: (thscode) => {
      cancelStockFloatClose();
      const [ticker, exchange] = thscode.split('.');
      if (!ticker) return;
      // 用户显式点了正文里的标的 -> 本轮不再自动切图
      agentWorkspace.markUserPick(thscode);
      const matchedName = KNOWN_STOCK_NAMES[ticker] || '';
      handleOpenStockWorkspace({ ticker, exchange: exchange || 'SH', name: matchedName });
    },
  });

  // ─────────────────── 全局 API 与桥接 ───────────────────
  onMounted(() => {
    window.__openKLineWorkspace = (ticker = '600519', exchange = 'SH', name = '贵州茅台') => {
      agentWorkspace.open('kline', [{ ticker, exchange, name }] as any, 0);
    };
    agentWorkspace.sendToChatCallback.value = (text) => {
      if (deps.inputFieldRef.value?.triggerSend) {
        deps.inputFieldRef.value.triggerSend(text);
      }
    };
  });

  return {
    stockFloat,
    cancelStockFloatClose,
    scheduleStockFloatClose,
    handleOpenStockWorkspace,
    maybeAutoSwitchChart,
    resetFinanceForNewTurn,
  };
}
