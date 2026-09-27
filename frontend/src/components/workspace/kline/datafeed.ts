import type { DatafeedSubscribeCallback, KLineData, Period, SymbolInfo } from './types';

export type Adjust = 'none' | 'forward' | 'backward';

export interface ZettarancDatafeedOptions {
  adjust?: Adjust;
  onDataLoaded?: (data: KLineData[]) => void;
  /**
   * 取不到任何行情时触发。单独开一个回调而不是让 onDataLoaded 收到空数组，
   * 是因为"数据加载成功但结果是空的"和"根本没加载"在 UI 上要区别对待：
   * 前者要显示"本地无该标的行情"，后者是加载中。
   */
  onNoData?: (symbol: SymbolInfo) => void;
}

export class ZettarancDatafeed {
  private adjust: Adjust;
  private onDataLoaded?: (data: KLineData[]) => void;
  private onNoData?: (symbol: SymbolInfo) => void;

  constructor(opts: ZettarancDatafeedOptions = {}) {
    this.adjust = opts.adjust ?? 'forward';
    this.onDataLoaded = opts.onDataLoaded;
    this.onNoData = opts.onNoData;
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
    } else if (dataList.length === 0) {
      // 空结果也要通知上层：最常见的原因是代码根本不存在（模型幻觉出来的
      // 代码段，或本地代码表没收录）。不通知的话图表就是一片黑，用户无从
      // 判断是加载中还是数据不存在。
      this.onNoData?.(symbol);
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
