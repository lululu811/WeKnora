import assert from 'node:assert/strict'
import test from 'node:test'

import {
  haloSlotDimensions,
  hasHaloSlots,
  maskHaloSlots,
} from './haloPlaceholders.ts'

// 照抄 python-service/halo/analyze.py 的 `_md_dimension` 实际输出形状：
// 分数槽位带反引号（代码 span），分析槽位不带。
const SKELETON = [
  '## 五、低淘汰率',
  '',
  '壁垒能否穿越技术迭代与竞争，决定长期持有价值。',
  '',
  '`{{moat_score}}`',
  '',
  '量化锚点：',
  '',
  '- `gross_margin` = 0.8523',
  '',
  '分析：{{moat_analysis}}',
].join('\n')

test('hasHaloSlots 识别分数与分析两类槽位', () => {
  assert.equal(hasHaloSlots(SKELETON), true)
  assert.equal(hasHaloSlots('`{{moat_score}}`'), true)
  assert.equal(hasHaloSlots('分析：{{moat_analysis}}'), true)
})

test('hasHaloSlots 对没有槽位的报告返回 false', () => {
  assert.equal(hasHaloSlots('# 正常报告\n\n| 维度 | 评分 |\n| HALO 六维 | 3.42 |'), false)
  assert.equal(hasHaloSlots(''), false)
  assert.equal(hasHaloSlots('分析：已完成'), false)
})

test('hasHaloSlots 不会因为单次 test 而吃掉全局正则的 lastIndex', () => {
  // 正则是带 g 的模块级常量，若直接 .test() 会污染 lastIndex 导致第二次返回
  // false。这条是回归护栏：连续调用两次必须都成立。
  assert.equal(hasHaloSlots(SKELETON), true)
  assert.equal(hasHaloSlots(SKELETON), true)
})

test('maskHaloSlots 替换分数槽位并吃掉外层反引号', () => {
  const out = maskHaloSlots(SKELETON)
  assert.ok(!out.includes('{{moat_score}}'), '分数槽位必须被替换')
  assert.ok(out.includes('**待判分**'), '分数槽位应显示为待判分')
  // 反引号要连同占位符一起吃掉，否则留下空的代码 span `` ` `` 会被渲染成
  // 一个只有边框没有内容的小方块。
  assert.ok(!/`\s*\*\*待判分\*\*/.test(out), `不应残留孤立的反引号，实际输出：\n${out}`)
})

test('maskHaloSlots 替换分析槽位', () => {
  const out = maskHaloSlots(SKELETON)
  assert.ok(!out.includes('{{moat_analysis}}'), '分析槽位必须被替换')
  assert.ok(out.includes('**（定性分析待补）**'))
})

test('maskHaloSlots 保留量化锚点等真实数据', () => {
  const out = maskHaloSlots(SKELETON)
  assert.ok(out.includes('- `gross_margin` = 0.8523'), '锚点是数据，不能被顺手改掉')
  assert.ok(out.includes('壁垒能否穿越技术迭代与竞争，决定长期持有价值。'), '正文不能丢')
  assert.ok(out.includes('## 五、低淘汰率'), '章节结构不能丢')
})

test('maskHaloSlots 保留槽位后紧跟的「⚠️ 无量化锚点」标记', () => {
  const withMark = '`{{moat_score}}` ⚠️ 无量化锚点'
  const out = maskHaloSlots(withMark)
  assert.ok(out.includes('⚠️ 无量化锚点'), '缺锚点标记是重要信息，必须保留')
  assert.equal(out, '**待判分** ⚠️ 无量化锚点')
})

test('maskHaloSlots 幂等', () => {
  const once = maskHaloSlots(SKELETON)
  assert.equal(maskHaloSlots(once), once)
  assert.equal(hasHaloSlots(once), false, '替换后不应再检测到槽位')
})

test('maskHaloSlots 对空串与非字符串安全', () => {
  assert.equal(maskHaloSlots(''), '')
  assert.equal(maskHaloSlots(undefined as unknown as string), '')
  assert.equal(maskHaloSlots(null as unknown as string), '')
})

test('haloSlotDimensions 列出缺失维度且去重保序', () => {
  const md = '{{moat_score}}{{moat_analysis}}{{risk_score}}{{valuation_analysis}}'
  assert.deepEqual(haloSlotDimensions(md), ['moat', 'risk', 'valuation'])
})

test('haloSlotDimensions 对完整报告返回空数组', () => {
  assert.deepEqual(haloSlotDimensions('# 报告\n\n没有槽位。'), [])
  assert.deepEqual(haloSlotDimensions(''), [])
})
