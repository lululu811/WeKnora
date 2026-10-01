import { ref, computed, inject, provide, type InjectionKey, type Ref, type ComputedRef } from 'vue';
import type { WorkspaceType, PickItem } from '@/components/workspace/types';
import type { KLineAnchor } from '@/utils/klineAnchors';

export const WORKSPACE_MIN_WIDTH = 450;
export const WORKSPACE_MAX_WIDTH = 1400;
export const WORKSPACE_DEFAULT_WIDTH = 560;
/** 折叠态窄边宽度：只够放一个竖排把手，但能让聊天区几乎完全回来。 */
export const WORKSPACE_RAIL_WIDTH = 36;
const STORAGE_KEY_WIDTH = 'weknora:chat:workspace-width';

export interface AgentWorkspaceContext {
  isOpen: Ref<boolean>;
  /** 折叠态：面板收成右侧窄边而非消失，当前股票/指标/周期全部保留。 */
  isCollapsed: Ref<boolean>;
  activeType: Ref<WorkspaceType>;
  width: Ref<number>;
  /** 实际占位宽度：折叠时是窄边宽度，展开时是用户拖出来的宽度。 */
  effectiveWidth: ComputedRef<number>;
  picks: Ref<PickItem[]>;
  activeIndex: Ref<number>;
  activeThscode: ComputedRef<string>;
  activePick: ComputedRef<PickItem | null>;
  open: (type: WorkspaceType, picks?: PickItem[], index?: number) => void;
  close: () => void;
  toggle: () => void;
  toggleCollapsed: () => void;
  setWidth: (w: number) => void;
  setActiveIndex: (idx: number) => void;
  setActiveThscode: (thscode: string) => void;
  addOrSwitchPick: (pick: PickItem) => void;
  nextStock: () => void;
  prevStock: () => void;
  sendToChatCallback?: Ref<((text: string) => void) | null>;
  sendToChat: (text: string) => void;
  /**
   * 正文日期区间 → 右侧 K 线图。聊天侧调用，K 线侧注册处理器。
   *
   * 这是「聊天 → 图表」第二条通道（第一条是正文 ticker 点击走 picks/activeIndex）。
   * 走注册而非直接持 chart 引用，是为了让两个组件保持解耦：K 线组件是
   * `registry.ts` 里的 `defineAsyncComponent`，加载时机不确定，工作台不该假设它已就绪。
   */
  registerChartFocus: (handler: ChartFocusHandler | null) => void;
  focusRange: (range: DateRangeFocus) => void;
  /**
   * 当前这条回答的锚点集合（见 utils/klineAnchors.ts）。
   *
   * 「一条回答 = 一组锚点」：切到另一条回答时整组替换，而不是往里累积——
   * 累积会很快退化成图上几十个框，正是之前清理掉的那种糊屏。
   *
   * 这条通道替代了「hover 时单发一个区间」的老做法：锚点是**常驻**的，
   * 不需要用户先猜到「这里能悬停」才能建立正文与图的对应关系。
   */
  anchors: Ref<KLineAnchor[]>;
  setAnchors: (list: KLineAnchor[]) => void;
  /** 当前被 hover 的锚点编号（null = 没有）。图侧据此压暗框外并叠统计。 */
  hoveredAnchorIndex: Ref<number | null>;
  setHoveredAnchor: (index: number | null) => void;
  /** 点击正文锚点：让图滚到它。 */
  focusAnchor: (index: number) => void;
  registerAnchorFocus: (handler: AnchorFocusHandler | null) => void;
  /**
   * 用户在本轮里手动选过的标的（null = 还没表态）。
   *
   * 放在工作台上下文里而不是聊天视图里，是因为「手动选标的」这件事有三个入口
   * 分属不同组件：正文 ticker 点击与票签点击在聊天视图，对比条点击在 K 线组件。
   * 三者必须写同一个标志位，否则对比条选的票会被下一次自动联动抢走。
   */
  userPickedThscode: Ref<string | null>;
  markUserPick: (thscode: string) => void;
  resetUserPick: () => void;
}

