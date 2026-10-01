/**
 * 跨栈一致性检查的 JS 侧。
 *
 * 由 internal/indicators/conformance_test.go 调用：
 *
 *   cd frontend && npx tsx --tsconfig tsconfig.app.json conformance/indicator-oracle.ts
 *
 * 输入是一段 JSON K 线（stdin），输出是同样格式的 JSON（stdout）。Go 侧用同一段
 * 输入、同一套来自 config/indicators.yaml 的参数算一遍，然后逐点比。
 *
 * 这里 import 的是**前端真正在用的** calc 函数，不是另写一份 —— 否则比的是自己跟
 * 自己，等于没比。周期一律从 config/indicators.yaml 生成的 indicator-meta.ts 读，
 * 代码里不写死数字。
 *
 * 为什么用 --tsconfig tsconfig.app.json：indicators.ts 会 import 到用了 `@/`
 * 别名的 stock-score.ts，tsx 默认不认别名，得显式指到 app 的 tsconfig。
 */

import { calcDEMA, calcLongBBI, calcZXBrick, type KLineData } from '../src/components/workspace/kline/stock-score';
import {
  calcBBI,
  calcSMA,
  calcKDJ,
  calcMACD,
  calcPctRet,
  calcVOL,
} from '../src/components/workspace/kline/indicators';
import { indicatorMeta } from '../src/components/workspace/kline/indicator-meta';

interface OracleSeries {
  key: string;
  values: Array<number | null>;
}

interface OracleResult {
  indicators: Record<string, OracleSeries[]>;
}

function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    let buf = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => {
      buf += chunk;
    });
    process.stdin.on('end', () => resolve(buf));
    process.stdin.on('error', reject);
  });
}

function params(id: string): number[] {
  return indicatorMeta(id).params.map((p) => p.value);
}

function seriesParams(id: string, index: number): number[] {
  return indicatorMeta(id).series[index].params;
}

function namedParam(id: string, name: string): number {
  const found = indicatorMeta(id).params.find((p) => p.name === name);
  if (!found) {
    throw new Error(`config/indicators.yaml: ${id} 没有名为 ${name} 的参数`);
  }
  return found.value;
}

async function run() {
  const input = await readStdin();
  const bars = JSON.parse(input) as KLineData[];
  const result: OracleResult = { indicators: {} };

  // ZG_WHITE — 白线 DEMA(EMA(c,n),n)
  result.indicators.ZG_WHITE = [
    { key: 'zg_white', values: calcDEMA(bars, seriesParams('ZG_WHITE', 0)[0]) },
  ];

  // DG_YELLOW — 多空线 (MA14+MA28+MA57+MA114)/4
  result.indicators.DG_YELLOW = [
    { key: 'dg_yellow', values: calcLongBBI(bars, seriesParams('DG_YELLOW', 0)) },
  ];

  // Z_BBI — 牵牛绳 (MA3+MA6+MA12+MA24)/4
  result.indicators.Z_BBI = [{ key: 'bbi', values: calcBBI(bars) }];

  // BBI 的四条均线原样导出：Go↔JS 差一分钱时，只有看到两边各自的 MA 才能
  // 判清是"公式不同"还是"同一条 MA 在小数第三位四舍五入上分歧"。
  result.indicators.__BBI_MA = indicatorMeta('Z_BBI')
    .series[0]
    .params.map((p, i) => ({ key: `ma${p}`, values: calcSMA(bars, p) }));
  result.indicators.__LONG_BBI_MA = indicatorMeta('DG_YELLOW')
    .series[0]
    .params.map((p, i) => ({ key: `ma${p}`, values: calcSMA(bars, p) }));

  // Z_MAIN — 复合主图：三条线必须与各自独立指标一致
  const mainWhite = indicatorMeta('Z_MAIN').series.find((s) => s.formula === 'DEMA')!;
  const mainYellow = indicatorMeta('Z_MAIN').series.find((s) => s.formula === 'LONGBBI')!;
  const mainBbi = indicatorMeta('Z_MAIN').series.find((s) => s.formula === 'BBI')!;
  result.indicators.Z_MAIN = [
    { key: mainWhite.key, values: calcDEMA(bars, mainWhite.params[0]) },
    { key: mainYellow.key, values: calcLongBBI(bars, mainYellow.params) },
    { key: mainBbi.key, values: calcBBI(bars) },
  ];

  // Z_VOL — 均量线。总量柱是原始值，比均量线没有信息量，交给 Go 侧对公式。
  const vol = calcVOL(bars);
  result.indicators.Z_VOL = [
    { key: 'ma5', values: vol.map((v) => v.ma5) },
    { key: 'ma10', values: vol.map((v) => v.ma10) },
  ];

  // Z_MACD — (12,26,9)
  result.indicators.Z_MACD = (() => {
    const [short, long, signal] = params('Z_MACD');
    const macd = calcMACD(bars, short, long, signal);
    return [
      { key: 'dif', values: macd.map((m) => m.dif) },
      { key: 'dea', values: macd.map((m) => m.dea) },
      { key: 'macd', values: macd.map((m) => m.macd) },
    ];
  })();

  // Z_KDJ — (9,3,3)
  result.indicators.Z_KDJ = (() => {
    const kdj = calcKDJ(bars, namedParam('Z_KDJ', 'n'));
    return [
      { key: 'k', values: kdj.map((v) => v.k) },
      { key: 'd', values: kdj.map((v) => v.d) },
      { key: 'j', values: kdj.map((v) => v.j) },
    ];
  })();

  // Z_PCT_RET — 3 日 / 21 日两个周期的**涨跌幅**（2026-10-01 由 Z_RSL 改名）。
  const [retShort, retLong] = params('Z_PCT_RET');
  const retSeries = indicatorMeta('Z_PCT_RET').series;
  result.indicators.Z_PCT_RET = [
    { key: retSeries[0].key, values: calcPctRet(bars, retShort) },
    { key: retSeries[1].key, values: calcPctRet(bars, retLong) },
  ];

  // ZX_BRICK / Z_BRICK — 砖型图。两者是同一份实现，必须逐点相同。
  const brickKey = indicatorMeta('ZX_BRICK').series[0].key;
  for (const id of ['ZX_BRICK', 'Z_BRICK']) {
    result.indicators[id] = [
      { key: brickKey, values: calcZXBrick(bars).map((b) => b.brick) },
    ];
  }

  process.stdout.write(JSON.stringify(result) + '\n');
}

run().catch((err) => {
  process.stderr.write(String((err as Error)?.stack ?? err) + '\n');
  process.exit(1);
});
