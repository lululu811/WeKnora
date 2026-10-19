<!--
  除权除息日历与高股息/高送转雷达（大盘工作台）。

  数据源：market.duckdb -> raw_adjustment_events (57,623 行)
  提供三个维度：
    1. 全部除权日历：按除息除权日先后排序，一览近期分红公司。
    2. 高股息标的：基于现金派现与最新股价计算真实股息率（Dividend Yield %），服务红利策略。
    3. 高送转红利：筛选转增股与送股比例 >= 0.3 的题材标的。
-->
<template>
  <article class="div-cal">
    <header class="div-cal__head">
      <div class="div-cal__title-row">
        <div class="div-cal__title-wrap">
          <t-icon name="money-circle" size="16px" class="div-cal__icon" />
          <span class="div-cal__title">{{ t('dividend.title') }}</span>
        </div>
        <div class="div-cal__tabs" role="tablist">
          <button
            type="button"
            class="div-cal__tab"
            role="tab"
            :aria-selected="mode === 'all'"
            :class="{ 'is-active': mode === 'all' }"
            @click="switchMode('all')"
          >
            {{ t('dividend.tabAll') }}
          </button>
          <button
            type="button"
            class="div-cal__tab"
            role="tab"
            :aria-selected="mode === 'yield'"
            :class="{ 'is-active': mode === 'yield' }"
            @click="switchMode('yield')"
          >
            {{ t('dividend.tabYield') }}
          </button>
          <button
            type="button"
            class="div-cal__tab"
            role="tab"
            :aria-selected="mode === 'bonus'"
            :class="{ 'is-active': mode === 'bonus' }"
            @click="switchMode('bonus')"
          >
            {{ t('dividend.tabBonus') }}
          </button>
        </div>
        <span class="div-cal__caption">{{ captionText }}</span>
      </div>
      <button type="button" class="div-cal__toggle" @click="expanded = !expanded">
        {{ expanded ? t('common.collapse') : t('dividend.expand') }}
      </button>
    </header>

    <p v-if="loading" class="div-cal__empty">{{ t('common.loading') }}</p>
    <p v-else-if="failed" class="div-cal__empty">{{ t('dividend.failed') }}</p>
    <p v-else-if="!items.length" class="div-cal__empty">{{ t('marketDashboard.noData') }}</p>

    <div v-else class="div-cal__content">
      <!-- 汇总统计条 -->
      <div class="div-cal__stats">
        <div class="div-cal__stat-chip">
          <span class="div-cal__stat-lbl">{{ t('dividend.statCount') }}</span>
          <span class="div-cal__stat-num">{{ items.length }}</span>
        </div>
        <div v-if="avgYieldText" class="div-cal__stat-chip">
          <span class="div-cal__stat-lbl">{{ t('dividend.statAvgYield') }}</span>
          <span class="div-cal__stat-num is-yield">{{ avgYieldText }}</span>
        </div>
        <div v-if="maxBonusItem" class="div-cal__stat-chip">
          <span class="div-cal__stat-lbl">{{ t('dividend.statTopBonus') }}</span>
          <span class="div-cal__stat-val">{{ maxBonusItem.name }} ({{ t('dividend.bonusTen', { value: ((maxBonusItem.per_share_bonus ?? 0) * 10).toFixed(1) }) }})</span>
        </div>
      </div>

      <!-- 网格列表 -->
      <div class="div-cal__grid" :class="{ 'is-collapsed': !expanded }">
        <div
          v-for="item in displayItems"
          :key="item.thscode + '-' + (item.ex_date || '')"
          class="div-cal__card"
          @click="handleClickStock(item.thscode)"
        >
          <div class="div-cal__card-top">
            <div class="div-cal__card-stock">
              <span class="div-cal__card-name">{{ item.name }}</span>
              <span class="div-cal__card-code">{{ item.thscode }}</span>
            </div>
            <span class="div-cal__card-date">{{ item.ex_date || '—' }}</span>
          </div>

          <div class="div-cal__card-payout">
            <div v-if="(item.dividend_per_share ?? 0) > 0" class="div-cal__payout-item">
              <span class="div-cal__payout-k">{{ t('dividend.payoutLabel') }}:</span>
              <span class="div-cal__payout-v">{{ t('dividend.cashPerShare', { value: (item.dividend_per_share ?? 0).toFixed(2) }) }}</span>
            </div>
            <div v-if="(item.per_share_bonus ?? 0) > 0" class="div-cal__payout-item is-bonus">
              <span class="div-cal__payout-k">{{ t('dividend.bonusLabel') }}:</span>
              <span class="div-cal__payout-v">{{ t('dividend.bonusShares', { value: ((item.per_share_bonus ?? 0) * 10).toFixed(1) }) }}</span>
            </div>
          </div>

          <div class="div-cal__card-bottom">
            <div class="div-cal__price">
              <span class="div-cal__price-k">{{ t('dividend.priceLabel') }}:</span>
              <span class="div-cal__price-v">¥{{ item.close ? item.close.toFixed(2) : '—' }}</span>
            </div>
            <div
              v-if="item.dividend_yield !== null && item.dividend_yield !== undefined"
              class="div-cal__yield-pill"
              :class="yieldPillClass(item.dividend_yield)"
            >
              {{ t('dividend.yieldPill', { value: item.dividend_yield.toFixed(2) }) }}
            </div>
          </div>
        </div>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getDividendCalendar, type DividendEventItem } from '@/finance/api/market'

