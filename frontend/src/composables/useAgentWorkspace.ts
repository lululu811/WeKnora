import { ref, computed, inject, provide, type InjectionKey, type Ref, type ComputedRef } from 'vue';
import type { WorkspaceType, PickItem } from '@/components/workspace/types';

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

  // 用户是否已在本轮手动表态。自动联动只允许在它为空时发生。
  const userPickedThscode = ref<string | null>(null);
  const markUserPick = (thscode: string) => {
    userPickedThscode.value = thscode;
  };
  const resetUserPick = () => {
    userPickedThscode.value = null;
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
