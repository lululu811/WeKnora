/**
 * 行情数字的统一格式化与着色。
 *
 * 为什么单独一个文件：`MarketDashboard.vue`、`EtfWorkspace.vue`、design-lab 的
 * 几个页面各自抄了一份 `fmtPct` / `trendClass`。抄第三遍的时候就会开始漂 ——
 * 有的用 `−`（U+2212）有的用 `-`，有的把 0 染成灰有的留黑，而**红涨绿跌
 * 这条约定一旦有一处反了，那个数字就会被读成相反的结论**。
 *
 * 新代码一律从这里取。存量三处暂未合并（改动已验证过的页面风险大于收益），
 * 属于待办，不要在别的页面再抄第四份。
 *
 * 语义：
 * * 红 = 涨 / 流入（A 股惯例，**不跟随欧美配色**）
 * * 绿 = 跌 / 流出
 * * null 一律渲染成「—」，**绝不用 0 顶替**：0 是"真的没动"这个结论，
 *   null 是"没有数据/没有可比的上期"，混起来会读出一个不存在的判断。
 */

/** 缺少数据时统一用它。 */
export const DASH = '—'

/** 百分比。null → 「—」。正数带 +，负数用 U+2212 减号（与等宽数字对齐）。 */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH
  const sign = v > 0 ? '+' : v < 0 ? '−' : ''
  return `${sign}${Math.abs(v).toFixed(digits)}%`
}

/** 普通数字，固定小数位。null → 「—」。 */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH
  return v.toFixed(digits)
}

/** 金额（元）→ 亿 / 万，带正负号。null → 「—」。 */
export function fmtYi(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH
  const sign = v > 0 ? '+' : v < 0 ? '−' : ''
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(digits)} 亿`
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(digits)} 万`
  return `${sign}${abs.toFixed(0)}`
}

/**
 * 涨跌着色的 class。
 *
 * null 与 0 都归 `is-flat`：null 是"没数据"，与"平"视觉上同样不需要强调；
 * 但两者在数据层仍必须分开，格式化函数的 `—` 就是那个区分的证据。
 */
export function trendClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return 'is-flat'
  return v > 0 ? 'is-up' : 'is-down'
}
