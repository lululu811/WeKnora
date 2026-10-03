export interface TradeTarget {
  cost?: number
  stopLoss?: number
}

const STORAGE_PREFIX = 'weknora:trade_targets:'

/** 读取某标的的持仓成本与防守止损价 */
export function getTradeTarget(thscode: string): TradeTarget | null {
  if (!thscode) return null
  try {
    const raw = localStorage.getItem(`${STORAGE_PREFIX}${thscode}`)
    if (!raw) return null
    return JSON.parse(raw) as TradeTarget
  } catch {
    return null
  }
}

/** 保存某标的的持仓成本与防守止损价 */
export function saveTradeTarget(thscode: string, target: TradeTarget | null): void {
  if (!thscode) return
  try {
    if (!target || (!target.cost && !target.stopLoss)) {
      localStorage.removeItem(`${STORAGE_PREFIX}${thscode}`)
    } else {
      localStorage.setItem(`${STORAGE_PREFIX}${thscode}`, JSON.stringify(target))
    }
  } catch {}
}
