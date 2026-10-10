<!--
  权重 ETF 工作区（个股追踪 → 权重 ETF tab）。

  从大盘预览那张速览卡升上来的。升级换来的不是更多列，是三件事：

    1. **全量**。卡片在 768px 高的视口里只能放 4~8 行，且靠 overflow 滚动；
       工作区是正常流，18 只（宽基 8 + 行业 10）一次铺完。
    2. **分组筛选**。池子里现在有两种东西，它们的含义不同，必须能分开看：
         broad  = 汇金历史重仓宽基 → "国家队在进出宽基"只由这一组得出
         sector = 行业 ETF，补充视野，**不进**上面那条结论
       混在一起会让"n 只变了"这个分母失去意义。
    3. **口径可见**。份额是季频，观测日必须印在表头上，否则 "+57%" 会被读成今天。

  分组是后端 `etf_pool.yaml` 的职责，前端**不从 name 猜赛道**。
  后端还没升级（item 上没有 group 字段）时，一律回落 broad ——
  宁可只显示宽基，也不要把行业混进汇金那组。
-->
<template>
  <section class="etf-ws">
    <header class="etf-ws__head">
      <div class="etf-ws__title-row">
        <h3 class="etf-ws__title">{{ $t('tracking.etf.title') }}</h3>
        <span class="etf-ws__caption">
          {{ $t('tracking.etf.caption', { date: shareDate, basis: granularityLabel }) }}
        </span>
      </div>

      <div class="etf-ws__toolbar">
        <div class="etf-ws__seg" role="radiogroup" :aria-label="$t('tracking.etf.groupLabel')">
          <button
            v-for="g in groups"
            :key="g.key"
            type="button"
            class="etf-ws__seg-btn"
            :class="{ 'is-active': group === g.key }"
            :disabled="g.count === 0"
            role="radio"
            :aria-checked="group === g.key"
            @click="group = g.key"
          >
            {{ g.label }}
            <span class="etf-ws__seg-n">{{ g.count }}</span>
          </button>
        </div>

        <div class="etf-ws__sort" role="radiogroup" :aria-label="$t('tracking.etf.sortLabel')">
          <button
            v-for="s in SORTS"
            :key="s.key"
            type="button"
            class="etf-ws__sort-btn"
            :class="{ 'is-active': sort === s.key }"
            role="radio"
            :aria-checked="sort === s.key"
            @click="sort = s.key"
          >
            {{ $t(s.labelKey) }}
          </button>
        </div>
      </div>
    </header>

    <p v-if="groupNote" class="etf-ws__note">{{ groupNote }}</p>

    <div v-if="loading" class="etf-ws__empty">{{ $t('common.loading') }}</div>
    <p v-else-if="!visible.length" class="etf-ws__empty">{{ $t('marketDashboard.noData') }}</p>

    <div v-else class="etf-ws__table">
      <div class="etf-ws__tr etf-ws__tr--head">
        <span>{{ $t('tracking.etf.col.name') }}</span>
        <span class="is-num">{{ $t('tracking.etf.col.price') }}</span>
        <span class="is-num">{{ $t('tracking.etf.col.share') }}</span>
        <span class="is-num">{{ $t('tracking.etf.col.multiple') }}</span>
        <span>{{ $t('tracking.etf.col.signal') }}</span>
        <span class="is-num">{{ $t('tracking.etf.col.observed') }}</span>
      </div>

      <div
        v-for="row in visible"
        :key="row.it.thscode"
        class="etf-ws__tr"
        :class="{ 'is-signalled': row.it.signal }"
      >
        <span class="etf-ws__name">
          <span v-if="row.it.signal" class="etf-ws__dot" :title="$t('tracking.etf.signalTitle')" />
          {{ row.it.name }}
          <span class="etf-ws__code">{{ row.it.thscode }}</span>
        </span>
        <span class="is-num etf-ws__num">{{ priceOf(row.it) }}</span>
        <!--
          份额变动是这张表的主角。null 显示「—」，**绝不显示 0%** ——
          0% 是"这个季度没动"这个结论，null 是"没有上一观测点可比"。
          没有份额观测点时退回近 5 日涨跌，并把口径标出来，不混为一谈。
        -->
        <span class="is-num etf-ws__num" :class="trendClass(row.primary.value)">
          {{ fmtPct(row.primary.value) }}<template v-if="row.primary.basis === 'price'">*</template>
        </span>
        <span class="is-num etf-ws__num">{{ multipleOf(row.it) }}</span>
        <span class="etf-ws__signal">{{ row.it.signal ? $t('marketDashboard.etf.signal') : '—' }}</span>
        <span class="is-num etf-ws__num etf-ws__date">{{ row.it.trade_date ?? '—' }}</span>
      </div>
    </div>

    <p v-if="visible.some((r) => r.primary.basis === 'price')" class="etf-ws__foot">
      * {{ $t('tracking.etf.footPriceFallback') }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getEtfFlow } from '@/finance/api/pulse'
