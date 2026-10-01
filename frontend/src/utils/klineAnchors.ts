/**
 * klineAnchors — 解析模型输出的 `<anchor/>` 标记，把它变成正文里可交互的锚点。
 *
 * ## 为什么需要这个，而不是继续用正则抽日期
 *
 * 正则只能抽「绝对日期」，实测 20 条真实回答里只有 11 条含有绝对日期（55%），
 * 而模型真正在论述的单位经常没有日期——「第一波」「这段回调」「B1 建仓波」，
 * 单条回答里这类表述最多出现 32 次。正则抽不到它们，所以联动只覆盖了回答里
 * 的一小块，而且未必是重点。这是「联动很蠢」的根源。
 *
 * 改成由模型显式标记：**它知道自己在说什么，正则不知道**。锚点还带模型自己的
 * 标签，于是正文里读到的词和图上标的词是同一个。
 *
 * ## 语法
 *
 * 与仓库既有的 `<kb/>` / `<web/>` 同族，复用同一套属性解析与占位符保护：
 *
 *   <anchor kind="range" from="2026-05-20" to="2026-06-10" label="第一波"/>
 *   <anchor kind="level" value="72.4" label="第一目标"/>
 *
 * ## 失效降级
 *
 * 语法本身**永远不出现在用户眼前**，但它承载的内容是正文的一部分，不能吞掉
 * ——「①这段最强」被吞掉会读不通。所以无效锚点渲染成它的 label 纯文本。
 */

import { parseTagAttributes, escapeHtml } from './citationMarkdown'

export const KLINE_ANCHOR_CLASS = 'kline-anchor'
/** 正文里那个圆编号，与图上 overlay 的徽章同形同色。 */
export const KLINE_ANCHOR_NUM_CLASS = 'kline-anchor__num'
/** 正文锚点上承载语义的属性，供 DOM 事件层读取。 */
export const KLINE_ANCHOR_INDEX_ATTR = 'data-anchor-index'

export type AnchorKind = 'range' | 'level'

export interface KLineAnchor {
  /** 正文出现顺序，从 1 开始。正文与图上共用这一个编号。 */
  index: number
  kind: AnchorKind
  /** `range`：起始日期（ISO）。 */
  from?: string
  /** `range`：结束日期（ISO）。 */
  to?: string
  /** `level`：价格。 */
  value?: number
  /** 模型给的标签。缺失时由渲染层给一个兜底文案。 */
  label: string
  /** 解析是否成功。无效锚点只渲染 label 文本，不做交互。 */
  valid: boolean
}

/** 一次命中：锚点 + 它在原文里的位置，供替换使用。 */
export interface FoundAnchor extends KLineAnchor {
  start: number
  end: number
  /** 原始标签文本。 */
  raw: string
}

const ANCHOR_RE = /<anchor\b([^>]*?)\s*\/?>/gi
// 受保护、不解析的片段：代码块、行内代码、以及**除 anchor 以外**的 HTML 标签。
//
// 必须排除 anchor：它本身就是 HTML 标签的形态，若不排除会被当成"要保护的标签"
// 一起跳过，于是永远解析不到（第一版就是这么哑掉的）。
// 其余标签要保护是因为它们的属性值里可能有日期或数字，改写会打断标签。
const CODE_SPLIT_RE = /(`{3}[\s\S]*?`{3}|`[^`\n]*`|<\/?(?!anchor\b)[A-Za-z][^>]*>)/g

/** ISO 日期（`2026-05-20` / `2026/5/20`）。只认这一种，不做自然语言推断。 */
const ISO_DATE_RE = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/

function normalizeIso(raw: string | undefined): string | undefined {
  if (!raw) return undefined
  const m = ISO_DATE_RE.exec(raw.trim())
  if (!m) return undefined
  const y = Number(m[1])
  const mo = Number(m[2])
  const d = Number(m[3])
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return undefined
  const t = Date.UTC(y, mo - 1, d)
  const back = new Date(t)
  // 拒绝 2 月 30 日这类溢出日期——和 levels.ts 同一个判据。
  if (back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d) return undefined
  return `${m[1]}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

function buildAnchor(attrs: Record<string, string>, index: number): Omit<FoundAnchor, 'start' | 'end' | 'raw'> {
  const kindRaw = (attrs.kind || '').toLowerCase()
  const label = (attrs.label || '').trim()

  if (kindRaw === 'range') {
    const from = normalizeIso(attrs.from)
    const to = normalizeIso(attrs.to) ?? from
    const valid = Boolean(from && to && from <= to)
    return { index, kind: 'range', from, to, label, valid }
  }

  if (kindRaw === 'level') {
    const value = Number(attrs.value)
    const valid = Number.isFinite(value) && value > 0
    return { index, kind: 'level', value: valid ? value : undefined, label, valid }
  }

  // 认不出 kind 的一律视为无效：宁可退化成文本，也不要猜它想表达什么。
  return { index, kind: 'range', label, valid: false }
}

