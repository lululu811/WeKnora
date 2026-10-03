/**
 * Markdown 预处理插件注册表。
 *
 * 背景：
 *   `chatMarkdownRenderer.ts` 的渲染管线是一串「文本→文本」的预处理步骤，
 *   早期只有通用步骤（引用、图片、数学公式等）。当金融模块需要把 A 股
 *   ticker（`600519.SH`）包成可交互的 `<span class="kline-ticker">` 时，
 *   `injectKLineTickers` 被直接 import 进通用管线 —— 这是金融逻辑污染
 *   通用设施的典型案例。
 *
 * 本模块提供一个**单点插入**的插件注册表：金融模块在 barrel 入口
 *   （`src/finance/index.ts`）调用 `registerMarkdownPreprocessor` 把
 *   `kline-ticker` 注册进去；通用渲染器只调用 `applyMarkdownPreprocessors`，
 *   不再知道任何金融细节。
 *
 * 设计约束：
 *   - 插件系统**不替换**整条预处理管线，而是在管线的一个**固定插入点**
 *     （streaming 安全处理之后、legacy image 处理之前）执行所有已注册插件。
 *     这个位置正是原本 `injectKLineTickers` 的位置，因此语义零变化。
 *   - 插件按注册顺序执行。重复注册（同名）被忽略。
 *   - 插件可以是"无操作"的：如果金融模块没有接入（例如部署时关闭了
 *     python-service），注册表就是空的，渲染器照跑不误。
 *   - `streaming` 标志透传给插件，让插件能区分流式/完整渲染（ticker
 *     注入当前不区分，但未来插件可能需要）。
 */

export interface MarkdownPreprocessorPlugin {
  /** 插件唯一标识。用于去重和调试日志。 */
  name: string;
  /** 文本→文本的转换函数。 */
  transform: (text: string, ctx: { streaming?: boolean }) => string;
}

const plugins: MarkdownPreprocessorPlugin[] = [];

/**
 * 注册一个预处理插件。重复注册（按 name 判等）静默忽略。
 *
 * 应在 barrel 入口的副作用 import 里调用（如 `src/finance/index.ts` 顶部），
 * 而不是在组件的 `onMounted` 里 —— 注册是模块级的，必须早于任何渲染。
 */
export function registerMarkdownPreprocessor(plugin: MarkdownPreprocessorPlugin): void {
  if (plugins.some((p) => p.name === plugin.name)) {
    return;
  }
  plugins.push(plugin);
}

/**
 * 按注册顺序应用所有预处理插件。注册表为空时返回原文本。
 *
 * 调用方：`chatMarkdownRenderer.ts` 的 `renderChatMarkdown`。
 */
export function applyMarkdownPreprocessors(
  text: string,
  ctx: { streaming?: boolean } = {},
): string {
  if (!text || plugins.length === 0) return text;
  return plugins.reduce((acc, plugin) => plugin.transform(acc, ctx), text);
}

/**
 * 测试 / 调试用：清空已注册插件。生产环境不应调用。
 */
export function __resetMarkdownPreprocessorsForTest(): void {
  plugins.length = 0;
}
