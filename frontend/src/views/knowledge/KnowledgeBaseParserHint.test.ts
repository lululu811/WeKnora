import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

// 本 fork 把 KB 头部拆成了 detail/KBDetailHeader.vue，提示标记住在那里，
// 页面本身只管状态与数据 —— 与 KnowledgeBaseFolderCounts 测试是同一处适配。
const source = readFileSync(new URL('./detail/KBDetailHeader.vue', import.meta.url), 'utf8')
const condition = source.match(/<p v-if="([^"]+)" class="parser-hint"/)?.[1]

function shouldShowParserHint(canEdit: boolean, unsupportedFileTypes: string[]): boolean {
  assert.ok(condition, 'parser hint has a render condition')
  return Boolean(new Function('canEdit', 'unsupportedFileTypes', `return ${condition}`)(canEdit, unsupportedFileTypes))
}

test('read-only users do not see the parser-engine warning (#4486)', () => {
  assert.equal(shouldShowParserHint(false, ['pdf']), false)
})

test('editable users still see the parser-engine warning when needed (#4486)', () => {
  assert.equal(shouldShowParserHint(true, ['pdf']), true)
})