/**
 * 找出正文里所有锚点，按出现顺序编号。
 *
 * code fence / 行内代码 / HTML 标签内部都不解析——前者是示例代码，后者是别的
 * 标签的属性值。
 */
export function findAnchors(text: string): FoundAnchor[] {
  if (!text) return []
  const found: FoundAnchor[] = []
  const parts = text.split(CODE_SPLIT_RE)

  // split 后奇数下标是被保护的片段；只有偶数下标是正文。累加偏移量把下标换算回原文。
  let offset = 0
  for (let i = 0; i < parts.length; i++) {
    const seg = parts[i]
    if (i % 2 === 0) {
      ANCHOR_RE.lastIndex = 0
      let m: RegExpExecArray | null
      while ((m = ANCHOR_RE.exec(seg)) !== null) {
        const attrs = parseTagAttributes(m[1] || '')
        const built = buildAnchor(attrs, found.length + 1)
        found.push({
          ...built,
          start: offset + m.index,
          end: offset + m.index + m[0].length,
          raw: m[0],
        })
      }
    }
    offset += seg.length
  }
  return found
}

/** 无效锚点的兜底文案：连 label 都没有时给一个不误导的说法。 */
function fallbackLabel(anchor: KLineAnchor): string {
  if (anchor.label) return anchor.label
  if (anchor.kind === 'level' && anchor.value !== undefined) return String(anchor.value)
  if (anchor.kind === 'range' && anchor.from) return anchor.from
  return '这一段'
}

function wrapAnchor(anchor: FoundAnchor): string {
  const attrs = [
    `class="${KLINE_ANCHOR_CLASS}"`,
    `${KLINE_ANCHOR_INDEX_ATTR}="${anchor.index}"`,
    `data-anchor-kind="${anchor.kind}"`,
    `data-anchor-valid="${anchor.valid ? '1' : '0'}"`,
  ]
  if (anchor.from) attrs.push(`data-anchor-from="${anchor.from}"`)
  if (anchor.to) attrs.push(`data-anchor-to="${anchor.to}"`)
  if (anchor.value !== undefined) attrs.push(`data-anchor-value="${anchor.value}"`)
  if (anchor.label) attrs.push(`data-anchor-label="${escapeHtml(anchor.label)}"`)
  // 编号必须出现在正文里——「正文① ↔ 图上①」是整个设计的支点，图上画了编号
  // 而正文不显示，用户就没有可对照的符号，联动又退回"藏在交互后面"。
  // 单独一个 span 是为了能把它样式化成圆徽章，与图上的圆徽章同形。
  const badge = anchor.valid
    ? `<span class="${KLINE_ANCHOR_NUM_CLASS}" aria-hidden="true">${anchor.index}</span>`
    : ''
  return `<span ${attrs.join(' ')}>${badge}${escapeHtml(fallbackLabel(anchor))}</span>`
}

/**
 * 把锚点标记替换成可交互的 span，返回可交给 marked 的 markdown。
 *
 * 无效锚点同样被替换，但只渲染 label 文本、带 `data-anchor-valid="0"`：
 * **语法本身绝不露出来**，内容一个字不丢。
 */
export function injectKLineAnchors(markdown: string): string {
  if (!markdown) return markdown
  const found = findAnchors(markdown)
  if (found.length === 0) return markdown

  let out = ''
  let cursor = 0
  for (const anchor of found) {
    out += markdown.slice(cursor, anchor.start)
    out += wrapAnchor(anchor)
    cursor = anchor.end
  }
  out += markdown.slice(cursor)
  return out
}

/**
 * 流式期间未闭合的 `<anchor …` 尾部。
 *
 * 打字打到一半会露出半截标签（`<anchor kind="ra`），必须在渲染前切掉，
 * 否则用户会看到一串乱码闪一下。与 `stripIncompleteCitationTag` 同一思路，
 * 但那一个按 `<k`/`<w` 前缀匹配，不认识 `<a`——所以这里自己判。
 */
export function stripIncompleteAnchorTag(content: string): string {
  if (!content) return content
  const start = content.lastIndexOf('<')
  if (start < 0) return content
  const tail = content.slice(start)
  // 已经有 `>` 说明标签闭合了；`</` 是闭合标签，不是锚点开头。
  if (tail.includes('>')) return content
  if (tail.startsWith('</')) return content
  const isAnchorPrefix = /^<a(?:n(?:c(?:h(?:o(?:r(?:\s[\s\S]*)?)?)?)?)?)?$/i.test(tail)
  return isAnchorPrefix ? content.slice(0, start) : content
}
