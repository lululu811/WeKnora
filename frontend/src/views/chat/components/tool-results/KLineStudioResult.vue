<template>
  <div class="kline-studio-result">
    <div class="kline-studio-header">
      <div class="kline-studio-title">
        <span class="kline-studio-icon" aria-hidden="true">📈</span>
        <span>K 线挑股</span>
        <span v-if="count > 0" class="kline-studio-count">{{ count }} 只</span>
      </div>
      <div class="kline-studio-actions">
        <button
          v-if="hasPick"
          type="button"
          class="kline-studio-open-panel"
          :title="t('chat.klineStudio.openInPanel')"
          @click="openInPanel(0)"
        >
          <t-icon name="chart" size="12px" />
          <span>{{ t('chat.klineStudio.openInPanel') }}</span>
        </button>
      </div>
    </div>

    <p v-if="!hasPick" class="kline-studio-empty">
      {{ t('chat.klineStudio.empty') }}
    </p>

    <ul v-else class="kline-studio-tickers">
      <li
        v-for="(pick, idx) in pickList"
        :key="`${pick.ticker}-${pick.exchange}-${idx}`"
      >
        <button
          type="button"
          class="kline-studio-ticker"
          :title="`${pick.ticker}.${pick.exchange}`"
          @click="openInPanel(idx)"
        >
          <span class="ticker-code">{{ pick.ticker }}</span>
          <span class="ticker-exchange">{{ pick.exchange }}</span>
        </button>
      </li>
    </ul>

    <!--
      可信度信息。后端早就把这些算好并要求透出（python-service/main.py 对
      price_merged_rows=0 的注释：「必须让调用方知道，否则"没选出票"会被读成
      "市场里没有"」），但此前界面上一条都没露，空结果只剩一句"没有推送"。
      现在无论有没有票都显示：扫了多少、命中多少、什么被过滤掉了。
    -->
    <ul v-if="coverage.facts.length" class="kline-studio-coverage">
      <li
        v-for="(fact, idx) in coverage.facts"
        :key="`${fact.key}-${idx}`"
        :class="['coverage-item', `coverage-${fact.tone}`]"
      >
        {{ t(`chat.klineStudio.coverage.${fact.key}`, fact.params || {}) }}
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { KlineStudioData } from '@/types/tool-results'
import { useChatKLinePanel } from '@/finance/composables/useChatKLinePanel'
import { readScreenerCoverage, resolveScreenerPayload } from './screenerCoverage'

const props = defineProps<{
  data: KlineStudioData | Record<string, unknown>
}>()

const { t } = useI18n()
const panel = useChatKLinePanel()

const record = computed(() => (props.data || {}) as Record<string, unknown>)

const pickList = computed(() => {
  const value = record.value.tickers
  if (Array.isArray(value) && value.length > 0) {
    return value
      .filter((item): item is { ticker: string; exchange: string } => {
        if (!item || typeof item !== 'object') return false
        const o = item as Record<string, unknown>
        return typeof o.ticker === 'string' && typeof o.exchange === 'string'
      })
      .map((item) => ({ ticker: item.ticker, exchange: item.exchange, name: (item as any).name }))
  }

  // 兼容 zettaranc.screener 的选股返回结构。拆包交给 resolveScreenerPayload，
  // 与下面的 coverage 共用同一份解析——两处各写一遍迟早漂移。
  const rawData = resolveScreenerPayload(record.value)
  const stocksVal = rawData.stocks || record.value.stocks
  if (Array.isArray(stocksVal)) {
    return stocksVal
      .map((item: any) => {
        const thscode = String(item.thscode || item.ticker || '')
        const parts = thscode.split('.')
        if (parts.length === 2) {
          return {
            ticker: parts[0],
            exchange: parts[1],
            name: item.name,
            pattern: item.strategy || (item.signals && item.signals[0]) || undefined,
          }
        }
        return null
      })
      .filter(Boolean) as Array<{ ticker: string; exchange: string; name?: string; pattern?: string }>
  }

  return []
})

const hasPick = computed(() => pickList.value.length > 0)

// 扫描口径与失效提示。后端已经算好，这里只负责把它露出来。
const coverage = computed(() => readScreenerCoverage(record.value))

const count = computed(() => {
  const value = record.value.count
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return pickList.value.length
})

const openInPanel = (idx: number) => {
  if (!panel || !hasPick.value) return
  panel.open(pickList.value, idx)
}
</script>

<style lang="less" scoped>
.kline-studio-result {
  margin: 8px 0;
  padding: 12px 14px;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-md);
  background: var(--td-bg-color-container);
}

.kline-studio-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.kline-studio-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: var(--app-text-base);
}

.kline-studio-icon {
  font-size: var(--app-text-xl);
}

.kline-studio-count {
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
  font-weight: 400;
}

.kline-studio-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.kline-studio-open-panel {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid var(--td-brand-color);
  border-radius: var(--app-radius-xs);
  background: var(--td-brand-color);
  color: var(--td-text-color-anti);
  font-size: var(--app-text-sm);
  cursor: pointer;
  transition: background var(--app-motion-fast) ease;

  &:hover {
    background: var(--td-brand-color-active);
  }
}

.kline-studio-empty {
  margin: 0;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-sm);
}

.kline-studio-tickers {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.kline-studio-ticker {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 16px;
  border: 1px solid var(--td-component-stroke);
  background: transparent;
  color: inherit;
  font-size: var(--app-text-sm);
  cursor: pointer;
  transition: background 0.12s ease, border-color var(--app-motion-instant) ease;

  &:hover {
    background: var(--td-brand-color-light);
    border-color: var(--td-brand-color);
  }
}

.ticker-code {
  font-weight: 600;
  font-family: var(--td-font-family-mono);
}

.ticker-exchange {
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
}

// 扫描口径与失效提示。用色刻意比正文弱一档：它是"这次结果可信到什么程度"的
// 旁注，不该盖过票本身；但 warn 项用警示色，因为那意味着"没选出票"的原因
// 根本不是市场里没有。
.kline-studio-coverage {
  margin: 8px 0 0;
  padding: 6px 0 0;
  border-top: 1px dashed var(--td-component-stroke);
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.coverage-item {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  line-height: 1.5;
  word-break: break-word;
}

.coverage-warn {
  color: var(--td-warning-color);
}
</style>