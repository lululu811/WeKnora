<!--
  人气与飙升雷达（大盘工作台）。

  数据源：special.duckdb -> v_skyrocket (飙升榜) & v_hot_stock (人气总榜)
  核心用途：
    1. 飙升黑马榜：按 rank_change 降序排列，捕捉日内或跨日关注度暴增的黑马标的（位次骤升往往伴随资金点火）。
    2. 人气总榜：按综合关注度与热度排序，一览市场主线龙头与高辨识度核心标的。
-->
<template>
  <article class="hot-radar">
    <header class="hot-radar__head">
      <div class="hot-radar__title-row">
        <div class="hot-radar__title-wrap">
          <t-icon name="fire" size="16px" class="hot-radar__icon" />
          <span class="hot-radar__title">人气与飙升雷达</span>
        </div>
        <div class="hot-radar__tabs" role="tablist">
          <button
            type="button"
            class="hot-radar__tab"
            :class="{ 'is-active': mode === 'skyrocket' }"
            @click="switchMode('skyrocket')"
          >
            飙升黑马榜
          </button>
          <button
            type="button"
            class="hot-radar__tab"
            :class="{ 'is-active': mode === 'rank' }"
            @click="switchMode('rank')"
          >
            人气总榜 (Top 50)
          </button>
        </div>
        <span class="hot-radar__caption">{{ captionText }}</span>
      </div>
      <button type="button" class="hot-radar__toggle" @click="expanded = !expanded">
        {{ expanded ? t('common.collapse') : '展开更多' }}
      </button>
    </header>

    <p v-if="loading" class="hot-radar__empty">{{ t('common.loading') }}</p>
    <p v-else-if="failed" class="hot-radar__empty">人气数据未就绪</p>
    <p v-else-if="!items.length" class="hot-radar__empty">{{ t('marketDashboard.noData') }}</p>

    <div v-else class="hot-radar__content">
      <!-- 汇总提示条 -->
      <div class="hot-radar__summary-bar">
        <div class="hot-radar__stat">
          <span class="hot-radar__stat-lbl">观测日期</span>
          <span class="hot-radar__stat-val">{{ tradeDate || '最新' }}</span>
        </div>
        <div class="hot-radar__stat" v-if="topSurgeItem && mode === 'skyrocket'">
          <span class="hot-radar__stat-lbl">最大飙升</span>
          <span class="hot-radar__stat-val is-surge">{{ topSurgeItem.name }} (暴增 +{{ topSurgeItem.rank_change }}位)</span>
        </div>
        <div class="hot-radar__stat" v-if="topHeatItem && mode === 'rank'">
          <span class="hot-radar__stat-lbl">人气榜首</span>
          <span class="hot-radar__stat-val is-surge">{{ topHeatItem.name }} (热度 {{ fmtHeat(topHeatItem.heat) }})</span>
        </div>
      </div>

      <!-- 网格列表 -->
      <div class="hot-radar__grid" :class="{ 'is-collapsed': !expanded }">
        <div
          v-for="item in displayItems"
          :key="item.thscode + '-' + item.rank"
          class="hot-radar__card"
          @click="handleClickStock(item.thscode)"
        >
          <div class="hot-radar__card-top">
            <div class="hot-radar__rank-badge" :class="rankBadgeClass(item.rank)">
              #{{ item.rank }}
            </div>
            <div class="hot-radar__card-stock">
              <span class="hot-radar__card-name">{{ item.name }}</span>
              <span class="hot-radar__card-code">{{ item.thscode }}</span>
            </div>
            <div v-if="item.rank_change > 0" class="hot-radar__change-pill">
              ▲ +{{ item.rank_change }}
            </div>
            <div v-else-if="item.rank_change < 0" class="hot-radar__change-pill is-down">
              ▼ {{ item.rank_change }}
            </div>
          </div>

          <div class="hot-radar__card-bottom">
            <span class="hot-radar__heat-lbl">关注热度:</span>
            <span class="hot-radar__heat-val">{{ fmtHeat(item.heat) }}</span>
            <span v-if="item.rank_trend" class="hot-radar__trend-tag" :class="'is-' + item.rank_trend">
              {{ trendText(item.rank_trend) }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHotStocks, type HotStockItem } from '@/finance/api/market'

const { t } = useI18n()

type Mode = 'skyrocket' | 'rank'
const mode = ref<Mode>('skyrocket')
const expanded = ref(false)
const loading = ref(false)
const failed = ref(false)
const items = ref<HotStockItem[]>([])
const tradeDate = ref<string | null>(null)

const emit = defineEmits<{
  (e: 'select-stock', thscode: string): void
}>()

const captionText = computed(() => {
  if (mode.value === 'skyrocket') {
    return '位次暴增黑马（短线点火、关注度剧烈激增）'
  }
  return '全市场综合关注度最高标的（主线龙头与高辨识度核心）'
})