const { t } = useI18n()

type Mode = 'all' | 'yield' | 'bonus'
const mode = ref<Mode>('yield')
const expanded = ref(false)
const loading = ref(false)
const failed = ref(false)
const items = ref<DividendEventItem[]>([])

const emit = defineEmits<{
  (e: 'select-stock', thscode: string): void
}>()

const captionText = computed(() => {
  if (mode.value === 'yield') return t('dividend.captionYield')
  if (mode.value === 'bonus') return t('dividend.captionBonus')
  return t('dividend.captionAll')
})

const displayItems = computed(() => {
  if (!expanded.value) {
    return items.value.slice(0, 8)
  }
  return items.value
})

const avgYieldText = computed(() => {
  const valid = items.value.filter(i => (i.dividend_yield ?? 0) > 0)
  if (!valid.length) return null
  const sum = valid.reduce((acc, cur) => acc + (cur.dividend_yield ?? 0), 0)
  return (sum / valid.length).toFixed(2) + '%'
})

const maxBonusItem = computed(() => {
  const sorted = [...items.value].filter(i => (i.per_share_bonus ?? 0) > 0)
  sorted.sort((a, b) => (b.per_share_bonus ?? 0) - (a.per_share_bonus ?? 0))
  return sorted[0] || null
})

function yieldPillClass(y: number) {
  if (y >= 8.0) return 'is-super'
  if (y >= 5.0) return 'is-high'
  return 'is-normal'
}

function handleClickStock(thscode: string) {
  emit('select-stock', thscode)
}

async function loadData() {
  loading.value = true
  failed.value = false
  try {
    let params: Parameters<typeof getDividendCalendar>[0] = { limit: 40 }
    if (mode.value === 'yield') {
      params = { min_dividend: 0.1, sort_by: 'yield', limit: 40 }
    } else if (mode.value === 'bonus') {
      params = { min_bonus: 0.25, sort_by: 'bonus', limit: 40 }
    } else {
      params = { sort_by: 'date', limit: 40 }
    }
    const res = await getDividendCalendar(params)
    if (res && res.ok) {
      items.value = res.items || []
    } else {
      items.value = []
    }
  } catch {
    failed.value = true
    items.value = []
  } finally {
    loading.value = false
  }
}

function switchMode(newMode: Mode) {
  if (mode.value === newMode) return
  mode.value = newMode
  loadData()
}

onMounted(() => {
  loadData()
})
</script>

