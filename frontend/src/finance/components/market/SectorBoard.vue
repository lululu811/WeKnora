<!--
  板块榜（大盘工作台 · 大盘 tab）。

  回答的是「今天钱往哪个方向走」。库里 index.v_index_universe 有 848 个板块
  （行业 320 / 概念 390 / 特色 105 / 地域 33），`v_index_daily` 有它们的五年日线
  —— 这些数据此前**没有任何端点在读**。本页把它们接出来，并且只把"异常的"
  放到首屏：

  默认只显示 top N，排序是后端做的字典序：涨停家数 → 放量倍数 → |涨跌幅|。
  为什么不是按涨跌幅排：涨得多不代表有资金，而"10 只涨停"是封板资金，
  是最硬的证据。文化传媒今天涨 5.78% 不是最猛的，但它排第一。

  848 个全量在「展开全部」里，按分类切换 —— 全量是**可核对性**，不是首屏内容。
  没有可核对性的助手视角会让人怀疑"它凭什么说这几个重要"。
-->
<template>
  <article class="sec">
    <header class="sec__head">
      <div class="sec__title-row">
        <span class="sec__title">{{ $t('marketDashboard.sectors.title') }}</span>
        <span class="sec__caption">{{ caption }}</span>
      </div>
      <div class="sec__actions">
        <div class="sec__seg" role="radiogroup" :aria-label="$t('marketDashboard.sectors.tagLabel')">
          <button v-for="g in TAGS" :key="g.key" type="button" class="sec__seg-btn"
            :class="{ 'is-active': tag === g.key }" role="radio" :aria-checked="tag === g.key"
            @click="tag = g.key">
            {{ $t(g.labelKey) }}
          </button>
        </div>
        <button type="button" class="sec__more" @click="expanded = !expanded">
          {{ expanded ? $t('common.collapse') : $t('marketDashboard.sectors.showAll') }}
        </button>
      </div>
    </header>

    <p v-if="failed" class="sec__empty">{{ $t('marketDashboard.sectors.failed') }}</p>
    <p v-else-if="loading" class="sec__empty">{{ $t('common.loading') }}</p>
    <p v-else-if="!rows.length" class="sec__empty">{{ $t('marketDashboard.noData') }}</p>

    <div v-else class="sec__table">
      <div class="sec__tr sec__tr--head">
        <span>{{ $t('marketDashboard.sectors.col.name') }}</span>
        <span class="is-num">{{ $t('marketDashboard.sectors.col.change') }}</span>
        <span class="is-num">{{ $t('marketDashboard.sectors.col.volume') }}</span>
        <span class="is-num">{{ $t('marketDashboard.sectors.col.limitUp') }}</span>
      </div>
      <div v-for="s in rows" :key="s.thscode" class="sec__tr">
        <span class="sec__name">
          {{ s.name }}
          <span class="sec__code">{{ s.thscode }}</span>
        </span>
        <!-- 放量倍数与涨停家数缺失时显示「—」，不显示 0 / 1.0：
             「没有历史」与「量能正常」是两件事，混起来会读出假结论。 -->
        <span class="is-num" :class="trendClass(s.change_pct)">{{ fmtPct(s.change_pct) }}</span>
        <span class="is-num">
          {{ s.volume_multiple === null ? '—' : `${s.volume_multiple.toFixed(2)}×` }}
        </span>
        <span class="is-num sec__lu" :class="{ 'has-lu': (s.limit_up_count ?? 0) > 0 }">
          {{ s.limit_up_count === null ? '—' : s.limit_up_count }}
        </span>
      </div>
    </div>

    <p v-if="expanded && data?.total" class="sec__foot">
      {{ $t('marketDashboard.sectors.foot', { shown: rows.length, total: data.total }) }}
    </p>
  </article>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getSectors, type SectorRow } from '@/finance/api/market'
import { fmtPct, trendClass } from '@/finance/utils/format'

const { t } = useI18n()

/** 默认取多少条进首屏。超过这个数就属于"翻资料"，不是"扫一眼"。 */
const TOP_N = 12
/** 展开后的条数上限 —— 848 行铺满一屏只会让每行都失去意义。 */
const EXPANDED_N = 60