const displayItems = computed(() => {
  if (!expanded.value) {
    return items.value.slice(0, 10)
  }
  return items.value
})

const topSurgeItem = computed(() => {
  if (!items.value.length) return null
  return [...items.value].sort((a, b) => b.rank_change - a.rank_change)[0] || null
})

const topHeatItem = computed(() => {
  if (!items.value.length) return null
  return [...items.value].sort((a, b) => (b.heat ?? 0) - (a.heat ?? 0))[0] || null
})

function rankBadgeClass(rank: number) {
  if (rank === 1) return 'is-first'
  if (rank === 2) return 'is-second'
  if (rank === 3) return 'is-third'
  return ''
}

function fmtHeat(heat: number | null) {
  if (heat === null || heat === undefined) return '—'
  if (heat >= 10000) {
    return (heat / 10000).toFixed(1) + '万'
  }
  return heat.toLocaleString()
}

function trendText(trend: string) {
  if (trend === 'up') return '升温'
  if (trend === 'down') return '降温'
  return '持平'
}

function handleClickStock(thscode: string) {
  emit('select-stock', thscode)
}

async function loadData() {
  loading.value = true
  failed.value = false
  try {
    const res = await getHotStocks({ mode: mode.value, limit: 40 })
    if (res && res.ok) {
      items.value = res.items || []
      tradeDate.value = res.trade_date
    } else {
      items.value = []
      tradeDate.value = null
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
.hot-radar {
  background: var(--app-panel-bg, #fff);
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
    color: #ef4444;
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary, #111827);
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
    color: var(--app-text-secondary, #6b7280);
    cursor: pointer;
    transition: all 0.15s ease;

    &:hover {
      color: var(--app-text-primary, #111827);
    }

    &.is-active {
      background: var(--app-panel-bg, #fff);
      color: var(--app-text-primary, #111827);
      font-weight: 600;
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
    }
  }

  &__caption {
    font-size: 11px;
    color: var(--app-text-tertiary, #9ca3af);
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
    color: var(--app-text-tertiary, #9ca3af);
    text-align: center;
    padding: 20px 0;
    margin: 0;
  }

  &__summary-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 10px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--app-border, rgba(0, 0, 0, 0.05));
    font-size: 11px;
  }

  &__stat {
    display: flex;
    align-items: center;
    gap: 5px;
  }

  &__stat-lbl {
    color: var(--app-text-secondary, #6b7280);
  }

  &__stat-val {
    font-family: var(--app-font-mono, monospace);
    font-weight: 600;
    color: var(--app-text-primary, #111827);

    &.is-surge {
      color: #dc2626;
    }
  }

  &__grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
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
      border-color: #ef4444;
      transform: translateY(-1px);
    }
  }

  &__card-top {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }

  &__rank-badge {
    font-size: 10px;
    font-family: var(--app-font-mono, monospace);
    font-weight: 700;
    padding: 1px 4px;
    border-radius: 3px;
    background: rgba(0, 0, 0, 0.06);
    color: var(--app-text-secondary, #6b7280);

    &.is-first {
      background: #ef4444;
      color: #fff;
    }
    &.is-second {
      background: #f97316;
      color: #fff;
    }
    &.is-third {
      background: #eab308;
      color: #fff;
    }
  }

  &__card-stock {
    display: flex;
    align-items: baseline;
    gap: 4px;
    flex: 1;
    min-width: 0;
  }

  &__card-name {
    font-size: 12px;
    font-weight: 600;
    color: var(--app-text-primary, #111827);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__card-code {
    font-size: 10px;
    font-family: var(--app-font-mono, monospace);
    color: var(--app-text-tertiary, #9ca3af);
  }

  &__change-pill {
    font-size: 10px;
    font-family: var(--app-font-mono, monospace);
    font-weight: 700;
    color: #dc2626;
    background: rgba(220, 38, 38, 0.1);
    padding: 1px 5px;
    border-radius: 4px;
    white-space: nowrap;

    &.is-down {
      color: #059669;
      background: rgba(5, 150, 105, 0.1);
    }
  }

  &__card-bottom {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 11px;
    padding-top: 3px;
    border-top: 1px dashed var(--app-border, rgba(0, 0, 0, 0.04));
  }

  &__heat-lbl {
    color: var(--app-text-tertiary, #9ca3af);
  }

  &__heat-val {
    font-family: var(--app-font-mono, monospace);
    font-weight: 500;
    color: var(--app-text-secondary, #4b5563);
  }

  &__trend-tag {
    font-size: 10px;
    padding: 0 4px;
    border-radius: 2px;

    &.is-up {
      color: #dc2626;
      background: rgba(220, 38, 38, 0.08);
    }
    &.is-down {
      color: #059669;
      background: rgba(5, 150, 105, 0.08);
    }
    &.is-flat {
      color: var(--app-text-tertiary, #9ca3af);
    }
  }
}
</style>
