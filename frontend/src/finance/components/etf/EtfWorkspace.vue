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
  <section class="etf-ws ledger-block">
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

    <!-- ── 机构控盘排行模式 ── -->
    <div v-if="group === 'institutional'">
      <div v-if="instLoading" class="etf-ws__empty">{{ $t('common.loading') }}</div>
      <p v-else-if="!instItems.length" class="etf-ws__empty">{{ $t('marketDashboard.noData') }}</p>
      <div v-else class="etf-ws__card ledger-block">
        <div class="etf-ws__table">
          <div class="etf-ws__tr etf-ws__tr--head etf-ws__tr--inst">
            <span class="etf-ws__col-name">{{ $t('etfWorkspace.col.fundName') }}</span>
            <span class="is-center">{{ $t('etfWorkspace.col.instHolding') }}</span>
            <span class="is-num">{{ $t('etfWorkspace.col.retailHolding') }}</span>
            <span class="is-num">{{ $t('etfWorkspace.holderCount') }}</span>
            <span class="is-num">{{ $t('etfWorkspace.col.avgShare') }}</span>
            <span class="is-center">{{ $t('etfWorkspace.col.action') }}</span>
          </div>

          <div
            v-for="row in instItems"
            :key="row.fund_thscode"
            class="etf-ws__tr etf-ws__tr--inst"
            @click="openHoldersDrawer(row.fund_thscode, row.fund_name || row.fund_thscode)"
          >
            <span class="etf-ws__name">
              <span class="etf-ws__label" :title="row.fund_name || ''">{{ row.fund_name || row.fund_thscode }}</span>
              <span class="etf-ws__code">{{ row.fund_thscode }}</span>
            </span>

            <span class="is-center etf-ws__inst-bar-col">
              <div class="etf-ws__ratio-bar-wrap">
                <div class="etf-ws__ratio-bar-inner" :style="{ width: `${row.ins_position ?? 0}%` }" />
              </div>
              <span class="etf-ws__ratio-pct">{{ row.ins_position ? row.ins_position.toFixed(2) + '%' : '—' }}</span>
            </span>

            <span class="is-num etf-ws__num">{{ row.psnl_rate ? row.psnl_rate.toFixed(2) + '%' : '—' }}</span>
            <span class="is-num etf-ws__num">{{ $t('etfWorkspace.holderAccounts', { count: row.holder_amount?.toLocaleString() ?? '—' }) }}</span>
            <span class="is-num etf-ws__num">{{ fmtShare(row.avg_holder_share) }}</span>
            <span class="is-center">
              <button
                type="button"
                class="etf-ws__holder-btn"
                @click.stop="openHoldersDrawer(row.fund_thscode, row.fund_name || row.fund_thscode)"
              >
                {{ $t('etfWorkspace.holders.penetrateTop10') }}
              </button>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 宽基 / 行业普通模式 ── -->
    <div v-else>
      <div v-if="loading" class="etf-ws__empty">{{ $t('common.loading') }}</div>
      <p v-else-if="!visible.length" class="etf-ws__empty">{{ $t('marketDashboard.noData') }}</p>

      <div v-else class="etf-ws__card ledger-block">
        <div class="etf-ws__table">
          <div class="etf-ws__tr etf-ws__tr--head">
            <span class="etf-ws__col-name">{{ $t('tracking.etf.col.name') }}</span>
            <span class="is-num">{{ $t('tracking.etf.col.price') }}</span>
            <span class="is-num">{{ $t('tracking.etf.col.share') }}</span>
            <span class="is-num">{{ $t('tracking.etf.col.multiple') }}</span>
            <span class="is-center">{{ $t('tracking.etf.col.signal') }}</span>
            <span class="is-num">{{ $t('tracking.etf.col.observed') }}</span>
            <span class="is-center">{{ $t('etfWorkspace.col.chipPenetration') }}</span>
          </div>

          <div
            v-for="row in visible"
            :key="row.it.thscode"
            class="etf-ws__tr"
            :class="{ 'is-signalled': row.it.signal }"
            @click="openHoldersDrawer(row.it.thscode, row.it.name)"
          >
            <span class="etf-ws__name">
              <span v-if="row.it.signal" class="etf-ws__dot" :title="$t('tracking.etf.signalTitle')" />
              <span class="etf-ws__label" :title="row.it.name">{{ row.it.name }}</span>
              <span class="etf-ws__code">{{ row.it.thscode }}</span>
            </span>
            <span class="is-num etf-ws__num">{{ priceOf(row.it) }}</span>
            <span class="is-num etf-ws__num">
              <span class="etf-ws__pct-pill" :class="trendClass(row.primary.value)">
                {{ fmtPct(row.primary.value) }}<template v-if="row.primary.basis === 'price'">*</template>
              </span>
            </span>
            <span class="is-num etf-ws__num etf-ws__mult">{{ multipleOf(row.it) }}</span>
            <span class="is-center etf-ws__signal">
              <span v-if="row.it.signal" class="etf-ws__signal-badge">{{ $t('marketDashboard.etf.signal') }}</span>
              <span v-else class="etf-ws__muted">—</span>
            </span>
            <span class="is-num etf-ws__num etf-ws__date">{{ row.it.trade_date ?? '—' }}</span>
            <span class="is-center">
              <button
                type="button"
                class="etf-ws__holder-btn"
                @click.stop="openHoldersDrawer(row.it.thscode, row.it.name)"
              >
                {{ $t('etfWorkspace.holders.title') }}
              </button>
            </span>
          </div>
        </div>
      </div>

      <p v-if="visible.some((r) => r.primary.basis === 'price')" class="etf-ws__foot">
        * {{ $t('tracking.etf.footPriceFallback') }}
      </p>
    </div>

    <!-- ── ETF 持有人穿透 Drawer ── -->
    <t-drawer
      v-model:visible="drawerVisible"
      :header="drawerTitle"
      size="540px"
      :footer="false"
    >
      <div v-if="drawerLoading" class="etf-drawer__loading">{{ $t('common.loading') }}</div>
      <div v-else-if="!holdersDetail" class="etf-drawer__empty">{{ $t('marketDashboard.noData') }}</div>
      <div v-else class="etf-drawer__content">
        <!-- 机构 vs 散户结构 -->
        <div v-if="holdersDetail.structure" class="etf-drawer__struct-card ledger-block">
          <div class="etf-drawer__struct-head">
            <span class="etf-drawer__struct-title">{{ $t('etfWorkspace.drawer.structureTitle') }}</span>
            <span class="etf-drawer__struct-date">{{ holdersDetail.structure.report_date || $t('etfWorkspace.drawer.latestPeriod') }}</span>
          </div>

          <!-- 双色比例条 -->
          <div class="etf-drawer__bar-container">
            <div
              class="etf-drawer__bar-ins"
              :style="{ width: `${holdersDetail.structure.ins_position ?? 50}%` }"
              :title="$t('etfWorkspace.drawer.insTitle', { value: holdersDetail.structure.ins_position ?? '—' })"
            />
            <div
              class="etf-drawer__bar-psnl"
              :style="{ width: `${holdersDetail.structure.psnl_rate ?? 50}%` }"
              :title="$t('etfWorkspace.drawer.retailTitle', { value: holdersDetail.structure.psnl_rate ?? '—' })"
            />
          </div>

          <div class="etf-drawer__legend-row">
            <div class="etf-drawer__legend-item">
              <span class="etf-drawer__dot is-ins" />
              <span>{{ $t('etfWorkspace.drawer.instLabel') }}: <b>{{ holdersDetail.structure.ins_position ?? '—' }}%</b></span>
            </div>
            <div class="etf-drawer__legend-item">
              <span class="etf-drawer__dot is-psnl" />
              <span>{{ $t('etfWorkspace.drawer.retailLabel') }}: <b>{{ holdersDetail.structure.psnl_rate ?? '—' }}%</b></span>
            </div>
          </div>

          <div class="etf-drawer__stat-grid">
            <div class="etf-drawer__stat-cell">
              <span class="etf-drawer__stat-k">{{ $t('etfWorkspace.holderCount') }}</span>
              <span class="etf-drawer__stat-v">{{ $t('etfWorkspace.holderAccounts', { count: holdersDetail.structure.holder_amount?.toLocaleString() ?? '—' }) }}</span>
            </div>
            <div class="etf-drawer__stat-cell">
              <span class="etf-drawer__stat-k">{{ $t('etfWorkspace.avgHolding') }}</span>
              <span class="etf-drawer__stat-v">{{ fmtShare(holdersDetail.structure.avg_holder_share) }}</span>
            </div>
          </div>
        </div>

        <!-- 前十大持有人穿透 -->
        <div class="etf-drawer__top-section">
          <h4 class="etf-drawer__sub-title">{{ $t('etfWorkspace.drawer.topHoldersTitle') }}</h4>
          <div v-if="!holdersDetail.top_holders.length" class="etf-drawer__empty-hint">{{ $t('etfWorkspace.drawer.noHoldersData') }}</div>
          <div v-else class="etf-drawer__holder-list">
            <div v-for="h in holdersDetail.top_holders" :key="h.rank + h.holder_name" class="etf-drawer__holder-row">
              <span class="etf-drawer__holder-rank">#{{ h.rank }}</span>
              <div class="etf-drawer__holder-info">
                <div class="etf-drawer__holder-top">
                  <span class="etf-drawer__holder-name" :title="h.holder_name">{{ h.holder_name }}</span>
                  <span class="etf-drawer__holder-badge" :class="holderBadgeClass(h.holder_type)">{{ h.holder_type }}</span>
                </div>
                <div class="etf-drawer__holder-bottom">
                  <span>{{ $t('etfWorkspace.holder.holdRateLabel') }}: <b>{{ h.hold_rate_pct ? h.hold_rate_pct.toFixed(2) + '%' : '—' }}</b></span>
                  <span v-if="h.hold_share">{{ $t('etfWorkspace.holder.holdShareLabel') }}: {{ fmtShare(h.hold_share) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </t-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getEtfFlow } from '@/finance/api/pulse'
import type { EtfFlowItem } from '@/finance/api/pulse'
import { getEtfHoldersDetail, getEtfInstitutionalRanking } from '@/finance/api/market'
import type { EtfHoldersResponse, EtfInstitutionalRankingItem } from '@/finance/api/market'

const props = withDefaults(
  defineProps<{
    /** 大盘面板已经取过一次就传进来，省一轮请求；没传就自己取。 */
    items?: EtfFlowItem[] | null
    loading?: boolean
  }>(),
  { items: null, loading: false },
)

const { t } = useI18n()

type GroupKey = 'broad' | 'sector' | 'institutional'
type SortKey = 'signal' | 'share' | 'signalCount'

const group = ref<GroupKey>('broad')
const sort = ref<SortKey>('signal')

const SORTS: Array<{ key: SortKey; labelKey: string }> = [
  { key: 'signal', labelKey: 'tracking.etf.sort.signal' },
  { key: 'share', labelKey: 'tracking.etf.sort.share' },
  { key: 'signalCount', labelKey: 'tracking.etf.sort.multiple' },
]

// ── 机构控盘 & 持有人穿透状态 ──────────────────────────────────────────
const drawerVisible = ref(false)
const drawerLoading = ref(false)
const drawerTitle = ref('')
const holdersDetail = ref<EtfHoldersResponse | null>(null)

const instItems = ref<EtfInstitutionalRankingItem[]>([])
const instLoading = ref(false)

async function loadInstitutional() {
  instLoading.value = true
  try {
    const res = await getEtfInstitutionalRanking({ limit: 30 })
    instItems.value = res.items || []
  } catch (err) {
    console.error('Failed to load institutional ranking', err)
  } finally {
    instLoading.value = false
  }
}

async function openHoldersDrawer(thscode: string, name: string) {
  drawerTitle.value = t('etfWorkspace.drawer.title', { name, code: thscode })
  drawerVisible.value = true
  drawerLoading.value = true
  holdersDetail.value = null
  try {
    const res = await getEtfHoldersDetail(thscode)
    holdersDetail.value = res
  } catch (err) {
    console.error('Failed to load holders detail', err)
  } finally {
    drawerLoading.value = false
  }
}

function fmtShare(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toLocaleString()
}

function holderBadgeClass(type: string): string {
  if (!type) return 'is-other'
  if (type.includes('国家队') || type.includes('汇金') || type.includes('社保') || type.includes('证金')) return 'is-state'
  if (type.includes('险') || type.includes('保')) return 'is-insurance'
  if (type.includes('券商') || type.includes('证券')) return 'is-broker'
  if (type.includes('银行')) return 'is-bank'
  if (type.includes('信托')) return 'is-trust'
  if (type.includes('QFII') || type.includes('外资')) return 'is-qfii'
  if (type.includes('个人')) return 'is-personal'
  return 'is-other'
}

void loadInstitutional()

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
    {
      key: 'institutional' as const,
      label: t('etfWorkspace.group.institutional'),
      count: instItems.value.length || 30,
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
/*
 * 墨账体例（achromatic-ledger）—— 规范见 src/finance/styles/ledger.less 文件头：
 *   1. 无框块：块与块之间只靠留白与标题层级分开，不画卡片；
 *   2. 图表用墨色：比例条一律墨色，不按涨跌给整条染色；
 *   3. 浓淡表用墨阶：--ledger-ink-1…5。
 * 涨跌红绿是金融语义，只出现在**数字**上，保留
 * （--app-quote-up 红 / --app-quote-down 绿随主题切换）。
 */
.etf-ws {
  display: flex;
  flex-direction: column;
  gap: 10px;
  /* 墨账：块根归零（没有卡片外壳了），内缩就由本块自己给。
     外层壳（平台侧栏的 .platform-route-outlet 与 dev 路由）都不给内缩，
     不给这里整块内容会贴到屏幕边缘（edge-text）。口径与大盘 tab 的嵌入态一致：
     MarketDashboard.vue 的 .md-page.is-embedded。 */
  padding: 4px var(--ledger-inset) 20px;
  /* 不加这行，flex item 的 min-width:auto 会让本块按表格 min-content 撑开，
     把最后一列（观测日）顶出视口。 */
  min-width: 0;
}

/* 涨跌色是数据语义（--app-quote-up 红 / --app-quote-down 绿随主题切换），保留在数字上。 */
.is-up { color: var(--app-quote-up); }
.is-down { color: var(--app-quote-down); }
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
  /* 块标题层级：展示字 + 600 字重 + 墨色，与其它墨账块一个口径。 */
  font-weight: 600;
  color: var(--td-text-color-primary);
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

/* 分组筛选与排序是 chrome：去掉描边、底色与圆角，只用留白和字重分主次。
   （原来是一块灰底圆角盒 + 珊瑚实底选中态：品牌色被当状态色用了。） */
.etf-ws__seg,
.etf-ws__sort {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.etf-ws__seg-btn,
.etf-ws__sort-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
  cursor: pointer;
  transition: color 0.15s ease;

  &:hover {
    color: var(--td-text-color-primary);
  }
}

.etf-ws__seg-btn.is-active,
.etf-ws__sort-btn.is-active {
  /* 选中态用墨色字重，不用品牌珊瑚 —— 珊瑚是品牌色，不是状态色。 */
  color: var(--td-text-color-primary);
  font-weight: 600;
}

/* 0 只的分组置灰而不是隐藏：让"扩池了但这组是空的"这件事本身可见。 */
.etf-ws__seg-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* 分组计数是 chrome 徽章：中性墨阶，不占品牌珊瑚。 */
.etf-ws__seg-n {
  font-size: var(--app-text-2xs);
  padding: 0 4px;
  border-radius: 3px;
  background: var(--ledger-ink-1);
  color: var(--td-text-color-secondary);
}

.etf-ws__seg-btn.is-active .etf-ws__seg-n {
  background: var(--ledger-ink-3);
  color: var(--td-text-color-primary);
}

/* 说明行是注释，不是卡片：去掉琥珀底色块，改用一条淡墨竖线把它从表格里分出来。
   这句话本身讲的是"行业池还没数据"，是提示不是告警，不需要颜色来喊。 */
.etf-ws__note {
  margin: 0;
  padding: 1px 0 1px 10px;
  border-left: 2px solid var(--ledger-ink-3);
  color: var(--td-text-color-secondary);
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
  外壳是 .ledger-block（无框块）：底色 / 描边 / 圆角 / 阴影由全局工具类归零，
  这里不再复制一份"归零四行"。
*/
.etf-ws__card {
  width: 100%;
  max-width: 1080px;
}

.etf-ws__table {
  display: flex;
  flex-direction: column;
  width: 100%;
}

.etf-ws__tr {
  display: grid;
  grid-template-columns: minmax(180px, 2fr) minmax(75px, 0.9fr) minmax(100px, 1fr) minmax(80px, 0.9fr) minmax(70px, 0.8fr) minmax(95px, 1fr) minmax(70px, 0.8fr);
  gap: 12px;
  align-items: center;
  padding: 9px 0;
  /* 表格的横线用淡墨阶，比容器描边灰更像"账本上的栏线"。 */
  border-bottom: 1px solid var(--ledger-ink-2);
  font-size: var(--app-text-sm);
  transition: background-color 0.15s ease;

  &:hover:not(.etf-ws__tr--head) {
    /* 行的悬停是纸面上的淡墨，不画圆角块。 */
    background: var(--ledger-ink-1);
  }
}

.etf-ws__tr--inst {
  grid-template-columns: minmax(180px, 2fr) minmax(160px, 1.6fr) minmax(90px, 1fr) minmax(100px, 1.1fr) minmax(100px, 1.1fr) minmax(110px, 1.1fr);
  cursor: pointer;
}

.etf-ws__inst-bar-col {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

/* 机构持仓比例条是图表：墨账里用墨色，不按方向染色。 */
.etf-ws__ratio-bar-wrap {
  flex: 1;
  height: 6px;
  background: var(--ledger-ink-1);
  border-radius: 3px;
  overflow: hidden;
}

.etf-ws__ratio-bar-inner {
  height: 100%;
  background: var(--ledger-ink-max);
  border-radius: 3px;
}

.etf-ws__ratio-pct {
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
  font-size: var(--app-text-xs);
  font-variant-numeric: tabular-nums;
  min-width: 45px;
  text-align: right;
}

/* 行内动作是次级操作：墨账里用文字链接，不画按钮框，也不占品牌珊瑚。 */
.etf-ws__holder-btn {
  padding: 0;
  font-size: var(--app-text-xs);
  border: 0;
  border-radius: 0;
  color: var(--td-text-color-secondary);
  background: transparent;
  cursor: pointer;
  white-space: nowrap;
  transition: color 0.15s ease;

  &:hover {
    color: var(--td-text-color-primary);
    text-decoration: underline;
    text-underline-offset: 2px;
  }
}

.etf-ws__tr--head {
  font-size: var(--app-text-xs);
  font-weight: 600;
  color: var(--td-text-color-secondary);
  padding-bottom: 8px;
  border-bottom: 1px solid var(--ledger-ink-2);
}

/* 表格里的数字一律等宽 + 表格数字，对齐才能逐行扫读。 */
.is-num {
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
  font-variant-numeric: tabular-nums;
  text-align: right;
}

/* 数字默认墨色；方向色（红涨绿跌）由 .is-up / .is-down 单独覆盖。 */
.etf-ws__num {
  color: var(--td-text-color-primary);
}

.is-center {
  text-align: center;
}

.etf-ws__tr--head .is-num { text-align: right; }
.etf-ws__tr--head .is-center { text-align: center; }

/*
 * 份额变动（季频）是这块的主角 —— 它是"国家队在不在进出"的唯一证据。
 * 墨账：涨跌红绿是金融语义，留在**数字**上；但不给它铺底色块：
 * 红绿当装饰会被读成"这一行是涨的"，而密集表格里真正要读的是那个数字本身。
 * 所以：去底色、留墨阶对齐、加粗、数字带红绿。
 */
.etf-ws__pct-pill {
  display: inline-block;
  padding: 0;
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  font-size: var(--app-text-sm);
  text-align: right;

  &.is-up {
    color: var(--app-quote-up);
  }
  &.is-down {
    color: var(--app-quote-down);
  }
  &.is-flat {
    color: var(--td-text-color-placeholder);
  }
}

/* 「异动」是状态标记，走墨阶 —— 品牌珊瑚全产品只出现一处，不铺到这里。 */
.etf-ws__signal-badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: var(--app-text-xs);
  font-weight: 600;
  background: var(--ledger-ink-2);
  color: var(--td-text-color-primary);
}

.etf-ws__muted {
  color: var(--td-text-color-placeholder);
}

.etf-ws__name {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.etf-ws__label {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  color: var(--td-text-color-primary);
}

.etf-ws__dot,
.etf-ws__code {
  flex: 0 0 auto;
}

.etf-ws__code {
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
}

/* 异动小点：状态标记走墨阶，不占品牌珊瑚，也不跟涨跌红绿抢颜色。 */
.etf-ws__dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--ledger-ink-4);
  flex: 0 0 auto;
}

.etf-ws__signal { font-size: var(--app-text-xs); }

/* 放量倍数是次要量：小字灰字，不跟份额变动抢读数。 */
.etf-ws__mult { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }
.etf-ws__date { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

.etf-ws__foot {
  margin: 0;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

/* ── 持有人穿透 Drawer 样式 ── */
.etf-drawer__loading,
.etf-drawer__empty {
  padding: 32px 16px;
  text-align: center;
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-sm);
}

.etf-drawer__content {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 4px 0 24px;
}

/* 结构卡不是卡片：外壳由全局 .ledger-block 归零，这里只管内部排布。 */
.etf-drawer__struct-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.etf-drawer__struct-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.etf-drawer__struct-title {
  font-weight: 600;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-primary);
}

.etf-drawer__struct-date {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
}

/* 机构 vs 散户 是**分类**比例条：段的位置就是标签，两段都用墨阶的浓淡分，
   方向与强弱都不在这条上（它没有方向语义），所以不套涨跌红绿。 */
.etf-drawer__bar-container {
  display: flex;
  height: 10px;
  border-radius: 2px;
  overflow: hidden;
  background: var(--ledger-ink-1);
}

.etf-drawer__bar-ins {
  background: var(--ledger-ink-max);
  transition: width var(--app-motion-normal) ease;
}

.etf-drawer__bar-psnl {
  background: var(--ledger-ink-2);
  transition: width var(--app-motion-normal) ease;
}

.etf-drawer__legend-row {
  display: flex;
  gap: 20px;
  font-size: var(--app-text-xs);
}

.etf-drawer__legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* 图例小方块跟上面那两段同色：读者靠它把段和名字对上。 */
.etf-drawer__dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;

  &.is-ins { background: var(--ledger-ink-max); }
  &.is-psnl { background: var(--ledger-ink-2); }
}

.etf-drawer__stat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding-top: 6px;
  border-top: 1px solid var(--ledger-ink-2);
}

