<!--
  期股联动（大盘工作台 · 大盘 tab）。

  包含两个维度：
    1. 股指期货基差：IF/IC/IH/IM 主力连续收盘 − 现货指数收盘，提供 5 年分位数与背离对照。
    2. 大宗商品期股联动：铜/铝/金/银/螺纹/原油/集运/生猪等核心商品最新行情及 A 股产业链映射。
-->
<template>
  <article class="bas ledger-block">
    <header class="bas__head">
      <div class="bas__title-row">
        <span class="bas__title">{{ $t('marketDashboard.basis.title') }}</span>
        <div class="bas__tabs">
          <button
            type="button"
            class="bas__tab"
            :class="{ 'is-active': mode === 'index' }"
            @click="mode = 'index'"
          >
            {{ $t('marketDashboard.basis.tabIndex') }}
          </button>
          <button
            type="button"
            class="bas__tab"
            :class="{ 'is-active': mode === 'commodity' }"
            @click="mode = 'commodity'"
          >
            {{ $t('marketDashboard.basis.tabCommodity', { count: commodities.length || 12 }) }}
          </button>
          <button
            type="button"
            class="bas__tab"
            :class="{ 'is-active': mode === 'warehouse' }"
            @click="mode = 'warehouse'"
          >
            {{ $t('marketDashboard.basis.tabWarehouse') }}
          </button>
        </div>
        <span class="bas__caption">{{ mode === 'index' ? caption : (mode === 'commodity' ? commodityCaption : $t('marketDashboard.basis.warehouseCaption')) }}</span>
      </div>
      <button v-if="mode === 'index' && data?.items?.length" type="button" class="bas__toggle" @click="expanded = !expanded">
        {{ expanded ? $t('common.collapse') : $t('marketDashboard.basis.expand') }}
      </button>
    </header>

    <!-- ── 股指期货模式 ── -->
    <div v-if="mode === 'index'">
      <p v-if="failed" class="bas__empty">{{ $t('marketDashboard.basis.failed') }}</p>
      <p v-else-if="loading" class="bas__empty">{{ $t('common.loading') }}</p>
      <p v-else-if="!data?.items?.length" class="bas__empty">{{ $t('marketDashboard.noData') }}</p>

      <div v-else>
        <div class="bas__row">
          <div v-for="b in data.items" :key="b.variety" class="bas__cell">
            <span class="bas__cell-k">{{ b.name }}</span>
            <span class="bas__cell-v" :class="trendClass(b.basis_pct)">{{ fmtPct(b.basis_pct) }}</span>
            <span class="bas__cell-p">{{ $t('marketDashboard.basis.percentile', { v: b.percentile ?? '—' }) }}</span>
          </div>
        </div>

        <div v-if="expanded && focus" class="bas__chart">
          <div class="bas__chart-head">
            <span class="bas__chart-title">{{ $t('marketDashboard.basis.divergence', { name: focus.name }) }}</span>
            <div class="bas__legend">
              <span class="bas__legend-item"><i class="bas__swatch bas__swatch--basis" />{{ $t('marketDashboard.basis.legendBasis') }}</span>
              <span class="bas__legend-item"><i class="bas__swatch bas__swatch--spot" />{{ $t('marketDashboard.basis.legendSpot') }}</span>
            </div>
          </div>
          <svg class="bas__svg" viewBox="0 0 560 96" preserveAspectRatio="none" role="img"
            :aria-label="$t('marketDashboard.basis.divergence', { name: focus.name })">
            <line x1="0" x2="560" y1="48" y2="48" class="bas__zero" vector-effect="non-scaling-stroke" />
            <polyline :points="pathOf(focus.series, 'basis_pct')" fill="none"
              class="bas__line bas__line--basis" vector-effect="non-scaling-stroke" />
            <polyline :points="pathOf(focus.series, 'spot_pct')" fill="none"
              class="bas__line bas__line--spot" vector-effect="non-scaling-stroke" />
          </svg>
          <p class="bas__chart-foot">
            {{ $t('marketDashboard.basis.window', { n: focus.series.length }) }}
          </p>
        </div>
      </div>
    </div>

    <!-- ── 大宗商品联动模式 ── -->
    <div v-else-if="mode === 'commodity'" class="bas__commodity-panel">
      <!-- 类别过滤器 -->
      <div class="bas__comm-filter-bar">
        <button
          v-for="cat in categories"
          :key="cat.key"
          type="button"
          class="bas__cat-btn"
          :class="{ 'is-active': activeCategory === cat.key }"
          @click="activeCategory = cat.key"
        >
          {{ cat.label }} ({{ cat.count }})
        </button>
      </div>

      <p v-if="commLoading" class="bas__empty">{{ $t('common.loading') }}</p>
      <p v-else-if="!filteredCommodities.length" class="bas__empty">{{ $t('marketDashboard.noData') }}</p>
      <div v-else class="bas__comm-grid">
        <div v-for="c in filteredCommodities" :key="c.variety" class="bas__comm-card">
          <div class="bas__comm-top">
            <div class="bas__comm-title">
              <span class="bas__comm-name">{{ c.name }}</span>
              <span class="bas__comm-contract">{{ c.contract_name || c.thscode }}</span>
            </div>
            <span class="bas__comm-tag">{{ c.category }}</span>
          </div>

          <div class="bas__comm-price">
            <span class="bas__comm-price-val">¥{{ c.close_price?.toLocaleString() ?? '—' }}</span>
            <span
              v-if="c.change_pct !== null && c.change_pct !== undefined"
              class="bas__comm-chg-pill"
              :class="trendClass(c.change_pct)"
            >
              {{ fmtPct(c.change_pct) }}
            </span>
          </div>

          <div class="bas__comm-meta">
            <span v-if="c.turnover" class="bas__comm-turnover">{{ $t('marketDashboard.basis.turnover', { value: fmtTurnover(c.turnover) }) }}</span>
            <span v-if="c.trade_date" class="bas__comm-date">{{ c.trade_date }}</span>
          </div>

          <div class="bas__comm-stocks">
            <span class="bas__comm-hint-label">{{ $t('marketDashboard.basis.linkedStocks') }}</span>
            <div class="bas__comm-chips">
              <span
                v-for="stk in (c.linked_stocks && c.linked_stocks.length ? c.linked_stocks : [c.a_share_hint])"
                :key="stk"
                class="bas__stk-chip"
              >
                {{ stk }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 注册仓单异动模式 ── -->
    <div v-else class="bas__wh-panel">
      <p v-if="whLoading" class="bas__empty">{{ $t('common.loading') }}</p>
      <p v-else-if="!warehouseItems.length" class="bas__empty">{{ $t('marketDashboard.noData') }}</p>
      <div v-else class="bas__wh-grid">
        <div v-for="w in warehouseItems" :key="w.thscode" class="bas__wh-card">
          <div class="bas__wh-top">
            <span class="bas__wh-contract">{{ w.thscode }}</span>
            <span
              v-if="(w.amount_change ?? 0) < 0"
              class="bas__wh-change is-destock"
            >
              {{ $t('marketDashboard.basis.destock', { value: w.amount_change?.toLocaleString() }) }}
            </span>
            <span
              v-else-if="(w.amount_change ?? 0) > 0"
              class="bas__wh-change is-accumulate"
            >
              {{ $t('marketDashboard.basis.accumulate', { value: w.amount_change?.toLocaleString() }) }}
            </span>
            <span v-else class="bas__wh-change is-flat">{{ $t('marketDashboard.basis.flat') }}</span>
          </div>
          <div class="bas__wh-bottom">
            <span class="bas__wh-lbl">{{ $t('marketDashboard.basis.warehouseLabel') }}:</span>
            <span class="bas__wh-val">{{ w.amount?.toLocaleString() || '—' }} ({{ $t('marketDashboard.basis.warehouseLots', { count: w.equivalent_lots || 0 }) }})</span>
          </div>
        </div>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  getFuturesBasis,
  getCommodities,
  getFuturesWarehouse,
  type BasisItem,
  type BasisPoint,
  type BasisResponse,
  type CommodityItem,
  type FuturesWarehouseItem,
} from '@/finance/api/market'
import { fmtPct, trendClass } from '@/finance/utils/format'

