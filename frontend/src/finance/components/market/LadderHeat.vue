<!--
  连板梯队与短线异动监控（大盘工作台 · 大盘 tab）。

  包含三个维度：
    1. 连板热力矩阵：近 N 日 × 2/3/4/5/6/7+ 板分布热力，研判市场高度拓展还是梯队坍塌。
    2. 今日连板梯队清单：最高空间板标杆、各连板高度个股明细、封单金额与首次封板时间。
    3. 炸板与跌停监控：盘中触及涨停被砸开的炸板股亏钱效应，以及跌停核按钮排雷。
-->
<template>
  <article class="lad ledger-block">
    <header class="lad__head">
      <div class="lad__title-row">
        <span class="bas-title">{{ $t('marketDashboard.ladder.title') }}</span>
        <div class="lad__tabs" role="tablist">
          <button
            type="button"
            class="lad__tab"
            role="tab"
            :aria-selected="viewMode === 'matrix'"
            :class="{ 'is-active': viewMode === 'matrix' }"
            @click="viewMode = 'matrix'"
          >
            {{ $t('marketDashboard.ladder.tabMatrix') }}
          </button>
          <button
            type="button"
            class="lad__tab"
            role="tab"
            :aria-selected="viewMode === 'stocks'"
            :class="{ 'is-active': viewMode === 'stocks' }"
            @click="viewMode = 'stocks'"
          >
            {{ $t('marketDashboard.ladder.tabStocks', { count: ladderDetail?.total_count ?? 18 }) }}
          </button>
          <button
            type="button"
            class="lad__tab"
            role="tab"
            :aria-selected="viewMode === 'break'"
            :class="{ 'is-active': viewMode === 'break' }"
            @click="viewMode = 'break'"
          >
            {{ $t('marketDashboard.ladder.tabBreak', { count: (breakDownData?.break_count ?? 0) + (breakDownData?.down_count ?? 0) }) }}
          </button>
        </div>
        <span class="lad__caption">{{ subCaption }}</span>
      </div>
    </header>

    <!-- ── 模式 1：热力矩阵 ── -->
    <div v-if="viewMode === 'matrix'">
      <p v-if="failed" class="lad__empty">{{ $t('marketDashboard.ladder.failed') }}</p>
      <p v-else-if="loading" class="lad__empty">{{ $t('common.loading') }}</p>
      <p v-else-if="!matrix.length" class="lad__empty">{{ $t('marketDashboard.noData') }}</p>

      <div v-else class="lad__wrap">
        <div class="lad__grid" :style="{ gridTemplateColumns: `44px repeat(${daysShown}, minmax(0, 1fr))` }">
          <span class="lad__corner" />
          <span v-for="d in matrix" :key="`h-${d}`" class="lad__col-head">{{ shortDate(d) }}</span>

          <template v-for="lv in LEVELS" :key="lv.key">
            <span class="lad__row-head">{{ $t(lv.labelKey) }}</span>
            <span
              v-for="d in matrix"
              :key="`${lv.key}-${d}`"
              class="lad__cell"
              :class="cellClass(countOf(d, lv.key))"
              :title="cellTitle(d, lv.key)"
            >
              <template v-if="countOf(d, lv.key) !== null">{{ countOf(d, lv.key) }}</template>
            </span>
          </template>
        </div>
      </div>
    </div>

    <!-- ── 模式 2：连板个股梯队清单 ── -->
    <div v-else-if="viewMode === 'stocks'" class="lad__stocks-wrap">
      <div v-if="ladderLoading" class="lad__empty">{{ $t('common.loading') }}</div>
      <div v-else-if="!ladderDetail?.items?.length" class="lad__empty">{{ $t('marketDashboard.noData') }}</div>
      <div v-else class="lad__ladder-panel">
        <!-- 空间板最高标提示 -->
        <div v-if="ladderDetail.max_board" class="lad__max-board-bar">
          <span class="lad__max-badge">{{ $t('marketDashboard.ladder.maxBoard', { count: ladderDetail.max_board }) }}</span>
          <span class="lad__max-date">{{ ladderDetail.trade_date }}</span>
        </div>

        <div class="lad__level-groups">
          <div v-for="(stocks, levelName) in ladderDetail.by_level" :key="levelName" class="lad__level-group">
            <div class="lad__level-head">
              <span class="lad__level-badge">{{ levelName }}</span>
              <span class="lad__level-count">{{ $t('marketDashboard.ladder.stockCount', { count: stocks.length }) }}</span>
            </div>
            <div class="lad__stock-chips">
              <div v-for="s in stocks" :key="s.thscode" class="lad__stock-card">
                <div class="lad__stock-top">
                  <span class="lad__stock-name">{{ s.name }}</span>
                  <span class="lad__stock-code">{{ s.thscode }}</span>
                </div>
                <div class="lad__stock-meta">
                  <span class="lad__stock-time" :title="$t('marketDashboard.ladder.sealTimeTitle')">{{ s.limit_up_time || '—' }}</span>
                  <span v-if="s.seal_money" class="lad__stock-money" :title="$t('marketDashboard.ladder.sealMoneyTitle')">
                    {{ $t('marketDashboard.ladder.sealMoney', { value: fmtMoney(s.seal_money) }) }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 模式 3：炸板与跌停监控 ── -->
    <div v-else class="lad__break-wrap">
      <div v-if="breakLoading" class="lad__empty">{{ $t('common.loading') }}</div>
      <div v-else-if="!breakDownData" class="lad__empty">{{ $t('marketDashboard.noData') }}</div>
      <div v-else class="lad__break-grid">
        <!-- 炸板池 -->
        <div class="lad__break-col">
          <div class="lad__col-title">
            <span>{{ $t('marketDashboard.ladder.breakPoolTitle', { count: breakDownData.break_count }) }}</span>
          </div>
          <div v-if="!breakDownData.break_pool.length" class="lad__empty-sub">{{ $t('marketDashboard.ladder.breakPoolEmpty') }}</div>
          <div v-else class="lad__break-list">
            <div v-for="b in breakDownData.break_pool" :key="b.thscode" class="lad__break-item">
              <div class="lad__break-info">
                <span class="lad__break-name">{{ b.name }}</span>
                <span class="lad__break-code">{{ b.thscode }}</span>
              </div>
              <div class="lad__break-stats">
                <span class="lad__break-times">{{ $t('marketDashboard.ladder.openTimes', { count: b.open_times }) }}</span>
                <span class="lad__break-pct" :class="b.price_change_ratio && b.price_change_ratio >= 0 ? 'is-up' : 'is-down'">
                  {{ b.price_change_ratio ? (b.price_change_ratio > 0 ? '+' : '') + b.price_change_ratio.toFixed(2) + '%' : '—' }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- 跌停池 -->
        <div class="lad__break-col">
          <div class="lad__col-title">
            <span>{{ $t('marketDashboard.ladder.downPoolTitle', { count: breakDownData.down_count }) }}</span>
          </div>
          <div v-if="!breakDownData.down_pool.length" class="lad__empty-sub">{{ $t('marketDashboard.ladder.downPoolEmpty') }}</div>
          <div v-else class="lad__break-list">
            <div v-for="d in breakDownData.down_pool" :key="d.thscode" class="lad__break-item">
              <div class="lad__break-info">
                <span class="lad__break-name">{{ d.name }}</span>
                <span class="lad__break-code">{{ d.thscode }}</span>
              </div>
              <div class="lad__break-stats">
                <span class="lad__down-time">{{ $t('marketDashboard.ladder.limitDownAt', { time: d.first_limit_time || '—' }) }}</span>
                <span class="lad__down-pct is-down">
                  {{ d.price_change_ratio ? d.price_change_ratio.toFixed(2) + '%' : '—' }}
                </span>
              </div>
            </div>
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
  getLimitUpLadder,
  getLadderDetail,
  getLimitBreakDown,
  type LadderResponse,
  type LadderDetailResponse,
  type LimitBreakDownResponse,
} from '@/finance/api/market'

const { t } = useI18n()

const viewMode = ref<'matrix' | 'stocks' | 'break'>('matrix')

const data = ref<LadderResponse | null>(null)
const loading = ref(true)
const failed = ref(false)

const ladderDetail = ref<LadderDetailResponse | null>(null)
const ladderLoading = ref(false)

const breakDownData = ref<LimitBreakDownResponse | null>(null)
const breakLoading = ref(false)

const LEVELS = [
  { key: 'two_board', labelKey: 'marketDashboard.ladder.level.two' },
  { key: 'three_board', labelKey: 'marketDashboard.ladder.level.three' },
  { key: 'four_board', labelKey: 'marketDashboard.ladder.level.four' },
  { key: 'five_board', labelKey: 'marketDashboard.ladder.level.five' },
  { key: 'six_board', labelKey: 'marketDashboard.ladder.level.six' },
  { key: 'seven_over', labelKey: 'marketDashboard.ladder.level.seven' },
] as const

const MAX_COLS = 20

async function loadMatrix() {
  loading.value = true
  failed.value = false
  try {
    data.value = await getLimitUpLadder(30)
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function loadLadderDetail() {
  ladderLoading.value = true
  try {
    ladderDetail.value = await getLadderDetail({ min_board: 2, limit: 50 })
  } catch {
    // 降级
  } finally {
    ladderLoading.value = false
  }
}

async function loadBreakDown() {
  breakLoading.value = true
  try {
    breakDownData.value = await getLimitBreakDown(30)
  } catch {
    // 降级
  } finally {
    breakLoading.value = false
  }
}

onMounted(() => {
  loadMatrix()
  loadLadderDetail()
  loadBreakDown()
})

const matrix = computed(() =>
  (data.value?.days ?? []).slice(0, MAX_COLS).map((d) => d.trade_date as string),
)
const daysShown = computed(() => matrix.value.length)

const subCaption = computed(() => {
  if (viewMode.value === 'stocks') {
    return ladderDetail.value?.trade_date
      ? t('marketDashboard.ladder.tradeDate', { date: ladderDetail.value.trade_date })
      : t('marketDashboard.ladder.subCaptionStocks')
  }
  if (viewMode.value === 'break') {
    return t('marketDashboard.ladder.subCaptionBreak')
  }
  return t('marketDashboard.ladder.caption', { n: daysShown.value })
})

function countOf(date: string, level: string): number | null {
  const day = (data.value?.days ?? []).find((d) => d.trade_date === date)
  if (!day) return null
  const hit = day.levels.find((l) => l.board_level === level)
  return hit ? hit.board_num : 0
}

function cellTitle(date: string, level: string): string {
  // 这里必须走 t() 解析：直接拼 labelKey 会把 'marketDashboard.ladder.level.two'
  // 这种原始 key 显示给用户（悬浮提示里一眼就能看到）。
  const label = LEVELS.find((l) => l.key === level)?.labelKey
  return `${date} · ${label ? t(label) : level}: ${countOf(date, level) ?? 0}`
}

function cellClass(v: number | null): string {
  if (v === null) return 'is-void'
  if (v === 0) return 'is-zero'
  return v >= 8 ? 'is-hi' : v >= 4 ? 'is-mid' : 'is-lo'
}

function shortDate(d: string): string {
  return d ? d.slice(5) : ''
}

function fmtMoney(num: number | null): string {
  if (num === null || num === undefined) return '—'
  if (num >= 1e8) return `${(num / 1e8).toFixed(2)} 亿`
  if (num >= 1e4) return `${(num / 1e4).toFixed(0)} 万`
  return `${num}`
}
</script>

<style scoped lang="less">
.lad {
  /* 墨账：块不画卡片。块之间的距离由父级栅格的 gap 给，不是靠各自的边框。 */
}

.lad__head { margin-bottom: 10px; }
.lad__title-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.bas-title { font-family: var(--app-font-display); font-size: var(--app-text-md); }
.lad__caption { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

/* tab 条是 chrome：去掉底色与圆角，选中态靠字重与墨色讲。
   （原来选中态是珊瑚字，亮底上对比度只有 3.4:1，读不清。） */
.lad__tabs {
  display: inline-flex;
  gap: 2px;
}

.lad__tab {
  border: none;
  background: transparent;
  padding: 2px 8px;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  cursor: pointer;
  transition: color 0.15s ease;

  &.is-active {
    color: var(--td-text-color-primary);
    font-weight: 600;
  }
}

.lad__empty {
  margin: 0; padding: 14px 0;
  color: var(--td-text-color-placeholder); font-size: var(--app-text-sm);
}

.lad__wrap { overflow-x: auto; }
.lad__grid { display: grid; gap: 2px; min-width: 520px; align-items: center; }

.lad__col-head,
.lad__row-head {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
  text-align: center;
}

.lad__row-head { text-align: left; white-space: nowrap; }

.lad__cell {
  display: flex; align-items: center; justify-content: center;
  height: 22px; border-radius: 3px;
  font-family: var(--app-font-family-mono);
  font-size: var(--app-text-xs);
  font-variant-numeric: tabular-nums;
}

/* 浓淡表用墨阶：越浓越多。原来是品牌色深浅（珊瑚），那是装饰色，
   会跟涨跌红绿抢读。墨阶由 --td-text-color-primary 派生，亮暗主题自动跟随。
   浓格上的字用容器底色（亮主题=白字压深墨，暗主题=暗字压亮墨），淡格上用墨字。 */
.lad__cell.is-void { background: transparent; }
.lad__cell.is-zero { background: var(--ledger-ink-1); color: var(--td-text-color-placeholder); }
.lad__cell.is-lo { background: var(--ledger-ink-2); color: var(--td-text-color-primary); }
.lad__cell.is-mid { background: var(--ledger-ink-3); color: var(--td-text-color-primary); }
/* 最浓档：底色与字色都取随主题翻的令牌（见 ledger.less 的说明）。
   之前这里是 ink-5 底 + 白字，亮色主题下只有 3.08:1（11px 要 4.5）。 */
.lad__cell.is-hi { background: var(--ledger-ink-max); color: var(--ledger-on-ink-max); font-weight: 600; }

/* 连板个股清单面板 */
.lad__stocks-wrap {
  margin-top: 4px;
}

/* 最高空间板是 chrome 徽章，不是数据色：去掉那层红底，字重与墨色来讲重点。 */
.lad__max-board-bar {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 10px;
  padding: 0;
  background: transparent;
}

.lad__max-badge {
  font-family: var(--app-font-display);
  font-size: var(--app-text-xs);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.lad__max-date {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}

.lad__level-groups {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 280px;
  overflow-y: auto;
}

.lad__level-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lad__level-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* 连板高度徽章是 chrome：品牌浅底改成淡墨阶，不与涨跌红绿抢颜色。 */
.lad__level-badge {
  font-family: var(--app-font-display);
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--ledger-ink-2);
  color: var(--td-text-color-primary);
}

.lad__level-count {
  font-size: 11px;
  color: var(--td-text-color-secondary);
}

.lad__stock-chips {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 6px;
}

/* 个股卡不是卡片，是一行有栏线的记录：去掉描边与圆角，只留一条淡墨横线。 */
.lad__stock-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px 8px 6px;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--ledger-ink-2);
  border-radius: 0;
}

.lad__stock-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.lad__stock-name {
  font-size: var(--app-text-xs);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.lad__stock-code {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.lad__stock-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: var(--app-text-xs);
}

.lad__stock-time {
  color: var(--td-text-color-secondary);
}

.lad__stock-money {
  /* 封单金额是**量级**，不是方向：涨停时封单只有买盘，没有红绿可讲。
     原来用品牌珊瑚，一屏就是一片珊瑚 —— 品牌色被当装饰用了。
     墨账：量级用墨色，方向留给真正有方向的数字。 */
  color: var(--td-text-color-primary);
  font-weight: 500;
}

/* 炸板与跌停监控 */
.lad__break-wrap {
  margin-top: 4px;
}

.lad__break-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.lad__break-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lad__col-title {
  font-size: var(--app-text-xs);
  font-weight: 600;
  color: var(--td-text-color-secondary);
  padding-bottom: 4px;
  border-bottom: 1px solid var(--ledger-ink-2);
}

.lad__empty-sub {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
  padding: 10px 0;
}

.lad__break-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 250px;
  overflow-y: auto;
}

/* 同上：炸板 / 跌停条目也是一行记录，不是小卡片。 */
.lad__break-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 8px 5px;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--ledger-ink-2);
  border-radius: 0;
  font-size: 11px;
}

.lad__break-info {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.lad__break-name {
  font-weight: 500;
  color: var(--td-text-color-primary);
}

.lad__break-code {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.lad__break-stats {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1px;
}

.lad__break-times {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.lad__break-pct,
.lad__down-pct {
  font-family: var(--app-font-display);
  font-weight: 600;
}

.lad__down-time {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.is-up { color: var(--app-quote-up); }
.is-down { color: var(--app-quote-down); }
</style>
