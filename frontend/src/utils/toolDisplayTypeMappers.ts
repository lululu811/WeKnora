/**
 * 第三方模块（如金融模块）注册「事件 → display_type」映射器。
 *
 * 背景：
 *   `AgentStreamDisplay.vue` 的 `resolveToolDisplayType` 把事件流里的 tool_name
 *   翻译成 display_type，供 `ToolResultRenderer` 选择渲染组件。早期只有通用工具
 *   （shell、sandbox、read_skill…），后来金融模块接入时直接加了一条：
 *
 *     if (event?.tool_name === 'zettaranc.screener' && event?.success !== false) {
 *       return 'kline_picks'
 *     }
 *
 *   这是"为了一个租户改分发逻辑"的典型渗透 —— 通用渲染分发函数不应该
 *   知道任何具体工具名。
 *
 * 本模块提供一个 mapper 注册表：金融模块在 barrel 入口
 *   （`src/finance/index.ts`）调用 `registerToolDisplayTypeMapper` 注册自己的
 *   映射；`AgentStreamDisplay.vue` 在自己无法判定时调用
 *   `resolveExtensionDisplayType` 兜底。
 *
 * 设计约束：
 *   - mapper 按注册顺序执行，第一个返回非空结果的胜出。
 *   - 重复注册同一个 mapper（按引用判等）被忽略。
 *   - 注册表为空时返回 undefined，调用方按"无 display_type"处理（落到 fallback）。
 *   - 与 `markdownPlugins.ts` 一样，注册发生在 barrel 副作用 import 里，
 *     早于任何渲染。
 */

export type ToolDisplayTypeMapper = (event: any) => string | undefined | null;

const mappers: ToolDisplayTypeMapper[] = [];

/**
 * 注册一个「事件 → display_type」映射器。重复注册（按引用判等）静默忽略。
 */
export function registerToolDisplayTypeMapper(mapper: ToolDisplayTypeMapper): void {
  if (mappers.includes(mapper)) {
    return;
  }
  mappers.push(mapper);
}

/**
 * 通用 `resolveToolDisplayType` 在自己无法判定时调用，遍历所有已注册 mapper。
 * 返回第一个非空结果，全部返回空则返回 undefined。
 */
export function resolveExtensionDisplayType(event: any): string | undefined {
  for (const mapper of mappers) {
    const result = mapper(event);
    if (result) return result;
  }
  return undefined;
}

/**
 * 测试 / 调试用：清空已注册 mapper。生产环境不应调用。
 */
export function __resetToolDisplayTypeMappersForTest(): void {
  mappers.length = 0;
}
