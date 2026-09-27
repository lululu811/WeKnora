import type { DatafeedSubscribeCallback, KLineData, Period, SymbolInfo } from './types';

export type Adjust = 'none' | 'forward' | 'backward';

export interface ZettarancDatafeedOptions {
  adjust?: Adjust;
  onDataLoaded?: (data: KLineData[]) => void;
}

export class ZettarancDatafeed {
  private adjust: Adjust;
  private onDataLoaded?: (data: KLineData[]) => void;

  constructor(opts: ZettarancDatafeedOptions = {}) {
    this.adjust = opts.adjust ?? 'forward';
    this.onDataLoaded = opts.onDataLoaded;
  }

  setAdjust(adj: Adjust) {
    this.adjust = adj;
  }

  async searchSymbols(search = ''): Promise<SymbolInfo[]> {
    const q = encodeURIComponent(search.trim());
    const res = await fetch(`/api/symbols/search?q=${q}`);
    if (!res.ok) return [];
    const json = await res.json();
    const rows = json.data || [];
    return rows.map((s: any) => ({
      exchange: s.exchange ?? 'SH',
      market: 'stocks',
      name: s.name ?? s.ticker,
      shortName: s.name ?? s.ticker,
      ticker: s.ticker,
      priceCurrency: 'cny',
      type: 'stock',
    }));
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

    const klineRes = await fetch(
      `/api/kline?symbol=${encodeURIComponent(symbolStr)}&adjust=${this.adjust}&period=${periodParam}&limit=5000`,
    )
      .then((r) => r.json())
      .catch(() => ({ code: -1, data: [] }));

    const klineRows = klineRes.code === 0 ? klineRes.data : [];

    const dataList: KLineData[] = klineRows.map((r: any) => ({
      timestamp: r.ts * 1000,
      open: r.open,
      high: r.high,
      low: r.low,
      close: r.close,
      volume: r.volume,
      turnover: r.turnover,
    }));

    if (this.onDataLoaded && dataList.length > 0) {
      this.onDataLoaded(dataList);
    }

    return dataList;
  }

  subscribe(
    _symbol: SymbolInfo,
    _period: Period,
    _callback: DatafeedSubscribeCallback,
  ): void {
    // 纯复盘模式，不开启盘中推送
  }

  unsubscribe(_symbol: SymbolInfo, _period: Period): void {
    // 纯复盘模式
  }
}
