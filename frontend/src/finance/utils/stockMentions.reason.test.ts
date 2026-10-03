import assert from 'node:assert/strict'
import test from 'node:test'

import { extractTrackingReason } from './stockMentions.ts'

/**
 * 入池理由抽取的回归测试。
 *
 * 这段逻辑是「默认理由」唯一的一环：它错了不会报错，只会让 note 里静静躺着
 * 一段不相干的话，用户几个月后回看也未必发现。所以下面每条断言都对着一个
 * 真实会发生的回答形状，而不是对着理想输入。
 *
 * 这几条测试不是摆设：写它们的时候当场抓到了三个真 bug——带后缀写法被裸码
 * 正则重复匹配、引出句判定过宽导致跳过头、以及理由被抽成了**另一只票**的
 * 分析段落。第三个最危险，因为它读起来完全通顺。
 */

test('取最后一次出现所在段落，而不是第一次', () => {
  // 模型的分析结构：引出 → 展开 → 结论。结论才是理由。
  const text = [
    '我们来看看 601127.SH 的情况。',
    '',
    '赛力斯的销量数据：',
    '',
    '结论：2026Q3 交付创新高，订单能见度到明年一季度，值得等回踩。',
  ].join('\n')

  const reason = extractTrackingReason(text, '601127.SH')
  assert.ok(reason.includes('结论'), `应取到结论段，实际：${reason}`)
  assert.ok(reason.includes('订单能见度'))
  assert.ok(!reason.includes('我们来看看'), '引出句不该被当成理由')
  assert.ok(!reason.includes('销量数据'), '小标题不该被当成理由')
})

test('同一只票出现多次时，段落取最后一次而非首次', () => {
  const text = [
    '先说 600519.SH，它是白酒龙头，估值一直不便宜。',
    '',
    '补充：600519.SH 最近批价回落到合理区间，渠道库存也降下来了。',
    '',
    '最后再提一句 600519.SH，长期逻辑没变。',
  ].join('\n')

  const reason = extractTrackingReason(text, '600519.SH')
  assert.ok(reason.includes('长期逻辑没变'), `实际：${reason}`)
  assert.ok(!reason.includes('白酒龙头'))
})

test('票出现在正文第一段时退化为从 0 开始，而不是返回空', () => {
  const text = '600519.SH 的批价回落值得留意，这是本轮的核心结论。\n\n后面是别的内容。'
  const reason = extractTrackingReason(text, '600519.SH')
  assert.ok(reason.includes('批价回落'), `实际：${reason}`)
})

test('去掉 markdown 记号，保留文字', () => {
  const text = [
    '## 结论',
    '',
    '**601127.SH** 的 `订单` 数据很强，可以关注。',
  ].join('\n')

  const reason = extractTrackingReason(text, '601127.SH')
  assert.ok(reason.includes('订单'))
  assert.ok(!reason.includes('**'), `应去掉加粗记号：${reason}`)
  assert.ok(!reason.includes('`'), `应去掉行内代码记号：${reason}`)
  assert.ok(!reason.includes('##'), `应去掉标题记号：${reason}`)
})

test('硬截到 maxLen 之内', () => {
  const long = '这是很长的分析。'.repeat(200)
  const text = `600519.SH 的分析：${long}`
  const reason = extractTrackingReason(text, '600519.SH', 100)
  assert.ok(reason.length <= 100, `长度应 ≤100，实际 ${reason.length}`)
})

test('正文里没有这只票时返回空串', () => {
  assert.equal(extractTrackingReason('今天聊聊宏观。', '601127.SH'), '')
})

test('空输入安全返回空串', () => {
  assert.equal(extractTrackingReason('', '601127.SH'), '')
  assert.equal(extractTrackingReason('有正文', ''), '')
})

test('8 位日期不被误当成 6 位代码', () => {
  // '20260927' 里含 '202609'——边界规则必须让它落空。
  const text = [
    '有一串日期 20260927 需要注意。',
    '',
    '至于 601127.SH，结论是暂时观望。',
  ].join('\n')
  const reason = extractTrackingReason(text, '601127.SH')
  assert.ok(reason.includes('暂时观望'), `实际：${reason}`)
})

