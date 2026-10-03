/**
 * 阶段 3.1 —— 侧边导航共享类型。
 *
 * menu.vue 拆分后，Shell / NavList / SessionList / UserArea 四个子组件共用
 * 这里的结构定义，避免各自重复声明导致 props 漂移。
 *
 * 纯类型模块：不含任何运行时代码，拆分只搬结构不改数据流。
 */

/** 侧栏主菜单项（来自 stores/menu.ts 的 menuArr 条目）。 */
export interface MenuItem {
  title: string;
  icon: string;
  path: string;
  childrenPath?: string;
  children?: any[];
}

/** 工具箱入口右侧的图标叠加预览项。 */
export interface ToolboxPreviewItem {
  key: string;
  title: string;
  icon: string;
}

/** 浏览器连接状态，驱动工具箱角标的连通/离线小圆点。 */
export type BrowserStackStatus = '' | 'connected' | 'offline';

/** 会话分组（按日期或按智能体），透传给 SidebarSessionList 渲染。 */
export interface SidebarSessionGroup<T = any> {
  key: string;
  label?: string;
  items: T[];
}

/** 当前会话来源筛选器的一个选项。 */
export interface SessionSourceOptionView {
  key: string;
  label: string;
  icon?: string;
}

/** SessionSidebarRow 需要的行数据（结构保持与 stores/menu.ts 的 children 一致）。 */
export interface SessionRowView {
  id: string;
  path: string;
  title?: string;
  is_pinned?: boolean;
  created_at?: string;
  updated_at?: string;
  isNoTitle?: boolean;
  [key: string]: unknown;
}

/** 会话行右上角菜单的点击载荷。 */
export interface SessionMenuClickPayload {
  value: string;
}

/** resize 手柄事件载荷（由 PanelResizeHandle 抛出）。 */
export interface SidebarResizePayload {
  delta: number;
  keyboard: boolean;
}
