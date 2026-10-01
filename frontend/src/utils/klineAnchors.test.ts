import assert from 'node:assert/strict'
import test from 'node:test'

import {
  findAnchors,
  injectKLineAnchors,
  stripIncompleteAnchorTag,
  KLINE_ANCHOR_INDEX_ATTR,
} from './klineAnchors.ts'

test('解析 range 锚点', () => {
  const [a] = findAnchors('<anchor kind="range" from="2026-05-20" to="2026-06-10" label="第一波"/>')
  assert.ok(a)
  assert.equal(a.kind, 'range')
  assert.equal(a.from, '2026-05-20')
  assert.equal(a.to, '2026-06-10')
  assert.equal(a.label, '第一波')
  assert.equal(a.valid, true)
  assert.equal(a.index, 1)
})

test('解析 level 锚点', () => {
  const [a] = findAnchors('<anchor kind="level" value="72.4" label="第一目标"/>')
  assert.equal(a.kind, 'level')
  assert.equal(a.value, 72.4)
  assert.equal(a.valid, true)
})

test('编号按正文出现顺序，从 1 开始', () => {
  const found = findAnchors(
    '先 <anchor kind="level" value="72.4" label="目标"/> 再 <anchor kind="range" from="2026-05-20" to="2026-06-10" label="第一波"/>',
  )
  assert.equal(found.length, 2)
  assert.deepEqual(found.map((a) => a.index), [1, 2])
  assert.equal(found[0].label, '目标')
  assert.equal(found[1].label, '第一波')
})

test('日期归一：斜杠写法与单位数补零', () => {
  const [a] = findAnchors('<anchor kind="range" from="2026/5/2" to="2026-06-10" label="x"/>')
  assert.equal(a.from, '2026-05-02')
})

test('to 缺失时退化成单日区间', () => {
  const [a] = findAnchors('<anchor kind="range" from="2026-05-20" label="x"/>')
  assert.equal(a.to, '2026-05-20')
  assert.equal(a.valid, true)
})

test('无效日期 -> valid=false，但不吞内容', () => {
  const [a] = findAnchors('<anchor kind="range" from="2026-02-30" to="2026-03-01" label="假日期"/>')
  assert.equal(a.valid, false)
  assert.equal(a.label, '假日期')
})

test('from > to -> 无效', () => {
  const [a] = findAnchors('<anchor kind="range" from="2026-06-10" to="2026-05-20" label="倒置"/>')
  assert.equal(a.valid, false)
})

test('未知 kind -> 无效（宁可退化成文本，不猜）', () => {
  const [a] = findAnchors('<anchor kind="banana" label="怪东西"/>')
  assert.equal(a.valid, false)
})

test('level 的非正数/非数字 -> 无效', () => {
  assert.equal(findAnchors('<anchor kind="level" value="0" label="x"/>')[0].valid, false)
  assert.equal(findAnchors('<anchor kind="level" value="-3" label="x"/>')[0].valid, false)
  assert.equal(findAnchors('<anchor kind="level" value="abc" label="x"/>')[0].valid, false)
})

test('注入：有效锚点变成带编号的 span', () => {
  const html = injectKLineAnchors('<anchor kind="range" from="2026-05-20" to="2026-06-10" label="第一波"/>')
  assert.match(html, /class="kline-anchor"/)
  assert.match(html, new RegExp(`${KLINE_ANCHOR_INDEX_ATTR}="1"`))
  assert.match(html, /data-anchor-from="2026-05-20"/)
  assert.match(html, /data-anchor-to="2026-06-10"/)
  assert.match(html, />第一波</)
})

test('注入：语法本身绝不露出来', () => {
  const html = injectKLineAnchors('看 <anchor kind="level" value="72.4" label="第一目标"/> 这里')
  // 判据是「没有残留的 anchor 标签」，而不是「没有 kind= 这个子串」——
  // 生成出来的 span 上就有 data-anchor-kind="level"，按子串判会误报。
  assert.ok(!/<anchor\b/i.test(html), `不该残留标签：${html}`)
  // 外层是锚点、内层是编号徽章——两层都是我们生成的，结构确定。
  assert.match(html, /看 <span class="kline-anchor"[^>]*><span class="kline-anchor__num"[^>]*>1<\/span>第一目标<\/span> 这里/)
})

