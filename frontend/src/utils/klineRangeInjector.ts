/**
 * klineRangeInjector — 在 markdown 进入 marked 之前，把助手回答里的**绝对日期**
 * 包成可悬停/可点的 `<span class="kline-range">`，让右侧 K 线能滚动到对应位置。
 *
 * 与 `klineTickerInjector` 并列的第二种「正文 → 图表」标记：ticker 回答「看哪只票」，
 * 区间回答「看图上哪一段」。两者都用 `data-*` 属性承载语义，由同一套
 * `useKLineTickerObserver` MutationObserver 机制挂事件，因此流式打字过程中
 * 也能逐个变得可交互。
 *
 * 为什么只认绝对日期，不认「近三个月」「上周」「Q3」：
 * 相对时间必须锚定一个「现在」，而「现在」对一段历史分析来说完全模糊——同一个
 * 「近三个月」在图上可以对应三段不同区间，画错一段比不画伤害更大。提示词
 * （agent_system_prompt.yaml「标的书写约定」第 4 条）已要求模型用可解析的写法，
 * 前端实现与那条约定是配套的，改一边就要改另一边。
 *
 * 也不在这里做交易日换算：抽取层不知道图上加载了哪些交易日，把 `2026-05-20`
 * 换算成「第 N 根 K 线」必须由持有行情数据的图表侧完成。抽取层只保证「这个日期
 * 确实在正文里出现过」，换算与降级在 KLineWorkspace 里做。
 *
 * 正则用数字捕获组而不是具名组：同一个模式要同时匹配「2026-05-20」和「5月20日」
 * 两种写法，具名组在两个分支里重名会被引擎拒绝（`redefinition of group name`）。
 */

