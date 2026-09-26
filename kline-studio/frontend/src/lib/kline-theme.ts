import { type DeepPartial, type Styles, PolygonType, LineType } from 'klinecharts';

/**
 * KLineChart 主题 —— A 股交易终端深色风
 * - 红涨绿跌（A 股习惯，与 global.css 一致）
 * - 深色背景 / 弱化网格 / 高对比度十字光标
 */
export const klineStyles: DeepPartial<Styles> = {
  candle: {
    bar: {
      // A 股：红涨绿跌
      upColor: '#ef4444',
      downColor: '#10b981',
      noChangeColor: '#888888',
      upBorderColor: '#ef4444',
      downBorderColor: '#10b981',
      upWickColor: '#ef4444',
      downWickColor: '#10b981',
    },
    priceMark: {
      last: {
        show: true,
        upColor: '#ef4444',
        downColor: '#10b981',
        noChangeColor: '#888888',
        line: {
          show: true,
          dashedValue: [4, 4],
        },
        text: {
          show: true,
          color: '#ffffff',
        },
      },
      high: {
        show: true,
        color: '#ef4444',
        textSize: 10,
      },
      low: {
        show: true,
        color: '#10b981',
        textSize: 10,
      },
    },
  },
  indicator: {
    bars: [
      {
        style: PolygonType.Fill,
        borderStyle: LineType.Solid,
        borderSize: 1,
        borderDashedValue: [2, 2],
        upColor: '#ef4444',
        downColor: '#10b981',
        noChangeColor: '#888888',
      },
    ],
    lines: [
      { size: 1, color: '#FF9600' },
    ],
  },
  grid: {
    show: true,
    horizontal: {
      show: true,
      color: '#1c2128',
    },
    vertical: {
      show: true,
      color: '#1c2128',
    },
  },
  crosshair: {
    show: true,
    horizontal: {
      show: true,
      line: { color: '#4b5563', dashedValue: [4, 4], size: 1 },
      text: {
        color: '#c9d1d9',
        backgroundColor: '#1c2128',
        size: 11,
        family: 'SF Mono, Menlo, monospace',
      },
    },
    vertical: {
      show: true,
      line: { color: '#4b5563', dashedValue: [4, 4], size: 1 },
      text: {
        color: '#c9d1d9',
        backgroundColor: '#1c2128',
        size: 11,
        family: 'SF Mono, Menlo, monospace',
      },
    },
  },
  xAxis: {
    show: true,
    axisLine: { color: '#21262d' },
    tickText: { color: '#8b949e', size: 11, family: 'SF Mono, Menlo, monospace' },
    tickLine: { color: '#21262d' },
  },
  yAxis: {
    show: true,
    axisLine: { color: '#21262d' },
    tickText: { color: '#8b949e', size: 11, family: 'SF Mono, Menlo, monospace' },
    tickLine: { color: '#21262d' },
  },
};
