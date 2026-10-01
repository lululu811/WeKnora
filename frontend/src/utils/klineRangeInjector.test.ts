import assert from 'node:assert/strict'
import test from 'node:test'

import {
  injectKLineRanges,
  parseKLineRanges,
  KLINE_RANGE_FROM_ATTR,
  KLINE_RANGE_TO_ATTR,
  KLINE_RANGE_MD_ATTR,
} from './klineRangeInjector.ts'

test('识别带年份的完整日期', () => {
  const [r] = parseKLineRanges('在 2026-05-20 那天见顶')
  assert.ok(r, '应该解析出一个日期')
  assert.equal(new Date(r.from!).toISOString().slice(0, 10), '2026-05-20')
  assert.equal(r.to, undefined)
})

test('识别中文年月日与斜杠写法', () => {
  for (const text of ['2026年5月20日', '2026/5/20', '2026-05-20', '2026年05月20日']) {
    const [r] = parseKLineRanges(`在${text}见顶`)
    assert.ok(r, `${text} 应该被识别`)
    assert.equal(new Date(r.from!).toISOString().slice(0, 10), '2026-05-20', text)
  }
})

test('缺年份的「5月20日」只给 md，不给 from', () => {
  const [r] = parseKLineRanges('5月20日那天放量')
  assert.ok(r)
  assert.equal(r.md, '05-20')
  assert.equal(r.from, undefined)
})

test('解析日期区间并给出两端', () => {
  const [r] = parseKLineRanges('2026-05-20 至 2026-06-10 这段最强')
  assert.ok(r)
  assert.equal(new Date(r.from!).toISOString().slice(0, 10), '2026-05-20')
  assert.equal(new Date(r.to!).toISOString().slice(0, 10), '2026-06-10')
})

test('区间两端混写年份也认', () => {
  const [r] = parseKLineRanges('2026-05-20 到 6月10日')
  assert.ok(r)
  assert.equal(new Date(r.from!).toISOString().slice(0, 10), '2026-05-20')
  // 右端缺年份 -> 没有 to，交给图表侧就近定位
  assert.equal(r.to, undefined)
})

test('区间两端都缺年份时不合并为区间，退化成两个独立月日', () => {
  const found = parseKLineRanges('5月20日至6月10日这段最强')
  // 不能合成区间（无法确定年份），但两个日期各自仍可标注
  assert.equal(found.length, 2)
  assert.equal(found[0].md, '05-20')
  assert.equal(found[1].md, '06-10')
  assert.ok(found.every((r) => r.to === undefined), '不应产出区间终点')
})

test('拒绝不存在的日期', () => {
  assert.equal(parseKLineRanges('2026-02-30 发布').length, 0)
  assert.equal(parseKLineRanges('2026-13-01 发布').length, 0)
  assert.equal(parseKLineRanges('2026-00-10 发布').length, 0)
})

test('不从 8 位数字里截取日期片段', () => {
  // 20260927 是一个 8 位数字，不该被拆成 2026-09-27
  assert.equal(parseKLineRanges('交易日 20260927 收盘').length, 0)
})

test('不误伤普通数量词与比例', () => {
  for (const text of ['营收 1205 亿', '毛利率 45%', '第 3 季度', '成交 1234567 手']) {
    const found = parseKLineRanges(text)
    // 允许 0 个；不允许把上面任何一段解析成一个完整年月日区间
    for (const r of found) assert.ok(r.label.length <= 12, `${text} -> ${r.label}`)
  }
})

test('区间优先于单点：同一段文本不重复标注', () => {
  const found = parseKLineRanges('从 2026-05-20 到 2026-06-10 一路上涨')
  assert.equal(found.length, 1)
  assert.equal(found[0].to !== undefined, true)
})

test('多个互不重叠的日期各自产出', () => {
  const found = parseKLineRanges('2026-05-20 见顶，2026-06-10 见底')
  assert.equal(found.length, 2)
})

test('注入器产出带 data 属性的 span', () => {
  const html = injectKLineRanges('2026-05-20 至 2026-06-10 走强')
  assert.match(html, new RegExp(`class="kline-range"`))
  assert.match(html, new RegExp(`${KLINE_RANGE_FROM_ATTR}="`))
  assert.match(html, new RegExp(`${KLINE_RANGE_TO_ATTR}="`))
})

test('缺年份的日期注入 md 属性', () => {
  const html = injectKLineRanges('5月20日放量')
  assert.match(html, new RegExp(`${KLINE_RANGE_MD_ATTR}="05-20"`))
})

test('代码块内的日期不注入', () => {
  const md = '成交日期 2026-05-20：\n```\n2026-05-20\n```\n'
  const html = injectKLineRanges(md)
  const inline = injectKLineRanges('记 `2026-05-20` 这个日子')
  // 代码块内不应产生标记
  const fencePart = html.split('```')[1] ?? ''
  assert.ok(!fencePart.includes('kline-range'), 'fence 内不应注入')
  assert.ok(!inline.includes('kline-range'), 'inline code 内不应注入')
})

test('注入是单次扫描，不会嵌套 span', () => {
  const html = injectKLineRanges('2026-05-20 至 2026-06-10')
  const opens = (html.match(/<span /g) ?? []).length
  const closes = (html.match(/<\/span>/g) ?? []).length
  assert.equal(opens, closes)
  assert.equal(opens, 1)
})

test('空输入与无日期输入原样返回', () => {
  assert.equal(injectKLineRanges(''), '')
  assert.equal(injectKLineRanges('今天大盘不错'), '今天大盘不错')
})

// ---------------------------------------------------------------------------
// 不能改写 HTML 标签内部。
//
// 这是个**真实缺陷**，不是假想：日期正则会匹配属性值里的日期，把 span 塞进
// 属性中间，标签直接损坏。既有引用标签已经中招：
//   <web url="..." title="2026-05-20 复盘"/>  ->  title=<span ...>&quot;2026-05-20</span>复盘"
// 而新的锚点语法把日期放在 from/to 属性里，不修这条就没法用。
// ---------------------------------------------------------------------------

test('不改写标签属性里的日期（否则打断标签）', () => {
  const src = '<anchor kind="range" from="2026-05-20" to="2026-06-10" label="第一波"/>'
  assert.equal(injectKLineRanges(src), src, '标签内部一个字都不该动')
})

test('不改写既有引用标签 title 里的日期', () => {
  const src = '<web url="https://example.com/x" title="2026-05-20 复盘"/>'
  assert.equal(injectKLineRanges(src), src)
})

test('标签外面的日期照常标注（修复不能把功能一起关掉）', () => {
  const out = injectKLineRanges('见 <b>2026-05-20</b> 那天')
  assert.match(out, /kline-range/)
  assert.match(out, /<b>/)  // 标签本身保持原样
})

test('自闭合与带属性的标签都保护', () => {
  for (const src of ['<br/>', '<span class="x">', '</span>', '<anchor kind="level" value="72.4"/>']) {
    assert.equal(injectKLineRanges(src), src, src)
  }
})

test('普通小于号不是标签，不该被当成标签跳过', () => {
  // `a < b` 后面跟着日期时，日期仍然要标
  const out = injectKLineRanges('涨了 a < b 2026-05-20')
  assert.match(out, /kline-range/)
})
