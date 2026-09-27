/**
 * klineTickerInjector — 在 markdown 进入 marked 之前，把形如 `600519.SH` 的
 * A 股 ticker 文本包成 `<span class="kline-ticker" data-thscode="...">`，
 * 让前端可以挂 hover/click 事件触发 kline-studio 抽屉。
 *
 * 设计原则：
 *  - 只处理 6 位数字 + `.SH`/`.SZ`/`.BJ`，避免误匹配电话号码、订单号、版本号等。
 *  - code fence (```...```) 与 inline code (`...`) 内的文本保持原样，避免在
 *    代码示例里给无意义的 ticker 加交互。
 *  - 不做去重、不分首次/末次出现 —— 同一股票在答案里出现多少次就标记多少次，
 *    因为每处都是独立可交互入口。
 *
 * 真正的触发逻辑在 `useKLineTickerObserver` 里挂 DOM 事件。
 */

// 严格 ticker：6 位数字 + 交易所后缀 (.SH/.SZ/.BJ)。无歧义。
const TICKER_RE = /\b(\d{6})\.(SH|SZ|BJ)\b/g
// 中文括号或 ASCII 括号内的纯 6 位数字（如「平潭发展（000592）」
// 「(000592)」）。允许数字紧邻括号但不允许其它字符在中间。
const TICKER_BARE_PAREN_RE = /([（(])(\d{6})(?=[)）])/g
const CODE_SPLIT_RE = /(`{3}[\s\S]*?`{3}|`[^`\n]*`)/g

export const KLINE_TICKER_CLASS = 'kline-ticker'
export const KLINE_TICKER_ATTR = 'data-thscode'

/**
 * 注入 ticker 标签，返回可直接交给 marked 的 markdown 文本。
 * - 文本 ticker（如 `600519.SH`） → `<span class="kline-ticker" data-thscode="600519.SH">600519.SH</span>`
 * - code block / inline code 里的 ticker 不动
 */
export function injectKLineTickers(markdown: string): string {
  if (!markdown) return markdown
  // 偶数下标是 markdown，奇数下标是 code 块（被正则 split 抽出的部分）。
  const parts = markdown.split(CODE_SPLIT_RE)
  for (let i = 0; i < parts.length; i += 2) {
    // 严格 ticker：`600519.SH` / `000001.SZ` / `830799.BJ`
    parts[i] = parts[i].replace(TICKER_RE, (_match, ticker: string, exchange: string) => {
      const thscode = `${ticker}.${exchange}`
      return wrapTicker(thscode)
    })
    // 中文/ASCII 括号包裹的纯 6 位数字：`（000592）` / `(000592)`。
    // 没有交易所后缀时按 SH 处理（A 股主板大头在沪市）；用户 hover 抽屉打开
    // 后仍可手动切换交易所（kline-studio frontend 会按 thscode 解析）。
    parts[i] = parts[i].replace(TICKER_BARE_PAREN_RE, (_match, _open: string, ticker: string) => {
      const thscode = `${ticker}.SH`
      // 替换括号里的数字为 <x-kline>，保留外层括号。
      return `${_open}${wrapTicker(thscode)}`
    })
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