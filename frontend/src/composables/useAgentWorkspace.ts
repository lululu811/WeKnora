import { ref, computed, inject, provide, type InjectionKey, type Ref, type ComputedRef } from 'vue';
import type { WorkspaceType, PickItem } from '@/components/workspace/types';

export const WORKSPACE_MIN_WIDTH = 450;
export const WORKSPACE_MAX_WIDTH = 1400;
const STORAGE_KEY_WIDTH = 'weknora:chat:workspace-width';

export interface AgentWorkspaceContext {
  isOpen: Ref<boolean>;
  activeType: Ref<WorkspaceType>;
  width: Ref<number>;
  picks: Ref<PickItem[]>;
  activeIndex: Ref<number>;
  activeThscode: ComputedRef<string>;
  activePick: ComputedRef<PickItem | null>;
  open: (type: WorkspaceType, picks?: PickItem[], index?: number) => void;
  close: () => void;
  toggle: () => void;
  setWidth: (w: number) => void;
  setActiveIndex: (idx: number) => void;
  setActiveThscode: (thscode: string) => void;
  addOrSwitchPick: (pick: PickItem) => void;
  nextStock: () => void;
  prevStock: () => void;
  sendToChatCallback?: Ref<((text: string) => void) | null>;
  sendToChat: (text: string) => void;
}

const WorkspaceKey: InjectionKey<AgentWorkspaceContext> = Symbol('AgentWorkspace');

let defaultWorkspaceContext: AgentWorkspaceContext | null = null;

export function createAgentWorkspaceContext(): AgentWorkspaceContext {
  const isOpen = ref(false);
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
    return 650;
  })();
  const width = ref(initialWidth);

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
    isOpen.value = true;
  };

  const close = () => {
    isOpen.value = false;
  };

  const toggle = () => {
    isOpen.value = !isOpen.value;
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

  const ctx: AgentWorkspaceContext = {
    isOpen,
    activeType,
    width,
    picks,
    activeIndex,
    activeThscode,
    activePick,
    open,
    close,
    toggle,
    setWidth,
    setActiveIndex,
    setActiveThscode,
    addOrSwitchPick,
    nextStock,
    prevStock,
    sendToChatCallback,
    sendToChat,
  };

  return ctx;
}

export function provideAgentWorkspace(): AgentWorkspaceContext {
  const ctx = createAgentWorkspaceContext();
  defaultWorkspaceContext = ctx;
  provide(WorkspaceKey, ctx);
  return ctx;
}

export function useAgentWorkspace(): AgentWorkspaceContext {
  const ctx = inject(WorkspaceKey, null);
  if (ctx) {
    return ctx;
  }
  if (defaultWorkspaceContext) {
    return defaultWorkspaceContext;
  }
  defaultWorkspaceContext = createAgentWorkspaceContext();
  return defaultWorkspaceContext;
}
