import assert from 'node:assert/strict'
import test from 'node:test'

import {
  fetchKline,
  computeChange,
  resetKlineCache,
  KlineFetchError,
  type KlineRequest,
} from './kline-cache.ts'

/** 造一个假的 /api/kline 响应。 */
function okBody(rows: Array<Partial<{ ts: number; open: number; high: number; low: number; close: number; volume: number }>>) {
  return {
    code: 0,
    data: rows.map((r, i) => ({
      ts: r.ts ?? 1_600_000_000 + i * 86400,
      open: r.open ?? 10,
      high: r.high ?? 11,
      low: r.low ?? 9,
      close: r.close ?? 10,
      volume: r.volume ?? 100,
    })),
  }
}

interface StubCall {
  url: string
}

/** 装一个 fetch 桩，返回调用记录。 */
function stubFetch(
  handler: (url: string) => { status?: number; body?: unknown } | Promise<{ status?: number; body?: unknown }>,
): { calls: StubCall[] } {
  const calls: StubCall[] = []
  ;(globalThis as any).fetch = async (url: string) => {
    calls.push({ url })
    const res = await handler(url)
    const status = res.status ?? 200
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => res.body,
    }
  }
  return { calls }
}

test('同一请求命中缓存，只打一次网络', async () => {
  resetKlineCache()
  const { calls } = stubFetch(() => ({ body: okBody([{}, {}]) }))
  await fetchKline({ symbol: '600519.SH' })
  await fetchKline({ symbol: '600519.SH' })
  assert.equal(calls.length, 1)
})

test('不同周期/复权/limit 不共享缓存', async () => {
  resetKlineCache()
  const { calls } = stubFetch(() => ({ body: okBody([{}]) }))
  const base: KlineRequest = { symbol: '600519.SH' }
  await fetchKline(base)
  await fetchKline({ ...base, period: 'week' })
  await fetchKline({ ...base, adjust: 'none' })
  await fetchKline({ ...base, limit: 5000 })
  // 4 个不同的键 -> 4 次请求（若只按 symbol 缓存，这里会是 1）
  assert.equal(calls.length, 4)
})

test('并发同 key 共享在途 Promise，不重复请求', async () => {
  resetKlineCache()
  let resolveBody: (v: unknown) => void = () => {}
  const gate = new Promise((r) => { resolveBody = r })
  const { calls } = stubFetch(async () => {
    await gate
    return { body: okBody([{}, {}]) }
  })

  const p1 = fetchKline({ symbol: '000858.SZ' })
  const p2 = fetchKline({ symbol: '000858.SZ' })
  const p3 = fetchKline({ symbol: '000858.SZ' })
  resolveBody(null)
  const [a, b, c] = await Promise.all([p1, p2, p3])
  assert.equal(calls.length, 1, '并发只应打一次网络')
  assert.equal(a.length, 2)
  assert.equal(a, b, '同一 key 的并发调用应共享同一个数组引用')
  assert.equal(b, c)
})

test('404 -> no-data；其余 HTTP 错误 -> transient', async () => {
  resetKlineCache()
  stubFetch(() => ({ status: 404 }))
  await assert.rejects(
    () => fetchKline({ symbol: '688487.SH' }),
    (err: unknown) => err instanceof KlineFetchError && err.kind === 'no-data',
  )

  resetKlineCache()
  stubFetch(() => ({ status: 502 }))
  await assert.rejects(
    () => fetchKline({ symbol: '600519.SH' }),
    (err: unknown) => err instanceof KlineFetchError && err.kind === 'transient',
  )
})

test('HTTP 200 但 code!=0 -> transient', async () => {
  resetKlineCache()
  stubFetch(() => ({ body: { code: 7, data: [] } }))
  await assert.rejects(
    () => fetchKline({ symbol: '600519.SH' }),
    (err: unknown) => err instanceof KlineFetchError && err.kind === 'transient',
  )
})

test('HTTP 200、code=0 但零根 K 线 -> no-data', async () => {
  resetKlineCache()
  stubFetch(() => ({ body: { code: 0, data: [] } }))
  await assert.rejects(
    () => fetchKline({ symbol: '600519.SH' }),
    (err: unknown) => err instanceof KlineFetchError && err.kind === 'no-data',
  )
})

test('非 JSON 响应 -> transient（不是 no-data）', async () => {
  resetKlineCache()
  ;(globalThis as any).fetch = async () => ({
    ok: false,
    status: 502,
    json: async () => { throw new SyntaxError('Unexpected token <') },
  })
  await assert.rejects(
    () => fetchKline({ symbol: '600519.SH' }),
    (err: unknown) => err instanceof KlineFetchError && err.kind === 'transient',
  )
})

test('失败不进缓存：下次调用会重试', async () => {
  resetKlineCache()
  let fail = true
  const { calls } = stubFetch(() => {
    if (fail) return { status: 502 }
    return { body: okBody([{}]) }
  })
  await assert.rejects(() => fetchKline({ symbol: '600519.SH' }))
  fail = false
  const rows = await fetchKline({ symbol: '600519.SH' })
  assert.equal(rows.length, 1)
  assert.equal(calls.length, 2, '失败后必须能重试')
})

test('请求 URL 带上 period/adjust/limit', async () => {
  resetKlineCache()
  const { calls } = stubFetch(() => ({ body: okBody([{}]) }))
  await fetchKline({ symbol: '600519.SH', period: 'week', adjust: 'none', limit: 5000 })
  assert.match(calls[0].url, /period=week/)
  assert.match(calls[0].url, /adjust=none/)
  assert.match(calls[0].url, /limit=5000/)
  assert.match(calls[0].url, /symbol=600519\.SH/)
})

test('ts 秒 -> 毫秒，字段透传', async () => {
  resetKlineCache()
  stubFetch(() => ({ body: { code: 0, data: [{ ts: 1000, open: 1, high: 2, low: 0.5, close: 1.5, volume: 9 }] } }))
  const rows = await fetchKline({ symbol: '600519.SH' })
  assert.equal(rows[0].timestamp, 1_000_000)
  assert.equal(rows[0].close, 1.5)
  assert.equal(rows[0].volume, 9)
})

test('computeChange：两根以上给涨跌幅', () => {
  const rows = [
    { timestamp: 1, open: 0, high: 0, low: 0, close: 100, volume: 0 },
    { timestamp: 2, open: 0, high: 0, low: 0, close: 110, volume: 0 },
  ]
  const r = computeChange(rows)
  assert.equal(r?.close, 110)
  assert.equal(r?.pctChange, 10)
})

test('computeChange：只有一根时涨跌幅为 null 而不是 0', () => {
  const rows = [{ timestamp: 1, open: 0, high: 0, low: 0, close: 100, volume: 0 }]
  const r = computeChange(rows)
  assert.equal(r?.close, 100)
  // 关键：不能是 0。0 会被渲染成「+0.00%」，看起来像一个确定的涨跌值。
  assert.equal(r?.pctChange, null)
})

test('computeChange：空数据返回 null', () => {
  assert.equal(computeChange([]), null)
})
