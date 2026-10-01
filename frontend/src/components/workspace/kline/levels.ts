/**
 * levels — 支撑/阻力位计算，画在 K 线上的水平位来源。
 *
 * ## 为什么是前端算，而不是复用 Go 那个工具
 *
 * 后端已有 `hithink.finance.analysis.levels`，但它是**给 agent 用的工具**：
 * 前端既拿不到它的输出（它的 ToolResult.Data 为 nil，结果只在对话文本里），
 * 也没有 `/api/levels` 这样的 HTTP 端点。要在图上画线只有两条路——加一个
 * 后端端点，或者在本地按同样的公式算。
 *
 * 这里选本地算，理由是这张图要能显示**用户随手浏览到的任何标的**，而不只是
 * agent 恰好分析过的那几只。走端点意味着每换一只票就多一次请求，而 K 线数据
 * 已经在手里了（图表本就持有 5000 根 OHLC）。
 *
 * ## 与后端的公式必须一致
 *
 * 下面每个函数都是 `internal/agent/tools/hithink_finance/analysis/levels.go`
 * 的逐行对照实现（`calcPivotPoints` / `calcFibonacci` / `roundNumbers` /
 * `findSwings` / `findNearest`）。**改一边必须改另一边**：同一个价位如果
 * agent 在回答里报一个数、图上画另一个数，用户就没有可信的依据了。
 * `levels.test.ts` 里钉住的是公式本身，不是随手抄的期望值。
 *
 * 只实现纯 OHLC 的那几类（枢轴点、斐波那契、摆动高低点、整数关口）。
 * 均线/布林/VWAP 依赖后端那条派生指标管线，前端重算会引入第二种口径，
 * 而图上本来就有均线指标，不需要再来一套。
 */

/**
 * 计算关键位真正需要的最小字段。
 *
 * 刻意不写成仓库里的 `KLineData`（那个要求 `volume: number`）：关键位只用
 * 得到高低收，而图表的底层库把 volume 声明成可选。写宽了就会在两个数据源
 * 之间产生一次不必要的类型强转，而强转会把「字段真的对不上」这种问题一起吞掉。
 * 声明成最小形状后，两边都天然满足，调用处不需要任何 cast。
 */
export interface LevelBar {
  timestamp: number;
  high: number;
  low: number;
  close: number;
}

export interface PivotPoints {
  pp: number;
  r1: number;
  r2: number;
  r3: number;
  s1: number;
  s2: number;
  s3: number;
}

export interface FibonacciLevels {
  high: number;
  low: number;
  /** 键是百分比字符串（'23.6' 等），与后端输出一致。 */
  levels: Record<string, number>;
}

export interface SwingLevel {
  price: number;
  index: number;
  type: 'swing_high' | 'swing_low';
}

export interface NearestLevel {
  price: number;
  /** 来源名，如 `Pivot_S1` / `Fib_38.2%` / `Round_100`。 */
  source: string;
}

export interface ComputedLevels {
  price: number;
  pivot: PivotPoints;
  fibonacci: FibonacciLevels;
  swings: SwingLevel[];
  roundNumbers: { above: number[]; below: number[] };
  /** 全部候选位，带来源名。 */
  all: Array<{ name: string; price: number }>;
  nearestSupport: NearestLevel | null;
  nearestResistance: NearestLevel | null;
}

/** 与后端一致：斐波那契取这五档。 */
const FIB_PCTS: ReadonlyArray<readonly [string, number]> = [
  ['23.6', 0.236],
  ['38.2', 0.382],
  ['50.0', 0.5],
  ['61.8', 0.618],
  ['78.6', 0.786],
];

/** 摆动点的半窗口。后端调用处写死 5（`findSwings(rows, 5)`）。 */
const SWING_WINDOW = 5;

/** 后端每侧最多取 5 个摆动点。 */
const MAX_SWINGS_PER_SIDE = 5;

/** 对照 Go `calcPivotPoints`：经典枢轴点公式。 */
export function calcPivotPoints(h: number, l: number, c: number): PivotPoints {
  const pp = (h + l + c) / 3;
  const r = h - l;
  return {
    pp,
    r1: 2 * pp - l,
    r2: pp + r,
    r3: h + 2 * (pp - l),
    s1: 2 * pp - h,
    s2: pp - r,
    s3: l - 2 * (h - pp),
  };
}