test('不把别的票的段落当成这只票的理由', () => {
  const text = [
    '600519.SH 的批价回落值得关注，这是一句很长的引出语，用来占满长度测试边界判定是否稳定可靠。',
    '',
    '000001.SZ 的息差正在收窄，逻辑在变差。',
  ].join('\n')
  const reason = extractTrackingReason(text, '600519.SH')
  assert.ok(reason.includes('批价回落'), `实际：${reason}`)
  assert.ok(!reason.includes('息差'), '理由栏不能存成另一只票的分析')
})

test('裸 6 位码也算命中（模型不一定带交易所后缀）', () => {
  // 「先说 600519」是引出句且短，后面才是实质内容 → 应跳到后一段。
  const text = [
    '先说 600519。',
    '',
    '批价回落到合理区间，渠道库存下降，逻辑仍然成立。',
  ].join('\n')
  const reason = extractTrackingReason(text, '600519.SH')
  assert.ok(reason.includes('批价回落'), `实际：${reason}`)
})

test('较长的首段不被误判成引出句（判定必须偏保守）', () => {
  // 31 字 > 24 字阈值，所以它算实质内容，不该被跳过。宁可漏判引出句，
  // 也不要为了跳过而丢掉真正有信息的一段。
  const text = [
    '先说 600519，它是白酒龙头，这里是一段足够长的实质分析内容。',
    '',
    '批价回落到合理区间，渠道库存下降。',
  ].join('\n')
  const reason = extractTrackingReason(text, '600519.SH')
  assert.ok(reason.includes('白酒龙头'), `实际：${reason}`)
  assert.ok(!reason.includes('批价回落'))
})

test('带后缀的写法不会被裸码规则重复匹配到错误位置', () => {
  // 601127.SH 里的前 6 位也能被裸码正则命中；两条正则各报一个位置时，
  // 取谁决定了引出句判定是否成立，所以这里锁死结果必须是引出句那一段。
  const text = [
    '我们来看看 601127.SH 的情况。',
    '',
    '这是一段足够长的实质分析，交付量在增长，订单能见度延长，估值仍在合理区间。',
  ].join('\n')
  const reason = extractTrackingReason(text, '601127.SH')
  assert.ok(reason.includes('交付量'), `实际：${reason}`)
  assert.ok(!reason.includes('我们来看看'))
})

/**
 * 图表锚点标签的回归测试。
 *
 * 现场翻车的那条 note：
 *   新强联(300850.SZ) 当前收盘价@<anchor kind="level" value="31.81"
 *   label="当前价31.81"...
 * 锚点标签是给 K 线图定位用的，note 存成原文之后又被 varchar(200) 从中间
 * 切断，于是备注栏里出现一串半截的 XML。它不会报错，只会安静地坏掉。
 */
test('把图表锚点标签换成可读文字，不让原始标签进 note', () => {
  const text = [
    '新强联(300850.SZ) 当前收盘价@<anchor kind="level" value="31.81" label="当前价31.81"/>，量能未跟上。',
  ].join('\n')

  const reason = extractTrackingReason(text, '300850.SZ')
  assert.ok(!reason.includes('<anchor'), `不该留下标签本体，实际：${reason}`)
  assert.ok(!reason.includes('kind='), `不该留下属性，实际：${reason}`)
  // 「价位 @xx」写法用裸值，比 label 自然
  assert.ok(reason.includes('31.81'), `应保留价位，实际：${reason}`)
  assert.ok(!/\/\s*$/.test(reason), `不该留下自闭合尾巴，实际：${reason}`)
})

test('没有 @ 前缀时用 label 作为可读文本', () => {
  const text = '新强联(300850.SZ) 上方<anchor kind="level" value="72.4" label="第一目标"/>是第一目标位。'
  const reason = extractTrackingReason(text, '300850.SZ')
  assert.ok(reason.includes('第一目标'), `应取 label，实际：${reason}`)
  assert.ok(!reason.includes('<anchor'))
})

test('note 长度按服务端列宽（200）截断，而不是 500', () => {
  const long = '新强联(300850.SZ) ' + '逻辑连贯的判断依据。'.repeat(60)
  const reason = extractTrackingReason(long, '300850.SZ')
  assert.ok(
    reason.length <= 200,
    `超出 varchar(200) 会被服务端静默切断，实际长度：${reason.length}`,
  )
})