.etf-drawer__stat-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.etf-drawer__stat-k {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
}

.etf-drawer__stat-v {
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
  font-size: var(--app-text-sm);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.etf-drawer__top-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.etf-drawer__sub-title {
  margin: 0;
  font-size: var(--app-text-sm);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.etf-drawer__empty-hint {
  padding: 16px;
  text-align: center;
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-xs);
}

.etf-drawer__holder-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 持有人行不是小卡片，是一行有栏线的记录：去掉描边与圆角，只留一条淡墨横线。 */
.etf-drawer__holder-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--ledger-ink-2);
  border-radius: 0;
  transition: background-color 0.15s ease;

  &:hover {
    background: var(--ledger-ink-1);
  }
}

/* 名次走墨阶：前三名用墨色正色 + 粗体，其余次级墨色。 */
.etf-drawer__holder-rank {
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
  width: 24px;
}

.etf-drawer__holder-row:nth-child(-n + 3) .etf-drawer__holder-rank {
  color: var(--td-text-color-primary);
}

.etf-drawer__holder-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.etf-drawer__holder-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.etf-drawer__holder-name {
  font-weight: 500;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 持有人类型是 chrome 标签，不是数据：原来给每类配了一种装饰色（含紫/蓝/绿），
   一屏七八种颜色，谁也读不出重点。墨账：全部走中性墨阶，
   只有"国家队"这类要强调的用浓一档 + 粗体。 */
.etf-drawer__holder-badge {
  font-size: var(--app-text-xs);
  padding: 1px 6px;
  border-radius: 3px;
  white-space: nowrap;
  background: var(--ledger-ink-1);
  color: var(--td-text-color-secondary);

  &.is-state {
    background: var(--ledger-ink-3);
    color: var(--td-text-color-primary);
    font-weight: 600;
  }
}

.etf-drawer__holder-bottom {
  display: flex;
  gap: 16px;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);

  b {
    color: var(--td-text-color-primary);
    font-family: var(--app-font-family-mono); /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
  }
}
</style>
