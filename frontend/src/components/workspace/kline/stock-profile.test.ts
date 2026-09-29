import assert from 'node:assert/strict'
import test from 'node:test'

import {
  normalizeProfile,
  pickSectors,
  type SectorChip,
} from './stock-profile.ts'

/**
 * 悬浮框的画像归一。这套测试的断言重点只有一个：
 * **缺数据不许变成 0 或空数组** —— 0 在金融语义里是"真的等于零"。
 * 任何一条把 null 抹平的改动都必须让这里变红。
 */

const NOW = Date.parse('2026-09-29T12:00:00+08:00')

/** 生成距今 n 天的日期串，n=0 即今天。 */
function daysAgo(n: number): string {
  const t = new Date(NOW - n * 86_400_000)
  const y = t.getFullYear()
  const m = String(t.getMonth() + 1).padStart(2, '0')
  const d = String(t.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

test('PE/PB/PS 为 null 时保持 null，绝不退化成 0', () => {
  const p = normalizeProfile(
    {
      valuation: { snapshot_date: '2026-09-25', pe_ttm: null, pb_mrq: null, ps_ttm: null },
    },
    '600519.SH',
  )
  assert.equal(p.valuation?.peTtm, null)
  assert.equal(p.valuation?.pbMrq, null)
  assert.equal(p.valuation?.psTtm, null)
})

// 亏损股的 pe_ttm 是负数，-15.3 精确相等已经排除了"被抹成 0"。
test('PE 为负（亏损）时保留负号，不改写成 0', () => {
  const p = normalizeProfile({ valuation: { pe_ttm: -15.3 } }, '000001.SZ')
  assert.equal(p.valuation?.peTtm, -15.3)
})

test('valuation 整块缺失时为 null，而不是空对象', () => {
  const p = normalizeProfile({ valuation: null }, '688487.SH')
  assert.equal(p.valuation, null)
})

test('无资金异动记录 → capital 为 null（是结论，不是数据缺失）', () => {
  const p = normalizeProfile(
    { capital: { limit_up: [], limit_break: [], dragon_tiger: [], hot: [] } },
    '600519.SH',
  )
  assert.equal(p.capital, null)
})

test('capital 字段整体缺失时为 null', () => {
  const p = normalizeProfile({}, '600519.SH')
  assert.equal(p.capital, null)
})

test('资金面只统计 30 日窗口，窗口外的涨停不计入', () => {
  const p = normalizeProfile(
    {
      capital: {
        // 5 天前 + 45 天前 → 只有 1 次该进窗口
        limit_up: [{ trade_date: daysAgo(5), continue_day_cnt: 2 }, { trade_date: daysAgo(45), continue_day_cnt: 5 }],
        limit_break: [],
        dragon_tiger: [],
        hot: [],
      },
    },
    '601811.SH',
  )
  assert.equal(p.capital?.limitUpCount, 1)
  // 最高连板也只取窗口内的 2 板，窗口外的 5 板不该把它顶上去
  assert.equal(p.capital?.maxContinueDays, 2)
})

test('v_hot_stock 用 capture_date 而非 trade_date 参与窗口判断', () => {
  const p = normalizeProfile(
    {
      capital: {
        limit_up: [],
        limit_break: [],
        dragon_tiger: [],
        // 若按 trade_date 取，这里会被误判为 45 天前而丢弃
        hot: [{ capture_date: daysAgo(3), rank: 7, heat: 1e6 }],
      },
    },
    '000592.SZ',
  )
  assert.equal(p.capital?.hotRank, 7)
})

test('龙虎榜净买额为 null 时不补 0', () => {
  const p = normalizeProfile(
    {
      capital: {
        limit_up: [],
        limit_break: [],
        dragon_tiger: [{ trade_date: daysAgo(1), net_value: null }],
        hot: [],
      },
    },
    '000592.SZ',
  )
  assert.equal(p.capital?.dragonCount, 1)
  assert.equal(p.capital?.dragonNetValue, null)
})

test('unavailable 原样透传，用于卡片上说明哪块数据源没查到', () => {
  const p = normalizeProfile({ unavailable: ['special', 'index'] }, '600519.SH')
  assert.deepEqual(p.unavailable, ['special', 'index'])
})

test('unavailable 缺失时是空数组而非 undefined', () => {
  const p = normalizeProfile({}, '600519.SH')
  assert.deepEqual(p.unavailable, [])
})

test('sectors 里 name 非字符串的脏数据被剔除', () => {
  const p = normalizeProfile(
    { sectors: [{ name: '白酒', tag: 'industry' }, { tag: 'region' }, { name: null }] },
    '600519.SH',
  )
  assert.equal(p.sectors.length, 1)
  assert.equal(p.sectors[0].name, '白酒')
})

test('板块裁剪：行业/地域在前，概念限量，特色指数只计数', () => {
  const sectors: SectorChip[] = [
    { name: '白酒', tag: 'industry' },
    { name: '贵州', tag: 'region' },
    ...['概念A', '概念B', '概念C', '概念D', '概念E', '概念F'].map((name) => ({ name, tag: 'cn_concept' })),
    ...['特1', '特2', '特3', '特4'].map((name) => ({ name, tag: 'tszs' })),
  ]
  const { shown, hidden } = pickSectors(sectors)
  const names = shown.map((s) => s.name)

  assert.ok(names.includes('白酒'), '行业必须展示')
  assert.ok(names.includes('贵州'), '地域必须展示')
  // 概念限 4 个
  assert.equal(shown.filter((s) => s.tag === 'cn_concept').length, 4)
  // tszs 限 0 个，一个不展示
  assert.equal(shown.filter((s) => s.tag === 'tszs').length, 0)
  // 被折叠的：概念 2 + 特指 4 = 6
  assert.equal(hidden, 6)
})

test('板块裁剪：全 A 典型 20 标签的票，展示数可控且 hidden 为正', () => {
  const sectors: SectorChip[] = [
    { name: '白酒', tag: 'industry' },
    { name: '贵州', tag: 'region' },
    ...Array.from({ length: 8 }, (_, i) => ({ name: `概念${i}`, tag: 'cn_concept' })),
    ...Array.from({ length: 10 }, (_, i) => ({ name: `特指${i}`, tag: 'tszs' })),
  ]
  const { shown, hidden } = pickSectors(sectors)
  assert.ok(shown.length <= 7, `实际 ${shown.length} 个，不该超过 7`)
  // 概念 8 个超限 4，特色 10 个全折叠
  assert.equal(hidden, 4 + 10)
  // 行业1 + 地域1 + 概念4 = 6 展示
  assert.equal(shown.length, 6)
  assert.equal(shown.length + hidden, 20, '总数必须守恒，不能凭空丢标签')
})

test('板块裁剪：无板块时返回空数组，hidden 为 0', () => {
  const { shown, hidden } = pickSectors([])
  assert.deepEqual(shown, [])
  assert.equal(hidden, 0)
})

// ---- 渲染分支：什么样的数据该显示成什么样 ----
// 卡片模板里有 5 个 v-if 分支。这里把"该显示哪几块"的判定抽出来测，
// 避免只能靠肉眼在浏览器里比对 —— 那条路需要登录。

test('大盤股（无资金异动）只显示估值与板块，不显示资金面', () => {
  const p = normalizeProfile(
    { valuation: { pe_ttm: 18.99, pb_mrq: 6.15, ps_ttm: 8.92 }, capital: null, sectors: [{ name: '白酒', tag: 'industry' }] },
    '600519.SH',
  )
  assert.equal(p.capital, null, '茅台没有资金异动，capital 应为 null')
  assert.ok(p.valuation, '估值应存在')
  assert.equal(p.sectors.length, 1)
})

test('题材妖股（5 连板）资金面三个维度全有值', () => {
  const p = normalizeProfile(
    {
      capital: {
        limit_up: [{ trade_date: daysAgo(0), continue_day_cnt: 5, seal_money: 1.3e8 }],
        limit_break: [{ trade_date: daysAgo(1), open_times: 2 }],
        dragon_tiger: [{ trade_date: daysAgo(2), net_value: 4.75e8 }],
        hot: [{ capture_date: daysAgo(0), rank: 3, heat: 7.1e6 }],
        sources: { limit_up: 'special.v_limit_up_pool', dragon_tiger: 'special.v_dragon_tiger', hot: 'special.v_hot_stock' },
      },
    },
    '601811.SH',
  )
  assert.equal(p.capital?.limitUpCount, 1)
  assert.equal(p.capital?.maxContinueDays, 5)
  assert.equal(p.capital?.limitBreakCount, 1)
  assert.equal(p.capital?.dragonCount, 1)
  assert.equal(p.capital?.hotRank, 3)
  assert.equal(p.capital?.sources.limit_up, 'special.v_limit_up_pool')
})

test('龙虎榜净卖出保留负号，金额转「亿」', () => {
  const p = normalizeProfile(
    { capital: { limit_up: [], limit_break: [], dragon_tiger: [{ trade_date: daysAgo(1), net_value: -2.5e8 }], hot: [] } },
    '000592.SZ',
  )
  assert.equal(p.capital?.dragonNetValue, -2.5e8, '负值不能被截断成 0')
  assert.ok(p.capital!.dragonNetValue! < 0, '净卖出必须是负数')
})

test('只有板块没有估值时，sector_source 说明 join 了两张表', () => {
  const p = normalizeProfile(
    { valuation: null, sectors: [{ name: '银行', tag: 'industry' }] },
    '000001.SZ',
  )
  assert.equal(p.valuation, null)
  assert.equal(p.sectors[0].tag, 'industry')
})
