// pulseScale 的单测。
//
// 这些函数的共同特点是「算错了看不出来」：条形比例差 10%，静态截图上完全正常，
// 但「2.59 倍」和「2.30 倍」在图上会长得差不多。所以它们的边界值必须有断言。

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  PULSE_SCALE_CAP,
  PULSE_TICKS,
  barWidthPercent,
  dayDiff,
  formatMultiple,
  formatPct,
  groupEventsByDate,
  isOverflowBar,
  parseYmd,
  relativeDayLabel,
  tickPositionPercent,
  toBareCode,
  todayYmd,
} from './pulseScale.ts'

test('toBareCode 去掉交易所后缀，两侧归一规则必须一致', () => {
  assert.equal(toBareCode('301190.SZ'), '301190')
  assert.equal(toBareCode('600519.SH'), '600519')
  assert.equal(toBareCode('  301190.SZ  '), '301190')
  // 无后缀（pulse items 回显的裸码）原样透传，不能变成空串。
  assert.equal(toBareCode('301190'), '301190')
  // 板块 `.TI` 也只是去后缀 —— 剩下的交给后端判 missing，不在这里悄悄丢。
  assert.equal(toBareCode('880001.TI'), '880001')
})

test('barWidthPercent 按倍数线性映射并在 cap 处封顶', () => {
  assert.equal(barWidthPercent(0), 0)
  assert.equal(barWidthPercent(1), 40) // 1.0 / 2.5
  assert.equal(barWidthPercent(PULSE_SCALE_CAP), 100)
  // 2.65 倍必须画成满格，否则一条 8 倍的票会把整屏压扁。
  assert.equal(barWidthPercent(2.65), 100)
  assert.equal(barWidthPercent(8), 100)
  // 缩量方向也要有长度：0.5 倍是一根明显更短的条，而不是消失。
  assert.equal(barWidthPercent(0.5), 20)
})

test('barWidthPercent 对非有限数返回 0 而不是 NaN', () => {
  // NaN 会直接渲染成 `width: NaN%`，整根条消失 —— 而且不会报错，很难发现。
  assert.equal(barWidthPercent(NaN), 0)
  // Infinity 同样给 0：它不是"量特别大"，而是数据坏了。画成满格等于替后端
  // 断言了一个"极端放量"的结论，那比画不出条更糟。
  assert.equal(barWidthPercent(Infinity), 0)
  assert.equal(barWidthPercent(-Infinity), 0)
  assert.equal(barWidthPercent(-1), 0)
})

test('isOverflowBar 标记顶格条，2.5 与 8 倍必须可区分', () => {
  assert.equal(isOverflowBar(2.65), true)
  assert.equal(isOverflowBar(8), true)
  assert.equal(isOverflowBar(2.5), false)
  assert.equal(isOverflowBar(1.2), false)
})

test('tickPositionPercent 与 barWidthPercent 共用同一把尺子', () => {
  // 三条刻度都在 cap 之内，位置必须严格递增且落在 (0, 100)。
  const positions = PULSE_TICKS.map((tick) => tickPositionPercent(tick.value))
  assert.deepEqual(positions, [(0.618 / 2.5) * 100, 40, (1.382 / 2.5) * 100])
  assert.ok(positions[0] < positions[1])
  assert.ok(positions[1] < positions[2])
  assert.ok(positions.every((p) => p > 0 && p < 100))
  // 中枢（1.0）必须落在刻度正中，否则读者会把 1.0 倍读成"偏低"。
  assert.equal(tickPositionPercent(1), barWidthPercent(1))
})