// 受保护、不做任何改写的片段：
//  1. 代码块与行内代码 —— 代码示例里的数字不是标的、日期也不是日期。
//  2. **HTML 标签整体** —— 这一条是后补的，因为踩到了真实的损坏：
//     日期正则的前导 `(?:^|[^\d])` 会把标签的 `>` 当成"前导字符"一起吃掉，
//     `见 <b>2026-05-20</b>` 会被改写成 `见 <b<span ...>>2026-05-20</span></b>`，
//     标签直接被劈开；属性值里的日期同样会被包进 span，把标签写坏
//     （`<web title="2026-05-20 复盘"/>` 已中招）。
//     用 `[A-Za-z]` 开头而不是任意字符，是为了不把 `a < b`、`1 < 2` 这类
//     普通比较误判成标签——那些后面的日期仍然要标。
const CODE_SPLIT_RE = /(`{3}[\s\S]*?`{3}|`[^`\n]*`|<\/?[A-Za-z][^>]*>)/g

/**
 * 单个日期：
 *   1=年(可缺) 2=月 3=日
 * 年份可选，所以 `2026-05-20` 与 `5月20日` 走同一套捕获组。
 * 不写具名组：见文件头。
 */
const DATE = '(\\d{4})?\\s*[-/年]?\\s*(\\d{1,2})\\s*[-/月]\\s*(\\d{1,2})\\s*日?'

// 区间：日期 连接词 日期。左侧 DATE 整体是 g1（年/月/日 = 2/3/4），
// 右侧 DATE 整体是 g5（年/月/日 = 6/7/8）。
const RANGE_RE = new RegExp(`(${DATE})\\s*(?:至|到|~|～|—|－|-->|—>)\\s*(${DATE})`, 'g')
// 单点：前面不能是数字（避免从 8 位日期里截后 6 位），后面同理。
// 这里是 DATE 整体 = g1（年/月/日 = 2/3/4）。
const SINGLE_RE = new RegExp(`(?:^|[^\\d])(${DATE})(?![\\d])`, 'g')

export const KLINE_RANGE_CLASS = 'kline-range'
/** 区间起点，epoch 毫秒（UTC 零点）。带年份才有。 */
export const KLINE_RANGE_FROM_ATTR = 'data-kline-from'
/** 区间终点，epoch 毫秒。 */
export const KLINE_RANGE_TO_ATTR = 'data-kline-to'
/** 缺年份的「5月20日」，值为 `MM-DD`，由图表侧在已加载数据里就近定位。 */
export const KLINE_RANGE_MD_ATTR = 'data-kline-md'

export interface ParsedKLineRange {
  /** 起点。仅当该侧带年份时存在。 */
  from?: number;
  /** 终点。仅当该侧带年份时存在。 */
  to?: number;
  /** 「5月20日」这种缺年份的写法，`MM-DD`。区间不做（两端都缺年份无法定位）。 */
  md?: string;
  /** 正文原文，用于 title 提示。 */
  label: string;
}

export interface FoundRange extends ParsedKLineRange {
  index: number;
  length: number;
}

function toUtc(y: number, m: number, d: number): number | null {
  if (m < 1 || m > 12 || d < 1 || d > 31) return null
  const t = Date.UTC(y, m - 1, d)
  const back = new Date(t)
  // 拒绝 2 月 30 日、13 月 1 日这类溢出日期
  if (back.getUTCMonth() !== m - 1 || back.getUTCDate() !== d) return null
  return t
}

interface Side {
  ms?: number
  md?: string
}

function parseSide(year: string | undefined, month: string, day: string): Side {
  const m = Number(month)
  const d = Number(day)
  if (!Number.isFinite(m) || !Number.isFinite(d)) return {}
  if (year !== undefined) {
    const ms = toUtc(Number(year), m, d)
    return ms === null ? {} : { ms }
  }
  return { md: `${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}` }
}

function escapeAttr(v: string): string {
  return v.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
}

function wrap(parsed: ParsedKLineRange): string {
  const attrs = [`class="${KLINE_RANGE_CLASS}"`]
  if (parsed.from !== undefined) attrs.push(`${KLINE_RANGE_FROM_ATTR}="${parsed.from}"`)
  if (parsed.to !== undefined) attrs.push(`${KLINE_RANGE_TO_ATTR}="${parsed.to}"`)
  if (parsed.md) attrs.push(`${KLINE_RANGE_MD_ATTR}="${parsed.md}"`)
  return `<span ${attrs.join(' ')}>${escapeAttr(parsed.label)}</span>`
}

/**
 * 找出文本里所有绝对日期区间/单点，按出现顺序返回。
 *
 * 区间优先：被区间覆盖的那段单点日期不会重复产出，同一句话因此只标一次。
 */
export function parseKLineRanges(text: string): FoundRange[] {
  if (!text) return []
  const found: FoundRange[] = []

  const scanRange = () => {
    RANGE_RE.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = RANGE_RE.exec(text)) !== null) {
      const a = parseSide(m[2], m[3], m[4])
      const b = parseSide(m[6], m[7], m[8])
      // 两端都定位不了就整段放弃：不猜。
      if (a.ms === undefined && !a.md) continue
      if (b.ms === undefined && !b.md) continue
      // 两端都只有月日时无法确定是哪一年，同样不标。
      if (a.ms === undefined && b.ms === undefined) continue
      found.push({
        index: m.index,
        length: m[0].length,
        label: m[0].trim(),
        from: a.ms,
        to: b.ms,
        md: undefined,
      })
    }
  }

  const scanSingle = () => {
    SINGLE_RE.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = SINGLE_RE.exec(text)) !== null) {
      const a = parseSide(m[2], m[3], m[4])
      if (a.ms === undefined && !a.md) continue
      found.push({
        index: m.index,
        length: m[0].length,
        label: m[0].trim(),
        from: a.ms,
        to: undefined,
        md: a.md,
      })
    }
  }

  scanRange()
  scanSingle()

  // 区间必须压过任何与它重叠的单点。
  //
  // 不能只按 index 排：`从 2026-05-20 到 ...` 里的单点是从空格这个前导字符开始
  // 匹配的，index 比区间早 1，于是会排到区间前面、把区间挡掉。所以「谁包含谁」
  // 用实际跨度判定，而不是只看起点。
  found.sort((x, y) => x.index - y.index || y.length - x.length)
  const isRange = (r: FoundRange) => r.to !== undefined
  const kept: FoundRange[] = []
  for (const item of found) {
    const overlaps = kept.some(
      (k) => item.index < k.index + k.length && k.index < item.index + item.length,
    )
    if (!overlaps) {
      kept.push(item)
      continue
    }
    // 区间比已在列表里的单点跨度大：替换掉那个单点。
    for (let i = 0; i < kept.length; i++) {
      const k = kept[i]
      const overlapsK = item.index < k.index + k.length && k.index < item.index + item.length
      if (overlapsK && isRange(item) && !isRange(k) && item.length > k.length) {
        kept[i] = item
        break
      }
    }
  }
  kept.sort((x, y) => x.index - y.index)
  return kept
}

/**
 * 把绝对日期包成可交互标记，返回可交给 marked 的 markdown 文本。
 * code fence 与 inline code 内的日期保持原样。
 */
export function injectKLineRanges(markdown: string): string {
  if (!markdown) return markdown
  const parts = markdown.split(CODE_SPLIT_RE)
  for (let i = 0; i < parts.length; i += 2) {
    const found = parseKLineRanges(parts[i])
    if (found.length === 0) continue
    let out = ''
    let cursor = 0
    for (const item of found) {
      out += parts[i].slice(cursor, item.index)
      out += wrap(item)
      cursor = item.index + item.length
    }
    out += parts[i].slice(cursor)
    parts[i] = out
  }
  return parts.join('')
}
