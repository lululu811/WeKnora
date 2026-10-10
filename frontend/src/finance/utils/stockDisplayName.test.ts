import assert from 'node:assert/strict'
import test from 'node:test'

import { bareCode, displayStockName, looksLikeCode } from './stockDisplayName.ts'

test('bareCode 去交易所后缀', () => {
  assert.equal(bareCode('002859.SZ'), '002859')
  assert.equal(bareCode('600519.SH'), '600519')
  // 没有后缀时原样返回，不吞字符
  assert.equal(bareCode('002859'), '002859')
})

test('looksLikeCode 认得出"存进去的其实是代码"', () => {
  assert.equal(looksLikeCode('002859', '002859.SZ'), true)
  assert.equal(looksLikeCode('002859.SZ', '002859.SZ'), true)
  assert.equal(looksLikeCode('', '002859.SZ'), true)
  assert.equal(looksLikeCode('   ', '002859.SZ'), true)
  // 真名不能被误判成代码
  assert.equal(looksLikeCode('洁美生物', '002859.SZ'), false)
  // 名字里带数字也不该被误判
  assert.equal(looksLikeCode('三六零', '601360.SH'), false)
})

test('displayStockName 优先用行情源那份', () => {
  // 库里存的是代码，行情源有真名 → 用真名
  assert.equal(displayStockName('002859', '002859.SZ', '洁美生物'), '洁美生物')
  // 两边都有名字 → 行情源优先（它来自标的表，不会被写坏）
  assert.equal(displayStockName('旧名字', '002859.SZ', '洁美生物'), '洁美生物')
})

test('displayStockName 两边都没有真名时返回空串，不让代码冒充名字', () => {
  // 这正是截图里的那一行：返回空串后模板只渲染 002859.SZ，不再出现两行同文
  assert.equal(displayStockName('002859', '002859.SZ', undefined), '')
  assert.equal(displayStockName('', '002859.SZ', '002859'), '')
  assert.equal(displayStockName('002859', '002859.SZ', null), '')
  // 库里存的是真名而行情源没数据 → 仍然能用清单那份
  assert.equal(displayStockName('洁美生物', '002859.SZ', undefined), '洁美生物')
})

test('displayStockName 两边都是代码时仍然返回空串', () => {
  assert.equal(displayStockName('002859', '002859.SZ', '002859.SZ'), '')
  assert.equal(displayStockName('002859.SZ', '002859.SZ', '002859'), '')
})
