import assert from 'node:assert/strict'
import test from 'node:test'

import {
  extractStockMentions,
  extractMentionedStocks,
  pickPrimaryMention,
  resolveTickerThscode,
} from './stockMentions.ts'
import { injectKLineTickers } from './klineTickerInjector.ts'

test('resolveTickerThscode 归一带后缀与裸码', () => {
  assert.equal(resolveTickerThscode('600519', 'SH'), '600519.SH')
  assert.equal(resolveTickerThscode('600519', 'sh'), '600519.SH')
  assert.equal(resolveTickerThscode('600519'), '600519.SH')
  assert.equal(resolveTickerThscode('000858'), '000858.SZ')
  assert.equal(resolveTickerThscode('300750'), '300750.SZ')
  assert.equal(resolveTickerThscode('830799'), '830799.BJ')
})

test('resolveTickerThscode 对前缀不明确的代码返回 null', () => {
  // 90/20 是 B 股与 YYYYMM 形态的日期，11-18 是基金债券，都不猜
  for (const code of ['202609', '900901', '123456', '100000']) {
    assert.equal(resolveTickerThscode(code), null, code)
  }
  // 非 6 位数字直接不认
  assert.equal(resolveTickerThscode('12345'), null)
  assert.equal(resolveTickerThscode('1234567'), null)
  assert.equal(resolveTickerThscode('abcdef'), null)
})

test('resolveTickerThscode 拒绝非 A 股交易所后缀', () => {
  assert.equal(resolveTickerThscode('00700', 'HK'), null)
  assert.equal(resolveTickerThscode('600519', 'US'), null)
})

test('同一只票的多种写法收敛成一个标的', () => {
  const found = extractStockMentions('贵州茅台(600519.SH)今天不错，600519 也涨了，贵州茅台领涨')
  assert.equal(found.length, 1)
  assert.equal(found[0].thscode, '600519.SH')
  assert.equal(found[0].name, '贵州茅台')
})

test('名称(代码) 形式取原文里的名称', () => {
  const [r] = extractStockMentions('赛力斯(601127.SH)最近很强')
  assert.equal(r.name, '赛力斯')
  assert.equal(r.exchange, 'SH')
})

test('原文名称优先于本地映射表', () => {
  // 映射表里 600519 是「贵州茅台」，但原文写的是别的名字时以原文为准
  const [r] = extractStockMentions('某某公司(600519.SH)')
  assert.equal(r.name, '某某公司')
})

test('只出现已知名称也能识别', () => {
  const found = extractStockMentions('比亚迪和宁德时代领涨')
  const codes = found.map((r) => r.thscode).sort()
  assert.deepEqual(codes, ['002594.SZ', '300750.SZ'])
})

test('多只票按首次出现顺序返回', () => {
  const found = extractStockMentions('先说比亚迪(002594.SZ)，再说五粮液(000858.SZ)，最后回到比亚迪')
  assert.deepEqual(found.map((r) => r.ticker), ['002594', '000858'])
})

test('同一段的交易所推断与正文注入器完全一致', () => {
  // 这是两条抽取路径曾经分裂的地方：同一段文本必须得到同一个 thscode。
  const text = '平潭发展(000592)和一只北交所票 830799 都值得看'
  const fromMentions = extractMentionedStocks(text).map((r) => r.thscode)
  const fromInjector = [...injectKLineTickers(text).matchAll(/data-thscode="([^"]+)"/g)].map((m) => m[1])
  for (const code of fromMentions) {
    assert.ok(fromInjector.includes(code), `${code} 应在注入结果里也出现`)
  }
})

test('前缀不明确的数字不产出标的', () => {
  const found = extractStockMentions('订单号 123456 和 900901 都不是股票')
  assert.equal(found.length, 0)
})

test('空输入返回空数组', () => {
  assert.deepEqual(extractStockMentions(''), [])
  assert.deepEqual(extractMentionedStocks(''), [])
})

test('pickPrimaryMention 取首个提及的标的', () => {
  const p = pickPrimaryMention('贵州茅台(600519.SH)今天走强，五粮液(000858.SZ)跟涨')
  assert.equal(p?.thscode, '600519.SH')
})

test('pickPrimaryMention 在引导语之后不猜，返回 null', () => {
  // 「说到…」后面那只才是讨论对象，但规则无法确定是哪只 -> 宁可空着
  const p = pickPrimaryMention('说到白酒，最近茅台走势不错')
  assert.equal(p, null)
})

test('pickPrimaryMention 识别名称(代码) 为明确指定', () => {
  const p = pickPrimaryMention('先聊聊别的，下面看贵州茅台(600519.SH)')
  assert.equal(p?.thscode, '600519.SH')
})

test('pickPrimaryMention 无标的时返回 null 而不是猜', () => {
  assert.equal(pickPrimaryMention('今天大盘表现一般'), null)
  assert.equal(pickPrimaryMention(''), null)
})