const TAGS = [
  { key: 'industry', labelKey: 'marketDashboard.sectors.tag.industry' },
  { key: 'concept', labelKey: 'marketDashboard.sectors.tag.concept' },
  { key: 'tszs', labelKey: 'marketDashboard.sectors.tag.tszs' },
  { key: 'region', labelKey: 'marketDashboard.sectors.tag.region' },
] as const

const tag = ref<(typeof TAGS)[number]['key']>('industry')
const expanded = ref(false)
const data = ref<Awaited<ReturnType<typeof getSectors>> | null>(null)
const loading = ref(true)
const failed = ref(false)

async function load() {
  loading.value = true
  failed.value = false
  try {
    // 展开与否不影响取数：一次拿够，靠 rows 截断。省掉第二次往返。
    data.value = await getSectors({ tag: tag.value, limit: EXPANDED_N })
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

watch(tag, load, { immediate: true })

const rows = computed<SectorRow[]>(() => (data.value?.items ?? []).slice(0, expanded.value ? EXPANDED_N : TOP_N))

/**
 * 副标题只带日期。
 *
 * 后端 `sort_rule` 字段（"limit_up_count > volume_multiple > abs(change_pct)"）
 * 保留在响应里供排查用，但**不渲染到界面上** —— 那是字段名，是给开发看的；
 * 界面上要说的是"按什么排"，那句话由 i18n 固定，措辞不随后端字段名一起变。
 */
const caption = computed(() => {
  const d = data.value
  if (!d?.trade_date) return t('marketDashboard.noData')
  return t('marketDashboard.sectors.caption', { date: d.trade_date })
})
</script>

<style scoped lang="less">
/* 沿用大盘预览的暖米色纸张体系与涨跌色（--md-up 红 / --md-down 绿随主题切换）。 */
.sec {
  --md-up: #dc2626;
  --md-down: #047857;
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--td-radius-medium);
  padding: 12px 14px;
}

:global(:root[theme-mode='dark']) .sec {
  --md-up: #f87171;
  --md-down: #34d399;
}

.is-up { color: var(--md-up); }
.is-down { color: var(--md-down); }
.is-flat { color: var(--td-text-color-placeholder); }

.sec__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.sec__title-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.sec__title { font-family: var(--app-font-display); font-size: var(--app-text-md); }
.sec__caption { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }
.sec__actions { display: flex; align-items: center; gap: 8px; }

.sec__seg { display: flex; gap: 2px; }

.sec__seg-btn,
.sec__more {
  border: 1px solid var(--td-component-border);
  border-radius: var(--td-radius-small);
  background: var(--td-bg-color-container);
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
  padding: 3px 9px;
  cursor: pointer;
}

.sec__seg-btn.is-active {
  background: var(--td-text-color-primary);
  border-color: var(--td-text-color-primary);
  color: var(--td-bg-color-container);
}

.sec__empty {
  margin: 0;
  padding: 14px 0;
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-sm);
}

/*
  列宽：数字列固定、名称列吃余量，整体封顶。
  数字位数可预期（-5.78% / 1.31× / 10），跟着视口拉伸只会被甩到屏幕最右端。
*/
.sec__table { max-width: 720px; }

.sec__tr {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 84px 84px 64px;
  gap: 10px;
  align-items: baseline;
  padding: 6px 0;
  border-bottom: 1px solid var(--td-bg-color-container-hover);
  font-size: var(--app-text-sm);
}

.sec__tr--head {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  padding-bottom: 4px;
}

.is-num {
  font-family: var(--app-font-family-mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.sec__name { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.sec__code { font-family: var(--app-font-family-mono); font-size: var(--app-text-xs); color: var(--td-text-color-placeholder); }

/* 有涨停的板块才强调：0 只是常态，强调 0 只会让每个数字都在喊。 */
.sec__lu { color: var(--td-text-color-placeholder); }
.sec__lu.has-lu { color: var(--md-up); font-weight: 600; }

.sec__foot { margin: 8px 0 0; font-size: var(--app-text-xs); color: var(--td-text-color-placeholder); }
</style>
