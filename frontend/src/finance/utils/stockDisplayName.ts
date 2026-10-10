/**
 * 自选股行的展示名。
 *
 * 背景：`stock_watches.name` 是**新增时**写进库的一列，之后没人回填。如果客户端
 * 当初只传了代码，库里存下的 name 就是 "002859"，于是列表里那一行会渲染成
 * 「002859」+「002859.SZ」——同一段代码出现两次，看起来像渲染 bug。
 *
 * 规则：优先用行情源（python-service 标的表）那份名字，那份不会存成代码；清单里
 * 存的当兜底；判断"这个字符串其实是代码"只看它是否等于 thscode 或裸代码 ——
 * 真名不会长这样，所以这样判不会误伤真名。
 *
 * 判定为代码时返回空串，让 thscode 单独当标识。比显示一个"看起来像名字的代码"
 * 诚实，也比什么都不显示好定位。
 */

/** 去掉交易所后缀：`002859.SZ` → `002859`。 */
export function bareCode(thscode: string): string {
  const dot = thscode.indexOf('.')
  return dot > 0 ? thscode.slice(0, dot) : thscode
}

/** 这个字符串是不是"其实就是代码"。 */
export function looksLikeCode(value: string, thscode: string): boolean {
  const v = value.trim()
  return !v || v === thscode.trim() || v === bareCode(thscode.trim())
}

/**
 * 行首显示的名字。识别不出真名时返回空串。
 *
 * @param stored    清单里存的那份（可能是代码，也可能是空）
 * @param thscode   带后缀的代码，用于识别与展示
 * @param quoteName 行情源那份，可靠性最高
 */
export function displayStockName(
  stored: string | null | undefined,
  thscode: string,
  quoteName?: string | null,
): string {
  // 行情源优先：它来自标的表，不存在"存了个代码进去"的情况。
  if (quoteName && !looksLikeCode(quoteName, thscode)) return quoteName.trim()
  if (stored && !looksLikeCode(stored, thscode)) return stored.trim()
  return ''
}