test('parseYmd / dayDiff 按日历天算，跨时区与夏令时不错天', () => {
  assert.equal(parseYmd('2026-10-09') !== null, true)
  // 越界必须判非法：Date.UTC 不查范围，'2026-13-09' 会静默进位成 2027-01-09，
  // '2026-02-30' 会进位成 3-02 —— 差 1~2 天的相对标签错得毫无痕迹。
  assert.equal(parseYmd('2026-13-09'), null)
  assert.equal(parseYmd('2026-00-09'), null)
  assert.equal(parseYmd('2026-02-30'), null)
  assert.equal(parseYmd('2026-04-31'), null)
  assert.equal(parseYmd('2026-10-00'), null)
  assert.equal(parseYmd('not-a-date'), null)
  assert.equal(parseYmd('2026-10-09 12:00'), null) // 只接受纯 YYYY-MM-DD
  // 合法边界仍然通过。
  assert.equal(parseYmd('2024-02-29') !== null, true) // 闰日
  assert.equal(parseYmd('2026-02-28') !== null, true)
  assert.equal(parseYmd('2026-12-31') !== null, true)

  assert.equal(dayDiff('2026-10-08', '2026-10-09'), 1)
  assert.equal(dayDiff('2026-10-09', '2026-10-08'), -1)
  // 跨月、跨年、闰日边界。
  assert.equal(dayDiff('2026-01-31', '2026-02-01'), 1)
  assert.equal(dayDiff('2026-12-31', '2027-01-01'), 1)
  assert.equal(dayDiff('2024-02-28', '2024-03-01'), 2) // 闰年 2 月 29 日
  assert.equal(dayDiff('2026-02-28', '2026-03-01'), 1) // 平年没有 2-29
  // 非法输入不参与运算。
  assert.equal(dayDiff('2026-13-09', '2026-10-08'), null)
})

test('todayYmd 补零，输出恒为 YYYY-MM-DD', () => {
  assert.match(todayYmd(new Date(2026, 0, 5)), /^2026-01-05$/)
  assert.match(todayYmd(new Date(2026, 11, 31)), /^2026-12-31$/)
})

test('relativeDayLabel 给出今天/明天/+N 天/过去，非法日期返回 null', () => {
  assert.deepEqual(relativeDayLabel('2026-10-08', '2026-10-08'), { key: 'watchPulse.cal.today' })
  assert.deepEqual(relativeDayLabel('2026-10-09', '2026-10-08'), { key: 'watchPulse.cal.tomorrow' })
  assert.deepEqual(relativeDayLabel('2026-10-12', '2026-10-08'), {
    key: 'watchPulse.cal.inDays',
    days: 4,
  })
  // 过去的日期不能渲染成 `+-2 天`。
  assert.deepEqual(relativeDayLabel('2026-10-06', '2026-10-08'), {
    key: 'watchPulse.cal.daysAgo',
    days: 2,
  })
  assert.equal(relativeDayLabel('bad-date', '2026-10-08'), null)
})

test('groupEventsByDate 按后端顺序分组，不自行排序', () => {
  const grouped = groupEventsByDate([
    { date: '2026-10-09', title: 'CPI', category: '宏观' },
    { date: '2026-10-09', title: '会议', category: '其他' },
    { date: '2026-10-12', title: '社融', category: '宏观' },
  ])
  assert.equal(grouped.length, 2)
  assert.equal(grouped[0].date, '2026-10-09')
  assert.equal(grouped[0].events.length, 2)
  assert.equal(grouped[0].events[0].title, 'CPI')
  assert.equal(grouped[1].date, '2026-10-12')
  assert.deepEqual(groupEventsByDate([]), [])
})

test('formatMultiple / formatPct 对非有限数给占位符，涨跌带符号', () => {
  assert.equal(formatMultiple(2.59), '2.59×')
  assert.equal(formatMultiple(1), '1.00×')
  assert.equal(formatMultiple(NaN), '—')
  assert.equal(formatPct(9.99), '+9.99%')
  assert.equal(formatPct(-3.21), '-3.21%')
  assert.equal(formatPct(0), '0.00%') // 0 不加号：加号是"涨"的语义，0 不是涨
  assert.equal(formatPct(NaN), '—')
})