<!--
  期股联动（大盘工作台 · 大盘 tab）。

  股指期货基差 = 主力连续收盘 − 现货指数收盘。负值是**贴水**。

  为什么默认只给一行四个数：这一行的价值不是那四个绝对值，而是**它们之间的
  分歧**。四个同时深度贴水 = 全市场对冲盘重、没人愿意做多；IF 贴水而 IM 升水
  = 大盘被空、小盘在被抢。风格判断从一行数字里出来，比从二十张卡片里快。

  为什么给分位数而不是绝对值：「-1.44% 贴水」本身没有信息量 —— 股指期货常年
  贴水，-1.44% 是常态还是极端只有历史能回答。分位 4% 才是判断。

  口径必须写清（写在图上，不是写在文档里）：
    · 基差日 = 期货与现货**共同**的最新交易日。实测期货滞后一天，
      所以这一行永远比大盘的指数晚一天 —— 拿今天的现货配昨天的期货
      算出来的是跨日差，不是基差，而且算完看起来完全正常。
    · 分位数的基准期是 2021-09 起约 1200 个交易日。
-->
<template>
  <article class="bas">
    <header class="bas__head">
      <div class="bas__title-row">
        <span class="bas__title">{{ $t('marketDashboard.basis.title') }}</span>
        <span class="bas__caption">{{ caption }}</span>
      </div>
      <button v-if="data?.items?.length" type="button" class="bas__toggle" @click="expanded = !expanded">
        {{ expanded ? $t('common.collapse') : $t('marketDashboard.basis.expand') }}
      </button>
    </header>

    <p v-if="failed" class="bas__empty">{{ $t('marketDashboard.basis.failed') }}</p>
    <p v-else-if="loading" class="bas__empty">{{ $t('common.loading') }}</p>
    <p v-else-if="!data?.items?.length" class="bas__empty">{{ $t('marketDashboard.noData') }}</p>

    <div v-else>
      <div class="bas__row">
        <div v-for="b in data.items" :key="b.variety" class="bas__cell">
          <span class="bas__cell-k">{{ b.name }}</span>
          <span class="bas__cell-v" :class="trendClass(b.basis_pct)">{{ fmtPct(b.basis_pct) }}</span>
          <!-- 分位：0~100，低于 20 = 贴水处在历史深位 -->
          <span class="bas__cell-p">{{ $t('marketDashboard.basis.percentile', { v: b.percentile ?? '—' }) }}</span>
        </div>
      </div>

      <!--
        展开：基差率与现货日涨跌两条线同图。分开两张图没人能看出背离 ——
        背离恰恰是这两条线**叠在一起**才读得出来的东西（今天指数涨了，
        基差率反而更贴水 = 这个涨是空头回补推的，不是多头进场）。
      -->
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
  </article>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getFuturesBasis, type BasisItem, type BasisPoint, type BasisResponse } from '@/finance/api/market'
import { fmtPct, trendClass } from '@/finance/utils/format'

const { t } = useI18n()

const data = ref<BasisResponse | null>(null)
const loading = ref(true)
const failed = ref(false)
const expanded = ref(false)

async function load() {
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

onMounted(load)

/** 展开时看哪个品种：默认沪深300（IF），它是流动性最好、信号最干净的那个。 */
const focus = computed<BasisItem | null>(() => {
  const items = data.value?.items ?? []
  return items.find((i) => i.variety === 'IF') ?? items[0] ?? null
})

const caption = computed(() => {
  const d = data.value
  if (!d?.basis_date) return ''
  // 期货与现货不同步时把两个日期都写出来。只写一个就是在骗人。
  const lag = d.spot_latest && d.spot_latest !== d.basis_date
    ? t('marketDashboard.basis.lagged', { basis: d.basis_date, spot: d.spot_latest })
    : t('marketDashboard.basis.caption', { date: d.basis_date })
  return lag
})

/**
 * 序列 → SVG polyline 点串。
 *
 * 跳过 null 点而**不连线**（折线会断开是对的：那天没有数据，连起来等于
 * 造出一个不存在的观测）。共享一条 Y 轴刻度，否则两条线的相对高低是假的。
 */
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
.bas {
  --md-up: #dc2626;
  --md-down: #047857;
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--td-radius-medium);
  padding: 12px 14px;
}

:global(:root[theme-mode='dark']) .bas {
  --md-up: #f87171;
  --md-down: #34d399;
}

.is-up { color: var(--md-up); }
.is-down { color: var(--md-down); }
.is-flat { color: var(--td-text-color-placeholder); }

.bas__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.bas__title-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.bas__title { font-family: var(--app-font-display); font-size: var(--app-text-md); }
.bas__caption { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

.bas__toggle {
  border: 1px solid var(--td-component-border);
  border-radius: var(--td-radius-small);
  background: var(--td-bg-color-container);
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
  padding: 3px 9px;
  cursor: pointer;
}

.bas__empty {
  margin: 0;
  padding: 14px 0;
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-sm);
}

/* 四个等宽格子：宽度固定才能横向对比，分歧一眼可见。 */
.bas__row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  max-width: 640px;
}

.bas__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-radius: var(--td-radius-small);
  background: var(--td-bg-color-container-hover);
}

.bas__cell-k { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }
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
.bas__swatch--spot { background: var(--td-brand-color); }

.bas__svg { width: 100%; height: 96px; display: block; }
.bas__zero { stroke: var(--td-component-stroke); stroke-width: 1; stroke-dasharray: 3 3; }
.bas__line { stroke-width: 1.5; stroke-linejoin: round; stroke-linecap: round; }
.bas__line--basis { stroke: var(--td-text-color-primary); }
/* 现货线用品牌色而不是涨跌色：它是"另一条线"而不是"涨或跌"的信号。 */
.bas__line--spot { stroke: var(--td-brand-color); stroke-dasharray: 4 3; }

.bas__chart-foot { margin: 4px 0 0; font-size: var(--app-text-xs); color: var(--td-text-color-placeholder); }
</style>
