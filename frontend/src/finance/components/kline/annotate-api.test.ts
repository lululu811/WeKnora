import assert from 'node:assert/strict'
import test from 'node:test'

import { isLlmAnnotation, annotationLabel, PATTERN_CONFIG, type Annotation } from './annotate-api.ts'

function ann(overrides: Partial<Annotation> = {}): Annotation {
  return {
    type: 'b1',
    date: '2026-05-20',
    price: 100,
    text: 'B1 建仓波',
    confidence: 0.8,
    metadata: {},
    ...overrides,
  }
}

test('缺省 source 视为算法识别，不是未知来源', () => {
  // 这个字段是后加的；历史响应里没有它。若把它当成未知来源，
  // 所有旧数据都会被静默丢弃——不报错，只是画面上少了标注。
  assert.equal(isLlmAnnotation(ann()), false)
  assert.equal(isLlmAnnotation(ann({ source: 'algorithm' })), false)
})

test('source=llm 被识别为模型主张', () => {
  assert.equal(isLlmAnnotation(ann({ source: 'llm' })), true)
})

test('模型主张的标签带「观点·」前缀', () => {
  assert.equal(annotationLabel(ann({ source: 'llm', text: '1400 是关键支撑' })), '观点·1400 是关键支撑')
})

test('算法识别的标签保持原样', () => {
  assert.equal(annotationLabel(ann({ text: 'B1 建仓波' })), 'B1 建仓波')
  assert.equal(annotationLabel(ann({ source: 'algorithm', text: 'S1 预警' })), 'S1 预警')
})

test('前缀是「色觉之外」的区分手段，两种来源的标签必然不同', () => {
  // 同一段文字，来源不同则标签必须不同；否则只剩颜色一个维度，
  // 而深色画布上金色/琥珀/橙色挤在一起，红绿色觉障碍用户更分不出。
  const text = '关键位'
  const algo = annotationLabel(ann({ text, source: 'algorithm' }))
  const llm = annotationLabel(ann({ text, source: 'llm' }))
  assert.notEqual(algo, llm)
})

test('PATTERN_CONFIG 覆盖算法侧的四种形态', () => {
  // 这四个是后端 annotator 的 dispatch 表里的全部类型；少一个会导致
  // 该形态在工具栏里显示成原始 key。
  for (const key of ['b1', 'key_k', 's1', 'violent_k']) {
    assert.ok(PATTERN_CONFIG[key], `缺少形态配置: ${key}`)
    assert.ok(PATTERN_CONFIG[key].label.length > 0)
    assert.ok(PATTERN_CONFIG[key].desc.length > 0)
  }
})
