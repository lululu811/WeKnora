/**
 * stockMentions — 从助手回答里抽取「被讨论的股票标的」，全站唯一真相源。
 *
 * 此前有两条互相不知情的抽取路径：
 *   - `klineTickerInjector.injectKLineTickers`（markdown 注入，正文可点标记）
 *   - `stock-score` 里的 `extractMentionedStocksFromText`（消息下方「本轮提及个股」标签行）
 * 两者用的正则、去重口径、交易所推断各不相同，已经出过「同一段文本在两个入口
 * 解析成不同 thscode」的 bug（`aShareTicker` 的文件头记录了这次事故）。
 * 现在两条路径都从这里取结果。
 *
 * 设计原则（与 klineTickerInjector 保持一致，不放宽）：
 *  - 只认前缀明确的板块，前缀判不出交易所就**不产出**该标的。宁可漏，不可误绑
 *    ——一个绑错的链接会让用户打开另一家公司的 K 线。
 *  - 归一 key 是「公司主体 + 上市地」：`600519.SH` 与假设存在的 `600519.HK`
 *    是两个标的；同一只票的裸码/带后缀/名称写法收敛成一个。
 *  - 纯名称命中只在本地已知映射表里成立，**不猜**没收录的公司。
 */

import { inferAShareExchange, isBoardExchange, BOARD_EXCHANGE, type AShareExchange, type TickerExchange } from './aShareTicker'

/** 本地已知的「代码 → 名称」映射。命中不了的名字不会被猜测成任何标的。 */
export const KNOWN_STOCK_NAMES: Readonly<Record<string, string>> = {
  '600487': '亨通光电',
  '000833': '粤桂股份',
  '300055': '万邦达',
  '002594': '比亚迪',
  '600519': '贵州茅台',
  '000001': '平安银行',
  '000592': '平潭发展',
  '601127': '赛力斯',
  '300750': '宁德时代',
  '300059': '东方财富',
  '600036': '招商银行',
  '601888': '中国中免',
  '601318': '中国平安',
  '002475': '立讯精密',
  '002415': '海康威视',
}
/** 运行期动态缓存的代码与名称对应表（从自选池、搜索、行情快照中扩充） */
const dynamicStockNames = new Map<string, string>()

export function registerStockName(tickerOrThscode: string, name: string): void {
  if (!tickerOrThscode || !name || tickerOrThscode === name) return
  const cleanTicker = tickerOrThscode.split('.')[0].trim()
  const cleanName = name.trim()
  if (/^\d{6}$/.test(cleanTicker) && cleanName && !KNOWN_STOCK_NAMES[cleanTicker]) {
    dynamicStockNames.set(cleanTicker, cleanName)
  }
}

export function getStockName(tickerOrThscode: string): string {
  const cleanTicker = tickerOrThscode.split('.')[0].trim()
  return KNOWN_STOCK_NAMES[cleanTicker] || dynamicStockNames.get(cleanTicker) || ''
}


export interface MentionedStock {
  ticker: string;
  exchange: TickerExchange;
  name: string;
  thscode: string;
}

/** 一次命中：某个标的在正文里的一次出现，携带它在原文中的位置。 */
export interface StockMention extends MentionedStock {
  /** 在输入文本中的起始下标，用于排序与「首次提及」判定。 */
  index: number;
}

/** 去重过程中的内部记录：`fromTextName` 标记名称是否来自原文而非本地映射表。 */
interface MentionRecord extends StockMention {
  fromTextName: boolean;
}

/** 上面那张表的反向索引：已知名称 → 代码。同样是静态查表。 */
const KNOWN_TICKER_BY_NAME: Readonly<Record<string, string>> = {
  亨通光电: '600487',
  粤桂股份: '000833',
  万邦达: '300055',
  比亚迪: '002594',
  贵州茅台: '600519',
  平安银行: '000001',
  平潭发展: '000592',
  赛力斯: '601127',
  宁德时代: '300750',
  东方财富: '300059',
  招商银行: '600036',
  中国中免: '601888',
  中国平安: '601318',
  立讯精密: '002475',
  海康威视: '002415',
}

// (^|[^\d.]) 前面不能是数字或小数点，否则 `1.600499` 会被切错
// (\d{6})     6 位代码
// (?!\d)      后面不能紧跟数字，否则 8 位日期 20260927 会被取前 6 位
// (\.[A-Z]+)?  可选交易所后缀，大小写不敏感
//
// 用捕获组而非 lookbehind：Safari 16.4 之前不支持 lookbehind。
const CODE_RE = /(^|[^\d.])(\d{6})(?!\d)(?:\.([A-Z]{2}))?/gi

