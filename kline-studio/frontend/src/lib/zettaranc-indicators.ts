/**
 * 注册 Zettaranc 专属指标到 KLineChart
 */
import { registerIndicator, LineType, IndicatorSeries } from 'klinecharts';

let registered = false;

const INDICATORS = [
  {
    name: 'ZG_WHITE',
    shortName: '白线',
    series: IndicatorSeries.Price,
    color: '#FFFFFF',
    plotKey: 'zg_white',
    plotTitle: '白线',
  },
  {
    name: 'DG_YELLOW',
    shortName: '黄线',
    series: IndicatorSeries.Price,
    color: '#FFD700',
    plotKey: 'dg_yellow',
    plotTitle: '黄线',
  },
  {
    name: 'Z_BBI',
    shortName: 'Z-BBI',
    series: IndicatorSeries.Normal,
    color: '#FF8C00',
    plotKey: 'bbi',
    plotTitle: 'BBI',
  },
  {
    name: 'Z_BRICK',
    shortName: 'Z-砖形',
    series: IndicatorSeries.Normal,
    color: '#FF4444',
    plotKey: 'brick',
    plotTitle: '砖形',
  },
  {
    name: 'Z_RSL_SHORT',
    shortName: 'Z-RSL短',
    series: IndicatorSeries.Normal,
    color: '#00BFFF',
    plotKey: 'rsl_short',
    plotTitle: 'RSL短',
  },
  {
    name: 'Z_RSL_LONG',
    shortName: 'Z-RSL长',
    series: IndicatorSeries.Normal,
    color: '#9370DB',
    plotKey: 'rsl_long',
    plotTitle: 'RSL长',
  },
];

export function registerZettarancIndicators() {
  if (registered) return;
  try {
    for (const ind of INDICATORS) {
      registerIndicator({
        name: ind.name,
        shortName: ind.shortName,
        icon: '',
        series: ind.series,
        calcParams: [],
        plots: [
          { key: ind.plotKey, title: ind.plotTitle, type: 'line', color: ind.color, lineStyle: LineType.Solid, lineSize: 1 },
        ],
        calc: (dataList: any[]) => dataList.map((d) => ({ [ind.plotKey]: d[ind.plotKey] ?? null })),
      } as any);
    }
    registered = true;
    console.log('[zettaranc] 6 indicators registered');
  } catch (err) {
    console.warn('[zettaranc] register skipped:', (err as Error).message);
  }
}