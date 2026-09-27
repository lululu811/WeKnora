import { type DeepPartial, type Styles, CandleType, LineType, PolygonType, TooltipShowRule, TooltipShowType } from 'klinecharts';
import { zettarancPalette } from './palette';

/**
 * KLineChart 画布主题（红涨绿跌，符合 A 股习惯）。
 *
 * 画布跟随平台主题，浅色/深色两套。战法主图那几条线的颜色不在这里配，
 * 而是从 palette.ts 取——因为 indicators.ts 里的自定义指标也要用同一套色，
 * 两边各写一份必然漂移。
 */
export function getKlineChartTheme(isDark: boolean): DeepPartial<Styles> {
  const p = zettarancPalette();

  // 语义色：红涨绿跌。色相在任何底色下都成立，但**明度**必须跟主题走——
  // 亮绿在暖米底上只有 2.4:1，看不见；深色底上反过来 7.3:1 正好。
  const upColor = p.up;
  const downColor = p.down;
  const noChangeColor = '#888888';

  // 浅色主题是**暖米色**而非纯白：纯白当大面积底色长时间看刺眼。选暖色就得
  // 整套一起暖——网格/坐标轴如果还用冷灰（#eef0f4 / #dfe3ea），铺在米底上会
  // 泛蓝，反而比纯白更难看。所以下面这些浅色值都是带一点黄调的。
  const bgColor = isDark ? '#11141a' : '#FAF7F0';
  const surfaceColor = isDark ? '#161b24' : '#FFFDF8';
  const gridColor = isDark ? '#171c25' : '#EDE7DA';
  const axisLineColor = isDark ? '#262d3a' : '#DDD5C6';
  const textColor = isDark ? '#94a3b8' : '#6B6259';
  const strongTextColor = isDark ? '#c9d1d9' : '#2A2520';
  const crosshairLineColor = isDark ? '#475569' : '#BFB4A2';

  return {
    candle: {
      type: CandleType.CandleSolid,
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
      tooltip: {
        showRule: TooltipShowRule.FollowCross,
        showType: TooltipShowType.Rect,
        text: {
          color: strongTextColor,
          size: 11,
          family: 'monospace',
          marginLeft: 8,
          marginTop: 6,
          marginRight: 8,
          marginBottom: 6,
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
          upColor,
          downColor,
          noChangeColor,
        },
      ],
      lines: [
        { size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: p.white },
        { size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: p.yellow },
        { size: 1.8, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: p.orange },
        { size: 1.5, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: p.sky },
        { size: 1.5, style: LineType.Solid, smooth: false, dashedValue: [2, 2], color: p.purple },
      ],
      lastValueMark: {
        show: true,
        text: { color: strongTextColor, size: 10, family: 'monospace' },
      },
      tooltip: {
        showRule: TooltipShowRule.FollowCross,
        showType: TooltipShowType.Standard,
        text: {
          color: strongTextColor,
          size: 11,
          family: 'monospace',
          marginLeft: 8,
          marginTop: 6,
          marginRight: 8,
          marginBottom: 6,
        },
      },
    },
    // 网格改虚线并压暗，让它退到背景层：横竖实线会把画面切成碎块，
    // 而蜡烛和主图线才是主角。
    grid: {
      show: true,
      horizontal: { show: true, color: gridColor, style: LineType.Dashed, dashedValue: [2, 3] },
      vertical: { show: true, color: gridColor, style: LineType.Dashed, dashedValue: [2, 3] },
    },
    separator: {
      size: 1,
      color: axisLineColor,
      fill: true,
      activeBackgroundColor: isDark ? '#1f2733' : '#f0f2f6',
    },
    crosshair: {
      show: true,
      horizontal: {
        show: true,
        line: { color: crosshairLineColor, dashedValue: [4, 4], size: 1 },
        text: { color: strongTextColor, backgroundColor: surfaceColor, size: 11, family: 'monospace' },
      },
      vertical: {
        show: true,
        line: { color: crosshairLineColor, dashedValue: [4, 4], size: 1 },
        text: { color: strongTextColor, backgroundColor: surfaceColor, size: 11, family: 'monospace' },
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
