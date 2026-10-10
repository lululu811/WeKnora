<template>
  <article class="fin-etf">
    <div class="fin-etf__head-row">
      <div class="fin-etf__head">
        <span>名称</span><span>现价</span><span>份额变动</span><span>放量</span><span>信号</span>
      </div>
      <span class="fin-etf__caption">{{ items.length }} 只 · 汇金重仓宽基</span>
    </div>

    <div v-for="it in items" :key="it.thscode" class="fin-etf__row">
      <span class="fin-etf__name">
        {{ it.name }}
        <em v-if="it.signal" class="fin-etf__dot" aria-label="异动" />
      </span>
      <span class="fin-etf__num">{{ it.close === null ? '—' : it.close.toFixed(3) }}</span>
      <!-- 份额优先；没有上一观测点时退回近 5 日涨跌，并标出这是另一个口径。
           null 一律显示「—」，不用 0 顶替 —— 0% 会被读成"没动"。 -->
      <span class="fin-etf__num" :class="trendClass(primary(it).value)">
        {{ fmtPct(primary(it).value) }}<template v-if="primary(it).basis === 'price'">（近5日）</template>
      </span>
      <span class="fin-etf__num">
        {{ it.turnover_multiple === null ? '—' : `${it.turnover_multiple.toFixed(2)}×` }}
      </span>
      <span class="fin-etf__sig">{{ it.signal ? '异动' : '—' }}</span>
    </div>
  </article>
</template>

<script setup lang="ts">
/**
 * ETF 区块。三个候选方案共用同一个组件 —— 它们在这一块上不应该有差别，
 * 差别只在"这块出现在哪一屏"。
 *
 * 与大盘预览原卡片的口径一致（MarketDashboard.vue）：信号由后端判定，
 * 前端只展示不复算；份额是季频，caption 由调用方补「份额截至 …（季频）」。
 */
import { fmtPct, trendClass, etfPrimaryChange } from './useMergeLab'
import type { EtfFlowItem } from '@/finance/api/pulse'

defineProps<{ items: EtfFlowItem[] }>()

function primary(it: EtfFlowItem) {
  return etfPrimaryChange(it)
}
</script>

<style scoped>
.fin-etf { background: #fdfaf5; border: 1px solid #eee3d4; border-radius: 9px; padding: 12px 14px; }
.fin-etf__head-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.fin-etf__head { display: grid; grid-template-columns: 1.6fr .8fr 1fr .6fr .5fr; gap: 8px;
  width: 100%; font-size: 11px; color: #a3988d; padding-bottom: 6px; border-bottom: 1px solid #f0e7da; }
.fin-etf__caption { font-size: 11px; color: #a3988d; white-space: nowrap; }
.fin-etf__row { display: grid; grid-template-columns: 1.6fr .8fr 1fr .6fr .5fr; gap: 8px;
  align-items: baseline; padding: 7px 0; border-bottom: 1px dashed #f0e7da; font-size: 12px; }
.fin-etf__row:last-child { border-bottom: 0; }
.fin-etf__name { display: flex; align-items: center; gap: 6px; color: #2c2622; }
.fin-etf__dot { width: 5px; height: 5px; border-radius: 50%; background: var(--md-up); display: inline-block; }
.fin-etf__num { font-family: var(--app-font-family-mono, ui-monospace, monospace);
  font-variant-numeric: tabular-nums; }
.fin-etf__sig { font-size: 11px; color: #a3988d; }
.is-up { color: var(--md-up); }
.is-down { color: var(--md-down); }
.is-flat { color: #8a8078; }
</style>
