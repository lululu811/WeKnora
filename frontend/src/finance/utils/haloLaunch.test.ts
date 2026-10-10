import assert from 'node:assert/strict'
import test from 'node:test'

import { HALO_ANALYZE_TOOL } from './haloAskPresets.ts'
import { LAUNCH_AGENT_PARAM, parseLaunchAgentId } from '../../utils/launchAgent.ts'
import {
  HALO_BUILTIN_AGENT_ID,
  buildFullReportPrompt,
  buildHaloLaunch,
  resolveHaloLaunchAgent,
} from './haloLaunch.ts'

// 回归：K 线面板「让 agent 生成完整报告」曾经只带 q、不带 agent，新会话于是沿用
// 全局默认（builtin-quick-answer）。快速问答走 RAG 管线，没有工具，模型只能回一句
// 「检索材料未包含该年报数据」—— 用户看到的就是「halo 分析一直失败」。
const CTX = { thscode: '600809.SH', name: '山西汾酒', period: '2025-12-31' }
const ALL_AGENTS = ['builtin-quick-answer', 'builtin-smart-reasoning', 'builtin-zettaranc', 'builtin-halo']

test('当前 agent 跑不了 HALO 时必须改绑 builtin-halo，而不是沿用当前 agent', () => {
  // 用户看到症状时的真实状态：默认快速问答，白名单为空。
  const picked = resolveHaloLaunchAgent({
    ...CTX,
    currentAgentId: 'builtin-quick-answer',
    currentTools: [],
    knownAgentIds: ALL_AGENTS,
  })
  assert.equal(picked, HALO_BUILTIN_AGENT_ID)
})

test('Z哥（白名单里没有 halo.analyze）同样要改绑', () => {
  const picked = resolveHaloLaunchAgent({
    ...CTX,
    currentAgentId: 'builtin-zettaranc',
    currentTools: ['zettaranc.screener', 'hithink.finance.analysis.levels'],
    knownAgentIds: ALL_AGENTS,
  })
  assert.equal(picked, HALO_BUILTIN_AGENT_ID)
})

test('当前 agent 本来就能跑 HALO 时保持不动', () => {
  const picked = resolveHaloLaunchAgent({
    ...CTX,
    currentAgentId: HALO_BUILTIN_AGENT_ID,
    currentTools: [HALO_ANALYZE_TOOL, 'halo.verify'],
    knownAgentIds: ALL_AGENTS,
  })
  assert.equal(picked, HALO_BUILTIN_AGENT_ID)

  // 自定义 agent 只要有 halo.analyze 就优先用它，不硬抢到 builtin-halo。
  const custom = resolveHaloLaunchAgent({
    ...CTX,
    currentAgentId: 'my-halo-agent',
    currentTools: [HALO_ANALYZE_TOOL],
    knownAgentIds: [...ALL_AGENTS, 'my-halo-agent'],
  })
  assert.equal(custom, 'my-halo-agent')
})

test('agent 列表没加载出来时不因为这个竞态关掉入口', () => {
  assert.equal(
    resolveHaloLaunchAgent({ ...CTX, currentAgentId: 'builtin-quick-answer', currentTools: [], knownAgentIds: null }),
    HALO_BUILTIN_AGENT_ID,
  )
  assert.equal(
    resolveHaloLaunchAgent({ ...CTX, currentAgentId: 'builtin-quick-answer', currentTools: [], knownAgentIds: [] }),
    HALO_BUILTIN_AGENT_ID,
  )
})

test('部署里真的没有 builtin-halo 时返回 null —— 不许跳到一个必定失败的会话', () => {
  const picked = resolveHaloLaunchAgent({
    ...CTX,
    currentAgentId: 'builtin-quick-answer',
    currentTools: [],
    knownAgentIds: ['builtin-quick-answer', 'builtin-zettaranc'],
  })
  assert.equal(picked, null)
  assert.equal(
    buildHaloLaunch({
      ...CTX,
      currentAgentId: 'builtin-quick-answer',
      currentTools: [],
      knownAgentIds: ['builtin-quick-answer'],
    }),
    null,
  )
})

test('落点 query 同时带上问法与 agent', () => {
  const launch = buildHaloLaunch({
    ...CTX,
    currentAgentId: 'builtin-quick-answer',
    currentTools: [],
    knownAgentIds: ALL_AGENTS,
  })
  assert.ok(launch)
  assert.equal(launch.path, '/platform/creatChat')
  // 键名取自 utils/launchAgent 的通用约定，creatChat 就是按这个键读回来的。
  assert.equal(launch.query[LAUNCH_AGENT_PARAM], HALO_BUILTIN_AGENT_ID)
  assert.match(launch.query.q, /山西汾酒 \(600809\.SH\) 的 2025-12-31 年报/)
  assert.match(launch.query.q, /halo\.analyze/)
  assert.match(launch.query.q, /护城河\/滞胀防御\/ESG\/管理层\/股东资金面\/估值\/风险/)
})

test('没有标的名字时只给代码，没有报告期时不写「的 年报」', () => {
  const p = buildFullReportPrompt({ thscode: '600809.SH', name: null, period: '' })
  assert.match(p, /请对 600809\.SH 执行 halo\.analyze/)
  assert.doesNotMatch(p, /null|undefined|的 {2}年报/)
})

// 落点写进去的 agent，creatChat 必须能原样读回来 —— 两端用同一个函数，
// 这条是防止有人把键名改成字面量后两边悄悄错开。
test('buildHaloLaunch 的落点能被 creatChat 侧的解析读回来', () => {
  const launch = buildHaloLaunch({
    ...CTX,
    currentAgentId: 'builtin-zettaranc',
    currentTools: ['zettaranc.screener'],
    knownAgentIds: ALL_AGENTS,
  })
  assert.ok(launch)
  assert.equal(parseLaunchAgentId(launch.query as Record<string, unknown>), HALO_BUILTIN_AGENT_ID)
})
