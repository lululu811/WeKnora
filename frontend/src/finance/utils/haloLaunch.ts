/**
 * 「让 agent 生成完整报告」这一跳的落点计算。
 *
 * 为什么单独成模块（而不是写在 HaloReportDialog 里）：组件挂不起来（klinecharts
 * 画布 + 行情数据流），而这里有一个**已经踩过的坑**，值得被钉住：
 *
 * 这个按钮原来只把问法塞进 `/platform/creatChat?q=...`，**没有携带 agent**。新会话
 * 于是沿用「全局默认」agent（默认就是 builtin-quick-answer）。快速问答走的是 RAG
 * 管线（`agent_enabled=false`），工具连 tool schema 都进不去 —— 模型只能拿知识库
 * 检索结果硬答，最典型的一句是「检索材料中未包含该年报数据」。
 *
 * 实测同一条问法（山西汾酒 600809.SH 2025-12-31）：
 *   · `agent_id=builtin-quick-answer` → 「无法为您生成…未包含…」，一个数字都没有；
 *   · `agent_id=builtin-halo`        → 15 秒内 halo.filing.sync + halo.analyze，
 *     给出 HALO 3.35/5、成长性 4.75/10、标准无保留意见、员工 14,026 人。
 *
 * 所以落点必须**显式**带一个白名单里有 `halo.analyze` 的 agent，而不是指望用户
 * 在新建对话前手动把模式切对。
 */

import { haloAskAvailable } from './haloAskPresets.ts'
// 相对路径而不是 `@/utils/...`：本模块的回归测试跑在 node 的 test runner 上
// （npm test = tsx --test），它只认相对说明符，不解析 tsconfig 的 path alias。
import { LAUNCH_AGENT_PARAM } from '../../utils/launchAgent.ts'

/** 本仓库内置的 HALO agent。config/builtin_agents.yaml 里的 id，改了这里要一起改。 */
export const HALO_BUILTIN_AGENT_ID = 'builtin-halo'

/** 新建对话的落点。与 Watchlist 的 sendToChat 走同一条既有通道。 */
export const HALO_LAUNCH_PATH = '/platform/creatChat'

export interface HaloLaunchInput {
  /** 带交易所后缀，如 600809.SH。 */
  thscode: string
  /** 标的名称，仅用于让 prompt 读起来是人话。 */
  name?: string | null
  /** 报告期，如 2025-12-31。缺省时 prompt 不写报告期。 */
  period?: string | null
  /** 当前选中的 agent（面板里看到的那个）。 */
  currentAgentId?: string | null
  /** 当前选中 agent 的工具白名单。 */
  currentTools?: readonly string[] | null
  /**
   * 本空间已知的 agent id 列表。
   * `null` / 空数组表示「agent 列表还没加载出来」—— 此时乐观地按内置 HALO agent
   * 处理，而不是因为这个异步竞态把入口关掉。
   */
  knownAgentIds?: readonly string[] | null
}

/**
 * 决定这一跳该把会话绑到哪个 agent。
 *
 * 优先级：
 *   1. 当前 agent 的白名单里已经有 `halo.analyze` —— 保持不动，不做无谓切换；
 *   2. 否则用内置的 builtin-halo（agent 列表未加载时也乐观地用它）；
 *   3. 列表已加载且里面没有 builtin-halo —— 返回 null，**调用方不得跳转**。
 *      跳过去只会得到和今天一样的「必定失败的动作」。
 */
export function resolveHaloLaunchAgent(input: HaloLaunchInput): string | null {
  const current = (input.currentAgentId ?? '').trim()
  if (current && haloAskAvailable(input.currentTools)) return current
  const known = input.knownAgentIds
  if (!known || known.length === 0) return HALO_BUILTIN_AGENT_ID
  return known.includes(HALO_BUILTIN_AGENT_ID) ? HALO_BUILTIN_AGENT_ID : null
}

/** 面板顶部那句免责声明的可执行版本：把七个定性维度的判分交给 agent。 */
export function buildFullReportPrompt(input: HaloLaunchInput): string {
  const name = (input.name ?? '').trim()
  const subject = name ? `${name} (${input.thscode})` : input.thscode
  const period = (input.period ?? '').trim()
  return (
    `请对 ${subject}${period ? ` 的 ${period} 年报` : ''} 执行 halo.analyze，生成完整分析报告。` +
    `我已经看过面板里的六维与成长性评分，这一步要的是护城河/滞胀防御/ESG/管理层/` +
    `股东资金面/估值/风险这七个定性维度的判分与结论。`
  )
}

export interface HaloLaunch {
  path: string
  query: { q: string; agent: string }
}

/**
 * 构造 router.push 的入参。返回 null 表示本部署没有能跑 HALO 的 agent，
 * 调用方应给出提示而不是跳转。
 *
 * query 里的 `agent` 用的是 utils/launchAgent 那条通用约定 —— creatChat 对它
 * 一视同仁，不认识 HALO 这套逻辑。
 */
export function buildHaloLaunch(input: HaloLaunchInput): HaloLaunch | null {
  const agent = resolveHaloLaunchAgent(input)
  if (!agent) return null
  return {
    path: HALO_LAUNCH_PATH,
    // 键名走 utils/launchAgent 那一份实现，不在这里手写 —— 写错一个字母就退化成
    // 「跳过去、agent 丢了」的老毛病，而那种失败在 UI 上完全看不出来。
    query: { q: buildFullReportPrompt(input), [LAUNCH_AGENT_PARAM]: agent },
  }
}
