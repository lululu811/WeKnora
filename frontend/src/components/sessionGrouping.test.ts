import assert from 'node:assert/strict'
import test from 'node:test'

import {
  API_EXTERNAL_USER_SESSION_OWNER_PREFIX,
  DEFAULT_SESSION_GROUP_MODE,
  EMBED_SESSION_MARKER_PREFIX,
  classifyDateBucket,
  configuredPlatforms,
  groupSessions,
  groupSessionsByAgent,
  groupSessionsBySource,
  readStoredGroupMode,
  resolveSessionOrigin,
  shouldShowSessionSourceBadge,
  type SessionForGrouping,
} from './sessionGrouping.ts'

const sourceLabels = {
  web: 'Web',
  api: 'API',
  embedFallback: 'Embed',
  embedChannel: (id: string) => `Embed ${id}`,
  imPlatform: (p: string) => `IM ${p}`,
}

test('resolveSessionOrigin distinguishes web, IM, and embed sessions', () => {
  assert.deepEqual(resolveSessionOrigin({ id: '1' }), { kind: 'web' })
  assert.deepEqual(resolveSessionOrigin({ id: '2', im_platform: 'feishu' }), {
    kind: 'im',
    platform: 'feishu',
  })
  assert.deepEqual(
    resolveSessionOrigin({
      id: '3',
      description: `${EMBED_SESSION_MARKER_PREFIX}ch-1`,
    }),
    { kind: 'embed', channelId: 'ch-1' },
  )
  assert.deepEqual(
    resolveSessionOrigin({ id: '4', user_id: 'api_tenant_key:1:10' }),
    { kind: 'api' },
  )
  assert.deepEqual(
    resolveSessionOrigin({ id: '5', user_id: `${API_EXTERNAL_USER_SESSION_OWNER_PREFIX}1:alice` }),
    { kind: 'api' },
  )
})

test('configuredPlatforms returns distinct platform keys in first-seen order', () => {
  const channels = [
    { platform: 'feishu' },
    { platform: 'wecom' },
    { platform: 'feishu' },
    { platform: '' },
  ]
  assert.deepEqual(configuredPlatforms(channels), ['feishu', 'wecom'])
})

test('groupSessionsBySource orders web, configured IM, then embed channels', () => {
  const sessions = [
    { id: 'w', title: 'web chat' },
    { id: 'f', title: 'feishu', im_platform: 'feishu' },
    { id: 'e', title: 'embed', description: `${EMBED_SESSION_MARKER_PREFIX}ec-1` },
  ]
  const groups = groupSessionsBySource(sessions, sourceLabels, { 'ec-1': 'Help widget' }, ['feishu'], 'Pinned')
  assert.deepEqual(
    groups.map((g) => g.key),
    ['web', 'im:feishu', 'embed:ec-1'],
  )
  assert.equal(groups[2]?.label, 'Help widget')
})

test('groupSessions date mode keeps pinned sessions in their own bucket', () => {
  const now = new Date().toISOString()
  const groups = groupSessions(
    'date',
    [
      { id: 'p', is_pinned: true, updated_at: now },
      { id: 't', updated_at: now },
    ],
    {
      pinnedLabel: 'Pinned',
      bucketLabels: {
        pinned: 'Pinned',
        today: 'Today',
        yesterday: 'Yesterday',
        last7Days: '7d',
        last30Days: '30d',
        lastYear: 'Year',
        earlier: 'Earlier',
      },
      categorizeDate: () => 'today',
      sourceLabels,
      embedChannelNames: {},
      configuredImPlatforms: [],
    },
  )
  assert.deepEqual(
    groups.map((g) => g.key),
    ['pinned', 'today'],
  )
})

test('classifyDateBucket buckets recent sessions as today', () => {
  assert.equal(classifyDateBucket(new Date().toISOString()), 'today')
})

test('shouldShowSessionSourceBadge is off when channels use separate folders', () => {
  assert.equal(shouldShowSessionSourceBadge('date'), false)
  assert.equal(shouldShowSessionSourceBadge('none'), false)
})

// ---------------------------------------------------------------------------
// groupSessionsByAgent —— 侧栏默认按智能体分组
// ---------------------------------------------------------------------------

type AgentRow = SessionForGrouping & { updated_at: string }

function agentRow(
  id: string,
  agentId: string | undefined,
  updatedAt: string,
  pinned = false,
): AgentRow {
  return { id, agent_id: agentId, updated_at: updatedAt, is_pinned: pinned, created_at: updatedAt }
}

const agentLabels = {
  'builtin-zettaranc': 'Z哥',
  'builtin-quick-answer': 'Quick Answer',
}

