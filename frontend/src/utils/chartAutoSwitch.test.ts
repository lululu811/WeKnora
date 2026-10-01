import assert from 'node:assert/strict'
import test from 'node:test'

import { shouldAutoSwitchChart, isStreamedAnswer, type AutoSwitchContext } from './chartAutoSwitch.ts'
import { pickPrimaryMention } from './stockMentions.ts'

/** 一个"什么条件都满足"的基线，各用例只改其中一项。 */
function ctx(overrides: Partial<AutoSwitchContext> = {}): AutoSwitchContext {
  return {
    messageCompleted: true,
    panelOpen: true,
    userPickedThscode: null,
    alreadySwitchedForId: null,
    messageId: 'msg-1',
    ...overrides,
  }
}

test('条件齐备时允许自动切图', () => {
  assert.equal(shouldAutoSwitchChart(ctx()), true)
})

test('回答未完成时不切（流式期间主语会变，图位会抖）', () => {
  assert.equal(shouldAutoSwitchChart(ctx({ messageCompleted: false })), false)
})

test('面板未打开时不切（不替用户决定"我要看K线"）', () => {
  // 这条是 KLineStudioResult 自动开图缺陷的防线：面板关着就绝不动它。
  assert.equal(shouldAutoSwitchChart(ctx({ panelOpen: false })), false)
})

test('用户本轮手动选过就不切', () => {
  assert.equal(shouldAutoSwitchChart(ctx({ userPickedThscode: '000858.SZ' })), false)
})

test('同一条回答只切一次', () => {
  assert.equal(shouldAutoSwitchChart(ctx({ alreadySwitchedForId: 'msg-1' })), false)
  // 换了回答就重新允许
  assert.equal(
    shouldAutoSwitchChart(ctx({ alreadySwitchedForId: 'msg-1', messageId: 'msg-2' })),
    true,
  )
})

test('没有 messageId 时不切（无法去重，会反复触发）', () => {
  assert.equal(shouldAutoSwitchChart(ctx({ messageId: null })), false)
  assert.equal(shouldAutoSwitchChart(ctx({ messageId: undefined })), false)
  assert.equal(shouldAutoSwitchChart(ctx({ messageId: '' })), false)
})

test('守卫之间是「与」的关系：任一不满足都不切', () => {
  const bad: Array<Partial<AutoSwitchContext>> = [
    { messageCompleted: false },
    { panelOpen: false },
    { userPickedThscode: '600519.SH' },
    { alreadySwitchedForId: 'msg-1' },
    { messageId: null },
  ]
  for (const override of bad) {
    assert.equal(shouldAutoSwitchChart(ctx(override)), false, JSON.stringify(override))
  }
})

test('与 pickPrimaryMention 串起来：主标的判不出时整条链路不动作', () => {
  // 端到端地把两个模块接起来看：守卫放行 + 判不出主语 = 不动图。
  const allowed = shouldAutoSwitchChart(ctx())
  assert.equal(allowed, true)
  assert.equal(pickPrimaryMention('今天大盘表现一般，没有具体标的'), null)

  // 判得出主语时才有目标
  const primary = pickPrimaryMention('贵州茅台(600519.SH)今天走强')
  assert.equal(primary?.thscode, '600519.SH')
})

test('与 pickPrimaryMention 串起来：引出语开头时不猜，整条链路不动作', () => {
  assert.equal(shouldAutoSwitchChart(ctx()), true)
  assert.equal(pickPrimaryMention('说到白酒，最近走势不错'), null)
})

// ---------------------------------------------------------------------------
// isStreamedAnswer：把自动切图限制在「正在生成的那条回答」上。
// 这个判据写错的两种表现都是静默的——放宽了会在加载历史会话时把图切走，
// 收紧错了则联动永远不触发。
// ---------------------------------------------------------------------------

test('按行 id 匹配', () => {
  assert.equal(isStreamedAnswer({ role: 'assistant', id: 'm1' }, 'm1'), true)
})

test('按 assistant_message_id 匹配（行 id 是 request id 的那条路径）', () => {
  assert.equal(
    isStreamedAnswer({ role: 'assistant', id: 'req-1', assistant_message_id: 'm1' }, 'm1'),
    true,
  )
})

test('两个字段都不匹配时为 false', () => {
  assert.equal(isStreamedAnswer({ role: 'assistant', id: 'req-9' }, 'm1'), false)
})

test('非 assistant 行一律为 false', () => {
  assert.equal(isStreamedAnswer({ role: 'user', id: 'm1' }, 'm1'), false)
})

test('currentId 为空时一律为 false（会话刚加载，还没开始生成）', () => {
  assert.equal(isStreamedAnswer({ role: 'assistant', id: 'm1' }, ''), false)
  assert.equal(isStreamedAnswer({ role: 'assistant', id: 'm1' }, null), false)
  assert.equal(isStreamedAnswer({ role: 'assistant', id: 'm1' }, undefined), false)
})

test('空行 / 空值安全', () => {
  assert.equal(isStreamedAnswer(null, 'm1'), false)
  assert.equal(isStreamedAnswer(undefined, 'm1'), false)
  assert.equal(isStreamedAnswer({}, 'm1'), false)
})

test('历史会话里那条已完成的回答不会被当成"刚刚完成"', () => {
  // 加载历史时 currentAssistantMessageId 为空，所以任何历史行都不该命中。
  const history = [
    { role: 'assistant', id: 'h1', assistant_message_id: 'h1' },
    { role: 'assistant', id: 'h2', assistant_message_id: 'h2' },
  ]
  for (const row of history) {
    assert.equal(isStreamedAnswer(row, ''), false, `历史行 ${row.id} 不该命中`)
  }
})