import type { EtfFlowItem } from '@/finance/api/pulse'

const props = withDefaults(
  defineProps<{
    /** 大盘面板已经取过一次就传进来，省一轮请求；没传就自己取。 */
    items?: EtfFlowItem[] | null
    loading?: boolean
  }>(),
  { items: null, loading: false },
)

const { t } = useI18n()

type GroupKey = 'broad' | 'sector'
type SortKey = 'signal' | 'share' | 'signalCount'

const group = ref<GroupKey>('broad')
const sort = ref<SortKey>('signal')

const SORTS: Array<{ key: SortKey; labelKey: string }> = [
  { key: 'signal', labelKey: 'tracking.etf.sort.signal' },
  { key: 'share', labelKey: 'tracking.etf.sort.share' },
  { key: 'signalCount', labelKey: 'tracking.etf.sort.multiple' },
]

// ── 取数 ────────────────────────────────────────────────────────────────
// 自己取的路径只在"没有传 items"时走；传入的可能是大盘面板正在加载中的 null，
// 那时也自己取一份，避免大盘没打开时这个 tab 是空的。

const selfItems = ref<EtfFlowItem[]>([])
const selfLoading = ref(false)
const selfFailed = ref(false)

async function loadSelf() {
  selfLoading.value = true
  selfFailed.value = false
  try {
    const r = await getEtfFlow()
    selfItems.value = r.items ?? []
  } catch {
    selfFailed.value = true
  } finally {
    selfLoading.value = false
  }
}

// props.items 是响应式的（父组件的 ref），所以判断要放在 computed 里，
// 不能在 setup 顶层做一次 —— 顶层只会看到传进来那一刻的值。
const source = computed<EtfFlowItem[]>(() => props.items ?? selfItems.value)
const loading = computed(() => props.loading || (props.items == null && selfLoading.value))

// 大盘没打开、这里也没传、还没开始取 → 自己去取一次。
if (props.items == null) void loadSelf()

// ── 分组 ────────────────────────────────────────────────────────────────

/** 后端升级前 item 上没有 group —— 回落 broad，宁可少显示也不要混口径。 */
function groupOf(it: EtfFlowItem): GroupKey {
  return it.group === 'sector' ? 'sector' : 'broad'
}

const groups = computed(() => {
  const all = source.value
  return [
    {
      key: 'broad' as const,
      label: t('tracking.etf.group.broad'),
      count: all.filter((i) => groupOf(i) === 'broad').length,
    },
    {
      key: 'sector' as const,
      label: t('tracking.etf.group.sector'),
      count: all.filter((i) => groupOf(i) === 'sector').length,
    },
  ]
})

/** 切到没有数据的分组时要说清为什么空，而不是留一片空白让人猜。 */
const groupNote = computed(() => {
  if (group.value !== 'sector') return ''
  const n = groups.value.find((g) => g.key === 'sector')?.count ?? 0
  if (n > 0) return ''
  return t('tracking.etf.sectorEmpty')
})

// ── 取值 ────────────────────────────────────────────────────────────────

/** 份额优先；没有上一观测点时退回近 5 日涨跌，并标记 basis 以便脚注说明。 */
function primaryOf(it: EtfFlowItem): { value: number | null; basis: 'share' | 'price' } {
  if (it.share_change_pct !== null && it.share_change_pct !== undefined) {
    return { value: it.share_change_pct, basis: 'share' }
  }
  if (it.change_5d_pct !== null && it.change_5d_pct !== undefined) {
    return { value: it.change_5d_pct, basis: 'price' }
  }
  return { value: null, basis: 'price' }
}

const rows = computed(() =>
  source.value
    .filter((it) => groupOf(it) === group.value)
    .map((it) => ({ it, primary: primaryOf(it) })),
)

const visible = computed(() => {
  const r = [...rows.value]
  if (sort.value === 'signal') {
    // 异动优先：先看有没有触发，再按份额变动幅度。缺失排最后（不当 0）。
    r.sort((a, b) => {
      const sa = a.it.signal ? 0 : 1
      const sb = b.it.signal ? 0 : 1
      if (sa !== sb) return sa - sb
      return mag(b.primary.value) - mag(a.primary.value)
    })
  } else if (sort.value === 'share') {
    r.sort((a, b) => mag(b.primary.value) - mag(a.primary.value))
  } else {
    r.sort(
      (a, b) =>
        (b.it.turnover_multiple ?? -1) - (a.it.turnover_multiple ?? -1),
    )
  }
  return r
})

