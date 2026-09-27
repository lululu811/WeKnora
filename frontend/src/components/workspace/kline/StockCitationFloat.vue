<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="stock-citation-float"
      :class="{ 'placement-below': isPlacementBelow }"
      :style="{ top: `${top}px`, left: `${computedLeft}px` }"
      @mouseenter="$emit('enter')"
      @mouseleave="$emit('leave')"
    >
      <!-- 头部：代码、名称、最新价与涨跌幅 -->
      <div class="stock-float__header">
        <div class="stock-float__title-row">
          <span class="stock-float__name">{{ stockName }}</span>
          <span class="stock-float__code">{{ thscode }}</span>
        </div>
        <div v-if="quote" class="stock-float__quote-row">
          <span class="stock-float__price" :class="quote.pctChange >= 0 ? 'is-up' : 'is-down'">
            {{ quote.close.toFixed(2) }}
          </span>
          <span class="stock-float__change" :class="quote.pctChange >= 0 ? 'is-up' : 'is-down'">
            {{ quote.pctChange >= 0 ? '+' : '' }}{{ quote.pctChange.toFixed(2) }}%
          </span>
        </div>
      </div>

      <!-- 评分卡片：五分制战法持股星级与标签 -->
      <div v-if="loading" class="stock-float__loading">
        <div class="stock-float__spinner" />
        <span>正在分析战法指标与量化结构...</span>
      </div>

      <template v-else-if="scoreResult">
        <div class="stock-float__score-card" :style="{ borderColor: scoreResult.themeColor }">
          <div class="score-card__stars">
            <span
              v-for="s in 5"
              :key="s"
              class="star-item"
              :class="{ 'is-active': s <= scoreResult.score }"
              :style="{ color: s <= scoreResult.score ? scoreResult.themeColor : '#cbd5e1' }"
            >★</span>
          </div>
          <span
            class="score-card__badge"
            :style="{ backgroundColor: `${scoreResult.themeColor}22`, color: scoreResult.themeColor }"
          >
            {{ scoreResult.ratingText }}
          </span>
        </div>

        <!-- 3 条核心战法速览提炼 -->
        <div class="stock-float__tactics">
          <div
            v-for="(point, idx) in scoreResult.bulletPoints"
            :key="idx"
            class="tactic-item"
          >
            <span class="tactic-dot" :style="{ backgroundColor: scoreResult.themeColor }" />
            <span class="tactic-text">{{ point }}</span>
          </div>
        </div>
      </template>

      <!-- 底部操作栏 -->
      <div class="stock-float__footer">
        <span class="stock-float__hint">同花顺知行量化指标引擎</span>
        <button
          type="button"
          class="stock-float__action-btn"
          @click="handleOpenWorkspace"
        >
          进入完整K线工作台 →
        </button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import type { KLineData } from 'klinecharts';
import { calcStockHoldingScore, type StockScoreResult } from './stock-score';

const props = defineProps<{
  visible: boolean;
  top: number;
  left: number;
  thscode: string;
  name?: string;
}>();

const emit = defineEmits<{
  (e: 'enter'): void;
  (e: 'leave'): void;
  (e: 'open-workspace', payload: { ticker: string; exchange: string; name: string }): void;
}>();

const loading = ref(false);
const scoreResult = ref<StockScoreResult | null>(null);
const quote = ref<{ close: number; pctChange: number } | null>(null);
const resolvedName = ref('');

const stockName = computed(() => {
  if (props.name) return props.name;
  if (resolvedName.value) return resolvedName.value;
  return props.thscode.split('.')[0] || props.thscode;
});

const isPlacementBelow = computed(() => {
  return props.top < 240;
});

const computedLeft = computed(() => {
  if (typeof window === 'undefined') return props.left;
  return Math.max(165, Math.min(window.innerWidth - 165, props.left));
});

const loadStockData = async (symbolStr: string) => {
  if (!symbolStr) return;
  loading.value = true;
  scoreResult.value = null;
  quote.value = null;
  resolvedName.value = '';

  const ticker = symbolStr.split('.')[0];
  if (!props.name && ticker) {
    fetch(`/api/symbols/search?q=${encodeURIComponent(ticker)}`)
      .then((r) => r.json())
      .then((json) => {
        if (json?.data?.[0]?.name) {
          resolvedName.value = json.data[0].name;
        }
      })
      .catch(() => {});
  }

  try {
    const res = await fetch(`/api/kline?symbol=${encodeURIComponent(symbolStr)}&period=day&limit=300`)
      .then((r) => r.json())
      .catch(() => ({ code: -1, data: [] }));

    if (res.code === 0 && Array.isArray(res.data) && res.data.length > 0) {
      const dataList: KLineData[] = res.data.map((r: any) => ({
        timestamp: r.ts * 1000,
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
        volume: r.volume,
        turnover: r.turnover,
      }));

      const last = dataList[dataList.length - 1];
      const prev = dataList.length > 1 ? dataList[dataList.length - 2] : last;
      const pctChange = prev.close > 0 ? ((last.close - prev.close) / prev.close) * 100 : 0;
      quote.value = { close: last.close, pctChange };

      scoreResult.value = calcStockHoldingScore(dataList);
    }
  } catch (err) {
    console.warn('[StockCitationFloat] failed to load stock kline data:', err);
  } finally {
    loading.value = false;
  }
};

