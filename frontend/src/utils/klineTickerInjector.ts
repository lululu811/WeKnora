/**
 * klineTickerInjector — 在 markdown 进入 marked 之前，把形如 `600519.SH` 的
 * A 股 ticker 文本包成 `<span class="kline-ticker" data-thscode="...">`，
 * 让前端可以挂 hover/click 事件打开 chat 右侧的 K 线工作台。
 *
 * 设计原则：
 *  - 6 位数字 + `.SH`/`.SZ`/`.BJ` 是无歧义形式，一律识别。
 *  - 6 位裸数字也识别（LLM 正文里写「600499 是科达制造」很常见），交易所由
 *    板块前缀推断；前缀不属于任何已知板块就原样放行，宁可漏也不误伤。
 *  - code fence (```...```) 与 inline code (`...`) 内的文本保持原样，避免在
 *    代码示例里给无意义的 ticker 加交互。
 *  - 不做去重、不分首次/末次出现 —— 同一股票在答案里出现多少次就标记多少次，
 *    因为每处都是独立可交互入口。
 *
 * 真正的触发逻辑在 `useKLineTickerObserver` 里挂 DOM 事件。
 */
import { resolveTickerThscode } from './stockMentions'

// 一次扫描同时吃下「带后缀」和「裸 6 位码」。
//
// 合并成一条正则而不是先 replace 带后缀、再 replace 裸码，是为了避免第二轮把
// 第一轮刚包好的 <span> 里的数字再包一层（`600499.SH` 会被二次处理成嵌套 span）。
//
// 捕获组而非 lookbehind：Safari 16.4 之前不支持 lookbehind，这个构建产物要跑在
// 用户的浏览器里，不值得为了一行正则设新下限。
//   (^|[^\d.])  前面不能是数字或小数点，否则 `1.600499` / `202609` 会被切错
//   (\d{6})     6 位代码
//   (?!\d)      后面不能紧跟数字，否则 8 位日期 20260927 会被取前 6 位
//   (\.(SH|SZ|BJ)\b)?  可选交易所后缀，大小写不敏感
const TICKER_RE = /(^|[^\d.])(\d{6})(?!\d)(?:\.(SH|SZ|BJ)\b)?/gi
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

export const KLINE_TICKER_CLASS = 'kline-ticker'
export const KLINE_TICKER_ATTR = 'data-thscode'

/**
 * 注入 ticker 标签，返回可直接交给 marked 的 markdown 文本。
 * - 带后缀的 ticker（`600519.SH`）→ `<span class="kline-ticker" data-thscode="600519.SH">600519.SH</span>`
 * - 裸 6 位码（`600499`，含「（000592）」这类括号包裹）→ 按板块前缀补出交易所
 * - 前缀不属于任何已知板块的 6 位数字（`123456`、订单号等）原样保留
 * - code block / inline code 里的 ticker 不动
 */
export function injectKLineTickers(markdown: string): string {
  if (!markdown) return markdown
  // 偶数下标是 markdown，奇数下标是 code 块（被正则 split 抽出的部分）。
  const parts = markdown.split(CODE_SPLIT_RE)
  for (let i = 0; i < parts.length; i += 2) {
    parts[i] = parts[i].replace(
      TICKER_RE,
      (match, lead: string, ticker: string, suffix?: string) => {
        // 判据在 stockMentions 里，与下方标签行共用同一个解析器：
        // 前缀判不出交易所就整段原样返回——宁可这个数字不可点，也不要把它
        // 绑到一只不相干的票上，那会让 hover 卡片显示出错误的公司名。
        const thscode = resolveTickerThscode(ticker, suffix)
        if (!thscode) return match
        return `${lead}${wrapTicker(thscode)}`
      },
    )
  }
  return parts.join('')
}

function wrapTicker(thscode: string): string {
  // 使用 <span class="kline-ticker"> 而非自定义 <x-kline>，DOMPurify 对
  // 标准 HTML 标签会完整保留 class 与 data-* 属性，对自定义元素即便加
  // ALLOWED_TAGS 也会清空内容。
  return `<span class="${KLINE_TICKER_CLASS}" ${KLINE_TICKER_ATTR}="${thscode}">${thscode}</span>`
}

/**
 * 在已渲染的 HTML 容器里找到所有未绑定的 `.kline-ticker` 元素，挂 hover/click
 * 事件，返回实际新绑定的元素数量。已绑过的会被 `data-kline-bound="1"` 标记。
 *
 * onActivate(ticker, exchange) 在用户激活（hover/click）时被调用，由 caller
 * 决定是否打开抽屉。
 */
export interface KLineTickerHandlerOptions {
  onHover?: (thscode: string, el: HTMLElement) => void
  onLeave?: () => void
  onClick?: (thscode: string, el: HTMLElement) => void
}

export type KLineTickerHandler = ((thscode: string) => void) | KLineTickerHandlerOptions

export function bindKLineTickerElements(
  root: ParentNode,
  handler: KLineTickerHandler,
): number {
  let bound = 0
  const candidates = root.querySelectorAll<HTMLElement>(`.${KLINE_TICKER_CLASS}[${KLINE_TICKER_ATTR}]:not([data-kline-bound])`)
  candidates.forEach((el) => {
    if (el.getAttribute('data-kline-bound') === '1') return
    el.setAttribute('data-kline-bound', '1')
    el.setAttribute('role', 'button')
    el.setAttribute('tabindex', '0')
    const thscode = el.getAttribute(KLINE_TICKER_ATTR) || ''

    if (typeof handler === 'function') {
      let activated = false
      const activate = () => {
        if (activated) return
        activated = true
        handler(thscode)
        setTimeout(() => { activated = false }, 120)
      }
      el.addEventListener('mouseenter', activate)
      el.addEventListener('focus', activate)
      el.addEventListener('click', (event) => {
        event.preventDefault()
        activate()
      })
      el.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          activate()
        }
      })
    } else {
      // 区分 hover 与 click：hover 唤起轻量 RAW 卡片，click 打开完整右侧工作台
      el.addEventListener('mouseenter', () => {
        handler.onHover?.(thscode, el)
      })
      el.addEventListener('mouseleave', () => {
        handler.onLeave?.()
      })
      el.addEventListener('focus', () => {
        handler.onHover?.(thscode, el)
      })
      el.addEventListener('blur', () => {
        handler.onLeave?.()
      })
      el.addEventListener('click', (event) => {
        event.preventDefault()
        handler.onClick?.(thscode, el)
      })
      el.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          handler.onClick?.(thscode, el)
        }
      })
    }

    bound += 1
  })
  return bound
}