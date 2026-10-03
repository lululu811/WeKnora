import type { WatchDiary } from '@/finance/api/watchlist'
import type { KLineData } from '@/finance/components/kline/types'

export interface DiaryOutcome {
  verdict: string
  tradeDate: string
  futureDays: number
  maxGainPct: number
  maxDrawdownPct: number
  closeGainPct: number
  status: 'win' | 'loss' | 'neutral'
  label: string
}

export interface DiaryBacktestSummary {
  totalBuys: number
  wins: number
  winRate: number
  avgMaxGain: number
}

function formatDate(ts: number): string {
  const d = new Date(ts)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/**
 * 纯量化计算：根据历史真实 K 线复算日记建议的后续 T+1~T+5 表现
 * 严格遵循 "Python/Go/前端代码计算数据，LLM 不做算术" 原则。
 */
export function computeDiaryOutcomes(
  diaries: WatchDiary[],
  bars: KLineData[],
): { outcomes: Record<string, DiaryOutcome>; summary: DiaryBacktestSummary } {
  const outcomes: Record<string, DiaryOutcome> = {}
  if (!diaries.length || !bars.length) {
    return {
      outcomes,
      summary: { totalBuys: 0, wins: 0, winRate: 0, avgMaxGain: 0 },
    }
  }

  // 建立日期 -> bar 下标的快速映射
  const barDateMap = new Map<string, number>()
  for (let i = 0; i < bars.length; i++) {
    barDateMap.set(formatDate(bars[i].timestamp), i)
  }

  let totalBuys = 0
  let wins = 0
  let totalMaxGain = 0

  for (const d of diaries) {
    const idx = barDateMap.get(d.trade_date)
    if (idx === undefined || idx >= bars.length - 1) {
      // 当天即最新交易日或找不到对应 K 线，尚无未来数据
      continue
    }

    const baseClose = bars[idx].close
    if (!baseClose || baseClose <= 0) continue

    const forwardLimit = Math.min(idx + 5, bars.length - 1)
    const futureDays = forwardLimit - idx

    let maxHigh = -Infinity
    let minLow = Infinity

    for (let j = idx + 1; j <= forwardLimit; j++) {
      if (bars[j].high > maxHigh) maxHigh = bars[j].high
      if (bars[j].low < minLow) minLow = bars[j].low
    }

    const lastClose = bars[forwardLimit].close
    const maxGainPct = ((maxHigh - baseClose) / baseClose) * 100
    const maxDrawdownPct = ((minLow - baseClose) / baseClose) * 100
    const closeGainPct = ((lastClose - baseClose) / baseClose) * 100

    let status: 'win' | 'loss' | 'neutral' = 'neutral'
    let label = `T+${futureDays} ${closeGainPct >= 0 ? '+' : ''}${closeGainPct.toFixed(1)}%`

    if (d.verdict === 'buy') {
      totalBuys++
      totalMaxGain += Math.max(0, maxGainPct)

      if (maxGainPct >= 3.0 && maxDrawdownPct > -3.5) {
        status = 'win'
        wins++
        label = `T+${futureDays} 最高 +${maxGainPct.toFixed(1)}% 🎯`
      } else if (maxDrawdownPct <= -3.0) {
        status = 'loss'
        label = `T+${futureDays} 回撤 ${maxDrawdownPct.toFixed(1)}% 🛑`
      } else {
        label = `T+${futureDays} 收益 ${closeGainPct >= 0 ? '+' : ''}${closeGainPct.toFixed(1)}%`
      }
    } else if (d.verdict === 'sell' || d.verdict === 'exit') {
      if (closeGainPct < -1.5) {
        status = 'win'
        label = `规避回撤 ${Math.abs(closeGainPct).toFixed(1)}% 🛡️`
      }
    }

    outcomes[d.trade_date] = {
      verdict: d.verdict,
      tradeDate: d.trade_date,
      futureDays,
      maxGainPct,
      maxDrawdownPct,
      closeGainPct,
      status,
      label,
    }
  }

  const winRate = totalBuys > 0 ? Number(((wins / totalBuys) * 100).toFixed(1)) : 0
  const avgMaxGain = totalBuys > 0 ? Number((totalMaxGain / totalBuys).toFixed(1)) : 0

  return {
    outcomes,
    summary: {
      totalBuys,
      wins,
      winRate,
      avgMaxGain,
    },
  }
}
