<!--
  连板梯队与短线异动监控（大盘工作台 · 大盘 tab）。

  包含三个维度：
    1. 连板热力矩阵：近 N 日 × 2/3/4/5/6/7+ 板分布热力，研判市场高度拓展还是梯队坍塌。
    2. 今日连板梯队清单：最高空间板标杆、各连板高度个股明细、封单金额与首次封板时间。
    3. 炸板与跌停监控：盘中触及涨停被砸开的炸板股亏钱效应，以及跌停核按钮排雷。
-->
<template>
  <article class="lad">
    <header class="lad__head">
      <div class="lad__title-row">
        <span class="bas-title">{{ $t('marketDashboard.ladder.title') }}</span>
        <div class="lad__tabs">
          <button
            type="button"
            class="lad__tab"
            :class="{ 'is-active': viewMode === 'matrix' }"
            @click="viewMode = 'matrix'"
          >
            热力矩阵
          </button>
          <button
            type="button"
            class="lad__tab"
            :class="{ 'is-active': viewMode === 'stocks' }"
            @click="viewMode = 'stocks'"
          >
            连板梯队 ({{ ladderDetail?.total_count ?? 18 }})
          </button>
          <button
            type="button"
            class="lad__tab"
            :class="{ 'is-active': viewMode === 'break' }"
            @click="viewMode = 'break'"
          >
            炸板跌停 ({{ (breakDownData?.break_count ?? 0) + (breakDownData?.down_count ?? 0) }})
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
          <span class="lad__max-badge">最高空间板：{{ ladderDetail.max_board }} 连板</span>
          <span class="lad__max-date">{{ ladderDetail.trade_date }}</span>
        </div>

        <div class="lad__level-groups">
          <div v-for="(stocks, levelName) in ladderDetail.by_level" :key="levelName" class="lad__level-group">
            <div class="lad__level-head">
              <span class="lad__level-badge">{{ levelName }}</span>
              <span class="lad__level-count">{{ stocks.length }} 只</span>
            </div>
            <div class="lad__stock-chips">
              <div v-for="s in stocks" :key="s.thscode" class="lad__stock-card">
                <div class="lad__stock-top">
                  <span class="lad__stock-name">{{ s.name }}</span>
                  <span class="lad__stock-code">{{ s.thscode }}</span>
                </div>
                <div class="lad__stock-meta">
                  <span class="lad__stock-time" title="封板时间">{{ s.limit_up_time || '—' }}</span>
                  <span v-if="s.seal_money" class="lad__stock-money" title="封单金额">
                    封单 {{ fmtMoney(s.seal_money) }}
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
            <span>炸板池（开板回落 {{ breakDownData.break_count }} 只）</span>
          </div>
          <div v-if="!breakDownData.break_pool.length" class="lad__empty-sub">今日无炸板个股</div>
          <div v-else class="lad__break-list">
            <div v-for="b in breakDownData.break_pool" :key="b.thscode" class="lad__break-item">
              <div class="lad__break-info">
                <span class="lad__break-name">{{ b.name }}</span>
                <span class="lad__break-code">{{ b.thscode }}</span>
              </div>
              <div class="lad__break-stats">
                <span class="lad__break-times">开板 {{ b.open_times }} 次</span>
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
            <span>跌停池（极端核按钮 {{ breakDownData.down_count }} 只）</span>
          </div>
          <div v-if="!breakDownData.down_pool.length" class="lad__empty-sub">今日无跌停个股</div>
          <div v-else class="lad__break-list">
            <div v-for="d in breakDownData.down_pool" :key="d.thscode" class="lad__break-item">
              <div class="lad__break-info">
                <span class="lad__break-name">{{ d.name }}</span>
                <span class="lad__break-code">{{ d.thscode }}</span>
              </div>
              <div class="lad__break-stats">
                <span class="lad__down-time">{{ d.first_limit_time || '—' }} 跌停</span>
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
    return ladderDetail.value?.trade_date ? `行情交易日：${ladderDetail.value.trade_date}` : '空间板梯队'
  }
  if (viewMode.value === 'break') {
    return '盘中开板与极端核按钮'
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
  return `${date} · ${LEVELS.find((l) => l.key === level)?.labelKey ?? level}: ${countOf(date, level) ?? 0}`
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
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--td-radius-medium);
  padding: 12px 14px;
}

.lad__head { margin-bottom: 10px; }
.lad__title-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.bas-title { font-family: var(--app-font-display); font-size: var(--app-text-md); }
.lad__caption { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

.lad__tabs {
  display: inline-flex;
  background: var(--td-bg-color-secondarycontainer);
  border-radius: var(--td-radius-small);
  padding: 2px;
  gap: 2px;
}

.lad__tab {
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
    color: var(--td-brand-color);
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
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
  font-size: var(--app-text-2xs, 10px);
  color: var(--td-text-color-placeholder);
  text-align: center;
}

.lad__row-head { text-align: left; white-space: nowrap; }

.lad__cell {
  display: flex; align-items: center; justify-content: center;
  height: 22px; border-radius: 3px;
  font-family: var(--app-font-family-mono);
  font-size: var(--app-text-2xs, 10px);
  font-variant-numeric: tabular-nums;
}

.lad__cell.is-void { background: transparent; }
.lad__cell.is-zero { background: var(--td-bg-color-container-hover); color: var(--td-text-color-placeholder); }
.lad__cell.is-lo { background: color-mix(in srgb, var(--td-brand-color) 14%, transparent); color: var(--td-text-color-primary); }
.lad__cell.is-mid { background: color-mix(in srgb, var(--td-brand-color) 34%, transparent); color: var(--td-text-color-primary); }
.lad__cell.is-hi { background: color-mix(in srgb, var(--td-brand-color) 62%, transparent); color: #fff; font-weight: 600; }

/* 连板个股清单面板 */
.lad__stocks-wrap {
  margin-top: 4px;
}

.lad__max-board-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  padding: 6px 10px;
  background: rgba(227, 77, 89, 0.08);
  border-radius: var(--td-radius-small);
}

.lad__max-badge {
  font-family: var(--app-font-display);
  font-size: var(--app-text-xs);
  font-weight: 600;
  color: var(--app-quote-up);
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

.lad__level-badge {
  font-family: var(--app-font-display);
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--td-brand-color-light);
  color: var(--td-brand-color);
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

.lad__stock-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px 8px;
  background: var(--td-bg-color-container-hover);
  border-radius: var(--td-radius-small);
  border: 1px solid var(--td-component-stroke);
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
  font-size: 10px;
  color: var(--td-text-color-placeholder);
}

.lad__stock-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 10px;
}

.lad__stock-time {
  color: var(--td-text-color-secondary);
}

.lad__stock-money {
  color: var(--td-brand-color);
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
  border-bottom: 1px solid var(--td-component-stroke);
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

.lad__break-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 8px;
  background: var(--td-bg-color-container-hover);
  border-radius: var(--td-radius-small);
  border: 1px solid var(--td-component-stroke);
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
  font-size: 10px;
  color: var(--td-text-color-placeholder);
}

.lad__break-stats {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1px;
}

.lad__break-times {
  font-size: 10px;
  color: var(--td-text-color-placeholder);
}

.lad__break-pct,
.lad__down-pct {
  font-family: var(--app-font-display);
  font-weight: 600;
}

.lad__down-time {
  font-size: 10px;
  color: var(--td-text-color-placeholder);
}

.is-up { color: var(--app-quote-up); }
.is-down { color: var(--app-quote-down); }
</style>
