import assert from 'node:assert/strict'
import test from 'node:test'

import {
  areAllGroupsCollapsed,
  ensureActiveGroupExpanded,
  readCollapsedGroupsFromStorage,
  toggleAllGroupKeys,
  toggleGroupKey,
  writeCollapsedGroupsToStorage,
  COLLAPSED_GROUPS_STORAGE_KEY,
} from './sessionGroupCollapse.ts'

function mockStorage(initial: Record<string, string> = {}): Storage {
  const store = new Map<string, string>(Object.entries(initial))
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, val: string) => store.set(key, val),
    removeItem: (key: string) => store.delete(key),
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    length: store.size,
  }
}

test('readCollapsedGroupsFromStorage returns empty set when storage empty or null', () => {
  const storage = mockStorage()
  assert.deepEqual(Array.from(readCollapsedGroupsFromStorage(storage)), [])
})

test('readCollapsedGroupsFromStorage parses valid JSON array', () => {
  const storage = mockStorage({
    [COLLAPSED_GROUPS_STORAGE_KEY]: JSON.stringify(['agent-a', 'agent-b']),
  })
  const set = readCollapsedGroupsFromStorage(storage)
  assert.equal(set.has('agent-a'), true)
  assert.equal(set.has('agent-b'), true)
  assert.equal(set.has('agent-c'), false)
})

test('readCollapsedGroupsFromStorage handles corrupt JSON gracefully', () => {
  const storage = mockStorage({
    [COLLAPSED_GROUPS_STORAGE_KEY]: 'not-json',
  })
  assert.deepEqual(Array.from(readCollapsedGroupsFromStorage(storage)), [])
})

test('writeCollapsedGroupsToStorage serializes set to JSON array', () => {
  const storage = mockStorage()
  writeCollapsedGroupsToStorage(new Set(['pinned', 'agent-1']), storage)
  const raw = storage.getItem(COLLAPSED_GROUPS_STORAGE_KEY)
  assert.ok(raw)
  const parsed = JSON.parse(raw!)
  assert.deepEqual(parsed.sort(), ['agent-1', 'pinned'])
})

test('toggleGroupKey toggles membership in set', () => {
  const set = new Set(['agent-a'])
  const afterAdd = toggleGroupKey(set, 'agent-b')
  assert.equal(afterAdd.has('agent-a'), true)
  assert.equal(afterAdd.has('agent-b'), true)

  const afterRemove = toggleGroupKey(afterAdd, 'agent-a')
  assert.equal(afterRemove.has('agent-a'), false)
  assert.equal(afterRemove.has('agent-b'), true)
})

test('areAllGroupsCollapsed correctly checks if all keys present', () => {
  const allKeys = ['agent-a', 'agent-b']
  assert.equal(areAllGroupsCollapsed(new Set(), allKeys), false)
  assert.equal(areAllGroupsCollapsed(new Set(['agent-a']), allKeys), false)
  assert.equal(areAllGroupsCollapsed(new Set(['agent-a', 'agent-b']), allKeys), true)
  assert.equal(areAllGroupsCollapsed(new Set(['agent-a', 'agent-b', 'extra']), allKeys), true)
})

test('toggleAllGroupKeys collapses all if any expanded, and expands all if all collapsed', () => {
  const allKeys = ['agent-a', 'agent-b', 'agent-c']

  // Partially collapsed -> collapse all
  const next1 = toggleAllGroupKeys(new Set(['agent-a']), allKeys)
  assert.deepEqual(Array.from(next1).sort(), ['agent-a', 'agent-b', 'agent-c'])

  // All collapsed -> expand all (empty set)
  const next2 = toggleAllGroupKeys(new Set(allKeys), allKeys)
  assert.deepEqual(Array.from(next2), [])

  // All expanded -> collapse all
  const next3 = toggleAllGroupKeys(new Set(), allKeys)
  assert.deepEqual(Array.from(next3).sort(), ['agent-a', 'agent-b', 'agent-c'])
})

test('ensureActiveGroupExpanded uncollapses group containing activeSessionPath', () => {
  const groups = [
    { key: 'agent-a', items: [{ path: 'chat/1' }, { path: 'chat/2' }] },
    { key: 'agent-b', items: [{ path: 'chat/3' }] },
  ]
  const collapsed = new Set(['agent-a', 'agent-b'])

  const res = ensureActiveGroupExpanded(collapsed, groups, 'chat/2')
  assert.equal(res.changed, true)
  assert.equal(res.collapsed.has('agent-a'), false)
  assert.equal(res.collapsed.has('agent-b'), true)
})

test('ensureActiveGroupExpanded uncollapses group containing revealedId', () => {
  const groups = [
    { key: 'agent-a', items: [{ id: 's1' }] },
    { key: 'agent-b', items: [{ id: 's2' }] },
  ]
  const collapsed = new Set(['agent-a', 'agent-b'])

  const res = ensureActiveGroupExpanded(collapsed, groups, undefined, 's2')
  assert.equal(res.changed, true)
  assert.equal(res.collapsed.has('agent-a'), true)
  assert.equal(res.collapsed.has('agent-b'), false)
})

test('ensureActiveGroupExpanded returns changed=false when target group already expanded', () => {
  const groups = [
    { key: 'agent-a', items: [{ path: 'chat/1' }] },
  ]
  const collapsed = new Set(['agent-b'])

  const res = ensureActiveGroupExpanded(collapsed, groups, 'chat/1')
  assert.equal(res.changed, false)
  assert.equal(res.collapsed.has('agent-b'), true)
})
