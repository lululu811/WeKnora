/**
 * 「今天谁在动」页面的**纯展示映射**。
 *
 * 这些函数刻意从 `WatchPulse.vue` 里拆出来：条形长度、刻度位置、相对日期、代码归一
 * 都是"算错了会骗人但看截图看不出来"的地方（条形比例错一点，静态图上完全正常），
 * 放进组件就只能靠人眼验。拆出来就有单测。
 *
 * 三条不变量，改动前先读：
 *
 * 1. **量能方向与价格方向正交**。`multiple`（量）和 `pct_change`（价）是两根轴，
 *    红涨绿跌只染**价**那一根（A 股惯例，见 Watchlist.vue 的 `--wl-up/--wl-down`）；
 *    量能那一列用「放量/缩量」文字标签表达，不靠颜色。合并成一个红绿信号会把
 *    「缩量上涨」读成「跌」。
 *
 * 2. **倍数轴封顶**。一根 2.65 倍的条按线性画会把同屏其它行压扁，而 8 倍的极端票
 *    更是直接冲出容器。所以条长按 `PULSE_SCALE_CAP` 封顶，超出部分只到 cap 为止
 *    —— **但溢出必须留痕**（`isOverflowBar`），否则 2.5 倍和 8 倍在图上完全一样，
 *    而这两个结论是天差地别的。
 *
 * 3. **日期按日历天算**，不按毫秒除。`new Date('2026-10-09')` 在东八区解析成
 *    UTC 零点，本地时区不同会差一天；夏令时切换日 `ms / 86400000` 也会给出小数。
 *    两种做法都会让「今天」偶尔显示成「明天」，所以统一用 UTC 分量解析。
 */

/** 量能倍数轴的视觉上限。再大就压不进一行，但溢出仍由 `isOverflowBar` 标记。 */
export const PULSE_SCALE_CAP = 2.5

/** 标尺刻度：斐波那契回撤位 + 中枢。渲染成三条竖线，中枢那条加重。 */
export const PULSE_TICKS = [
  { value: 0.618, key: 'watchPulse.tickFib' },
  { value: 1.0, key: 'watchPulse.tickPivot' },
  { value: 1.382, key: 'watchPulse.tickFibHigh' },
] as const

/**
 * watchlist 的 `thscode` 带交易所后缀（'301190.SZ'），pulse 接口要的是裸 6 位码。
 * 两边归一化必须用**同一个**函数 —— 归一规则一旦分叉，名字就会错配到另一只票上，
 * 而错配出来的页面看上去完全正常。
 *
 * 非 A 股标的（板块 `.TI`）原样返回：让它走 pulse 走不通，在 `missing` 里带原因
 * 回来比在这里悄悄丢掉更可信。
 */
export function toBareCode(code: string): string {
  return code.split('.')[0].trim()
}

/** 条形宽度（%）。`multiple <= 0` / 非有限数一律给 0，而不是 NaN%。 */
export function barWidthPercent(multiple: number, cap: number = PULSE_SCALE_CAP): number {
  if (!Number.isFinite(multiple) || multiple <= 0 || cap <= 0) return 0
  return (Math.min(multiple, cap) / cap) * 100
}

/** 刻度线在标尺上的横向位置（%），与 `barWidthPercent` 共用同一把尺子。 */
export function tickPositionPercent(value: number, cap: number = PULSE_SCALE_CAP): number {
  if (!Number.isFinite(value) || cap <= 0) return 0
  return Math.min(Math.max(value / cap, 0), 1) * 100
}

/** 条形是否顶到封顶 —— 顶到了就说明真实倍数高于 cap，值得在数字旁标出来。 */
export function isOverflowBar(multiple: number, cap: number = PULSE_SCALE_CAP): boolean {
  return Number.isFinite(multiple) && multiple > cap
}