// 名称(代码)：模型按提示词书写约定的形式，也是本地映射表里名称的唯一来源。
//
// 代码后面必须允许交易所后缀 —— `贵州茅台(600519.SH)` 正是提示词里明确要求的写法。
// 少了 `(?:\.([A-Z]{2}))?` 的话带后缀的写法整个失配，名称只能退回本地映射表的值，
// 于是原文写「某某公司(600519.SH)」会被显示成「贵州茅台」。
const NAME_PAREN_RE = /([一-龥A-Za-z0-9]{2,8})[（(](\d{6})(?:\.([A-Z]{2}))?[)）]/g

function normalizeExchange(raw: string | undefined, ticker: string): AShareExchange | null {
  if (raw) {
    const upper = raw.toUpperCase()
    if (upper === 'SH' || upper === 'SZ' || upper === 'BJ') return upper
    // 港股不是 A 股板块，走另一条路径，这里不认。
    return null
  }
  return inferAShareExchange(ticker)
}

/**
 * 判定一段 6 位数字（可带 `.SH`/`.SZ`/`.BJ` 后缀）是否是一个可绑定的 A 股标的，
 * 是则返回归一后的 thscode，否则返回 null。
 *
 * 这是「能不能变成一个可点的 K 线入口」的唯一判据。markdown 注入
 * （`injectKLineTickers`）和标的抽取（`extractStockMentions`）都走这里，
 * 因此正文的可点标记和下方标签行不会出现一个能点一个不能点的分裂。
 *
 * 判不出就返回 null，调用方据此原样放行 —— 宁可漏，不可把用户绑到别的公司。
 */
export function resolveTickerThscode(ticker: string, suffix?: string): string | null {
  // 板块/指数：只认**显式后缀** `.TI`。裸 `881101` 一律不认——它会被前缀表
  // 判成北交所股票（88 开头），而正文里的 6 位数字还可能是订单号。
  if (isBoardExchange(suffix)) return `${ticker}.${BOARD_EXCHANGE}`
  const exchange = normalizeExchange(suffix, ticker)
  return exchange ? `${ticker}.${exchange}` : null
}

/**
 * 抽取文本里全部股票提及，按首次出现顺序返回。
 *
 * 三种命中来源合并去重：
 *   1. 带交易所后缀的代码 `600519.SH` / 裸 6 位码 `600519`
 *   2. 名称(代码) `贵州茅台(600519.SH)` —— 名称来自原文，不被映射表覆盖
 *   3. 已知名称直接出现 `贵州茅台` —— 仅限 KNOWN_STOCK_NAMES 收录的
 */
export function extractStockMentions(text: string): StockMention[] {
  if (!text) return []
  const byThscode = new Map<string, MentionRecord>()

  const put = (record: MentionRecord) => {
    const existing = byThscode.get(record.thscode)
    if (!existing) {
      byThscode.set(record.thscode, record)
      return
    }
    // 名称优先级：原文里写的名称 > 本地映射表 > 代码本身。
    // 同一只票多次出现时保留最先出现的位置，但名称取更好的那个。
    if (record.fromTextName && !existing.fromTextName) {
      existing.name = record.name
      existing.fromTextName = true
    } else if (
      (!existing.name || existing.name === existing.ticker) &&
      record.name !== record.ticker
    ) {
      existing.name = record.name
    }
  }

  const push = (
    index: number,
    ticker: string,
    exchange: TickerExchange,
    name?: string,
    fromTextName = false,
  ) => {
    put({
      index,
      ticker,
      exchange,
      name: name || getStockName(ticker) || ticker,
      thscode: `${ticker}.${exchange}`,
      fromTextName,
    })
  }

  // --- 1. 代码（带后缀 / 裸码）---
  CODE_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = CODE_RE.exec(text)) !== null) {
    const lead = m[1]
    const ticker = m[2]
    const thscode = resolveTickerThscode(ticker, m[3])
    if (!thscode) continue
    // 分组 1 若非空，代码从 lead 之后开始
    push(m.index + lead.length, ticker, thscode.slice(-2) as TickerExchange)
  }

  // --- 2. 名称(代码) ---
  NAME_PAREN_RE.lastIndex = 0
  while ((m = NAME_PAREN_RE.exec(text)) !== null) {
    const name = m[1]
    const ticker = m[2]
    // 括号里写了后缀就以它为准，没写才按板块前缀推断。
    const thscode = resolveTickerThscode(ticker, m[3])
    if (!thscode) continue
    push(m.index, ticker, thscode.slice(-2) as TickerExchange, name, true)
  }

  // --- 3. 已知名称直接出现 ---
  for (const [name, ticker] of Object.entries(KNOWN_TICKER_BY_NAME)) {
    const at = text.indexOf(name)
    if (at < 0) continue
    const exchange = inferAShareExchange(ticker)
    if (!exchange) continue
    push(at, ticker, exchange, name)
  }
  for (const [ticker, name] of dynamicStockNames.entries()) {
    const at = text.indexOf(name)
    if (at < 0) continue
    const exchange = inferAShareExchange(ticker)
    if (!exchange) continue
    push(at, ticker, exchange, name)
  }

  return Array.from(byThscode.values())
    .sort((a, b) => a.index - b.index)
    .map(({ ticker, exchange, name, thscode, index }) => ({ ticker, exchange, name, thscode, index }))
}