/**
 * 对照 Go `calcFibonacci`。
 *
 * `trendDown` 的方向语义容易搞反：它是「当前价位于区间高点之下」，
 * 此时回撤位从**高点向下**排（`high - span*f`），也就是反弹的阻力位；
 * 否则从低点向上排。写反了整组线会镜像到错误的一侧。
 */
export function calcFibonacci(high: number, low: number, trendDown: boolean): FibonacciLevels {
  const span = high - low;
  const levels: Record<string, number> = {};
  for (const [label, f] of FIB_PCTS) {
    levels[label] = trendDown ? high - span * f : low + span * f;
  }
  return { high, low, levels };
}

/**
 * 对照 Go `roundNumbers`：按价格量级选步长，各取上下两个整数关口。
 *
 * ⚠️ 忠实复刻了后端循环上界 `v >= lower - step` 带来的一个不对称：
 * 当价格**恰好落在关口上**（`lower === price`）时，第一次迭代取到的正是价格
 * 本身、被 `< price` 挡掉，而循环随即到达下界退出，于是该侧只产出 **1** 个关口
 * 而不是 2 个。例：55 -> below `[50]`；105 -> below `[100, 90]`。
 *
 * 看起来像后端的 off-by-one（意图显然是"各取两个"），但这里**故意不修**：
 * 修了就会让前端画的位与 agent 在回答里报的位对不上，而"同一个价位两个数"
 * 比少一个关口严重得多。要改必须两边一起改，并由 `levels.test.ts` 的
 * 「恰好落在关口上」用例同时钉住。
 */
export function roundNumbers(price: number): { above: number[]; below: number[] } {
  let step: number;
  if (price > 1000) step = 50;
  else if (price > 100) step = 10;
  else if (price > 10) step = 5;
  else step = 1;

  const lower = Math.floor(price / step) * step;
  const upper = lower + step;

  const below: number[] = [];
  for (let v = lower; v >= lower - step && below.length < 2; v -= step) {
    if (v < price) below.push(v);
  }
  const above: number[] = [];
  for (let v = upper; v <= upper + step && above.length < 2; v += step) {
    if (v > price) above.push(v);
  }
  return { above, below };
}

/**
 * 对照 Go `findSwings`。
 *
 * 后端传进来的 rows 是**新到旧**（`rows[0]` 是最新一根），而这里的 `bars`
 * 是旧到新。为了与后端逐行一致，函数内部按新到旧遍历，返回的 `index` 是
 * **新到旧**序列里的下标（与后端一致），调用方若要映射回原数组需自行换算。
 */
export function findSwings(
  bars: readonly LevelBar[],
  window = SWING_WINDOW,
): { highs: number[]; lows: number[] } {
  // 新到旧
  const rows = [...bars].reverse();
  const n = rows.length;
  const highs: number[] = [];
  const lows: number[] = [];

  for (let i = window; i < n - window; i++) {
    let isHigh = true;
    let isLow = true;
    for (let j = i - window; j <= i + window; j++) {
      if (j === i) continue;
      if (rows[j].high >= rows[i].high) isHigh = false;
      if (rows[j].low <= rows[i].low) isLow = false;
    }
    if (isHigh) highs.push(i);
    if (isLow) lows.push(i);
  }
  return { highs, lows };
}

/** 对照 Go `findNearest`：价格下方最近的位是支撑，上方最近的是阻力。 */
export function findNearest(
  levels: Array<{ name: string; price: number }>,
  price: number,
): { support: NearestLevel | null; resistance: NearestLevel | null } {
  let support: NearestLevel | null = null;
  let resistance: NearestLevel | null = null;
  for (const lv of levels) {
    if (lv.price < price) {
      if (!support || lv.price > support.price) support = { price: lv.price, source: lv.name };
    } else if (lv.price > price) {
      if (!resistance || lv.price < resistance.price) resistance = { price: lv.price, source: lv.name };
    }
  }
  return { support, resistance };
}

/**
 * 汇总一段 K 线上的全部关键位。
 *
 * 数据不足（少于 2 根，或最新价为 0）时返回 null——调用方据此不画任何线，
 * 而不是画一堆由 0 或 NaN 算出来的位。
 */
