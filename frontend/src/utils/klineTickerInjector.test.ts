import assert from 'node:assert/strict'
import test from 'node:test'

import { inferAShareExchange, isBoardExchange, isPlausibleAShareCode } from './aShareTicker.ts'
import { injectKLineTickers } from './klineTickerInjector.ts'

/** 抽出注入结果里的 thscode 列表，按出现顺序。 */
function thscodes(markdown: string): string[] {
  return [...injectKLineTickers(markdown).matchAll(/data-thscode="([^"]+)"/g)].map((m) => m[1])
}

test('isBoardExchange 只认 .TI，且大小写不敏感', () => {
  assert.equal(isBoardExchange('TI'), true)
  assert.equal(isBoardExchange('ti'), true)
  assert.equal(isBoardExchange('SH'), false)
  assert.equal(isBoardExchange(''), false)
  // undefined 是"这一行没有 exchange 字段"，按个股处理而不是抛错
  assert.equal(isBoardExchange(undefined), false)
})

test('inferAShareExchange 按板块前缀判定交易所', () => {
  // 沪主板 / 科创板
  assert.equal(inferAShareExchange('600499'), 'SH')
  assert.equal(inferAShareExchange('601127'), 'SH')
  assert.equal(inferAShareExchange('688981'), 'SH')
  // 深主板 / 创业板
  assert.equal(inferAShareExchange('000001'), 'SZ')
  assert.equal(inferAShareExchange('002594'), 'SZ')
  assert.equal(inferAShareExchange('300750'), 'SZ')
  // 北交所 —— 旧实现 `startsWith('6') ? 'SH' : 'SZ'` 会全判成 SZ
  assert.equal(inferAShareExchange('430047'), 'BJ')
  assert.equal(inferAShareExchange('830799'), 'BJ')
  assert.equal(inferAShareExchange('871981'), 'BJ')
  assert.equal(inferAShareExchange('889999'), 'BJ')
  assert.equal(inferAShareExchange('920002'), 'BJ')
})

test('inferAShareExchange 对前缀不明确的代码返回 null', () => {
  // 20 开头会撞 YYYYMM 形态的日期，90/11/12/15 之类前缀不唯一 —— 一律不猜。
  assert.equal(inferAShareExchange('202609'), null)
  assert.equal(inferAShareExchange('900901'), null)
  assert.equal(inferAShareExchange('123456'), null)
  assert.equal(inferAShareExchange('100000'), null)
  // 非 6 位数字直接排除
  assert.equal(inferAShareExchange('60049'), null)
  assert.equal(inferAShareExchange('6004999'), null)
  assert.equal(inferAShareExchange(''), null)
  assert.equal(inferAShareExchange('60A499'), null)
})

test('isPlausibleAShareCode 与推断结果一致', () => {
  assert.equal(isPlausibleAShareCode('600499'), true)
  assert.equal(isPlausibleAShareCode('000592'), true)
  assert.equal(isPlausibleAShareCode('123456'), false)
})

test('注入器识别带交易所后缀的 ticker', () => {
  assert.deepEqual(thscodes('建议关注 600499.SH 和 000001.SZ'), ['600499.SH', '000001.SZ'])
  assert.deepEqual(thscodes('北交所 830799.BJ'), ['830799.BJ'])
  // 后缀大小写不敏感，输出统一大写
  assert.deepEqual(thscodes('600499.sh'), ['600499.SH'])
})

test('注入器识别裸 6 位码并按板块补出交易所', () => {
  // 这是「hover 卡片有的出不来」的主因：旧正则要求必须有后缀或括号。
  assert.deepEqual(thscodes('我看了 600499 这个位置'), ['600499.SH'])
  assert.deepEqual(thscodes('600499 是科达制造'), ['600499.SH'])
  assert.deepEqual(thscodes('买入600499.SH吧'), ['600499.SH'])
})

test('括号包裹的裸码按板块推断，而不是一律 .SH', () => {
  // 旧实现对 TICKER_BARE_PAREN_RE 硬编码 `${ticker}.SH`，
  // 导致深市票（000592 / 300750 / 002594）被绑到沪市代码上。
  assert.deepEqual(thscodes('平潭发展（000592）'), ['000592.SZ'])
  assert.deepEqual(thscodes('平潭发展(000592)'), ['000592.SZ'])
  assert.deepEqual(thscodes('宁德时代（300750）'), ['300750.SZ'])
  assert.deepEqual(thscodes('比亚迪（002594）'), ['002594.SZ'])
  // 括号本身保留
  assert.ok(injectKLineTickers('平潭发展（000592）').includes('（'))
  assert.ok(injectKLineTickers('平潭发展（000592）').includes('）'))
})

