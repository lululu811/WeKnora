import assert from 'node:assert/strict'
import test, { mock } from 'node:test'

import { createHoverDebounce } from './hoverDebounce.ts'

/**
 * 全部用 `mock.timers` 驱动虚拟时钟，不 sleep。
 *
 * 防抖测试天然要跨时间点，但真实等待既慢又不稳（CI 负载一高就飘）。虚拟时钟
 * 让「10ms 后鼠标移到下一个日期」变成确定的一步，测试是瞬时的、可重复的。
 */
function withFakeTimers(fn: () => void) {
  mock.timers.enable({ apis: ['setTimeout'] })
  try {
    fn()
  } finally {
    mock.timers.reset()
  }
}

test('停留满延时后才触发一次', () => {
  withFakeTimers(() => {
    const calls: string[] = []
    const d = createHoverDebounce(30, (v: string) => calls.push(v))
    d.schedule('a')
    assert.deepEqual(calls, [], '还没到延时不该触发')
    mock.timers.tick(29)
    assert.deepEqual(calls, [], '差 1ms 也不该触发')
    mock.timers.tick(1)
    assert.deepEqual(calls, ['a'])
  })
})

test('重复 schedule 只保留最后一次（这就是防抖的全部意义）', () => {
  withFakeTimers(() => {
    const calls: string[] = []
    const d = createHoverDebounce(40, (v: string) => calls.push(v))
    // 模拟鼠标划过多个日期：每次都不到延时就走下一个
    d.schedule('a')
    mock.timers.tick(10)
    d.schedule('b')
    mock.timers.tick(10)
    d.schedule('c')
    mock.timers.tick(80)
    assert.deepEqual(calls, ['c'], '路过的那几个都不该触发，只有停下来的那个算数')
  })
})

test('cancel 之后不再补触发', () => {
  withFakeTimers(() => {
    const calls: string[] = []
    const d = createHoverDebounce(30, (v: string) => calls.push(v))
    d.schedule('a')
    d.cancel()
    mock.timers.tick(100)
    assert.deepEqual(calls, [], '鼠标移开后不该再补一次')
  })
})

test('cancel 不影响已经触发过的', () => {
  withFakeTimers(() => {
    const calls: string[] = []
    const d = createHoverDebounce(20, (v: string) => calls.push(v))
    d.schedule('a')
    mock.timers.tick(50)
    d.cancel()
    assert.deepEqual(calls, ['a'])
  })
})

test('触发后可以再次 schedule', () => {
  withFakeTimers(() => {
    const calls: string[] = []
    const d = createHoverDebounce(20, (v: string) => calls.push(v))
    d.schedule('a')
    mock.timers.tick(50)
    d.schedule('b')
    mock.timers.tick(50)
    assert.deepEqual(calls, ['a', 'b'])
  })
})

test('pending 反映是否有待触发的调用', () => {
  withFakeTimers(() => {
    const d = createHoverDebounce(30, () => {})
    assert.equal(d.pending, false)
    d.schedule()
    assert.equal(d.pending, true)
    mock.timers.tick(60)
    assert.equal(d.pending, false, '触发后应回到空闲')
  })
})

test('cancel 后 pending 立刻变 false', () => {
  withFakeTimers(() => {
    const d = createHoverDebounce(30, () => {})
    d.schedule()
    assert.equal(d.pending, true)
    d.cancel()
    assert.equal(d.pending, false)
  })
})

test('多参数原样透传', () => {
  withFakeTimers(() => {
    const seen: Array<[string, number]> = []
    const d = createHoverDebounce(20, (a: string, b: number) => seen.push([a, b]))
    d.schedule('x', 7)
    mock.timers.tick(50)
    assert.deepEqual(seen, [['x', 7]])
  })
})