export function computeLevels(bars: readonly LevelBar[]): ComputedLevels | null {
  if (bars.length < 2) return null;
  // 后端用 rows[0]（最新一根）作为基准。
  const latest = bars[bars.length - 1];
  const price = latest.close;
  if (!Number.isFinite(price) || price <= 0) return null;

  const pivot = calcPivotPoints(latest.high, latest.low, latest.close);

  // 区间高低点扫全量，与后端一致（顺序无关）。
  let fibHigh = bars[0].high;
  let fibLow = bars[0].low;
  for (const b of bars) {
    if (b.high > fibHigh) fibHigh = b.high;
    if (b.low < fibLow) fibLow = b.low;
  }
  const fibonacci = calcFibonacci(fibHigh, fibLow, price < fibHigh);

  const { highs, lows } = findSwings(bars);
  const newestFirst = [...bars].reverse();
  const swings: SwingLevel[] = [];
  for (let i = 0; i < highs.length && i < MAX_SWINGS_PER_SIDE; i++) {
    const idx = highs[i];
    swings.push({ price: newestFirst[idx].high, index: idx, type: 'swing_high' });
  }
  for (let i = 0; i < lows.length && i < MAX_SWINGS_PER_SIDE; i++) {
    const idx = lows[i];
    swings.push({ price: newestFirst[idx].low, index: idx, type: 'swing_low' });
  }

  const rounds = roundNumbers(price);

  const all: Array<{ name: string; price: number }> = [];
  const add = (name: string, v: number) => {
    // 与后端一致：只收正数。0 表示"没算出来"，不是价位。
    if (Number.isFinite(v) && v > 0) all.push({ name, price: v });
  };
  add('Pivot_PP', pivot.pp);
  add('Pivot_R1', pivot.r1);
  add('Pivot_R2', pivot.r2);
  add('Pivot_R3', pivot.r3);
  add('Pivot_S1', pivot.s1);
  add('Pivot_S2', pivot.s2);
  add('Pivot_S3', pivot.s3);
  for (const [label, v] of Object.entries(fibonacci.levels)) add(`Fib_${label}%`, v);
  for (const s of swings) {
    add(`${s.type === 'swing_high' ? 'SwingHigh' : 'SwingLow'}_${s.index}`, s.price);
  }
  for (const v of rounds.above) add(`Round_${v}`, v);
  for (const v of rounds.below) add(`Round_${v}`, v);

  const { support, resistance } = findNearest(all, price);

  return {
    price,
    pivot,
    fibonacci,
    swings,
    roundNumbers: rounds,
    all,
    nearestSupport: support,
    nearestResistance: resistance,
  };
}

/**
 * 从全部候选位里挑出**该画到图上**的那些。
 *
 * 直接全画会有二十多条线，把 K 线糊死——关键位之所以叫关键位，是因为稀少。
 * 这里每侧只保留最近的 N 个，并且**先按价位合并**：枢轴点、斐波那契、
 * 摆动点在同一个价位上经常重合，重合本身就是最强的信号，画成多条重叠的线
 * 只会让颜色变深、看不出是"多个依据指向同一个价位"。
 *
 * 返回的顺序是价位从低到高，便于调用方稳定地分配颜色。
 */
export function pickChartLevels(
  levels: ComputedLevels,
  maxEachSide = 3,
  /** 价格差小于该比例视为同一个价位。默认 0.5%。 */
  mergeTolerance = 0.005,
): Array<{ price: number; sources: string[]; side: 'support' | 'resistance' }> {
  const price = levels.price;
  const tol = price * mergeTolerance;

  const below = levels.all.filter((l) => l.price < price).sort((a, b) => b.price - a.price);
  const above = levels.all.filter((l) => l.price > price).sort((a, b) => a.price - b.price);

  const collect = (
    sorted: Array<{ name: string; price: number }>,
    side: 'support' | 'resistance',
  ): Array<{ price: number; sources: string[]; side: 'support' | 'resistance' }> => {
    const out: Array<{ price: number; sources: string[]; side: 'support' | 'resistance' }> = [];
    for (const lv of sorted) {
      const hit = out.find((g) => Math.abs(g.price - lv.price) <= tol);
      if (hit) {
        hit.sources.push(lv.name);
        continue;
      }
      if (out.length >= maxEachSide) continue;
      out.push({ price: lv.price, sources: [lv.name], side });
    }
    return out;
  };

  const sup = collect(below, 'support');
  const res = collect(above, 'resistance');
  return [...sup, ...res].sort((a, b) => a.price - b.price);
}
