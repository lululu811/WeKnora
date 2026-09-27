/**
 * A 股代码的板块归属与交易所推断。
 *
 * 此前 hover 卡片（`klineTickerInjector`）和「本播提个股」条（`stock-score`）
 * 各自实现了一套识别逻辑，且结论互相矛盾：前者把括号里的裸码一律判成 .SH，
 * 后者查表推断。同一段文本在两个入口得到不同的 thscode，K 线取到的就是不同的票。
 * 这里收敛成唯一真相源，两边都从这里取。
 *
 * 只认前缀明确的板块。90/20（B 股）与 11/12/13/15/16/18（基金 / 债券）前缀不唯一，
 * 且 `20` 开头会撞上 YYYYMM 形态的日期（如 202609），因此这些一律返回 null，
 * 由调用方按「无法判定」处理，而不是猜一个交易所。
 */

export type AShareExchange = 'SH' | 'SZ' | 'BJ'

/**
 * 板块前缀 → 交易所。故意只列 A 股正股代码段：
 * 沪主板 600/601/603/605 与科创板 688 归 SH；
 * 深主板 000/001/002/003 与创业板 300/301 归 SZ；
 * 北交所 430/830-839/870-879/920 归 BJ。
 */
const EXCHANGE_BY_PREFIX: ReadonlyArray<readonly [RegExp, AShareExchange]> = [
  [/^6/, 'SH'],
  [/^00/, 'SZ'],
  [/^30/, 'SZ'],
  [/^(43|83|87|88|92)/, 'BJ'],
]

/**
 * 推断 6 位 A 股代码所属交易所；前缀不明确时返回 null。
 *
 * 注意 `^6` 而不是 `^60`：`688xxx`（科创板）也是沪市，但 `^6` 同样会接受
 * `699xxx` 这类不存在的代码——反正真实行情查不到，由下游返回空数据兜住，
 * 远比在这里堆一张完整的代码段表更容易维护。
 */
export function inferAShareExchange(ticker: string): AShareExchange | null {
  if (!/^\d{6}$/.test(ticker)) return null
  for (const [prefix, exchange] of EXCHANGE_BY_PREFIX) {
    if (prefix.test(ticker)) return exchange
  }
  return null
}

/** 该 6 位数字是否落在已知的 A 股板块前缀内。 */
export function isPlausibleAShareCode(ticker: string): boolean {
  return inferAShareExchange(ticker) !== null
}
