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

/**
 * 折叠态窄边上竖排显示的名字。
 *
 * 与 WORKSPACE_COMPONENTS 同源：新增工作台时一并登记，否则窄边会退回类型名原文
 * （'kline'）而不是中文标签。
 */
export const WORKSPACE_LABELS: Partial<Record<WorkspaceType, string>> = {
  kline: 'K线',
};