const { t } = useI18n()

const mode = ref<'index' | 'commodity' | 'warehouse'>('index')
const activeCategory = ref<string>('all')

const data = ref<BasisResponse | null>(null)
const loading = ref(true)
const failed = ref(false)
const expanded = ref(false)

const commodities = ref<CommodityItem[]>([])
const commLoading = ref(false)
const commTradeDate = ref<string | null>(null)

const warehouseItems = ref<FuturesWarehouseItem[]>([])
const whLoading = ref(false)

const categories = computed(() => {
  const map = new Map<string, number>()
  for (const c of commodities.value) {
    map.set(c.category, (map.get(c.category) || 0) + 1)
  }
  const list = [
    { key: 'all', label: t('common.all'), count: commodities.value.length },
  ]
  for (const [cat, count] of map.entries()) {
    list.push({ key: cat, label: cat, count })
  }
  return list
})

const filteredCommodities = computed(() => {
  if (activeCategory.value === 'all') return commodities.value
  return commodities.value.filter((c) => c.category === activeCategory.value)
})

async function loadBasis() {
  loading.value = true
  failed.value = false
  try {
    data.value = await getFuturesBasis()
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function loadCommodities() {
  commLoading.value = true
  try {
    const res = await getCommodities()
    if (res?.ok) {
      commodities.value = res.items || []
      commTradeDate.value = res.trade_date || null
    }
  } catch {
    // 降级不阻断
  } finally {
    commLoading.value = false
  }
}

async function loadWarehouse() {
  whLoading.value = true
  try {
    const res = await getFuturesWarehouse({ limit: 40 })
    if (res?.ok) {
      warehouseItems.value = res.items || []
    }
  } catch {
    // 降级不阻断
  } finally {
    whLoading.value = false
  }
}

onMounted(() => {
  loadBasis()
  loadCommodities()
  loadWarehouse()
})

const focus = computed<BasisItem | null>(() => {
  const items = data.value?.items ?? []
  return items.find((i) => i.variety === 'IF') ?? items[0] ?? null
})

const caption = computed(() => {
  const d = data.value
  if (!d?.basis_date) return ''
  const lag = d.spot_latest && d.spot_latest !== d.basis_date
    ? t('marketDashboard.basis.lagged', { basis: d.basis_date, spot: d.spot_latest })
    : t('marketDashboard.basis.caption', { date: d.basis_date })
  return lag
})

const commodityCaption = computed(() => {
  return commTradeDate.value
    ? t('marketDashboard.basis.tradeDate', { date: commTradeDate.value })
    : t('marketDashboard.basis.commodityFallback')
})

function fmtTurnover(turnover: number): string {
  if (turnover >= 1e8) {
    return `${(turnover / 1e8).toFixed(1)} 亿`
  }
  if (turnover >= 1e4) {
    return `${(turnover / 1e4).toFixed(0)} 万`
  }
  return `${turnover}`
}

function pathOf(series: BasisPoint[], key: 'basis_pct' | 'spot_pct'): string {
  const vals = series.map((p) => p[key]).filter((v): v is number => v !== null && v !== undefined)
  if (vals.length < 2) return ''
  const min = Math.min(...vals, 0)
  const max = Math.max(...vals, 0)
  const span = max - min || 1
  const step = 560 / Math.max(1, series.length - 1)
  return series
    .map((p, i) => {
      const v = p[key]
      if (v === null || v === undefined) return null
      return `${(i * step).toFixed(1)},${(96 - ((v - min) / span) * 92 - 2).toFixed(1)}`
    })
    .filter(Boolean)
    .join(' ')
}
</script>

<style scoped lang="less">
/*
 * 墨账体例（achromatic-ledger）—— 规范见 src/finance/styles/ledger.less 文件头：
 *   1. 无框块：块与块之间只靠留白与字号层级分开，不画卡片；
 *   2. 图表用墨色：折线一律墨色，方向交给数字讲；
 *   3. 浓淡表用墨阶：--ledger-ink-1…5。
 * 涨跌红绿是金融语义，只出现在数字上，保留。
 */
.bas {
  /* 块根：去掉卡片外壳（底色 / 描边 / 圆角 / 阴影）；块距由父级 grid 的 gap 给。 */
}

.is-up { color: var(--app-quote-up); }
.is-down { color: var(--app-quote-down); }
.is-flat { color: var(--td-text-color-placeholder); }

.bas__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.bas__title-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
/* 块的标题（展示字）：墨账靠字重与字号分层，不靠底色块。 */
.bas__title { font-family: var(--app-font-display); font-size: var(--app-text-md); font-weight: 600; }
.bas__caption { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

.bas__tabs {
  display: inline-flex;
  background: var(--td-bg-color-secondarycontainer);
  border-radius: var(--td-radius-small);
  padding: 2px;
  gap: 2px;
}

.bas__tab {
  border: none;
  background: transparent;
  padding: 2px 10px;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  border-radius: var(--td-radius-small);
  cursor: pointer;
  transition: all 0.15s ease;

  &.is-active {
    background: var(--td-bg-color-container);
    color: var(--td-text-color-primary);
    font-weight: 500;
    /* 墨账：去掉投影 —— 阴影是"这是一张卡片"的信号。 */
  }
}

/* 「展开 / 收起」是次级动作：墨账里用文字链接，不画按钮框，也不占用品牌珊瑚。 */
.bas__toggle {
  border: 0;
  background: transparent;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
  padding: 2px 4px;
  cursor: pointer;

  &:hover {
    color: var(--td-text-color-primary);
    text-decoration: underline;
  }
}

.bas__empty {
  margin: 0;
  padding: 14px 0;
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-sm);
}

/* 四个基差框：墨账里不是四张卡，是四组数 —— 靠留白与字重分组。 */
.bas__row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 24px;
  max-width: 640px;
}

.bas__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
}

