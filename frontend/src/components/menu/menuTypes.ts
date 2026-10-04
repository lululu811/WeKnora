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
  /**
   * 注册模块自带的图标资源 URL（见 modules/registry.ts 的 ModuleRegistration.iconSrc）。
   * 有它就不用为这个图标去改 menu.vue 的内置图标表。
   */
  iconSrc?: string;
}

/** 工具箱入口右侧的图标叠加预览项。 */
export interface ToolboxPreviewItem {
  key: string;
  title: string;
  /**
   * 叠加图标名。**可选**：`browserconnection` 那一项画的是 BrowserIcon 组件
   * （带连通状态小圆点），根本不用 icon 名 —— 它此前被要求必填，于是 menu.vue
   * 传 TOOLBOX_ITEMS 时类型对不上（那一条本来就没有 icon）。
   */
  icon?: string;
}

/** 浏览器连接状态，驱动工具箱角标的连通/离线小圆点。 */
export type BrowserStackStatus = '' | 'connected' | 'offline';

/** 会话分组（按日期或按智能体），透传给 SidebarSessionList 渲染。 */
export interface SidebarSessionGroup<T = any> {
  key: string;
  label?: string;
  items: T[];
}

/** 当前会话来源筛选器的一个选项。
 *
 * 直接复用 `sessionSidebarSourceFilter.ts` 的 `SessionSourceOption`，不另立
 * 一份 View 类型：那条链的产出方是 `buildSessionSourceOptions`，消费方是
 * `SessionSourceFilter.vue` 的 `SourceItem`，两边都是 `{value,label,logo?}`。
 * 中间再放一个 `{key,label,icon?}` 的同义类型，只会让字段名在传递处对不上。
 */

/** 会话行右上角菜单的一项（SessionSidebarRow 渲染，menu.vue 构造）。 */
export interface SessionMenuOption {
  content: string;
  value: string;
  theme?: 'default' | 'success' | 'warning' | 'error' | 'primary';
  prefixIcon?: unknown;
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