watch(
  () => [props.visible, props.thscode],
  ([vis, code]) => {
    if (vis && code) {
      loadStockData(code as string);
    }
  },
  { immediate: true },
);

const handleOpenWorkspace = () => {
  const [ticker, exchange] = props.thscode.split('.');
  emit('open-workspace', {
    ticker: ticker || props.thscode,
    exchange: exchange || 'SH',
    name: stockName.value,
  });
};
</script>

<style lang="less" scoped>
.stock-citation-float {
  position: fixed;
  z-index: 10050;
  width: 320px;
  max-width: 90vw;
  background: var(--td-bg-color-container, #ffffff);
  border: 1px solid var(--td-component-stroke, #e2e8f0);
  border-radius: 8px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.18), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: var(--td-text-color-primary, #1e293b);
  transform: translate(-50%, -100%) translateY(-10px);
  pointer-events: auto;
  animation: floatFadeIn 0.15s cubic-bezier(0.16, 1, 0.3, 1);

  &.placement-below {
    transform: translate(-50%, 14px);
    animation: floatFadeInBelow 0.15s cubic-bezier(0.16, 1, 0.3, 1);
  }

  :root[theme-mode="dark"] & {
    background: #181d26;
    border-color: #2e3846;
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
    color: #f1f5f9;
  }
}

@keyframes floatFadeIn {
  from {
    opacity: 0;
    transform: translate(-50%, -100%) translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translate(-50%, -100%) translateY(-10px);
  }
}

@keyframes floatFadeInBelow {
  from {
    opacity: 0;
    transform: translate(-50%, 4px);
  }
  to {
    opacity: 1;
    transform: translate(-50%, 14px);
  }
}

.stock-float__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--td-component-stroke, #f1f5f9);
  padding-bottom: 8px;

  :root[theme-mode="dark"] & {
    border-bottom-color: #28303d;
  }
}

.stock-float__title-row {
  display: flex;
  align-items: baseline;
  gap: 6px;

  .stock-float__name {
    font-size: 15px;
    font-weight: 700;
  }

  .stock-float__code {
    font-size: 12px;
    color: var(--td-text-color-placeholder, #94a3b8);
    font-family: monospace;
  }
}

.stock-float__quote-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-family: monospace;
  font-weight: 700;

  .stock-float__price {
    font-size: 14px;
    &.is-up { color: #ef4444; }
    &.is-down { color: #10b981; }
  }

  .stock-float__change {
    font-size: 12px;
    &.is-up { color: #ef4444; }
    &.is-down { color: #10b981; }
  }
}

.stock-float__loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 0;
  font-size: 12px;
  color: var(--td-text-color-secondary, #64748b);
  justify-content: center;

  .stock-float__spinner {
    width: 14px;
    height: 14px;
    border: 2px solid rgba(0, 82, 217, 0.2);
    border-top-color: var(--td-brand-color, #0052d9);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.stock-float__score-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  border-radius: 6px;
  border-left: 3px solid #ef4444;
  background: rgba(0, 0, 0, 0.02);

  :root[theme-mode="dark"] & {
    background: rgba(255, 255, 255, 0.03);
  }

  .score-card__stars {
    display: flex;
    gap: 2px;
    font-size: 14px;
    line-height: 1;
  }

  .score-card__badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
  }
}

.stock-float__tactics {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 0;
}

.tactic-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--td-text-color-secondary, #475569);

  :root[theme-mode="dark"] & {
    color: #cbd5e1;
  }

  .tactic-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-top: 6px;
  }

  .tactic-text {
    flex: 1;
  }
}

.stock-float__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid var(--td-component-stroke, #f1f5f9);

  :root[theme-mode="dark"] & {
    border-top-color: #28303d;
  }

  .stock-float__hint {
    font-size: 10px;
    color: var(--td-text-color-placeholder, #94a3b8);
  }

  .stock-float__action-btn {
    border: none;
    background: var(--td-brand-color, #0052d9);
    color: #ffffff;
    font-size: 11px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.15s ease;

    &:hover {
      opacity: 0.9;
      transform: translateY(-1px);
    }

    &:active {
      transform: translateY(0);
    }
  }
}
</style>