.bas__cell-k { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); font-weight: 500; }
.bas__cell-v { font-family: var(--app-font-display); font-size: var(--app-text-xl); }
.bas__cell-p { font-size: var(--app-text-xs); color: var(--td-text-color-placeholder); }

.bas__chart { margin-top: 12px; max-width: 600px; }
.bas__chart-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 6px; flex-wrap: wrap;
}
.bas__chart-title { font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }

.bas__legend { display: flex; gap: 12px; }
.bas__legend-item { display: inline-flex; align-items: center; gap: 5px; font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }
.bas__swatch { width: 10px; height: 2px; border-radius: 1px; display: inline-block; }
.bas__swatch--basis { background: var(--td-text-color-primary); }
/* 墨账：两条线都是墨色，靠实线 / 虚线区分，图例跟着线走。 */
/* 虚线用虚线边框画，不用 repeating-linear-gradient —— 渐变是禁令，
   而这条线要的就是"虚"这一个意思。 */
.bas__swatch--spot {
  height: 0;
  border-top: 2px dashed var(--td-text-color-secondary);
}

.bas__svg { width: 100%; height: 96px; display: block; }
.bas__zero { stroke: var(--td-component-stroke); stroke-width: 1; stroke-dasharray: 3 3; }
.bas__line { stroke-width: 1.5; stroke-linejoin: round; stroke-linecap: round; }
.bas__line--basis { stroke: var(--td-text-color-primary); }
.bas__line--spot { stroke: var(--td-text-color-secondary); stroke-dasharray: 4 3; }