test('agent mode: 按 agent 分组并使用展示名', () => {
  const groups = groupSessionsByAgent(
    [
      agentRow('a1', 'builtin-zettaranc', '2026-09-27T10:00:00Z'),
      agentRow('a2', 'builtin-quick-answer', '2026-09-27T11:00:00Z'),
      agentRow('a3', 'builtin-zettaranc', '2026-09-27T12:00:00Z'),
    ],
    agentLabels,
    '未指定智能体',
    '已置顶',
  )
  assert.deepEqual(groups.map((g) => g.label), ['Z哥', 'Quick Answer'])
  assert.deepEqual(groups.map((g) => g.items.length), [2, 1])
})

test('agent mode: 组内按更新时间倒序', () => {
  const groups = groupSessionsByAgent(
    [
      agentRow('old', 'builtin-zettaranc', '2026-09-20T10:00:00Z'),
      agentRow('new', 'builtin-zettaranc', '2026-09-27T10:00:00Z'),
      agentRow('mid', 'builtin-zettaranc', '2026-09-25T10:00:00Z'),
    ],
    agentLabels,
    '未指定智能体',
    '已置顶',
  )
  assert.deepEqual(groups[0].items.map((i) => i.id), ['new', 'mid', 'old'])
})

test('agent mode: 组间按会话数倒序', () => {
  const groups = groupSessionsByAgent(
    [
      agentRow('z1', 'builtin-quick-answer', '2026-09-27T10:00:00Z'),
      agentRow('z2', 'builtin-quick-answer', '2026-09-27T10:00:00Z'),
      agentRow('z3', 'builtin-quick-answer', '2026-09-27T10:00:00Z'),
      agentRow('y1', 'builtin-zettaranc', '2026-09-27T10:00:00Z'),
    ],
    agentLabels,
    '未指定智能体',
    '已置顶',
  )
  assert.deepEqual(groups.map((g) => g.label), ['Quick Answer', 'Z哥'])
})

test('agent mode: agent_id 缺失的会话不会被丢弃', () => {
  // 最容易写错的地方：静默过滤掉没有 agent_id 的会话，会让历史对话凭空消失。
  const groups = groupSessionsByAgent(
    [
      agentRow('withAgent', 'builtin-zettaranc', '2026-09-27T10:00:00Z'),
      agentRow('noAgent', undefined, '2026-09-27T10:00:00Z'),
      agentRow('emptyAgent', '   ', '2026-09-27T10:00:00Z'),
    ],
    agentLabels,
    '未指定智能体',
    '已置顶',
  )
  assert.equal(groups.reduce((sum, g) => sum + g.items.length, 0), 3)
  const fallback = groups.find((g) => g.label === '未指定智能体')
  assert.ok(fallback)
  assert.deepEqual(fallback!.items.map((i) => i.id).sort(), ['emptyAgent', 'noAgent'])
})

test('agent mode: 标签表缺项时回退展示 agent_id', () => {
  const groups = groupSessionsByAgent(
    [agentRow('a1', 'builtin-unknown-agent', '2026-09-27T10:00:00Z')],
    agentLabels,
    '未指定智能体',
    '已置顶',
  )
  assert.equal(groups[0].label, 'builtin-unknown-agent')
})

test('agent mode: 置顶会话独立成组且不重复出现', () => {
  const groups = groupSessionsByAgent(
    [
      agentRow('p1', 'builtin-zettaranc', '2026-09-27T10:00:00Z', true),
      agentRow('n1', 'builtin-zettaranc', '2026-09-27T10:00:00Z'),
    ],
    agentLabels,
    '未指定智能体',
    '已置顶',
  )
  assert.equal(groups[0].key, 'pinned')
  assert.equal(groups[0].label, '已置顶')
  assert.equal(groups[1].items.length, 1)
})

test('agent mode: 缺少时间戳不会抛错也不打乱排序', () => {
  const groups = groupSessionsByAgent(
    [
      { id: 'x1', agent_id: 'builtin-zettaranc' },
      agentRow('x2', 'builtin-zettaranc', '2026-09-27T10:00:00Z'),
    ],
    agentLabels,
    '未指定智能体',
    '已置顶',
  )
  assert.equal(groups[0].items.length, 2)
  assert.equal(groups[0].items[0].id, 'x2')
})

test('agent mode: 空输入返回空数组', () => {
  assert.deepEqual(groupSessionsByAgent([], agentLabels, '未指定智能体', '已置顶'), [])
})

test('默认分组模式是 agent', () => {
  assert.equal(DEFAULT_SESSION_GROUP_MODE, 'agent')
  assert.equal(readStoredGroupMode(), 'agent')
})

test('groupSessions 在 agent 模式下走新路径', () => {
  const grouped = groupSessions('agent', [agentRow('a1', 'builtin-zettaranc', '2026-09-27T10:00:00Z')], {
    pinnedLabel: '已置顶',
    bucketLabels: {} as never,
    categorizeDate: () => 'today',
    sourceLabels,
    embedChannelNames: {},
    configuredImPlatforms: [],
    agentLabels,
    noAgentLabel: '未指定智能体',
  })
  assert.deepEqual(grouped.map((g) => g.label), ['Z哥'])
})
