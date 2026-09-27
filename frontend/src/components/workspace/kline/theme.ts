import { type DeepPartial, type Styles, PolygonType, LineType } from 'klinecharts';

/**
 * KLineChart 主题配置（红涨绿跌，符合 A 股用户习惯）
 */
export function getKlineTheme(isDark = true): DeepPartial<Styles> {
  const upColor = '#ef4444';
  const downColor = '#10b981';
  const noChangeColor = '#888888';

  const bgColor = isDark ? '#11141a' : '#ffffff';
  const gridColor = isDark ? '#1b2029' : '#f0f0f0';
  const axisLineColor = isDark ? '#262d3a' : '#e5e7eb';
  const textColor = isDark ? '#94a3b8' : '#6b7280';
  const crosshairLineColor = isDark ? '#475569' : '#9ca3af';

  return {
    candle: {
      type: 'candle_solid' as any,
      bar: {
        upColor,
        downColor,
        noChangeColor,
        upBorderColor: upColor,
        downBorderColor: downColor,
        upWickColor: upColor,
        downWickColor: downColor,
      },
      priceMark: {
        last: {
          show: true,
          upColor,
          downColor,
          noChangeColor,
          line: { show: true, dashedValue: [4, 4] },
          text: { show: true, color: '#ffffff' },
        },
        high: { show: true, color: upColor, textSize: 10 },
        low: { show: true, color: downColor, textSize: 10 },
      },
    },
    indicator: {
      bars: [
        {
          style: PolygonType.Fill,
          borderStyle: LineType.Solid,
          borderSize: 1,
          borderDashedValue: [2, 2],
          upColor,
          downColor,
          noChangeColor,
        },
      ],
      lines: [
        { size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: '#FFFFFF' },
        { size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: '#FFD700' },
        { size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: '#FF8C00' },
        { size: 1.5, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: '#00BFFF' },
        { size: 1.5, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: '#C084FC' },
      ],
    },
    grid: {
      show: true,
      horizontal: { show: true, color: gridColor },
      vertical: { show: true, color: gridColor },
    },
    crosshair: {
      show: true,
      horizontal: {
        show: true,
        line: { color: crosshairLineColor, dashedValue: [4, 4], size: 1 },
        text: { color: isDark ? '#c9d1d9' : '#1f2937', backgroundColor: bgColor, size: 11, family: 'monospace' },
      },
      vertical: {
        show: true,
        line: { color: crosshairLineColor, dashedValue: [4, 4], size: 1 },
        text: { color: isDark ? '#c9d1d9' : '#1f2937', backgroundColor: bgColor, size: 11, family: 'monospace' },
      },
    },
    xAxis: {
      show: true,
      axisLine: { color: axisLineColor },
      tickText: { color: textColor, size: 11, family: 'monospace' },
    },
    yAxis: {
      show: true,
      axisLine: { color: axisLineColor },
      tickText: { color: textColor, size: 11, family: 'monospace' },
    },
  };
}