test('注入：无效锚点只渲染 label 纯文本（R5b 不吞字）', () => {
  const html = injectKLineAnchors('前面 <anchor kind="range" from="2026-02-30" label="第一波"/> 后面')
  assert.match(html, /data-anchor-valid="0"/)
  assert.match(html, />第一波</, '内容必须还在，否则句子读不通')
  assert.ok(!html.includes('<anchor'))
})

test('注入：label 缺失时给兜底文案，不留空', () => {
  const html = injectKLineAnchors('<anchor kind="level" value="72.4"/>')
  assert.match(html, />72\.4</)
})

test('代码块与行内代码里的锚点不解析', () => {
  const fenced = '```\n<anchor kind="level" value="72.4" label="x"/>\n```\n'
  assert.equal(findAnchors(fenced).length, 0)
  const inline = '记 `<anchor kind="level" value="72.4" label="x"/>` 这个'
  assert.equal(findAnchors(inline).length, 0)
})

test('HTML 标签属性里的锚点不解析（避免嵌套损坏）', () => {
  const src = '<web url="https://x" title="<anchor kind=level value=1 label=y/>"/>'
  assert.equal(findAnchors(src).length, 0)
})

test('单次扫描：每个锚点恰好一对 span，且开闭配平', () => {
  const html = injectKLineAnchors(
    '<anchor kind="level" value="72.4" label="A"/> 中间 <anchor kind="level" value="68.5" label="B"/>',
  )
  // 两个锚点各一个外层 span（编号徽章是它内部固定的一层）
  assert.equal((html.match(/class="kline-anchor"/g) || []).length, 2)
  assert.equal((html.match(/class="kline-anchor__num"/g) || []).length, 2)
  // 真正的不变量：开闭配平，没有半截标签
  assert.equal((html.match(/<span /g) || []).length, (html.match(/<\/span>/g) || []).length)
})