/** 相对日期标签的形态。具体文案交给 i18n，这里只给 key。 */
export type RelativeDayLabel =
  | { key: 'watchPulse.cal.today' }
  | { key: 'watchPulse.cal.tomorrow' }
  | { key: 'watchPulse.cal.inDays'; days: number }
  /** 理论上不该出现（接口返回过去的日子），但出现时必须有话可说，不能渲染 `+-2 天`。 */
  | { key: 'watchPulse.cal.daysAgo'; days: number }

const YMD_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

/**
 * `YYYY-MM-DD` → UTC 零点时间戳。时分秒一律归零，避开时区与夏令时。
 *
 * 必须**回读校验**：`Date.UTC` 不做范围检查，`Date.UTC(2026, 12, 9)` 会静默
 * 进位成 2027-01-09（`2026-13-09` 也就"解析成功"了），而 `2026-02-30` 会
 * 进位成 3-02。只判 `Number.isNaN` 的话这些脏日期会一路算出差 1 天、差 2 天的
 * 相对标签，页面上表现为「今天的事故显示在昨天」。所以解析后必须确认年月日
 * 三个分量原样回来。
 */
export function parseYmd(date: string): number | null {
  const m = YMD_PATTERN.exec(date.trim())
  if (!m) return null
  const year = Number(m[1])
  const month = Number(m[2])
  const day = Number(m[3])
  const ts = Date.UTC(year, month - 1, day)
  if (Number.isNaN(ts)) return null
  const d = new Date(ts)
  if (
    d.getUTCFullYear() !== year ||
    d.getUTCMonth() !== month - 1 ||
    d.getUTCDate() !== day
  ) {
    return null
  }
  return ts
}

/** 两个日历日之间相差几天（b - a）。格式非法返回 null。 */
export function dayDiff(a: string, b: string): number | null {
  const from = parseYmd(a)
  const to = parseYmd(b)
  if (from === null || to === null) return null
  return Math.round((to - from) / 86_400_000)
}

/** 今天的日历日（按**本地时区**取年月日再转 UTC，与浏览器所在用户一致）。 */
export function todayYmd(now: Date = new Date()): string {
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/**
 * 把日历事件日期转成相对标签（今天 / 明天 / +N 天）。
 *
 * 格式非法时返回 null —— 渲染侧据此退回显示绝对日期，而不是显示空白或 `NaN 天`。
 */
export function relativeDayLabel(date: string, today: string = todayYmd()): RelativeDayLabel | null {
  const diff = dayDiff(today, date)
  if (diff === null) return null
  if (diff === 0) return { key: 'watchPulse.cal.today' }
  if (diff === 1) return { key: 'watchPulse.cal.tomorrow' }
  return diff > 0 ? { key: 'watchPulse.cal.inDays', days: diff } : { key: 'watchPulse.cal.daysAgo', days: -diff }
}

export interface CalendarGroup {
  date: string
  events: Array<{ title: string; category: string }>
}

/**
 * 按日期分组，**保持后端给的日期顺序**（它已按 date 升序）。
 *
 * 刻意不排序：接口是权威顺序，前端再排一次一旦用了不同的比较（比如按字符串
 * 比 localeCompare），就会和后端给出���日历不一致，而这种不一致极难发现。
 */
export function groupEventsByDate(
  events: Array<{ date: string; title: string; category: string }>,
): CalendarGroup[] {
  const groups: CalendarGroup[] = []
  const byDate = new Map<string, CalendarGroup>()
  for (const ev of events) {
    let group = byDate.get(ev.date)
    if (!group) {
      group = { date: ev.date, events: [] }
      byDate.set(ev.date, group)
      groups.push(group)
    }
    group.events.push({ title: ev.title, category: ev.category })
  }
  return groups
}

/** 倍数展示：`2.59×`。非有限数返回 '—'（不返回 'NaN×'）。 */
export function formatMultiple(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return `${value.toFixed(2)}×`
}

/** 涨跌幅展示：带符号 `+9.99%` / `-3.21%`，红绿由调用方按符号决定。 */
export function formatPct(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}