/** 排序用的绝对幅度。null 一律排末尾，不当 0 参与比较。 */
function mag(v: number | null): number {
  if (v === null || v === undefined || Number.isNaN(v)) return -1
  return Math.abs(v)
}

const shareDate = computed(() => {
  const dates = source.value.map((i) => i.trade_date).filter((d): d is string => !!d).sort()
  return dates.length ? dates[dates.length - 1] : null
})

/** 份额口径照抄后端的 granularity，不在前端猜它是季频还是日频。 */
const granularityLabel = computed(() => {
  const g = source.value.find((i) => i.granularity)?.granularity
  return g === 'quarterly' ? t('tracking.etf.basis.quarterly') : t('tracking.etf.basis.unknown')
})

function priceOf(it: EtfFlowItem): string {
  return it.close === null || it.close === undefined ? '—' : it.close.toFixed(3)
}

function multipleOf(it: EtfFlowItem): string {
  return it.turnover_multiple === null || it.turnover_multiple === undefined
    ? '—'
    : `${it.turnover_multiple.toFixed(2)}×`
}

// ── 格式化与着色（与大盘预览同一套语义：红涨绿跌） ────────────────────────

function fmtPct(v: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)}%`
}

function trendClass(v: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return 'is-flat'
  return v > 0 ? 'is-up' : 'is-down'
}
</script>

<style scoped lang="less">
/* 沿用大盘预览的暖米色纸张体系与涨跌色（--md-up 红 / --md-down 绿随主题切换），
   不新造一套设计变量 —— 这张表和大盘那张卡是同一件东西的两种尺寸。 */
.etf-ws {
  --md-up: #dc2626;
  --md-down: #047857;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

:global(:root[theme-mode='dark']) .etf-ws {
  --md-up: #f87171;
  --md-down: #34d399;
}

.is-up { color: var(--md-up); }
.is-down { color: var(--md-down); }
.is-flat { color: var(--td-text-color-placeholder); }

.etf-ws__head {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.etf-ws__title-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.etf-ws__title {
  margin: 0;
  font-family: var(--app-font-display);
  font-size: var(--app-text-xl);
}

.etf-ws__caption {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
}

.etf-ws__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.etf-ws__seg { display: flex; gap: 4px; }

.etf-ws__seg-btn,
.etf-ws__sort-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--td-component-border);
  border-radius: var(--td-radius-small);
  background: var(--td-bg-color-container);
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-sm);
  cursor: pointer;
}

.etf-ws__seg-btn.is-active,
.etf-ws__sort-btn.is-active {
  background: var(--td-text-color-primary);
  border-color: var(--td-text-color-primary);
  color: var(--td-bg-color-container);
}

/* 0 只的分组置灰而不是隐藏：让"扩池了但这组是空的"这件事本身可见。 */
.etf-ws__seg-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.etf-ws__seg-n { font-size: var(--app-text-xs); opacity: 0.7; }
.etf-ws__sort { display: flex; gap: 4px; }

.etf-ws__note {
  margin: 0;
  padding: 7px 10px;
  border-radius: var(--td-radius-small);
  background: var(--td-warning-color-1);
  color: var(--td-warning-color-7);
  font-size: var(--app-text-xs);
}

.etf-ws__empty {
  margin: 0;
  padding: 18px 0;
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-sm);
}

/*
  表格宽度有两道约束，缺一不可：
    · 数字列给固定宽度 —— 它们的位数可预期（-72.24% / 1.70× / 2026-06-30），
      让它们跟着视口一起拉伸，只会让数字被甩到屏幕最右端。
    · 名称列再吃掉剩余空间后，整体封顶 1040px —— 不封顶的话名称列会吸收
      全部余量（实测 1500px 视口下它能涨到 700px+），而"上证50ETF华夏"只有
      十几字宽，一大片空白全落在它右边，读起来是空的不是从容。
*/
.etf-ws__table {
  display: flex;
  flex-direction: column;
  max-width: 1040px;
}

.etf-ws__tr {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 96px 128px 96px 80px 116px;
  gap: 10px;
  align-items: baseline;
  padding: 8px 0;
  border-bottom: 1px solid var(--td-bg-color-container-hover);
  font-size: var(--app-text-sm);
}

.etf-ws__tr--head {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  padding-bottom: 6px;
  border-bottom-color: var(--td-component-stroke);
}

.is-num {
  font-family: var(--app-font-family-mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.etf-ws__tr--head .is-num { text-align: right; }

.etf-ws__name {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.etf-ws__code {
  font-family: var(--app-font-family-mono);
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.etf-ws__dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--md-up);
  flex: 0 0 auto;
}

.etf-ws__signal { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }
.etf-ws__date { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

.etf-ws__foot {
  margin: 0;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}
</style>