/** 只要标的列表、不需要位置信息时用这个。 */
export function extractMentionedStocks(text: string): MentionedStock[] {
  return extractStockMentions(text).map(({ ticker, exchange, name, thscode }) => ({
    ticker,
    exchange,
    name,
    thscode,
  }))
}

/**
 * 抽取某只票的「关注理由」——入池时预填到 note 里的一段正文。
 *
 * 为什么取**最后一次**出现而不是第一次：`extractStockMentions` 按 thscode
 * 去重，只保留首次位置，而模型写股票分析的结构几乎总是
 * 「铺垫 → 展开 → 结论」。结论段才是理由；第一次提及往往在
 * 「我们来看看 601127.SH 的情况」这种引出句里，把它当理由存进池子，
 * 半年后回看只会得到一句废话。
 *
 * 但「最后一次」本身不够。它假设结论段会再提一次这只票——很多回答不会：
 * 代码只出现在开头的引出句里，之后全是分析，于是「最后一次」和「第一次」
 * 是同一段，抽出来仍然是一句废话。所以引出段会被跳过，取它之后的第一段
 * 实质内容（见 isLeadInParagraph）。
 *
 * 段落边界用空行（\n\n）而不是换行：一段 markdown 列表、单换行分行的表格
 * 都不该被切成两半。
 *
 * 找不到前边界就退化成从 0 开始，而不是返回空——宁可给一段可能不完整的理由，
 * 也好过让用户面对一个空输入框猜该填什么。
 *
 * 抽不出（非空但完全找不到这只票）返回空串，由调用方决定是否提示。
 *
 * maxLen 默认 200 = 服务端 `stock_watch.note` 的列宽。两者必须一致：前端按
 * 500 截，服务端按 200 再截一次，切口不落在句读上，note 就会在半句话甚至
 * 半个标签里断掉。
 */
export function extractTrackingReason(
  text: string,
  thscode: string,
  maxLen = 200,
): string {
  if (!text || !thscode) return ''

  const at = lastMentionIndex(text, thscode)
  if (at < 0) return ''

  let { start, end } = paragraphAt(text, at)

  // 引出段（"我们来看看 X"）后面通常才是正题。若后面还有实质内容就取它。
  //
  // 是个 while 而不是 if：模型常在引出句之后连着放小标题
  //（「我们来看看 X」→「赛力斯的销量数据：」→真正的结论），只跳一次会停在
  // 小标题上。跳过的段落数有上限，免得正文全是短行时一路跳到末尾。
  for (let hop = 0; hop < 3; hop++) {
    if (!isLeadInParagraph(text.slice(start, end))) break
    const next = nextParagraph(text, end)
    if (!next) break
    const body = text.slice(next.start, next.end)
    // 下一段在讲别的票，就地停下。理由栏存的是「为什么跟这只票」，存成
    // 另一只标的的分析比存一句引出话更糟——它读起来通顺，错误却极难发现。
    if (mentionsOtherSymbol(body, thscode)) break
    ;({ start, end } = next)
  }

  return cleanReason(text.slice(start, end), maxLen)
}

/** 这一段里出现的是不是别的标的（不含 thscode 自己）。 */
function mentionsOtherSymbol(para: string, thscode: string): boolean {
  const mine = thscode.split('.')[0]
  for (const other of extractStockMentions(para)) {
    if (other.ticker !== mine) return true
  }
  return false
}