test('label 里的引号与尖括号被转义，不破坏标签', () => {
  const html = injectKLineAnchors('<anchor kind="level" value="72.4" label="a&quot;b"/>')
  assert.ok(!/<span[^>]*data-anchor-label="a"b"/.test(html), '引号不该裸奔进属性')
  // 外层锚点 + 内层编号，恰好两层
  assert.equal((html.match(/<span /g) || []).length, 2)
  assert.equal((html.match(/<span /g) || []).length, (html.match(/<\/span>/g) || []).length)
})

test('无锚点时原样返回', () => {
  assert.equal(injectKLineAnchors('普通一段话'), '普通一段话')
  assert.equal(injectKLineAnchors(''), '')
})

test('流式截断：半截锚点标签被切掉', () => {
  assert.equal(stripIncompleteAnchorTag('前文 <a'), '前文 ')
  assert.equal(stripIncompleteAnchorTag('前文 <an'), '前文 ')
  assert.equal(stripIncompleteAnchorTag('前文 <anchor kind="ra'), '前文 ')
  assert.equal(stripIncompleteAnchorTag('前文 <anc'), '前文 ')
})

test('流式截断：完整标签与普通小于号不受影响', () => {
  const complete = '前文 <anchor kind="level" value="1" label="x"/> 后文'
  assert.equal(stripIncompleteAnchorTag(complete), complete)
  // `a < b` 不是锚点前缀，不该被切
  assert.equal(stripIncompleteAnchorTag('涨了 a < b'), '涨了 a < b')
  // 闭合标签同理
  assert.equal(stripIncompleteAnchorTag('前文 </sp'), '前文 </sp')
})

test('流式截断：`<a` 这类别的标签前缀不该被误切', () => {
  // `<article` 也以 <a 开头，但它不是锚点——只有严格等于 anchor 的各级前缀才切
  assert.equal(stripIncompleteAnchorTag('前文 <abbr'), '前文 <abbr')
  assert.equal(stripIncompleteAnchorTag('前文 <as'), '前文 <as')
})

// ---------------------------------------------------------------------------
// 与渲染管线的集成。既有的 chatMarkdownRenderer 测试都用 `sanitizeHtml: v => v`
// 绕过 DOMPurify，所以这一层能在 node 里测；「属性是否活过 DOMPurify」那一层
// 必须起浏览器，另有冒烟覆盖。
// ---------------------------------------------------------------------------

import { renderChatMarkdown } from './chatMarkdownRenderer.ts'
import { marked } from 'marked'

function render(text: string, streaming = false): string {
  return renderChatMarkdown(text, {
    renderer: new marked.Renderer(),
    escapeMarkdown: (s: string) => s,
    sanitizeHtml: (v: string) => v,
    streaming,
  } as never)
}

test('管线：锚点活过 marked，属性与编号都在', () => {
  const html = render('看 <anchor kind="range" from="2026-05-20" to="2026-06-10" label="第一波"/> 这段')
  assert.match(html, /class="kline-anchor"/)
  assert.match(html, /data-anchor-index="1"/)
  assert.match(html, /data-anchor-from="2026-05-20"/)
  assert.match(html, /第一波/)
  assert.ok(!/<anchor\b/i.test(html), '语法不该残留')
})

test('管线：锚点与 ticker、日期区间共存且互不嵌套', () => {
  const html = render(
    '贵州茅台(600519.SH) 在 <anchor kind="range" from="2026-05-20" to="2026-06-10" label="第一波"/> 走强，2026-07-01 见顶',
  )
  // 三种标记都在
  assert.match(html, /class="kline-anchor"/)
  assert.match(html, /class="kline-ticker"/)
  assert.match(html, /class="kline-range"/)
  // span 数量守恒（无嵌套错乱）
  assert.equal((html.match(/<span /g) || []).length, (html.match(/<\/span>/g) || []).length)
})

test('管线：锚点 label 里的日期不会被再包一层区间标记', () => {
  const html = render('<anchor kind="range" from="2026-05-20" to="2026-06-10" label="2026-05-20 第一波"/>')
  // 只数外层锚点类（`kline-anchor__num` 也含这个子串，不能按子串数）
  assert.equal((html.match(/class="kline-anchor"/g) || []).length, 1)
  assert.equal((html.match(/kline-range/g) || []).length, 0, `不该有嵌套区间标记：${html}`)
})

test('管线：无效锚点渲染成纯文本，不留语法', () => {
  const html = render('前面 <anchor kind="range" from="2026-02-30" label="第一波"/> 后面')
  assert.ok(!/<anchor\b/i.test(html))
  assert.match(html, /第一波/)
  assert.match(html, /data-anchor-valid="0"/)
})

test('管线：流式期间半截锚点不露出来', () => {
  const html = render('前面 <anchor kind="ra', true)
  assert.ok(!html.includes('anchor kind'), `不该露出半截标签：${html}`)
})

test('管线：代码块里的锚点原样保留，不被解析', () => {
  const html = render('```\n<anchor kind="level" value="72.4" label="x"/>\n```')
  assert.equal((html.match(/kline-anchor/g) || []).length, 0)
  assert.match(html, /&lt;anchor|<anchor/)
})

test('注入：正文里带上编号徽章（与图上编号对照的支点）', () => {
  const html = injectKLineAnchors('<anchor kind="level" value="72.4" label="第一目标"/>')
  assert.match(html, /class="kline-anchor__num"[^>]*>1</, `正文必须显示编号：${html}`)
  assert.match(html, />1<\/span>第一目标</)
})

test('注入：第二个锚点编号是 2', () => {
  const html = injectKLineAnchors(
    '<anchor kind="level" value="1" label="A"/> <anchor kind="level" value="2" label="B"/>',
  )
  assert.match(html, /class="kline-anchor__num"[^>]*>2</)
})

test('注入：无效锚点不显示编号（避免指向图上一个不存在的标记）', () => {
  const html = injectKLineAnchors('<anchor kind="level" value="0" label="坏的"/>')
  assert.ok(!html.includes('kline-anchor__num'), `无效锚点不该有编号：${html}`)
  assert.match(html, /坏的/)
})
