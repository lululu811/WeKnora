/**
 * klineCache — 行情请求的唯一入口与共享缓存。
 *
 * 此前 `StockCitationFloat`（hover 卡片）自带一份模块级缓存，图表侧自己走
 * datafeed 直接 fetch，多标的对比条又会再发一遍。同一只票在同一个屏幕上被
 * 三个组件各拉一次，而且缓存互不可见——hover 完再点开工作台，仍然要重新请求。
 *
 * 这里收敛成一个入口，三件事一次解决：
 *  1. 按 (symbol, period, adjust, limit) 缓存，TTL 内不重复打网络；
 *  2. **在途去重**：并发请求同一个 key 时共享同一个 Promise。缓存只能挡住
 *     「先后发生」的重复，挡不住「同时发生」的——而多标的对比条恰好是一批并发。
 *  3. 统一错误语义：HTTP 状态、非 JSON、业务 code 三类失败各自归类，沿用
 *     datafeed.ts 已经踩过的区分（404 = 没这只票；其余 = 链路故障）。
 */

import type { KLineData } from './types';

export type KlinePeriod = 'day' | 'week' | 'month';
export type KlineAdjust = 'none' | 'forward' | 'backward';

export interface KlineRequest {
  symbol: string;
  period?: KlinePeriod;
  adjust?: KlineAdjust;
  limit?: number;
}

/** 失败原因分类。调用方据此决定是「这只票没数据」还是「服务挂了」。 */
export type KlineFailureKind = 'no-data' | 'transient';

export class KlineFetchError extends Error {
  readonly kind: KlineFailureKind;

  constructor(kind: KlineFailureKind, message: string) {
    super(message);
    this.name = 'KlineFetchError';
    this.kind = kind;
  }
}

const DEFAULT_TTL = 60_000;
const REQUEST_TIMEOUT = 8000;

interface CacheEntry {
  at: number;
  rows: KLineData[];
}

/**
 * 缓存键必须带上 period/adjust/limit：同一个 symbol 在不同周期下的 K 线是
 * 完全不同的数据，只按 symbol 缓存会把日线当成周线画出来。
 */
function cacheKey(req: Required<KlineRequest>): string {
  return `${req.symbol}|${req.period}|${req.adjust}|${req.limit}`;
}

const cache = new Map<string, CacheEntry>();
/** 在途请求：挡住"同时发生"的重复，缓存只在请求返回后才生效。 */
const inflight = new Map<string, Promise<KLineData[]>>();

/** 测试用：清空缓存与在途表。 */
export function resetKlineCache(): void {
  cache.clear();
  inflight.clear();
}

/** 测试用：当前缓存条目数。 */
export function klineCacheSize(): number {
  return cache.size;
}

function normalize(raw: unknown): KLineData[] {
  const rows = Array.isArray(raw) ? raw : [];
  return rows.map((r: any) => ({
    timestamp: r.ts * 1000,
    open: r.open,
    high: r.high,
    low: r.low,
    close: r.close,
    volume: r.volume,
    turnover: r.turnover,
  }));
}

async function request(req: Required<KlineRequest>): Promise<KLineData[]> {
  const controller = new AbortController();
  // 用 globalThis 而非 window：这个模块要在 node:test 里跑（没有 window）。
  const timer = globalThis.setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  const url =
    `/api/kline?symbol=${encodeURIComponent(req.symbol)}` +
    `&adjust=${req.adjust}&period=${req.period}&limit=${req.limit}`;

  let resp: Response;
  try {
    resp = await fetch(url, { signal: controller.signal });
  } catch (err) {
    // AbortError 是超时；TypeError 是网络层失败。两者都是链路问题，不是"没数据"。
    const msg =
      err instanceof DOMException && err.name === 'AbortError'
        ? '请求超时'
        : err instanceof Error
          ? err.message
          : String(err);
    throw new KlineFetchError('transient', msg);
  } finally {
    globalThis.clearTimeout(timer);
  }

  let body: unknown;
  try {
    body = await resp.json();
  } catch {
    // 502 网关页 / 登录页 HTML 都会走到这里。
    throw new KlineFetchError('transient', `行情服务返回了非 JSON 响应（HTTP ${resp.status}）`);
  }

  if (!resp.ok) {
    // 只有 404 表示服务端确认这只票没有行情；4xx/5xx 其余都是故障。
    if (resp.status === 404) {
      throw new KlineFetchError('no-data', '本地无该标的的行情数据');
    }
    throw new KlineFetchError('transient', `行情服务返回 HTTP ${resp.status}`);
  }

  const code = (body as { code?: number } | null)?.code;
  if (code !== 0) {
    throw new KlineFetchError('transient', `行情服务返回 code=${String(code)}`);
  }

  const rows = normalize((body as { data?: unknown } | null)?.data);
  if (rows.length === 0) {
    // 请求成功、服务端也答了，只是没有任何一根 K 线。这才是"没这只票"。
    throw new KlineFetchError('no-data', '本地无该标的的行情数据');
  }
  return rows;
}

/**
 * 取一段 K 线。命中缓存直接返回；并发同 key 时共享同一个在途 Promise。
 *
 * 失败一律抛 `KlineFetchError`，调用方用 `err.kind` 分流：
 * `no-data` 是可预期的（代码不存在），`transient` 是要报警的（服务故障）。
 */
export function fetchKline(request_: KlineRequest): Promise<KLineData[]> {
  const req: Required<KlineRequest> = {
    symbol: request_.symbol,
    period: request_.period ?? 'day',
    adjust: request_.adjust ?? 'forward',
    limit: request_.limit ?? 300,
  };
  const key = cacheKey(req);

  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < DEFAULT_TTL) {
    return Promise.resolve(hit.rows);
  }

  const pending = inflight.get(key);
  if (pending) return pending;

  const promise = request(req)
    .then((rows) => {
      cache.set(key, { at: Date.now(), rows });
      return rows;
    })
    .finally(() => {
      inflight.delete(key);
    });

  inflight.set(key, promise);
  return promise;
}

/**
 * 取最新一根与上一根的收盘，算出涨跌幅。
 *
 * 少于两根时返回 null 而不是 0：只有一根时 `prev = last` 会让涨跌幅恒为 0，
 * 界面上显示成一个看起来很确定的「+0.00%」，实际是"没有数据"。
 */
export function computeChange(rows: KLineData[]): { close: number; pctChange: number | null } | null {
  if (rows.length === 0) return null;
  const last = rows[rows.length - 1];
  const prev = rows.length > 1 ? rows[rows.length - 2] : null;
  const pctChange =
    prev && prev.close > 0 ? Number((((last.close - prev.close) / prev.close) * 100).toFixed(2)) : null;
  return { close: last.close, pctChange };
}
