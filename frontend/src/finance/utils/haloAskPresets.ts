/**
 * K 线工作台底部「继续向 Agent 提问」里的 HALO 快捷问法。
 *
 * 为什么单独成模块而不是写在组件里：这段逻辑有两个容易错的点，而组件本身
 * （klinecharts 画布 + 行情数据流）没法在单测里挂载。
 *
 *   1. **门控**。`halo.*` 只在 builtin-halo 的白名单里（config/builtin_agents.yaml）。
 *      给 Z哥 显示这些问法，等于给用户一个必定失败的动作 —— 模型只会回
 *      "我没有这个工具"，而用户会以为功能坏了。
 *   2. **问法本身**。这些 prompt 直接进 chat，而 chat 有回答长度上限
 *      （快速问答/智能推理的每轮预算都是 4096 token）。所以问法要「要结论、
 *      不要长文」：**完整报告由面板里的 HaloReportDialog 出**，那是 Python
 *      算的、不受回答长度限制。让 agent 在 chat 里重写一遍完整报告，既会被
 *      截断，也不是数据层算出来的东西。
 */

export type HaloAskKind = 'six' | 'seven' | 'governance'

/** HALO 分析工具名。既是门控依据，也是 prompt 里要点的工具。 */
export const HALO_ANALYZE_TOOL = 'halo.analyze'

/** 当前 agent 能不能跑 HALO 链路。白名单里没有 halo.analyze 就不给这些问法。 */
export function haloAskAvailable(allowedTools: readonly string[] | undefined | null): boolean {
  return Array.isArray(allowedTools) && allowedTools.includes(HALO_ANALYZE_TOOL)
}

export interface HaloAskContext {
  /** 带交易所后缀，如 688111.SH。 */
  thscode: string
  /** 股票名，仅用于让 prompt 读起来是人话。 */
  name?: string | null
}

/** 构造发给 agent 的 prompt。三种问法都刻意要求「给结论、控篇幅」。 */
export function buildHaloAskPrompt(kind: HaloAskKind, ctx: HaloAskContext): string {
  // 三种问法共用同一个称呼格式，免得同一只票在三段 prompt 里写法不一致。
  const s = ctx.name ? `${ctx.name}(${ctx.thscode})` : ctx.thscode
  switch (kind) {
    case 'six':
      return (
        `用 halo.analyze 取 ${s} 的 HALO 六维与成长性评分。\n` +
        `只要：六维逐维得分表（含原始值与权重）+ 成长性总分 + 缺失项清单。\n` +
        `**不要展开成篇报告** —— 完整报告我在工作台面板里看，这里只要数字与缺失项。`
      )
    case 'seven':
      return (
        `用 halo.analyze 取 ${s} 的七个定性维度锚点（护城河 / 滞胀防御 / ESG / ` +
        `管理层 / 股东资金面 / 估值 / 风险），逐维给 0-10 分并写明判分依据，` +
        `再用 halo.verify 提交这七个分复算综合分。\n` +
        `每个维度控制在两句话以内，综合分以 halo.verify 的复算结果为准。`
      )
    case 'governance':
      return (
        `用 halo.filing.query 取 ${s} 的治理诚信事实：审计意见类型、内控审计意见、` +
        `是否被出具非标内控意见、近三年是否受证券监管处罚、董监高是否被处罚、员工人数。\n` +
        `逐条列出数值与来源页码；取不到的项如实标「缺失」，不要用别的口径顶替。`
      )
  }
}
