import assert from 'node:assert/strict'
import test from 'node:test'

import {
  readScreenerCoverage,
  resolveScreenerPayload,
} from './screenerCoverage.ts'

// 照抄 python-service/main.py `/zettaranc/screen` 的真实返回形状。
// Go 工具再包一层 {strategy, limit, data: <下面这个>}（internal/agent/tools/
// zettaranc/screener.go:180-184），所以两种嵌套都要测。

/** 全市场扫完、确实一个都没命中。 */
const SCANNED_CLEAN_BUT_EMPTY = {
  success: true,
  strategy: 'changan_combo',
  universe: 5571,
  market_size: 5571,
  scanned: 5571,
  scanned_from_universe: 5571,
  incomplete: 0,
  matched: 0,
  unsupported_signals: [],
  truncated: false,
  price_merged_rows: 0,
  price_available: false,
  risk_rejects: [],
  no_indicator_count: 0,
  warnings: [],
  stocks: [],
}

test('resolveScreenerPayload 拆掉 Go 那层 data 包装', () => {
  const wrapped = { strategy: 'changan_combo', limit: 20, data: SCANNED_CLEAN_BUT_EMPTY }
  const payload = resolveScreenerPayload(wrapped)

  // 关键回归：卡片此前只读顶层 tickers/stocks，穿过 data 包装后全丢。
  // 拆包后必须拿到内层的可信度字段。
  assert.equal(payload.scanned, 5571)
  assert.equal(payload.matched, 0)
  // 拿到的是**内层**对象，Go 包装层的字段不在里面（limit 只属于外层）。
  assert.equal('limit' in payload, false)
  // 顶层直挂的旧形态也照样认。
  assert.equal(resolveScreenerPayload(SCANNED_CLEAN_BUT_EMPTY).scanned, 5571)
})

test('resolveScreenerPayload 对垃圾输入不炸', () => {
  for (const bad of [null, undefined, 0, 'x', [], true]) {
    assert.deepEqual(resolveScreenerPayload(bad), {})
  }
})

test('扫全市场且确实为空：给扫描口径，不报警', () => {
  const cov = readScreenerCoverage({ data: SCANNED_CLEAN_BUT_EMPTY })

  assert.equal(cov.scanned, 5571)
  assert.equal(cov.universe, 5571)
  assert.equal(cov.matched, 0)

  const keys = cov.facts.map((f) => f.key)
  assert.ok(keys.includes('scanned'), '应说明扫了多少只')
  assert.ok(keys.includes('priceUnavailable'), 'price_available=false 必须透出')
  // 没有数据缺失就不该出现这些噪音条目
  assert.ok(!keys.includes('noIndicator'))
  assert.ok(!keys.includes('truncated'))
  assert.ok(!keys.includes('riskRejected'))
})

test('命中的票被风险过滤筛光 —— 这是最容易被误读成"市场里没有"的一种', () => {
  const cov = readScreenerCoverage({
    data: {
      ...SCANNED_CLEAN_BUT_EMPTY,
      matched: 12,
      risk_rejects: [1, 2, 3, 4, 5],
      price_available: true,
      price_merged_rows: 5571,
      stocks: [],
    },
  })

  assert.equal(cov.matched, 12, '风险过滤之前的命中数必须透出')
  const rejected = cov.facts.find((f) => f.key === 'riskRejected')
  assert.ok(rejected, '必须说明有多少只被风险筛掉')
  assert.equal(rejected?.params?.count, 5, 'risk_rejects 传数组时取长度')
  assert.ok(
    !cov.facts.some((f) => f.key === 'priceUnavailable'),
    '价量可用时不该报失效',
  )
})

test('因缺指标数据被丢掉的票数要透出，且标为 warn', () => {
  const cov = readScreenerCoverage({
    data: { ...SCANNED_CLEAN_BUT_EMPTY, no_indicator_count: 12 },
  })

  const f = cov.facts.find((x) => x.key === 'noIndicator')
  assert.ok(f)
  assert.equal(f?.params?.count, 12)
  assert.equal(f?.tone, 'warn')
})

test('价量并上 0 行也算失效 —— main.py 要求透出的那个 0', () => {
  // price_available 可能为 true 但合并 0 行（数据源在、这批没并上）。
  // 两种都要报，否则"放量突破没选出票"又会被读成"市场里没有"。
  const cov = readScreenerCoverage({
    data: { ...SCANNED_CLEAN_BUT_EMPTY, price_available: true, price_merged_rows: 0 },
  })
  assert.ok(cov.facts.some((f) => f.key === 'priceUnavailable'))
})

test('truncated 与 warnings 逐条透出', () => {
  const cov = readScreenerCoverage({
    data: {
      ...SCANNED_CLEAN_BUT_EMPTY,
      truncated: true,
      incomplete: 3,
      warnings: ['板块成分股未同步', 123, ''],
    },
  })

  assert.ok(cov.facts.some((f) => f.key === 'truncated'))
  const inc = cov.facts.find((f) => f.key === 'incomplete')
  assert.equal(inc?.params?.count, 3)
  const warnings = cov.facts.filter((f) => f.key === 'warning')
  assert.equal(warnings.length, 1, '非字符串和空串应被丢掉')
  assert.equal(warnings[0]?.params?.text, '板块成分股未同步')
})

test('板块限定时 scanned 与 universe 不同，两个数都要如实显示', () => {
  const cov = readScreenerCoverage({
    data: { ...SCANNED_CLEAN_BUT_EMPTY, universe: 120, scanned: 118 },
  })

  const f = cov.facts.find((x) => x.key === 'scanned')
  assert.equal(f?.params?.scanned, 118)
  assert.equal(f?.params?.universe, 120)
})

test('后端没给字段时不编造数字', () => {
  const cov = readScreenerCoverage({ data: { stocks: [] } })

  assert.equal(cov.scanned, null)
  assert.equal(cov.universe, null)
  assert.equal(cov.matched, null)
  assert.deepEqual(cov.facts, [], '一个字段都没有时不该产出任何事实')
})

test('负数与 NaN 不被当成有效计数', () => {
  const cov = readScreenerCoverage({
    data: { ...SCANNED_CLEAN_BUT_EMPTY, scanned: -1, no_indicator_count: Number.NaN },
  })
  assert.equal(cov.scanned, null)
  assert.ok(!cov.facts.some((f) => f.key === 'noIndicator'))
})
