/**
 * 外部模块注册表。
 *
 * 背景：
 *   主应用的菜单（`stores/menu.ts`）和路由（`router/index.ts`）曾经硬编码
 *   watchlist 菜单项和 `/platform/watchlist` 路由 —— 这是"为了一个租户改
 *   通用设施"的典型渗透。
 *
 * 本模块提供注册 API：外部模块（如金融模块）在 barrel 入口
 *   （`src/finance/index.ts`）的副作用 import 里调用 `registerModule`，
 *   注册自己的菜单项和路由。主应用在 store 初始化和 router 创建时读取
 *   注册表，动态插入菜单项和路由。
 *
 * 时序约束：
 *   注册必须发生在 router 创建和 menu store 初始化之前。
 *   通过 `main.ts` 顶部 `import '@/finance'` 确保 —— 该 import 早于
 *   `import router from './router'`，因此 finance barrel 的副作用先执行。
 *
 * 菜单布局：
 *   注册 API 支持 `insertAfter?: string`（默认插入到 `settings` 之前）。
 *   主 menu store 按此字段决定插入位置，注册方不需要知道全局菜单布局。
 *
 * 顶部/底部菜单区分：
 *   `components/menu.vue` 用 `BOTTOM_MENU_PATHS`（settings/logout）做排除法，
 *   其它菜单项一律视为顶部菜单。注册的外部模块自动成为顶部菜单项，
 *   menu.vue 不再硬编码任何外部模块 path。
 *
 * 不在本模块处理的事项：
 *   - 图标特殊处理（每个菜单项的独立 ref 与高亮态切换）属于视觉细节，
 *     仍由 `components/menu.vue` 按 `item.icon` 字段分发。注册方只需提供
 *     `icon` 字符串，menu.vue 负责如何渲染它。
 */

export interface ModuleRegistration {
  /** 模块唯一 ID（用于去重）。 */
  id: string;
  /** 菜单项 path（也作为路由的 path，挂到 /platform 下）。 */
  path: string;
  /** i18n key（如 `menu.watchlist`）。 */
  titleKey: string;
  /**
   * 图标名（用于 menu.vue 的图标映射，按 item.icon 字段分发）。
   * 内置菜单项走 menu.vue 的图标表；外部模块通常改成提供 iconSrc，
   * 免得为了一个图标去改通用菜单组件。
   */
  icon: string;
  /**
   * 图标资源 URL，由注册方自行 import。菜单优先用它，其次回退到
   * `icon` 在内置图标表里的查表结果。
   */
  iconSrc?: string;
  /**
   * 在菜单中的插入位置：插入到 path === insertAfter 的菜单项之后。
   * 默认：插入到 `settings` 之前（即 `toolbox` 之后）。
   */
  insertAfter?: string;
  /** 路由 name（vue-router）。 */
  routeName?: string;
  /** 路由组件（lazy import）。 */
  routeComponent?: () => Promise<any>;
  /** 路由 meta（与通用路由 meta 合并：`requiresInit: true, requiresAuth: true` 自动加）。 */
  routeMeta?: Record<string, any>;
}

const modules: ModuleRegistration[] = [];

/**
 * 注册一个外部模块。重复注册（按 id 判等）静默忽略。
 *
 * 应在 barrel 入口的副作用 import 里调用（如 `src/finance/index.ts`），
 * 必须在 router 创建之前执行（通过 main.ts 顶部 import 顺序保证）。
 */
export function registerModule(mod: ModuleRegistration): void {
  if (modules.some((m) => m.id === mod.id)) {
    return;
  }
  modules.push(mod);
}

/**
 * 读取所有已注册的模块。返回数组的拷贝，调用方不能修改注册表。
 */
export function getRegisteredModules(): readonly ModuleRegistration[] {
  return modules.slice();
}

/**
 * 测试 / 调试用：清空已注册模块。生产环境不应调用。
 */
export function __resetRegisteredModulesForTest(): void {
  modules.length = 0;
}
