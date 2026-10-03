import { inject, provide, type InjectionKey, type Ref } from 'vue';
import { provideAgentWorkspace, useAgentWorkspace, WORKSPACE_MIN_WIDTH, WORKSPACE_MAX_WIDTH, WORKSPACE_DEFAULT_WIDTH, type AgentWorkspaceContext } from './useAgentWorkspace';

export const KLINE_PANEL_MIN_WIDTH = WORKSPACE_MIN_WIDTH;
export const KLINE_PANEL_MAX_WIDTH = WORKSPACE_MAX_WIDTH;
export const KLINE_PANEL_DEFAULT_WIDTH = WORKSPACE_DEFAULT_WIDTH;

export interface KLinePick {
  ticker: string;
  exchange: string;
  name?: string;
  pattern?: string;
}

export type ChatKLinePanelContext = {
  visible: Ref<boolean>;
  picks: Ref<KLinePick[]>;
  activeIndex: Ref<number>;
  width: Ref<number>;
  setWidth: (width: number) => void;
  open: (tickers: KLinePick[], activeIndex?: number) => void;
  setActive: (index: number) => void;
  close: () => void;
  activeThscode: import('vue').ComputedRef<string | null>;
};

const CHAT_KLINE_PANEL_KEY: InjectionKey<ChatKLinePanelContext> = Symbol('chatKLinePanel');

/**
 * 桥接器：统一收归到底层 AgentWorkspace 中，无缝保持老接口兼容
 */
export function provideChatKLinePanel(existingWs?: AgentWorkspaceContext): ChatKLinePanelContext {
  const ws = existingWs || provideAgentWorkspace();

  const ctx: ChatKLinePanelContext = {
    visible: ws.isOpen,
    picks: ws.picks as any,
    activeIndex: ws.activeIndex,
    width: ws.width,
    setWidth: ws.setWidth,
    open: (tickers: KLinePick[], activeIndexArg?: number) => {
      ws.open('kline', tickers, activeIndexArg);
    },
    setActive: ws.setActiveIndex,
    close: ws.close,
    activeThscode: ws.activeThscode as any,
  };

  provide(CHAT_KLINE_PANEL_KEY, ctx);
  return ctx;
}

export function useChatKLinePanel(): ChatKLinePanelContext | null {
  try {
    const ws = useAgentWorkspace();
    return {
      visible: ws.isOpen,
      picks: ws.picks as any,
      activeIndex: ws.activeIndex,
      width: ws.width,
      setWidth: ws.setWidth,
      open: (tickers: KLinePick[], activeIndexArg?: number) => {
        ws.open('kline', tickers, activeIndexArg);
      },
      setActive: ws.setActiveIndex,
      close: ws.close,
      activeThscode: ws.activeThscode as any,
    };
  } catch {
    return inject(CHAT_KLINE_PANEL_KEY, null);
  }
}