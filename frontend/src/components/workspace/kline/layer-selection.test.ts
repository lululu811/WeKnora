import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SELECTION_ALL,
  isOptionEnabled,
  toggleOption,
  selectAllOptions,
  clearAllOptions,
  enabledCount,
  filterBySelection,
  isAllSelected,
  type LayerOption,
} from './layer-selection.ts'

const OPTS: LayerOption[] = [
  { value: '双顶', label: '双顶' },
  { value: '下降楔形', label: '下降楔形' },
  { value: '熊市旗形', label: '熊市旗形' },
  { value: '调整浪 A-B-C', label: '调整浪 A-B-C' },
]

// ---------------------------------------------------------------------------
// 这层的核心契约只有一条：**没被记过的项默认可见**。
// 存反了（存启用集）的后果是「识别出来了却不画」，且没有任何报错。
// ---------------------------------------------------------------------------

test('默认全选：空禁用集里每一项都可见', () => {
  for (const o of OPTS) assert.equal(isOptionEnabled(SELECTION_ALL, o.value), true)
  assert.equal(enabledCount(OPTS, SELECTION_ALL), 4)
  assert.equal(isAllSelected(OPTS, SELECTION_ALL), true)
})

test('没被记过的项默认可见（新增形态不会被静默藏起来）', () => {
  const sel = { disabled: ['双顶'] }
  assert.equal(isOptionEnabled(sel, '头肩底'), true, '用户从没对「头肩底」表过态')
})

test('toggle 翻转并保留其余项', () => {
  let sel = toggleOption(SELECTION_ALL, '熊市旗形')
  assert.deepEqual(sel.disabled, ['熊市旗形'])
  assert.equal(enabledCount(OPTS, sel), 3)

  sel = toggleOption(sel, '熊市旗形')
  assert.deepEqual(sel.disabled, [], '再点一次回到可见')
  assert.equal(enabledCount(OPTS, sel), 4)
})

test('toggle 不修改原对象', () => {
  const before = { disabled: ['双顶'] }
  const after = toggleOption(before, '下降楔形')
  assert.deepEqual(before.disabled, ['双顶'], '原状态应保持不变')
  assert.deepEqual(after.disabled, ['双顶', '下降楔形'])
})

test('全不选只关当前已知的项，之后出现的新名字仍然可见', () => {
  const sel = clearAllOptions(OPTS)
  assert.equal(enabledCount(OPTS, sel), 0)
  assert.equal(isAllSelected(OPTS, sel), false)
  // 这是**有意**的边界：用户没对「头肩底」表过态。
  assert.equal(isOptionEnabled(sel, '头肩底'), true)
})

test('全选把禁用集清空', () => {
  assert.deepEqual(selectAllOptions(), { disabled: [] })
})

test('空图（没有可选形态）算全选，按钮不显示歧义态', () => {
  assert.equal(isAllSelected([], { disabled: ['双顶'] }), true)
  assert.equal(enabledCount([], { disabled: ['双顶'] }), 0)
})

test('filterBySelection 按 value 过滤，保留原始顺序', () => {
  const items = OPTS.map((o) => ({ value: o.value, extra: o.label.length }))
  const kept = filterBySelection(items, { disabled: ['下降楔形', '调整浪 A-B-C'] })
  assert.deepEqual(kept.map((k) => k.value), ['双顶', '熊市旗形'])
})

test('计数与可见项一致，可直接喂给按钮的 (n/m)', () => {
  const sel = toggleOption(toggleOption(SELECTION_ALL, '双顶'), '熊市旗形')
  assert.equal(enabledCount(OPTS, sel), 2)
  assert.equal(OPTS.length, 4)
})