/** text 中包含 at 的那个空行分隔段落的 [start, end)。 */
function paragraphAt(text: string, at: number): { start: number; end: number } {
  const before = text.lastIndexOf('\n\n', at)
  const start = before < 0 ? 0 : before + 2
  const after = text.indexOf('\n\n', at)
  const end = after < 0 ? text.length : after
  return { start, end }
}

/** end 之后的下一个非空段落，没有则返回 null。 */
function nextParagraph(
  text: string, end: number,
): { start: number; end: number } | null {
  const start = text.indexOf('\n\n', end)
  if (start < 0) return null
  const from = start + 2
  const next = text.indexOf('\n\n', from)
  const to = next < 0 ? text.length : next
  if (text.slice(from, to).trim() === '') return null
  return { start: from, end: to }
}

/**
 * 一段文字是不是「引出句」而不是实质内容。
 *
 * 判定必须**窄**。这个函数的错误代价是不对称的：漏判一个引出句，用户看到一句
 * 废话、可以自己改；误判一段实质内容，理由就变成了下一段的无关内容，而
 * 下一段谈的可能是完全不同的票。所以宁可漏判。
 *
 * 因此只认两种明确形态：显式的引出语（说到/关于/比如…），以及「短到不可能
 * 是分析」的一句话转场——阈值压到 24 字，比一句话的常见长度更短，只有真正
 * 的转场才会落进来。带引出语的段落即使长也仍然算引出，那正是它的定义。
 */
function isLeadInParagraph(para: string): boolean {
  const text = para.trim()
  if (text === '') return false
  if (LEAD_IN_PREFIX_RE.test(text)) return true
  // 以冒号收尾且不长：小标题（「赛力斯的销量数据：」）。长段落以冒号收尾
  // 是正常的叙述，不算。
  if (/[：:]\s*$/.test(text) && text.length <= 40) return true
  return text.length <= 24
}

/**
 * 该票在文本中**最后一次**出现的位置，找不到返回 -1。
 *
 * 不复用 extractStockMentions：它按首次出现去重并丢掉后续位置，而这里要的
 * 恰恰是最后一次。共用一个「抽取」函数会把这个区别藏在某个排序细节里，
 * 而它正是这个功能成立与否的分界。
 *
 * 自己扫描而不是用一条大正则，是因为带后缀与裸码两种写法会互相重叠：
 * `601127.SH` 里，裸码模式同样能匹配到前 6 位，两条正则各报一个位置，
 * 取谁全看先后顺序——而这个顺序恰好决定了「引出句」判定是否成立。
 * 逐个位置手工判断前后字符，规则只写一次。
 */
function lastMentionIndex(text: string, thscode: string): number {
  const [code, exchange] = thscode.split('.')
  if (!code) return -1
  const upper = text.toUpperCase()
  const digits = code.toUpperCase()
  // 两种写法都算命中：带后缀的「600519.SH」和裸的「600519」。模型按提示词
  // 约定写前者，但人手打的对话里后者更常见，漏掉它等于让一半的会话抽不出理由。
  // 裸码的边界更严：后面不能跟字母，否则「6005198」「600519X」都不是这只票。
  const forms = exchange
    ? [digits + '.' + exchange.toUpperCase(), digits]
    : [digits]

  let last = -1
  for (const want of forms) {
    let from = 0
    for (;;) {
      const at = upper.indexOf(want, from)
      if (at < 0) break
      const prev = at > 0 ? upper[at - 1] : ''
      const next = upper[at + want.length] ?? ''
      // 前后不能是数字或小数点：20260927 里不能取出 202609，
      // 1.600519 里也不能取出 600519。裸码还要额外拒绝后接字母，
      // 带后缀的写法后面接中文或标点是正常的。
      const ok = !/[\d.]/.test(prev) && !/\d/.test(next) &&
        (want.length > digits.length || !/[A-Za-z]/.test(next))
      if (ok && at > last) last = at
      from = at + want.length
    }
  }
  return last
}