<style scoped lang="less">
.div-cal {
  background: var(--td-bg-color-container);
  border: 1px solid var(--app-border, rgba(0, 0, 0, 0.08));
  border-radius: var(--td-radius-medium, 8px);
  padding: 12px 14px;
  box-sizing: border-box;

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    gap: 8px;
  }

  &__title-row {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }

  &__title-wrap {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  &__icon {
    color: #f59e0b;
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
    color: var(--td-text-color-primary);
  }

  &__tabs {
    display: inline-flex;
    background: var(--app-bg-secondary, rgba(0, 0, 0, 0.04));
    border-radius: 6px;
    padding: 2px;
    gap: 2px;
  }

  &__tab {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
    border: none;
    background: transparent;
    color: var(--td-text-color-secondary);
    cursor: pointer;
    transition: all 0.15s ease;

    &:hover {
      color: var(--td-text-color-primary);
    }

    &.is-active {
      background: var(--td-bg-color-container);
      color: var(--td-text-color-primary);
      font-weight: 600;
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
    }
  }

  &__caption {
    font-size: 11px;
    color: var(--td-text-color-placeholder);
  }

  &__toggle {
    font-size: 11px;
    border: none;
    background: transparent;
    color: var(--td-brand-color, #0052d9);
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 4px;

    &:hover {
      background: rgba(0, 82, 217, 0.08);
    }
  }

  &__empty {
    font-size: 12px;
    color: var(--td-text-color-placeholder);
    text-align: center;
    padding: 20px 0;
    margin: 0;
  }

  &__stats {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--app-border, rgba(0, 0, 0, 0.05));
    flex-wrap: wrap;
  }

  &__stat-chip {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    background: var(--app-bg-secondary, rgba(0, 0, 0, 0.03));
    padding: 2px 8px;
    border-radius: 4px;
  }

  &__stat-lbl {
    color: var(--td-text-color-secondary);
  }

  &__stat-num {
    font-family: var(--app-font-mono, monospace);
    font-weight: 600;
    color: var(--td-text-color-primary);

    &.is-yield {
      color: #059669;
    }
  }

  &__stat-val {
    font-family: var(--app-font-mono, monospace);
    font-weight: 600;
    color: #d97706;
  }

  &__grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    gap: 8px;
    transition: max-height 0.25s ease;
  }

  &__card {
    background: var(--app-bg-secondary, rgba(0, 0, 0, 0.02));
    border: 1px solid var(--app-border, rgba(0, 0, 0, 0.06));
    border-radius: 6px;
    padding: 8px 10px;
    cursor: pointer;
    transition: border-color 0.15s ease, transform 0.15s ease;

    &:hover {
      border-color: var(--td-brand-color, #0052d9);
      transform: translateY(-1px);
    }
  }

  &__card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;
  }

  &__card-stock {
    display: flex;
    align-items: baseline;
    gap: 4px;
  }

  &__card-name {
    font-size: 12px;
    font-weight: 600;
    color: var(--td-text-color-primary);
  }

  &__card-code {
    font-size: 10px;
    font-family: var(--app-font-mono, monospace);
    color: var(--td-text-color-placeholder);
  }

  &__card-date {
    font-size: 10px;
    font-family: var(--app-font-mono, monospace);
    color: var(--td-text-color-secondary);
  }

  &__card-payout {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-bottom: 6px;
  }

  &__payout-item {
    font-size: 11px;
    display: flex;
    align-items: center;
    gap: 4px;
  }

  &__payout-k {
    color: var(--td-text-color-placeholder);
  }

  &__payout-v {
    font-family: var(--app-font-mono, monospace);
    font-weight: 500;
    color: var(--td-text-color-primary);
  }

  &__payout-item.is-bonus &__payout-v {
    color: #b45309;
    font-weight: 600;
  }

  &__card-bottom {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-top: 4px;
    border-top: 1px dashed var(--app-border, rgba(0, 0, 0, 0.05));
  }

  &__price {
    font-size: 10px;
    display: flex;
    gap: 2px;
  }

  &__price-k {
    color: var(--td-text-color-placeholder);
  }

  &__price-v {
    font-family: var(--app-font-mono, monospace);
    color: var(--td-text-color-secondary);
  }

  &__yield-pill {
    font-size: 10px;
    font-family: var(--app-font-mono, monospace);
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 4px;

    &.is-normal {
      background: rgba(16, 185, 129, 0.1);
      color: #059669;
    }

    &.is-high {
      background: rgba(245, 158, 11, 0.12);
      color: #d97706;
    }

    &.is-super {
      background: rgba(239, 68, 68, 0.12);
      color: #dc2626;
    }
  }
}
</style>