test('不误伤非 ticker 的数字', () => {
  // 8 位日期不能被取前 6 位
  assert.deepEqual(thscodes('今天是 20260927'), [])
  // 金额 / 订单号等前缀不属于任何板块
  assert.deepEqual(thscodes('成交额 123456 元'), [])
  assert.deepEqual(thscodes('订单 100000 号'), [])
  assert.deepEqual(thscodes('版本 202609 发布'), [])
  // 小数点后不能被切开
  assert.deepEqual(thscodes('价格 1.600499'), [])
})

// ---------------------------------------------------------------------------
// 非 A 股后缀必须被「消费掉」再交给判据，不能退化成裸码去猜交易所。
//
// 旧正则 `(?:\.(SH|SZ|BJ)\b)?` 只认三个后缀：遇到 `.TI`（同花顺板块/指数）或
// `.HK`（港股）时后缀组失配，正则只匹配到前 6 位数字，于是
// `resolveTickerThscode('881101', undefined)` 按前缀表把它判成 **BJ** ——
// 正文里点开的是不存在的 `881101.BJ`；`00700.HK` 同理被判成 `00700.SZ`。
// 而同一段文本在 stockMentions 里（CODE_RE 吃任意两个字母后缀）是被丢弃的：
// 两个入口对同一串数字给出相反结论，正是文件头警告的那类分裂。
// ---------------------------------------------------------------------------

test('板块/指数代码（.TI）不被截断成裸码乱猜交易所', () => {
  // 881101 以 88 开头，旧写法会命中 /^(43|83|87|88|92)/ 判成 BJ
  assert.deepEqual(thscodes('板块 881101.TI 今天走强'), [])
  assert.equal(injectKLineTickers('板块 881101.TI 今天走强'), '板块 881101.TI 今天走强')
  // 非 A 股后缀同理：00700 以 00 开头，旧写法会判成 SZ
  assert.deepEqual(thscodes('腾讯 00700.HK 的表现'), [])
})

test('未知后缀原样放行，不吞掉后面的文本', () => {
  assert.deepEqual(thscodes('600519.XX 是未知后缀'), [])
  assert.equal(injectKLineTickers('600519.XX'), '600519.XX')
  // 后缀后面的内容必须完整保留（`(?![A-Za-z0-9])` 保证不截断成两段）
  assert.equal(injectKLineTickers('600519.XX 和 600519.SH'), '600519.XX 和 '
    + '<span class="kline-ticker" data-thscode="600519.SH">600519.SH</span>')
})

test('后缀大小写与长度边界', () => {
  // 小写后缀仍按 A 股处理并归一成大写
  assert.deepEqual(thscodes('600499.sh'), ['600499.SH'])
  // 三个字母不是交易所后缀，整串应原样保留（数字也不该被单独标出来）
  assert.deepEqual(thscodes('600519.SHH'), [])
})

test('代码块内的 ticker 不注入', () => {
  assert.deepEqual(thscodes('```\n600499.SH\n```'), [])
  assert.deepEqual(thscodes('行内 `600499.SH` 代码'), [])
  // 但代码块外的正常 ticker 仍然注入
  assert.deepEqual(thscodes('看 `code` 这段，以及 600499.SH'), ['600499.SH'])
})

test('同一段文本里多种形式混排不会互相吞掉', () => {
  const out = injectKLineTickers('先看 600499，再看（000592），最后 300750.SZ')
  assert.deepEqual(thscodes('先看 600499，再看（000592），最后 300750.SZ'), [
    '600499.SH',
    '000592.SZ',
    '300750.SZ',
  ])
  // 单次扫描：不会出现嵌套 span
  assert.equal(out.split('<span').length - 1, 3)
  assert.equal(out.split('</span>').length - 1, 3)
})

test('单次扫描保证 span 内的数字不被二次包裹', () => {
  // 若分两轮 replace（先带后缀、再裸码），600499.SH 里的 600499 会被再包一层。
  const out = injectKLineTickers('600499.SH')
  assert.equal(out.indexOf('<span'), out.lastIndexOf('<span'))
  assert.equal(
    out,
    '<span class="kline-ticker" data-thscode="600499.SH">600499.SH</span>',
  )
})

// ---------------------------------------------------------------------------
// 与 klineRangeInjector 同一个缺陷：代码正则会匹配属性值里的数字，
// 把 span 塞进属性中间。`<kb doc="600519">` 这种引用标签会直接损坏。
// ---------------------------------------------------------------------------

test('不改写标签属性里的代码', () => {
  for (const src of ['<kb doc="600519" chunk_id="a"/>', '<web url="https://x?id=600519"/>']) {
    assert.equal(injectKLineTickers(src), src, src)
  }
})

test('标签外面的代码照常标注', () => {
  const out = injectKLineTickers('见 <b>600519</b> 这只')
  assert.match(out, /kline-ticker/)
  assert.match(out, /<b>/)
})