/**
 * 图表锚点标签 → 可读文本。
 *
 * 正文里的 `<anchor kind="level" value="72.4" label="第一目标"/>` 是给 K 线图
 * 用的定位标记，界面上会渲染成图上的圈号。抽取理由时它必须换成文字，否则
 * note 里会躺着一段 `<anchor kind="level" value="31.81" label="当前价31.81"...`
 * 这样的原始标签——而 note 落库是 varchar(200)，正好把标签从中间切断。
 *
 * 取值优先级：label（人写的说明）→ value/from~to（裸数据）→ 整段丢弃。
 * 紧跟在 `@` 后面的是「价位 @xx」的写法，此时用裸值比用 label 自然：
 * `当前收盘价@<anchor value="31.81" label="当前价31.81"/>` 读作
 * 「当前收盘价@31.81」，而不是「当前收盘价@当前价31.81」。
 */
const ANCHOR_TAG_RE = /@?\s*<anchor\b([^>]*?)\/>/g
const ANCHOR_ATTR_RE = /(\w+)\s*=\s*"([^"]*)"/g

function anchorAttrs(body: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const m of body.matchAll(ANCHOR_ATTR_RE)) out[m[1]] = m[2]
  return out
}

function readableAnchor(body: string, atPrefix: boolean): string {
  const a = anchorAttrs(body)
  if (atPrefix) return a.value ?? a.from ?? ''
  if (a.label) return a.label
  if (a.value) return a.value
  if (a.from && a.to) return `${a.from} ~ ${a.to}`
  if (a.from) return a.from
  return ''
}

function inlineAnchorTags(raw: string): string {
  return raw.replace(ANCHOR_TAG_RE, (_all, body: string) => {
    const atPrefix = /^\s*@/.test(_all)
    return readableAnchor(body, atPrefix)
  })
}

/**
 * 去掉 markdown 记号与多余空白，并硬截到 maxLen。
 *
 * 截断按**字符**而不是 grapheme：note 落库后由服务端按 rune 计长，
 * 这里只需要保证不会超得离谱，且 emoji 被从中间劈开不会造成任何问题。
 */
function cleanReason(raw: string, maxLen: number): string {
  const text = inlineAnchorTags(raw)
    // 代码围栏：连同 ``` 一起删掉，围栏里是给终端看的内容。
    .replace(/```[a-zA-Z]*\n?/g, '')
    // 行内记号：保留文字，去掉渲染用的符号。
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\*\*([^*]*)\*\*/g, '$1')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1$2')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^>\s?/gm, '')
    // 表格分隔行与整行竖线：note 存成竖线残骸对谁都没有意义。
    .replace(/^\s*\|?[\s:|-]+\|[\s:|-]*$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()

  if (text.length <= maxLen) return text
  // 截在最后一个句读之前，避免留下半个词；找不到就硬截。
  const tail = text.slice(0, maxLen)
  const cut = Math.max(
    tail.lastIndexOf('。'), tail.lastIndexOf('；'), tail.lastIndexOf('，'),
    tail.lastIndexOf('. '), tail.lastIndexOf('; '),
  )
  return (cut > maxLen * 0.6 ? tail.slice(0, cut + 1) : tail).trim()
}

/**
 * 判定某只票在一段文本里是不是「被讨论的对象」，而不是被顺带提到。
 *
 * 用来选主标的：判不出来就返回 null，让调用方**把图位空出来**，而不是猜一只。
 * 规则刻意保守，只覆盖「首个提及就是讨论对象」这一种明确形态：
 *   - 首个代码出现在 `名称(代码)` 里 → 明确的主标的
 *   - 首个代码前面没有「说到/关于/比如/例如/再看」这类引出语 → 主标的
 *   - 首个提及是裸名称（无代码）且只出现一次 → 不足以判定，返回 null
 */
const LEAD_IN_PREFIX_RE = /(?:说到|说道|谈谈|聊聊|关于|对于|比如|例如|再看|看看|提及|提到)\s*$/

export function pickPrimaryMention(text: string): MentionedStock | null {
  const mentions = extractStockMentions(text)
  if (mentions.length === 0) return null

  const first = mentions[0]
  // 名称(代码) 形式最明确：模型按约定写的第一个就是主标的。
  const nameParen = new RegExp(
    `[一-龥A-Za-z0-9]{2,8}[（(]\\s*${first.ticker}\\s*[)）]`,
  ).test(text.slice(0, first.index + 40))
  if (nameParen) {
    const { index, ...rest } = first
    void index
    return rest
  }

  // 首个提及前面有引出语 → 那是铺垫，标的本身是后一句的主语，判不准。
  const before = text.slice(Math.max(0, first.index - 12), first.index)
  if (LEAD_IN_PREFIX_RE.test(before)) return null

  const { index, ...rest } = first
  void index
  return rest
}
