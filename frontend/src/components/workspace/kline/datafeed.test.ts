import assert from 'node:assert/strict'
import test from 'node:test'

import { ZettarancDatafeed, type KLineErrorKind } from './datafeed.ts'
import type { KLineData, Period, SymbolInfo } from './types.ts'

/**
 * 这些用例钉住的是「取不到数据」的三种含义必须分开报：
 *   - 404 / 200+空数组 → onNoData（服务端确认该标的没有数据）
 *   - 4xx 带原因      → onError(kind='request')（服务端明确拒绝，重试无意义）
 *   - 网络/5xx/非 JSON → onError(kind='chain')（链路或服务端故障，可重试）
 *
 * 合并任意两种的代价都很大：曾经把网络失败吞成空数组，于是 nginx 502 被显示成
 * 「本地无该标的行情」；后来又把 422 一律显示成「取数链路的问题」，于是
 * 「这个接口不支持该标的」被显示成「去查 python-service 容器和 nginx 代理」。
 */

const BOARD: SymbolInfo = {
  exchange: 'TI',
  market: 'stocks',
  name: '种植业与林业',
  shortName: '种植业与林业',
  ticker: '881101',
  priceCurrency: 'cny',
  type: 'board',
}

const STOCK: SymbolInfo = {
  exchange: 'SH',
  market: 'stocks',
  name: '贵州茅台',
  shortName: '贵州茅台',
  ticker: '600519',
  priceCurrency: 'cny',
  type: 'stock',
}

const DAY: Period = { multiplier: 1, timespan: 'day', text: '日K' }

interface StubResult {
  status?: number
  body?: unknown
  /** 让 fetch 本身抛错（网络层失败）。 */
  throw?: unknown
  /** 让 resp.json() 抛错（非 JSON 响应，如 nginx 错误页）。 */
  nonJson?: boolean
}

/** 装一个 fetch 桩，返回它收到的 URL 列表。 */
function stubFetch(result: StubResult): { calls: string[] } {
  const calls: string[] = []
  // 桩只需要 Response 的 ok/status/json 三个成员，其余成员用不到；
  // 测试里没有真实 Response 可造，只能在这里做一次显式转换。
  const stub = async (input: unknown): Promise<Partial<Response>> => {
    calls.push(String(input))
    if (result.throw) throw result.throw
    const status = result.status ?? 200
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => {
        if (result.nonJson) throw new SyntaxError('Unexpected token < in JSON')
        return result.body
      },
    }
  }
  globalThis.fetch = stub as unknown as typeof fetch
  return { calls }
}

/** 建一个 datafeed 并收集三类回调。 */
function collect() {
  const errors: Array<{ message: string; kind: KLineErrorKind }> = []
  const noData: string[] = []
  let loaded: KLineData[] | null = null
  const feed = new ZettarancDatafeed({
    onError: (_symbol, message, kind) => errors.push({ message, kind }),
    onNoData: (symbol) => noData.push(`${symbol.ticker}.${symbol.exchange}`),
    onDataLoaded: (data) => {
      loaded = data
    },
  })
  return { errors, noData, feed, loaded: () => loaded }
}

test('4xx 带原因的拒绝归为 request，既不报成"无数据"也不报成链路故障', async () => {
  stubFetch({
    status: 422,
    body: { detail: { success: false, error: 'thscode 格式非法：881101.TI' } },
  })
  const c = collect()

  const data = await c.feed.getHistoryKLineData(BOARD, DAY, 0, 0)

  assert.deepEqual(data, [])
  assert.deepEqual(c.noData, [], '4xx 不能走 onNoData：服务端没说"没有数据"')
  assert.equal(c.errors.length, 1)
  assert.equal(c.errors[0].kind, 'request')
  // 服务端给的原因必须带出来，否则用户只看到"查询失败"却不知道该改什么
  assert.match(c.errors[0].message, /thscode 格式非法/)
})

test('5xx 归为 chain（服务端故障，可重试）', async () => {
  stubFetch({ status: 503, body: { detail: { error: 'market 数据源未就绪' } } })
  const c = collect()

  await c.feed.getHistoryKLineData(STOCK, DAY, 0, 0)

  assert.deepEqual(c.noData, [])
  assert.equal(c.errors.length, 1)
  assert.equal(c.errors[0].kind, 'chain')
  assert.match(c.errors[0].message, /数据源未就绪/)
})

