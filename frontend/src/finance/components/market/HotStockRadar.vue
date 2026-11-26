<!--
  人气与飙升雷达（大盘工作台）。

  数据源：special.duckdb -> v_skyrocket (飙升榜) & v_hot_stock (人气总榜)
  核心用途：
    1. 飙升黑马榜：按 rank_change 降序排列，捕捉日内或跨日关注度暴增的黑马标的（位次骤升往往伴随资金点火）。
    2. 人气总榜：按综合关注度与热度排序，一览市场主线龙头与高辨识度核心标的。
-->
<template>
  <article class="hot-radar ledger-block">
    <header class="hot-radar__head">
      <div class="hot-radar__title-row">
        <div class="hot-radar__title-wrap">
          <t-icon name="fire" size="16px" class="hot-radar__icon" />
          <span class="hot-radar__title">{{ t('hotStockRadar.title') }}</span>
        </div>
        <div class="hot-radar__tabs" role="tablist">
          <button
            type="button"
            class="hot-radar__tab"
            role="tab"
            :aria-selected="mode === 'skyrocket'"
            :class="{ 'is-active': mode === 'skyrocket' }"
            @click="switchMode('skyrocket')"
          >
            {{ t('hotStockRadar.tabSkyrocket') }}
          </button>
          <button
            type="button"
            class="hot-radar__tab"
            role="tab"
            :aria-selected="mode === 'rank'"
            :class="{ 'is-active': mode === 'rank' }"
            @click="switchMode('rank')"
          >
            {{ t('hotStockRadar.tabRank') }}
          </button>
        </div>
        <span class="hot-radar__caption">{{ captionText }}</span>
      </div>
      <button type="button" class="hot-radar__toggle" @click="expanded = !expanded">
        {{ expanded ? t('common.collapse') : t('hotStockRadar.expand') }}
      </button>
    </header>

    <p v-if="loading" class="hot-radar__empty">{{ t('common.loading') }}</p>
    <p v-else-if="failed" class="hot-radar__empty">{{ t('hotStockRadar.failed') }}</p>
    <p v-else-if="!items.length" class="hot-radar__empty">{{ t('marketDashboard.noData') }}</p>

    <div v-else class="hot-radar__content">
      <!-- 汇总提示条 -->
      <div class="hot-radar__summary-bar">
        <div class="hot-radar__stat">
          <span class="hot-radar__stat-lbl">{{ t('hotStockRadar.observeDate') }}</span>
          <span class="hot-radar__stat-val">{{ tradeDate || t('hotStockRadar.latest') }}</span>
        </div>
        <div class="hot-radar__stat" v-if="topSurgeItem && mode === 'skyrocket'">
          <span class="hot-radar__stat-lbl">{{ t('hotStockRadar.topSurge') }}</span>
          <span class="hot-radar__stat-val is-surge">{{ t('hotStockRadar.topSurgeValue', { name: topSurgeItem.name, value: topSurgeItem.rank_change }) }}</span>
        </div>
        <div class="hot-radar__stat" v-if="topHeatItem && mode === 'rank'">
          <span class="hot-radar__stat-lbl">{{ t('hotStockRadar.topHeat') }}</span>
          <span class="hot-radar__stat-val is-surge">{{ t('hotStockRadar.topHeatValue', { name: topHeatItem.name, value: fmtHeat(topHeatItem.heat) }) }}</span>
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
            <span class="hot-radar__heat-lbl">{{ t('hotStockRadar.heatLabel') }}:</span>
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
    return t('hotStockRadar.captionSkyrocket')
  }
  return t('hotStockRadar.captionRank')
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
  if (trend === 'up') return t('hotStockRadar.trend.up')
  if (trend === 'down') return t('hotStockRadar.trend.down')
  return t('hotStockRadar.trend.flat')
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
/*
 * 墨账体例（achromatic-ledger）—— 规范见 src/finance/styles/ledger.less 文件头：
 *   1. 无框块：块与块之间只靠留白与字号层级分开，不画卡片；
 *   2. 图表用墨色；
 *   3. 浓淡表用墨阶：--ledger-ink-1…5（名次徽标就是一张浓淡表）。
 * 涨跌红绿是金融语义，只出现在数字上，保留。
 */
