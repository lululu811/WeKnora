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
      当前没有推送标的。
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
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { KlineStudioData } from '@/types/tool-results'
import { useChatKLinePanel } from '@/composables/useChatKLinePanel'

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

  // 兼容 zettaranc.screener 的选股返回结构
  const rawData = (record.value.data as any) || record.value
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
  color: #fff;
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
</style>