test('网络层失败归为 chain，且不误报成"无该标的行情"', async () => {
  stubFetch({ throw: new TypeError('Failed to fetch') })
  const c = collect()

  await c.feed.getHistoryKLineData(STOCK, DAY, 0, 0)

  assert.deepEqual(c.noData, [], '网络失败必须留在 onError，不能吞成空数组')
  assert.equal(c.errors[0].kind, 'chain')
  assert.match(c.errors[0].message, /无法连接行情服务/)
})

test('非 JSON 响应（nginx 错误页）归为 chain', async () => {
  stubFetch({ status: 502, nonJson: true })
  const c = collect()

  await c.feed.getHistoryKLineData(STOCK, DAY, 0, 0)

  assert.equal(c.errors.length, 1)
  assert.equal(c.errors[0].kind, 'chain')
  assert.match(c.errors[0].message, /非 JSON/)
})

test('404 与 200+空数组都走 onNoData，不走 onError', async () => {
  stubFetch({ status: 404, body: { detail: '未找到股票 600519.SH 的行情数据' } })
  let c = collect()
  await c.feed.getHistoryKLineData(STOCK, DAY, 0, 0)
  assert.deepEqual(c.errors, [])
  assert.deepEqual(c.noData, ['600519.SH'])

  stubFetch({ status: 200, body: { code: 0, data: [] } })
  c = collect()
  await c.feed.getHistoryKLineData(STOCK, DAY, 0, 0)
  assert.deepEqual(c.errors, [], '合法格式 + 空数组不是故障')
  assert.deepEqual(c.noData, ['600519.SH'])
})

test('HTTP 200 但 code!==0 归为 chain，不是"无数据"', async () => {
  stubFetch({ status: 200, body: { code: 5001, message: '内部查询失败' } })
  const c = collect()

  await c.feed.getHistoryKLineData(STOCK, DAY, 0, 0)

  assert.deepEqual(c.noData, [])
  assert.equal(c.errors.length, 1)
  assert.equal(c.errors[0].kind, 'chain')
  assert.match(c.errors[0].message, /内部查询失败/)
})

test('成功响应映射成 KLineData（板块标的同样走这条路径）', async () => {
  stubFetch({
    status: 200,
    body: {
      code: 0,
      data: [
        { ts: 1_758_758_400, open: 1131.875, high: 1133.265, low: 1120.563, close: 1123.303, volume: 1_490_288_800, turnover: 0 },
      ],
    },
  })
  const c = collect()

  const data = await c.feed.getHistoryKLineData(BOARD, DAY, 0, 0)

  assert.equal(data.length, 1)
  assert.deepEqual(c.errors, [])
  assert.deepEqual(c.noData, [])
  assert.equal(data[0].timestamp, 1_758_758_400 * 1000, 'ts 是秒，要乘 1000')
  assert.equal(data[0].close, 1123.303)
  assert.deepEqual(c.loaded(), data)
})

test('searchSymbols 把板块行标成 board，个股行不变', async () => {
  stubFetch({
    status: 200,
    body: {
      code: 0,
      data: [
        { thscode: '600519.SH', ticker: '600519', name: '贵州茅台', exchange: 'SH', asset_type: 'a-share' },
        { thscode: '881105.TI', ticker: '881105', name: '煤炭开采加工', exchange: 'TI', asset_type: 'industry' },
      ],
    },
  })
  const c = collect()

  const rows = await c.feed.searchSymbols('煤炭')

  assert.deepEqual(
    rows.map((r) => ({ ticker: r.ticker, exchange: r.exchange, type: r.type, market: r.market })),
    [
      { ticker: '600519', exchange: 'SH', type: 'stock', market: 'stocks' },
      // 板块必须被标出来：写死 type:'stock' 的话，下游就不会置灰复权选择器、
      // 也不会跳过只服务个股的形态端点。
      { ticker: '881105', exchange: 'TI', type: 'board', market: 'boards' },
    ],
  )
  assert.equal(rows[1].name, '煤炭开采加工')
})
