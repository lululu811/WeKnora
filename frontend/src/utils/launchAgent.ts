/**
 * 「新建对话」落点携带 agent 的约定。
 *
 * 为什么需要它：`/platform/creatChat` 是通用入口，新会话用哪个 agent 取自
 * `settings.selectedAgentId`。而从会话页跳到它时，会话页的 `onBeforeRouteLeave`
 * 会 `restoreDefaultsIfSnapshotted()`，把整个 settings 换成「进入会话之前的全局
 * 默认」—— `settings.ts` 里的默认值是 `builtin-quick-answer`。于是**跳转前选好的
 * agent 会被原样丢掉**，新会话退回快速问答：走 RAG 管线，工具连 tool schema 都进不去。
 *
 * 症状长这样：用户让 agent 干一件必须用工具的活（K 线面板的 HALO 报告按钮、
 * 工作台底部那些「用 hithink.finance.analysis.* 分析…」的问法），模型却回一句
 * 「检索材料中没有相关信息」—— 因为它确实没有工具可用。
 *
 * 所以：凡是「带着一句 prompt 开新对话」的入口，都要把 agent 显式写进 URL，
 * 由 creatChat 在**路由守卫之后**应用（见 creatChat.vue 的 applyLaunchAgent）。
 */

/** 落点 query 里的 agent 参数名。 */
export const LAUNCH_AGENT_PARAM = 'agent'

/**
 * agent id 的合法形状。
 *
 * 内置 agent 形如 `builtin-halo`，自定义 agent 是 UUID；两者都在这个字符集里。
 * 这个值会直接进 `selectAgent()`，虽然它来自我们自己 push 的 URL（不是外部输入），
 * 但 query 是可以手改的 —— 与其把一个奇怪字符串塞进 store，不如当作没给。
 */
function isLaunchAgentId(value: string): boolean {
  return value.length > 0 && value.length <= 64 && /^[A-Za-z0-9._-]+$/.test(value)
}

/** 从 creatChat 的 route.query 里取回落点指定的 agent；没有/不合法时返回 null。 */
export function parseLaunchAgentId(query: Record<string, unknown> | null | undefined): string | null {
  const raw = query?.[LAUNCH_AGENT_PARAM]
  if (typeof raw !== 'string') return null
  const id = raw.trim()
  return isLaunchAgentId(id) ? id : null
}

/**
 * 把当前选中的 agent 挂到落点 query 上。
 *
 * 入参/出参都收窄成 `Record<string, string>`：这条通道只带字符串（`q` 与 `agent`），
 * 而 vue-router 的 `LocationQueryRaw` 恰好接受这个形状 —— 用 `Record<string, unknown>`
 * 会在 `router.push({ query })` 处报类型错。
 *
 * agentId 缺省/不合法时原样返回：拿不到就维持今天的行为（沿用全局默认），
 * 而不是把一个坏 id 写进 URL，让新会话选中一个不存在的 agent。
 * 传的即使是内置快速问答也照挂 —— 那是「本次就用它」的显式声明，
 * 由 creatChat 侧应用，幂等且无副作用（见 settings.selectAgentForLaunch）。
 */
export function withLaunchAgent(
  query: Record<string, string>,
  agentId: string | null | undefined,
): Record<string, string> {
  const id = (agentId ?? '').trim()
  if (!isLaunchAgentId(id)) return query
  return { ...query, [LAUNCH_AGENT_PARAM]: id }
}