/** 正文里一个日期区间在图上的诉求。任一端可为 undefined（单点日期）。 */
export interface DateRangeFocus {
  from?: number;
  to?: number;
  /** 「05-20」这种缺年份的月日，图表侧在已加载数据里就近定位。 */
  md?: string;
}

export type ChartFocusHandler = (range: DateRangeFocus) => void;
/** 图上点某个锚点时回调：正文据此高亮对应句子。 */
export type AnchorFocusHandler = (index: number) => void;

const WorkspaceKey: InjectionKey<AgentWorkspaceContext> = Symbol('AgentWorkspace');

export function createAgentWorkspaceContext(): AgentWorkspaceContext {
  const isOpen = ref(false);
  const isCollapsed = ref(false);
  const activeType = ref<WorkspaceType>('none');
  const picks = ref<PickItem[]>([]);
  const activeIndex = ref(0);
  const sendToChatCallback = ref<((text: string) => void) | null>(null);

  const initialWidth = (() => {
    try {
      const saved = Number(localStorage.getItem(STORAGE_KEY_WIDTH));
      if (Number.isFinite(saved) && saved >= WORKSPACE_MIN_WIDTH && saved <= WORKSPACE_MAX_WIDTH) {
        return saved;
      }
    } catch {}
    return WORKSPACE_DEFAULT_WIDTH;
  })();
  const width = ref(initialWidth);

  // 折叠不改 width：用户上次拖出来的宽度要留着，展开时立刻回到原样。
  const effectiveWidth = computed(() => (isCollapsed.value ? WORKSPACE_RAIL_WIDTH : width.value));

  const activeThscode = computed(() => {
    const p = picks.value[activeIndex.value];
    if (!p) return '';
    return `${p.ticker}.${p.exchange}`;
  });

  const activePick = computed(() => {
    return picks.value[activeIndex.value] || null;
  });

  const setWidth = (w: number) => {
    const clamped = Math.max(WORKSPACE_MIN_WIDTH, Math.min(WORKSPACE_MAX_WIDTH, Math.round(w)));
    width.value = clamped;
    try {
      localStorage.setItem(STORAGE_KEY_WIDTH, String(clamped));
    } catch {}
  };

  const open = (type: WorkspaceType, newPicks?: PickItem[], index = 0) => {
    activeType.value = type;
    if (newPicks && newPicks.length > 0) {
      picks.value = newPicks;
      activeIndex.value = Math.max(0, Math.min(index, newPicks.length - 1));
    }
    // 显式打开一定要展开：否则点了股票却只看到一条 36px 窄边，像点击失效。
    isCollapsed.value = false;
    isOpen.value = true;
  };

  const close = () => {
    isOpen.value = false;
  };

  const toggle = () => {
    if (!isOpen.value) isCollapsed.value = false;
    isOpen.value = !isOpen.value;
  };

  const toggleCollapsed = () => {
    isCollapsed.value = !isCollapsed.value;
  };

  const setActiveIndex = (idx: number) => {
    if (idx >= 0 && idx < picks.value.length) {
      activeIndex.value = idx;
    }
  };

  const setActiveThscode = (thscode: string) => {
    const parts = thscode.split('.');
    const ticker = parts[0];
    const exchange = parts[1] || 'SH';
    const foundIdx = picks.value.findIndex((p) => p.ticker === ticker && p.exchange === exchange);
    if (foundIdx >= 0) {
      activeIndex.value = foundIdx;
    } else {
      picks.value = [{ ticker, exchange }, ...picks.value];
      activeIndex.value = 0;
    }
    isCollapsed.value = false;
    isOpen.value = true;
  };

  const addOrSwitchPick = (pick: PickItem) => {
    const foundIdx = picks.value.findIndex((p) => p.ticker === pick.ticker && p.exchange === pick.exchange);
    if (foundIdx >= 0) {
      if (pick.name && !picks.value[foundIdx].name) {
        picks.value[foundIdx].name = pick.name;
      }
      activeIndex.value = foundIdx;
    } else {
      picks.value = [pick, ...picks.value];
      activeIndex.value = 0;
    }
    isCollapsed.value = false;
    isOpen.value = true;
  };

  const nextStock = () => {
    if (picks.value.length > 1) {
      activeIndex.value = (activeIndex.value + 1) % picks.value.length;
    }
  };

  const prevStock = () => {
    if (picks.value.length > 1) {
      activeIndex.value = (activeIndex.value - 1 + picks.value.length) % picks.value.length;
    }
  };

  const sendToChat = (text: string) => {
    if (sendToChatCallback.value) {
      sendToChatCallback.value(text);
    }
  };

  // 当前回答的锚点集合。切回答时整组替换（见接口注释）。
  const anchors = ref<KLineAnchor[]>([]);
  const setAnchors = (list: KLineAnchor[]) => {
    anchors.value = Array.isArray(list) ? [...list] : [];
    // 换了锚点集合，旧的 hover 编号就没意义了——不清会让图上压暗到不存在的锚点。
    hoveredAnchorIndex.value = null;
  };

  const hoveredAnchorIndex = ref<number | null>(null);
  const setHoveredAnchor = (index: number | null) => {
    hoveredAnchorIndex.value = index;
  };

  let anchorFocusHandler: AnchorFocusHandler | null = null;
  const registerAnchorFocus = (handler: AnchorFocusHandler | null) => {
    anchorFocusHandler = handler;
  };
  const focusAnchor = (index: number) => {
    if (anchorFocusHandler) anchorFocusHandler(index);
  };

  // 用户是否已在本轮手动表态。自动联动只允许在它为空时发生。
  const userPickedThscode = ref<string | null>(null);
  const markUserPick = (thscode: string) => {
    userPickedThscode.value = thscode;
  };
  const resetUserPick = () => {
    userPickedThscode.value = null;
  };

  // 图表侧注册的处理器。存 plain 变量而不是 ref：它是外部对象的方法引用，
  // 放进响应式系统只会带来无意义的代理与依赖收集。
  let chartFocusHandler: ChartFocusHandler | null = null;

  const registerChartFocus = (handler: ChartFocusHandler | null) => {
    chartFocusHandler = handler;
  };

  // 图还没就绪（面板未打开 / 组件仍在懒加载）时静默忽略：用户 hover 了一个日期
  // 却弹「加载中」是噪音。真正的反馈由 K 线侧在就绪后自行恢复。
  const focusRange = (range: DateRangeFocus) => {
    if (chartFocusHandler) chartFocusHandler(range);
  };

  const ctx: AgentWorkspaceContext = {
    isOpen,
    isCollapsed,
    activeType,
    width,
    effectiveWidth,
    picks,
    activeIndex,
    activeThscode,
    activePick,
    open,
    close,
    toggle,
    toggleCollapsed,
    setWidth,
    setActiveIndex,
    setActiveThscode,
    addOrSwitchPick,
    nextStock,
    prevStock,
    sendToChatCallback,
    sendToChat,
    registerChartFocus,
    focusRange,
    anchors,
    setAnchors,
    hoveredAnchorIndex,
    setHoveredAnchor,
    focusAnchor,
    registerAnchorFocus,
    userPickedThscode,
    markUserPick,
    resetUserPick,
  };

  return ctx;
}

export function provideAgentWorkspace(): AgentWorkspaceContext {
  const ctx = createAgentWorkspaceContext();
  provide(WorkspaceKey, ctx);
  return ctx;
}

/**
 * 必须在 `provideAgentWorkspace()` 的子树内调用。
 *
 * 这里刻意**不做模块级单例兜底**：多工作台场景下，一个模块级默认值会被每个
 * chat 实例的 provide 覆写，导致 provider 子树外的消费者静默拿到"最近创建的那个
 * chat"的工作台状态——跨会话/跨工作台串味且无任何报错。
 *
 * 缺失 provider 时显式抛错，由调用方决定兜底策略
 * （见 `useChatKLinePanel()` 的 catch 分支）。
 */
export function useAgentWorkspace(): AgentWorkspaceContext {
  const ctx = inject(WorkspaceKey, null);
  if (!ctx) {
    throw new Error(
      'useAgentWorkspace() 必须在 provideAgentWorkspace() 的子树内调用：' +
        '工作台状态是每会话独立的，不存在跨会话共享的默认值。',
    );
  }
  return ctx;
}
