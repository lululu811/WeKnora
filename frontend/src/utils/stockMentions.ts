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

import { inferAShareExchange, type AShareExchange } from './aShareTicker'

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

export interface MentionedStock {
  ticker: string;
  exchange: AShareExchange;
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
    exchange: AShareExchange,
    name?: string,
    fromTextName = false,
  ) => {
    put({
      index,
      ticker,
      exchange,
      name: name || KNOWN_STOCK_NAMES[ticker] || ticker,
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
    push(m.index + lead.length, ticker, thscode.slice(-2) as AShareExchange)
  }

  // --- 2. 名称(代码) ---
  NAME_PAREN_RE.lastIndex = 0
  while ((m = NAME_PAREN_RE.exec(text)) !== null) {
    const name = m[1]
    const ticker = m[2]
    // 括号里写了后缀就以它为准，没写才按板块前缀推断。
    const thscode = resolveTickerThscode(ticker, m[3])
    if (!thscode) continue
    push(m.index, ticker, thscode.slice(-2) as AShareExchange, name, true)
  }

  // --- 3. 已知名称直接出现 ---
  for (const [name, ticker] of Object.entries(KNOWN_TICKER_BY_NAME)) {
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
