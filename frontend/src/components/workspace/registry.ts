import { defineAsyncComponent, type Component } from 'vue';
import type { WorkspaceType } from './types';

/**
 * 智能体多态工作台组件注册表
 *
 * 采用 defineAsyncComponent 懒加载：只有在激活对应 Agent 工作台时才拉取组件资源，
 * 避免通用问答场景加载沉重的专业图表或沙箱库。
 */
export const WORKSPACE_COMPONENTS: Partial<Record<WorkspaceType, Component>> = {
  kline: defineAsyncComponent(() => import('./kline/KLineWorkspace.vue')),
};

export function hasWorkspace(type: WorkspaceType): boolean {
  return type !== 'none' && Boolean(WORKSPACE_COMPONENTS[type]);
}
