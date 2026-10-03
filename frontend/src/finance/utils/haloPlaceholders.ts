/**
 * HALO 骨架占位符的处理。
 *
 * **背景**：python-service 的 `render_markdown`（`halo/analyze.py`）对七个定性
 * 维度（护城河 / 滞胀防御 / ESG / 管理层 / 股东资金面 / 估值 / 风险）**只产出
 * 槽位，不产出分数与分析**——它写的是 `{{moat_score}}` 和 `{{moat_analysis}}`
 * 这样的字面占位符。填槽的责任在调用方（agent 拿到骨架后由模型判分），
 * `/halo/report` 这条链路上没有任何代码会填。
 *
 * 所以：**凡是把这份 markdown 直接渲染给人看的地方，都必须先过 `maskHaloSlots`。**
 * 否则用户会看到 `{{moat_score}}` 这种内部实现细节 —— 那是"读到了骨架"的
 * 内部状态，不是给读者看的东西。
 *
 * 刻意不在这里"顺手把槽位填了"：填分需要量化锚点支撑，而判分是模型的事。
 * 前端唯一该做的是**如实呈现"此处待判分"**，而不是编一个数字上去。
 */

/**
 * 占位符本体，容错写法。
 *
 * 三个细节都是照着 `analyze.py` 的实际输出写的，不是想当然：
 * 1. 外层反引号 —— `_md_dimension` 把分数槽位包在代码 span 里（`` `{{x_score}}` ``），
 *    而 `_md_dimension` 末尾的分析槽位**不**带反引号（`分析：{{x_analysis}}`）。
 *    所以反引号必须可选，且要连同反引号一起吃掉，否则会剩下一个空的代码 span。
 * 2. 内部空白 —— Python 用 f-string 的四花括号拼 `{{{{...}}}}`，实测输出没有
 *    多余空白，但容错成本为零。
 * 3. 维度名 —— 全部是小写字母与下划线（`moat` / `stag` / `esg` /
 *    `management` / `shareholder` / `valuation` / `risk`），不匹配就当没有。
 */
const HALO_SLOT_RE = /`?\{\{\s*([a-z_]+)_(score|analysis)\s*\}\}`?/g

/** 分数槽位与分析槽位给读者看的替代文案。 */
const SLOT_TEXT: Record<string, string> = {
  score: '**待判分**',
  analysis: '**（定性分析待补）**',
}

/**
 * 这份 markdown 里是否还有没填的槽位。
 *
 * 调用方用它决定要不要显示"骨架版"提示 —— 与 `hasHaloSlots` 配对的是
 * `maskHaloSlots`，两者都走同一个正则，所以不会出现"提示说有槽位、正文却
 * 没有"的分裂。
 */
export function hasHaloSlots(markdown: string): boolean {
  if (!markdown) return false
  return new RegExp(HALO_SLOT_RE.source, 'g').test(markdown)
}

/**
 * 把未填的槽位换成"待判分"字样。
 *
 * 幂等：已经替换过的文本不含 `{{...}}`，再跑一次不会二次改写。
 * 空串与非字符串原样返回 —— 调用方常把可能为 undefined 的字段直接传进来。
 */
export function maskHaloSlots(markdown: string): string {
  if (!markdown || typeof markdown !== 'string') return ''
  return markdown.replace(new RegExp(HALO_SLOT_RE.source, 'g'), (_match, _dim: string, kind: string) => {
    return SLOT_TEXT[kind] ?? '**待判分**'
  })
}

/**
 * 出现过的维度名（去重、保持出现顺序）。
 *
 * 用途是回答"这份报告缺哪几维" —— 骨架版的免责声明需要具体列出，而不是笼统
 * 地说"部分维度未判分"。
 */
export function haloSlotDimensions(markdown: string): string[] {
  if (!markdown || typeof markdown !== 'string') return []
  const seen: string[] = []
  const re = new RegExp(HALO_SLOT_RE.source, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(markdown)) !== null) {
    if (!seen.includes(m[1])) seen.push(m[1])
  }
  return seen
}
