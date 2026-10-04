import assert from 'node:assert/strict'
import test from 'node:test'

import {
  HALO_ANALYZE_TOOL,
  buildHaloAskPrompt,
  haloAskAvailable,
} from './haloAskPresets.ts'

// 门控：白名单里没有 halo.analyze 就不给这些问法。
// 给 Z哥（白名单里没有 halo.*）显示 HALO 问法，等于给用户一个必定失败的动作。
test('haloAskAvailable 只认白名单里的 halo.analyze', () => {
  assert.equal(haloAskAvailable([HALO_ANALYZE_TOOL, 'halo.verify']), true)
  assert.equal(haloAskAvailable(['hithink.finance.query.sql', 'zettaranc.analyze']), false)
  assert.equal(haloAskAvailable([]), false)
  assert.equal(haloAskAvailable(undefined), false)
  assert.equal(haloAskAvailable(null), false)
  // 名字相近但不是它 —— 早期 builtin-halo 的白名单里真出现过 `hittink.` 这种错拼。
  assert.equal(haloAskAvailable(['halo.analyse']), false)
})

const CTX = { thscode: '688111.SH', name: '金山办公' }

test('buildHaloAskPrompt 带上标的与要点的工具', () => {
  for (const kind of ['six', 'seven', 'governance'] as const) {
    const p = buildHaloAskPrompt(kind, CTX)
    assert.match(p, /金山办公\(688111\.SH\)/, `${kind} 没带上标的`)
    assert.match(p, /halo\./, `${kind} 没点名要用的工具`)
  }
  assert.match(buildHaloAskPrompt('six', CTX), /halo\.analyze/)
  assert.match(buildHaloAskPrompt('seven', CTX), /halo\.verify/)
  assert.match(buildHaloAskPrompt('governance', CTX), /halo\.filing\.query/)
})

test('buildHaloAskPrompt 缺名字时只给代码', () => {
  const p = buildHaloAskPrompt('six', { thscode: '688111.SH', name: null })
  assert.match(p, /688111\.SH/)
  assert.doesNotMatch(p, /null|undefined/)
})

// 这些 prompt 直接进 chat，而 chat 每轮有回答长度上限（4096 token）。
// 让 agent 在 chat 里重写一遍完整报告会被截断，也不是数据层算出来的东西 ——
// 所以「六维」这条必须明确要求控制篇幅。
test('六维问法要求控制篇幅，完整报告指向面板', () => {
  const p = buildHaloAskPrompt('six', CTX)
  assert.match(p, /不要展开成篇报告/)
  assert.match(p, /面板/)
})
