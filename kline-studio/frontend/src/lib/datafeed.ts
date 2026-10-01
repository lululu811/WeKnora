import type {
  DatafeedSubscribeCallback,
  KLineData,
  Period,
  SymbolInfo,
} from '@/types/klinecharts-pro';

const API_BASE = '/api';

interface ApiEnvelope<T> {
  code: number;
  message?: string;
  data: T;
}

async function httpGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`);
  const env = (await res.json()) as ApiEnvelope<T>;
  if (env.code !== 0) throw new Error(env.message ?? 'API error');
  return env.data;
}

interface BackendKLine {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  turnover?: number;
}

interface BackendSymbol {
  thscode: string;
  ticker: string;
  name: string | null;
  exchange: string | null;
  asset_type: string | null;
}

type Adjust = 'none' | 'forward' | 'backward';

export interface ZettarancDatafeedOptions {
  adjust?: Adjust;
}

/**
 * Zettaranc Datafeed —— 纯日线复盘
 *
 * 产品定位：盘后复盘。不做盘中实时报价。
 * subscribe/unsubscribe 是 no-op（仍实现接口以满足 KLineChart Pro 类型约束）。
 */
export class ZettarancDatafeed {
  private adjust: Adjust;

  constructor(opts: ZettarancDatafeedOptions = {}) {
    this.adjust = opts.adjust ?? 'none';
  }

  async searchSymbols(search = ''): Promise<SymbolInfo[]> {
    const rows = await httpGet<BackendSymbol[]>(
      `/symbols/search?q=${encodeURIComponent(search.trim())}`,
    );
    return rows.map(this.toProSymbol);
  }

  async getHistoryKLineData(
    symbol: SymbolInfo,
    period: Period,
    _from: number,
    _to: number,
  ): Promise<KLineData[]> {
    const timespan = period?.timespan;
    const periodParam = timespan === 'week' ? 'week' : timespan === 'month' ? 'month' : 'day';
    const symbolStr = `${symbol.ticker}.${symbol.exchange}`;

    // 并行获取 K 线和指标
    const [klineRows, indicatorRows] = await Promise.all([
      httpGet<BackendKLine[]>(
        `/kline?symbol=${encodeURIComponent(symbolStr)}` +
          `&adjust=${this.adjust}&period=${periodParam}&limit=5000`,
      ),
      httpGet<any[]>(
        `/indicators?symbol=${encodeURIComponent(symbolStr)}` +
          `&days=5000&categories=zettaranc`,
      ).catch(() => []),
    ]);

    // 建立日期 → 指标 映射
    const indMap = new Map<string, any>();
    indicatorRows.forEach((row) => {
      const date = String(row.date).slice(0, 10);
      indMap.set(date, row);
    });

    return klineRows.map((r) => {
      const date = new Date(r.ts * 1000).toISOString().slice(0, 10);
      const ind = indMap.get(date) || {};
      return {
        // KLineChart Pro 用 new Date(timestamp) 解析，期望毫秒级；
        // 后端 DuckDB DATE 转的 ts 是秒级，乘 1000 上送。
        timestamp: r.ts * 1000,
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
        volume: r.volume,
        turnover: r.turnover,
        // Zettaranc 专属指标（注入到 KLineData，供自定义指标使用）
        // 列名与 a-stock/scripts/add_zettaranc_columns.py 的 NEW_COLUMNS 一致。
        // RSL 两列 2026-10-01 由 *_short_3 / *_long_21 改名为
        // *_rank_15 / *_rank_105 —— 它是滚动窗口百分位排名，不是涨跌幅。
        zg_white: ind.zettaranc_zg_white_10 || null,
        dg_yellow: ind.zettaranc_dg_yellow_14 || null,
        bbi: ind.zettaranc_bbi || null,
        brick: ind.zettaranc_brick_value || null,
        rsl_short: ind.zettaranc_rsl_rank_15 || null,
        rsl_long: ind.zettaranc_rsl_rank_105 || null,
      } as KLineData;
    });
  }

  // 复盘场景不需要订阅：保留接口以满足 KLineChart Pro 类型约束，但 no-op。
  subscribe(
    _symbol: SymbolInfo,
    _period: Period,
    _callback: DatafeedSubscribeCallback,
  ): void {
    // intentionally empty
  }

  unsubscribe(_symbol: SymbolInfo, _period: Period): void {
    // intentionally empty
  }

  private toProSymbol = (s: BackendSymbol): SymbolInfo => ({
    exchange: s.exchange ?? 'SH',
    market: 'stocks',
    name: s.name ?? s.ticker,
    shortName: s.name ?? s.ticker,
    ticker: s.ticker,
    priceCurrency: 'cny',
    type: 'stock',
  });
}
