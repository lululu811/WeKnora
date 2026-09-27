export interface SymbolInfo {
  exchange: string;
  market: string;
  name: string;
  shortName: string;
  ticker: string;
  priceCurrency: string;
  type?: string;
}

export interface Period {
  multiplier: number;
  timespan: 'minute' | 'hour' | 'day' | 'week' | 'month' | 'year';
  text: string;
}

export interface KLineData {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  turnover?: number;
  [key: string]: any;
}

export type DatafeedSubscribeCallback = (data: KLineData) => void;