.bas__chart-foot { margin: 4px 0 0; font-size: var(--app-text-xs); color: var(--td-text-color-placeholder); }

/* 大宗商品网格 */
.bas__commodity-panel {
  margin-top: 4px;
}

.bas__comm-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 18px;
}

/* 同样是无框块：靠留白与字重分组，不画小卡。 */
.bas__comm-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
}

.bas__comm-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.bas__comm-title {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.bas__comm-name {
  font-weight: 600;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-primary);
}

.bas__comm-contract {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}

.bas__comm-tag {
  font-size: var(--app-text-xs);
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--td-bg-color-secondarycontainer);
  color: var(--td-text-color-secondary);
}

.bas__comm-filter-bar {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.bas__cat-btn {
  border: 1px solid var(--td-component-stroke);
  background: transparent;
  color: var(--td-text-color-secondary);
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;

  &:hover {
    color: var(--td-text-color-primary);
    border-color: var(--td-border-level-2-color);
  }

  /* 选中态用墨阶 + 字重标记，不占品牌珊瑚（珊瑚在墨账里是稀缺资源）。 */
  &.is-active {
    background: var(--ledger-ink-2);
    color: var(--td-text-color-primary);
    border-color: var(--td-text-color-primary);
    font-weight: 600;
  }
}

.bas__comm-price {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.bas__comm-price-val {
  font-family: var(--app-font-display);
  font-size: var(--app-text-md);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

/* 涨跌幅是数据语义：红涨绿跌保留，底色改用主题的 soft 令牌（跟随亮暗主题）。 */
.bas__comm-chg-pill {
  font-family: var(--app-font-display);
  font-size: 12px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;

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
    background: var(--td-bg-color-secondarycontainer);
  }
}

.bas__comm-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}

.bas__comm-turnover {
  color: var(--td-text-color-secondary);
}

.bas__comm-date {
  font-size: var(--app-text-xs);
}

.bas__comm-stocks {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 2px;
  /* 墨账：块内不画分隔线，靠「关联个股」这个标签分组。 */
}

.bas__comm-hint-label {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.bas__comm-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* 关联个股只是"这是相关标的"的标示，不带方向语义 —— 用墨阶，不用品牌珊瑚。 */
.bas__stk-chip {
  font-size: 11px;
  color: var(--td-text-color-secondary);
  background: var(--ledger-ink-1);
  padding: 1px 6px;
  border-radius: 3px;
  line-height: 1.4;
  white-space: nowrap;
}

/* 注册仓单异动网格 */
.bas__wh-panel {
  margin-top: 4px;
}

.bas__wh-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 18px;
}

/* 与大宗商品卡同一体例：无框块。 */
.bas__wh-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
}

.bas__wh-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.bas__wh-contract {
  font-family: var(--app-font-display);
  font-weight: 600;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-primary);
}

/* 累库 / 去库是方向数据：红绿保留，底色走主题 soft 令牌。 */
.bas__wh-change {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;

  &.is-destock {
    color: var(--app-quote-down);
    background: var(--app-quote-down-soft);
  }
  &.is-accumulate {
    color: var(--app-quote-up);
    background: var(--app-quote-up-soft);
  }
  &.is-flat {
    color: var(--td-text-color-placeholder);
    background: var(--td-bg-color-secondarycontainer);
  }
}

.bas__wh-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
}

.bas__wh-lbl {
  color: var(--td-text-color-placeholder);
}

.bas__wh-val {
  font-family: var(--app-font-display);
  color: var(--td-text-color-secondary);
}
</style>
