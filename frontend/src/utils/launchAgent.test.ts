import assert from 'node:assert/strict'
import test from 'node:test'

import { LAUNCH_AGENT_PARAM, parseLaunchAgentId, withLaunchAgent } from './launchAgent.ts'

// 这一对函数是「新建对话别把 agent 弄丢」的接缝：入口写进 URL，creatChat 读回来。
// 丢掉它的后果不是 UI 上的错，而是新会话悄悄退回快速问答 —— RAG 管线、没有工具，
// 模型只会回一句「检索材料里没有」。

test('parseLaunchAgentId 只认合法 id', () => {
  assert.equal(parseLaunchAgentId({ agent: 'builtin-halo' }), 'builtin-halo')
  assert.equal(parseLaunchAgentId({ agent: '  builtin-halo  ' }), 'builtin-halo')
  assert.equal(parseLaunchAgentId({ agent: 'a1b2-c3.d4_e5' }), 'a1b2-c3.d4_e5')
  assert.equal(parseLaunchAgentId({ agent: '9f8e7d6c-1234-5678-90ab-cdef01234567' }), '9f8e7d6c-1234-5678-90ab-cdef01234567')
  assert.equal(parseLaunchAgentId({ agent: '' }), null)
  assert.equal(parseLaunchAgentId({ agent: '   ' }), null)
  assert.equal(parseLaunchAgentId({ agent: '../../etc/passwd' }), null)
  assert.equal(parseLaunchAgentId({ agent: 'x'.repeat(65) }), null)
  assert.equal(parseLaunchAgentId({ agent: 42 }), null)
  assert.equal(parseLaunchAgentId({}), null)
  assert.equal(parseLaunchAgentId(null), null)
  assert.equal(parseLaunchAgentId(undefined), null)
})

test('withLaunchAgent 保留原有 query 并挂上 agent', () => {
  const query = withLaunchAgent({ q: '分析一下 600519' }, 'builtin-halo')
  assert.equal(query[LAUNCH_AGENT_PARAM], 'builtin-halo')
  assert.equal(query.q, '分析一下 600519')
  // 不改原对象：调用方往往把同一个 query 字面量复用给多个落点。
  assert.equal(withLaunchAgent({ q: 'x' }, 'builtin-halo').q, 'x')
})

test('拿不到 agent 时原样返回，不往 URL 里塞坏 id', () => {
  const base = { q: 'x' }
  assert.deepEqual(withLaunchAgent(base, ''), base)
  assert.deepEqual(withLaunchAgent(base, '   '), base)
  assert.deepEqual(withLaunchAgent(base, null), base)
  assert.deepEqual(withLaunchAgent(base, undefined), base)
  assert.deepEqual(withLaunchAgent(base, 'has space'), base)
  assert.equal(LAUNCH_AGENT_PARAM in withLaunchAgent(base, 'has space'), false)
})

test('写进去的值一定读得回来', () => {
  for (const id of ['builtin-halo', 'builtin-zettaranc', '9f8e7d6c-1234-5678-90ab-cdef01234567']) {
    assert.equal(parseLaunchAgentId(withLaunchAgent({}, id)), id)
  }
})
