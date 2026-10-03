import assert from 'node:assert/strict'
import { test } from 'node:test'

import { stripMarkdownToPreview, toRelativeTime } from './workbenchFormat.ts'

test('stripMarkdownToPreview 剥掉 markdown 结构只留可读正文', () => {
  assert.equal(stripMarkdownToPreview('# 标题\n\n正文一段。'), '标题 正文一段。')
  assert.equal(stripMarkdownToPreview('**加粗**与*斜体*'), '加粗与斜体')
  assert.equal(stripMarkdownToPreview('- 第一项\n- 第二项'), '第一项 第二项')
  assert.equal(stripMarkdownToPreview('> 引用行'), '引用行')
  assert.equal(stripMarkdownToPreview('`inline code`'), 'inline code')
  assert.equal(stripMarkdownToPreview('~~删除~~'), '删除')
  assert.equal(stripMarkdownToPreview('[链接文字](https://example.com)'), '链接文字')
  assert.equal(stripMarkdownToPreview('![图注](a.png)'), '图注')
  assert.equal(stripMarkdownToPreview('~~~\n<em>raw html</em>\n~~~'.replace(/~~~[\s\S]*?~~~/g, ' ')), '')
})

test('stripMarkdownToPreview 丢弃代码围栏（工具 JSON 不进预览）', () => {
  const raw = '结论如下：\n\n```json\n{"tool":"search","rows":42}\n```\n\n以上。'
  assert.equal(stripMarkdownToPreview(raw), '结论如下： 以上。')
})

test('stripMarkdownToPreview 超长截断并加省略号', () => {
  const out = stripMarkdownToPreview('啊'.repeat(200), 10)
  assert.equal(out.length, 11)
  assert.ok(out.endsWith('…'))
})

test('stripMarkdownToPreview 空输入安全', () => {
  assert.equal(stripMarkdownToPreview(''), '')
  assert.equal(stripMarkdownToPreview('   \n  '), '')
})

const t = (key: string) =>
  ({
    'createChat.workbench.minutesAgo': '{n} 分钟前',
    'createChat.workbench.hoursAgo': '{n} 小时前',
    'createChat.workbench.yesterday': '昨天',
    'createChat.workbench.daysAgo': '{n} 天前',
  })[key] ?? key

const NOW = new Date('2026-10-03T12:00:00Z').getTime()
const iso = (offsetMs: number) => new Date(NOW - offsetMs).toISOString()

test('toRelativeTime 分钟 / 小时档', () => {
  assert.equal(toRelativeTime(iso(5 * 60_000), t, NOW), '5 分钟前')
  assert.equal(toRelativeTime(iso(3 * 3_600_000), t, NOW), '3 小时前')
})

test('toRelativeTime 昨天与天数档', () => {
  assert.equal(toRelativeTime(iso(26 * 3_600_000), t, NOW), '昨天')
  assert.equal(toRelativeTime(iso(3 * 86_400_000), t, NOW), '3 天前')
})

test('toRelativeTime 更早回落为日期（YYYY-MM-DD）', () => {
  const out = toRelativeTime(iso(30 * 86_400_000), t, NOW)
  assert.match(out, /^\d{4}-\d{2}-\d{2}$/)
})

test('toRelativeTime 非法输入返回空串而不是 Invalid Date', () => {
  assert.equal(toRelativeTime('', t, NOW), '')
  assert.equal(toRelativeTime('not-a-date', t, NOW), '')
})

test('toRelativeTime 未来时间不渲染', () => {
  assert.equal(toRelativeTime(new Date(NOW + 60_000).toISOString(), t, NOW), '')
})

test('toRelativeTime 不足一分钟按 1 分钟计，不显示 0 分钟前', () => {
  assert.equal(toRelativeTime(iso(1000), t, NOW), '1 分钟前')
})