.hot-radar {
  /* 块根：去掉卡片外壳（底色 / 描边 / 圆角 / 阴影）；块距由父级 grid 的 gap 给。 */
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
    /* 图标是装饰，不是数据 —— 墨账里用墨色，不占涨跌红绿与品牌珊瑚。 */
    color: var(--td-text-color-secondary);
  }

  &__title {
    font-size: 13px;
    font-weight: 600;
    color: var(--td-text-color-primary);
  }

  &__tabs {
    display: inline-flex;
    /* 与 BasisStrip 的 tab 条同一体例：中性底色，不占品牌珊瑚。 */
    background: var(--td-bg-color-secondarycontainer);
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
      /* 墨账：去掉投影 —— 阴影是"这是一张卡片"的信号。 */
    }
  }

  &__caption {
    font-size: 11px;
    color: var(--td-text-color-placeholder);
  }

  /* 「展开更多 / 收起」是次级动作：墨账里用文字链接，不画按钮框，也不占品牌珊瑚。 */
  &__toggle {
    font-size: 11px;
    border: none;
    background: transparent;
    color: var(--td-text-color-secondary);
    cursor: pointer;
    padding: 2px 4px;

    &:hover {
      color: var(--td-text-color-primary);
      text-decoration: underline;
    }
  }

  &__empty {
    font-size: 12px;
    color: var(--td-text-color-placeholder);
    text-align: center;
    padding: 20px 0;
    margin: 0;
  }

  &__summary-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 14px;
    /* 墨账：块内不画分隔线，靠留白分层。 */
    font-size: 11px;
  }

  &__stat {
    display: flex;
    align-items: center;
    gap: 5px;
  }

  &__stat-lbl {
    color: var(--td-text-color-secondary);
  }

  &__stat-val {
    font-family: var(--app-font-mono, monospace);
    font-weight: 600;
    color: var(--td-text-color-primary);

    &.is-surge {
      color: var(--app-quote-up);
    }
  }

  &__grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: 10px 14px;
    transition: max-height 0.25s ease;
  }

  /* 个股格子同样不画卡片：无框，靠留白与字重分层。 */
  &__card {
    background: transparent;
    border: 0;
    border-radius: 0;
    padding: 6px 8px;
    cursor: pointer;
    transition: background-color 0.15s ease;

    &:hover {
      background: var(--ledger-ink-1);
    }
  }

  &__card-top {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }

  /* 名次徽标是一张"越浓越靠前"的浓淡表：用墨阶，不占用红绿与珊瑚。
     底色是淡墨阶，文字仍用主墨色 —— 亮暗两套主题下都读得清。 */
  &__rank-badge {
    font-size: var(--app-text-xs);
    font-family: var(--app-font-mono, monospace);
    font-weight: 700;
    padding: 1px 4px;
    border-radius: 3px;
    background: transparent;
    color: var(--td-text-color-secondary);

    &.is-first {
      background: var(--ledger-ink-3);
      color: var(--td-text-color-primary);
    }
    &.is-second {
      background: var(--ledger-ink-2);
      color: var(--td-text-color-primary);
    }
    &.is-third {
      background: var(--ledger-ink-1);
      color: var(--td-text-color-primary);
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
    color: var(--td-text-color-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__card-code {
    font-size: var(--app-text-xs);
    font-family: var(--app-font-mono, monospace);
    color: var(--td-text-color-placeholder);
  }

  /* 名次变化是方向数据：红涨绿跌保留，底色改用主题 soft 令牌。 */
  &__change-pill {
    font-size: var(--app-text-xs);
    font-family: var(--app-font-mono, monospace);
    font-weight: 700;
    color: var(--app-quote-up);
    background: var(--app-quote-up-soft);
    padding: 1px 5px;
    border-radius: 4px;
    white-space: nowrap;

    &.is-down {
      color: var(--app-quote-down);
      background: var(--app-quote-down-soft);
    }
  }

  &__card-bottom {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 11px;
    margin-top: 4px;
    /* 墨账：块内不画分隔线，靠留白分层。 */
  }

  &__heat-lbl {
    color: var(--td-text-color-placeholder);
  }

  &__heat-val {
    font-family: var(--app-font-mono, monospace);
    font-weight: 500;
    color: var(--td-text-color-secondary);
  }

  /* 趋势标签也是方向数据：红绿保留，走主题 soft 令牌。 */
  &__trend-tag {
    font-size: var(--app-text-xs);
    padding: 0 4px;
    border-radius: 2px;

    &.is-up {
      color: var(--app-quote-up);
      background: var(--app-quote-up-soft);
    }
    &.is-down {
      color: var(--app-quote-down);
      background: var(--app-quote-down-soft);
    }
    &.is-flat {
      color: var(--td-text-color-placeholder);
    }
  }
}
</style>
