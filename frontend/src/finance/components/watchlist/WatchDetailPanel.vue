<template>
  <aside class="wl-detail" :style="{ width: `${panelWidth}px` }">
    <header class="wl-detail__head">
      <div class="wl-detail__title">
        <span class="wl-detail__name">{{ displayName }}</span>
        <span class="wl-detail__code">{{ thscode }}</span>
      </div>
      <button type="button" class="wl-detail__close" :title="t('watchlist.cancel')" @click="$emit('close')">
        <t-icon name="close" size="16px" />
      </button>
    </header>

    <div class="wl-detail__quote" v-if="quote">
      <span class="wl-detail__price" :class="changeClass">{{ quote.close?.toFixed(2) ?? '—' }}</span>
      <span class="wl-detail__change" :class="changeClass">
        {{ signed(quote.change) }} <span class="wl-detail__pct">{{ pct(quote.change_pct) }}</span>
      </span>
      <span class="wl-detail__date">{{ quote.date }}</span>
    </div>

    <!-- 持仓操盘成本与防守止损位 -->
    <div class="wl-targets-card">
      <div v-if="!editingTarget" class="wl-targets-card__view">
        <div class="wl-targets-card__info">
          <div class="wl-targets-item">
            <span class="wl-targets-item__k">{{ t('watchlist.targetCost') }}:</span>
            <span v-if="tradeTarget?.cost" class="wl-targets-item__v" :class="costPnlClass">
              ¥{{ tradeTarget.cost.toFixed(2) }} ({{ costPnlText }})
            </span>
            <span v-else class="wl-targets-item__empty"> {{ t('watchDetail.notSet') }} </span>
          </div>
          <div class="wl-targets-item">
            <span class="wl-targets-item__k">{{ t('watchlist.targetStop') }}:</span>
            <span v-if="tradeTarget?.stopLoss" class="wl-targets-item__v is-stop">
              ¥{{ tradeTarget.stopLoss.toFixed(2) }} ({{ stopLossPnlText }})
            </span>
            <span v-else class="wl-targets-item__empty"> {{ t('watchDetail.notSet') }} </span>
          </div>
          <div class="wl-targets-item" v-if="tradeTarget?.shares">
            <span class="wl-targets-item__k">{{ t('watchlist.targetShares') }}:</span>
            <span class="wl-targets-item__v">
              {{ tradeTarget.shares.toLocaleString() }} 股 (市值 ¥{{ targetMarketValueText }})
            </span>
          </div>
          <div class="wl-targets-item" v-if="tradeTarget?.shares && tradeTarget?.cost && quote?.close">
            <span class="wl-targets-item__k">{{ t('watchlist.targetPnlAmount') }}:</span>
            <span class="wl-targets-item__v" :class="costPnlClass">
              ¥{{ targetPnlAmountText }}
            </span>
          </div>
        </div>
        <t-button size="small" variant="text" theme="primary" class="wl-targets-card__edit-btn" @click="startEditTarget">
          <template #icon><t-icon name="edit" /></template>
          {{ tradeTarget?.cost || tradeTarget?.stopLoss ? t('common.edit') : t('watchlist.setTarget') }}
        </t-button>
      </div>

      <div v-else class="wl-targets-card__form">
        <div class="wl-targets-form__row">
          <span class="wl-targets-form__lbl"> {{ t('watchDetail.cost') }}: </span>
          <t-input-number
            v-model="editCost"
            :decimal-places="2"
            :min="0"
            :step="0.1"
            size="small"
            :placeholder="t('watchDetail.costPlaceholder')"
            class="wl-targets-form__input"
          />
          <span class="wl-targets-form__lbl"> {{ t('watchDetail.stopLoss') }}: </span>
          <t-input-number
            v-model="editStop"
            :decimal-places="2"
            :min="0"
            :step="0.1"
            size="small"
            :placeholder="t('watchDetail.stopPlaceholder')"
            class="wl-targets-form__input"
          />
        </div>
        <div class="wl-targets-form__row">
          <span class="wl-targets-form__lbl"> {{ t('watchlist.targetShares') }}: </span>
          <t-input-number
            v-model="editShares"
            :min="0"
            :step="100"
            size="small"
            :placeholder="t('watchlist.sharesPlaceholder')"
            class="wl-targets-form__input"
          />
        </div>
        <div class="wl-targets-form__actions">
          <t-button size="small" theme="primary" @click="handleSaveTarget">{{ t('common.save') }}</t-button>
          <t-button size="small" variant="text" @click="handleClearTarget">{{ t('common.clear') }}</t-button>
          <t-button size="small" variant="text" @click="editingTarget = false">{{ t('common.cancel') }}</t-button>
        </div>
      </div>
    </div>

    <!-- HALO 年报分析入口。
         放在这里而不是只留在 K 线工作台的工具栏里：那个入口在「菜单自选 →
         点行 → 打开全功能工作台 → 94vw 模态 → 一排 16px 无文字图标」的最末端，
         实际没人找得到。本面板已经是「选中一只票之后的操作区」，加一个出口就够。 -->
    <section class="wl-detail__section wl-detail__halo">
      <div class="wl-detail__section-head">
        <h3 class="wl-detail__h3">{{ t('halo.title') }}</h3>
        <t-button
          size="small"
          variant="outline"
          theme="primary"
          class="wl-detail__halo-btn"
          @click="$emit('open-halo')"
        >
          <template #icon><t-icon name="article" /></template>
          {{ t('halo.open') }}
        </t-button>
      </div>
      <p class="wl-detail__hint">{{ t('halo.entryHint') }}</p>
    </section>

    <!-- K 线。选行即看图是本面板存在的理由，所以它常驻在日记上面。 -->
    <section class="wl-detail__section">
      <div class="wl-detail__section-head">
        <h3 class="wl-detail__h3">{{ t('watchlist.detailChart') }}</h3>
        <t-button size="small" variant="text" theme="primary" class="wl-detail__ws-btn" @click="$emit('open-workspace')">
          <template #icon><t-icon name="fullscreen" /></template>
          {{ t('watchlist.fullWorkspace') }}
        </t-button>
      </div>
      <!-- 多周期大势共振矩阵 -->
      <div v-if="resonanceResult" class="wl-resonance-card" :class="resonanceResult.themeClass">
        <div class="wl-resonance-card__head">
          <span class="wl-resonance-card__title">
            <t-icon name="chart-bubble" size="14px" />
            {{ t('watchlist.resonanceTitle') }}
          </span>
          <span class="wl-resonance-card__badge">{{ resonanceResult.label }}</span>
        </div>
        <div class="wl-resonance-card__matrix">
          <div class="matrix-item" :class="resonanceResult.monthly ? 'is-bull' : 'is-bear'">
            <span class="matrix-dot" />
            <span class="matrix-lbl">{{ t('watchlist.resonanceMonth') }}</span>
            <span class="matrix-status">{{ resonanceResult.monthly ? t('watchlist.trendBull') : t('watchlist.trendBear') }}</span>
          </div>
          <div class="matrix-item" :class="resonanceResult.weekly ? 'is-bull' : 'is-bear'">
            <span class="matrix-dot" />
            <span class="matrix-lbl">{{ t('watchlist.resonanceWeek') }}</span>
            <span class="matrix-status">{{ resonanceResult.weekly ? t('watchlist.trendBull') : t('watchlist.trendBear') }}</span>
          </div>
          <div class="matrix-item" :class="resonanceResult.daily ? 'is-bull' : 'is-bear'">
            <span class="matrix-dot" />
            <span class="matrix-lbl">{{ t('watchlist.resonanceDay') }}</span>
            <span class="matrix-status">{{ resonanceResult.daily ? t('watchlist.trendBull') : t('watchlist.trendBear') }}</span>
          </div>
        </div>
        <p class="wl-resonance-card__desc">{{ resonanceResult.desc }}</p>
      </div>

      <WatchKLineChart
        :thscode="thscode"
        :name="displayName"
        :conditions="conditions"
        :trade-target="tradeTarget"
        @loaded-bars="onBarsLoaded"
      />
    </section>

    <section class="wl-detail__section wl-detail__section--diary">
      <div class="wl-detail__section-head">
        <h3 class="wl-detail__h3">{{ t('watchlist.detailDiary') }}</h3>
        <!-- 胜率统计徽章 -->
        <span v-if="backtestSummary.totalBuys > 0" class="wl-diary__stats-badge" :title="t('watchlist.backtestBadgeTitle')">
          {{ t('watchlist.backtestBadgeText', { winRate: backtestSummary.winRate, wins: backtestSummary.wins, total: backtestSummary.totalBuys, maxGain: backtestSummary.avgMaxGain }) }}
        </span>
      </div>

      <p v-if="diaryLoading" class="wl-detail__hint">{{ t('watchlist.diaryLoading') }}</p>

      <div v-else-if="!diaries.length" class="wl-detail__empty">
        <p class="wl-detail__empty-title">{{ t('watchlist.diaryEmpty') }}</p>
        <p class="wl-detail__empty-hint">{{ t('watchlist.diaryEmptyHint') }}</p>
      </div>

      <ul v-else class="wl-diary">
        <li v-for="d in diaries" :key="d.trade_date" class="wl-diary__item">
          <div class="wl-diary__head">
            <span class="wl-diary__date">{{ d.trade_date }}</span>
            <span class="wl-diary__verdict" :class="`is-${d.verdict}`" :title="d.reasons">
              {{ verdictLabel(d.verdict) }}
            </span>
            <span class="wl-diary__conf">{{ t('watchlist.diaryConfidence', { n: d.confidence }) }}</span>
            <!-- 历史走势跟踪印章 -->
            <span v-if="diaryOutcomes[d.trade_date]" class="wl-diary__track-badge" :class="'is-' + diaryOutcomes[d.trade_date].status">
              {{ diaryOutcomes[d.trade_date].label }}
            </span>
          </div>
          <!-- AI 18维评分卡 -->
          <div v-if="d.final_score != null" class="wl-scorecard">
            <div class="wl-scorecard__summary" @click="toggleScorecard(d.trade_date)">
              <div class="wl-scorecard__left">
                <span class="wl-scorecard__badge">
                  <span class="wl-scorecard__val">{{ d.final_score.toFixed(1) }}</span>
                  <span class="wl-scorecard__lbl">{{ t('watchlist.scoreFinal') }}</span>
                </span>
                <span v-if="d.rank" class="wl-scorecard__rank">#{{ d.rank }}</span>
                <div class="wl-scorecard__dim-chips" v-if="parseDiaryDims(d)">
                  <span class="wl-dim-chip" :title="`技术面均分: ${parseDiaryDims(d)!.tech.toFixed(0)}`">
                    技术 {{ parseDiaryDims(d)!.tech.toFixed(0) }}
                  </span>
                  <span class="wl-dim-chip" :title="`资金面均分: ${parseDiaryDims(d)!.fund.toFixed(0)}`">
                    资金 {{ parseDiaryDims(d)!.fund.toFixed(0) }}
                  </span>
                  <span class="wl-dim-chip" :title="`基本面均分: ${parseDiaryDims(d)!.base.toFixed(0)}`">
                    基本面 {{ parseDiaryDims(d)!.base.toFixed(0) }}
                  </span>
                  <span class="wl-dim-chip" :title="`宏观质地均分: ${parseDiaryDims(d)!.macro.toFixed(0)}`">
                    宏观 {{ parseDiaryDims(d)!.macro.toFixed(0) }}
                  </span>
                </div>
              </div>
              <button type="button" class="wl-scorecard__toggle-btn" :title="t('watchlist.toggleDimensions')">
                <span>{{ expandedScorecards.has(d.trade_date) ? t('watchlist.collapseDimensions') : t('watchlist.expandDimensions') }}</span>
                <t-icon :name="expandedScorecards.has(d.trade_date) ? 'chevron-up' : 'chevron-down'" size="12px" />
              </button>
            </div>

            <!-- 展开的 18 题问答得分细目 -->
            <div v-if="expandedScorecards.has(d.trade_date) && parseScoresJson(d.scores).length" class="wl-scorecard__details">
              <div
                v-for="item in parseScoresJson(d.scores)"
                :key="item.key"
                class="wl-scorecard__q-row"
              >
                <div class="wl-scorecard__q-head">
                  <span class="q-tag">{{ item.category }}</span>
                  <span class="q-name">{{ item.name }}</span>
                  <span class="q-score" :class="qScoreClass(item.score)">{{ item.score }}分</span>
                </div>
                <div v-if="item.reason" class="wl-scorecard__q-reason">{{ item.reason }}</div>
              </div>
            </div>
          </div>
          <p class="wl-diary__body">{{ d.body }}</p>
          <p v-if="d.model_id" class="wl-diary__model">{{ t('watchlist.diaryBy', { model: d.model_id }) }}</p>

          <!--
            采纳 / 忽略。状态只在这里改，且只在用户点了之后改 ——
            模型的结论永远停在 pending_verdict 上，这正是设计文档第 1 节的立场。
            「忽略」也调用后端：它是唯一能回答「模型是不是一直看错」的证据。
          -->
          <div class="wl-diary__actions">
            <t-button
              v-if="acceptState(d)"
              size="small"
              theme="primary"
              variant="outline"
              :loading="acting === d.trade_date + ':accept'"
              @click="accept(d)"
            >
              {{ t('watchlist.diaryAccept') }} → {{ stateLabel(acceptState(d)!) }}
            </t-button>
            <t-button
              size="small"
              variant="text"
              :loading="acting === d.trade_date + ':ignore'"
              @click="ignore(d)"
            >
              {{ t('watchlist.diaryIgnore') }}
            </t-button>
            <t-button
              size="small"
              theme="default"
              variant="outline"
              @click="askAiAboutDiary(d)"
            >
              <template #icon><t-icon name="chat" /></template>
              {{ t('watchlist.diaryAskAI') }}
            </t-button>
            <t-button
              size="small"
              variant="text"
              @click="$emit('open-conditions')"
            >
              <template #icon><t-icon name="notification" /></template>
              {{ t('watchlist.cond') }}
            </t-button>
          </div>
        </li>
      </ul>
    </section>
  </aside>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { MessagePlugin } from 'tdesign-vue-next'
import WatchKLineChart from './WatchKLineChart.vue'
import {
  acceptDiary,
  ignoreDiary,
  listDiaries,
  DIARY_VERDICT_TARGET,
  type DiaryVerdict,
  type Quote,
  type WatchCondition,
  type WatchDiary,
  type WatchState,
} from '@/finance/api/watchlist'
import { getTradeTarget, saveTradeTarget, type TradeTarget } from '@/finance/utils/tradeTargets'
import { computeDiaryOutcomes } from '@/finance/utils/diaryBacktest'
import type { KLineData } from '@/finance/components/kline/types'

const props = defineProps<{
  thscode: string
  name?: string
  quote?: Quote
  conditions?: WatchCondition[]
  panelWidth?: number
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-workspace'): void
  (e: 'open-conditions'): void
  (e: 'open-halo'): void
}>()

const router = useRouter()

const { t } = useI18n()

const diaries = ref<WatchDiary[]>([])
const diaryLoading = ref(false)
/** 正在提交的那一篇（`日期:动作`），用来只禁用对应的那两个按钮。 */
const acting = ref('')

// ── 持仓操盘成本与防守止损 ──────────────────────────────────────────
const tradeTarget = ref<TradeTarget | null>(null)
const editingTarget = ref(false)
const editCost = ref<number | undefined>(undefined)
const editStop = ref<number | undefined>(undefined)
const editShares = ref<number | undefined>(undefined)

function refreshTradeTarget() {
  tradeTarget.value = getTradeTarget(props.thscode)
  editCost.value = tradeTarget.value?.cost
  editStop.value = tradeTarget.value?.stopLoss
  editShares.value = tradeTarget.value?.shares
  editingTarget.value = false
}

function startEditTarget() {
  editCost.value = tradeTarget.value?.cost
  editStop.value = tradeTarget.value?.stopLoss
  editShares.value = tradeTarget.value?.shares
  editingTarget.value = true
}

function handleSaveTarget() {
  const tVal: TradeTarget = {
    cost: editCost.value && editCost.value > 0 ? editCost.value : undefined,
    stopLoss: editStop.value && editStop.value > 0 ? editStop.value : undefined,
    shares: editShares.value && editShares.value > 0 ? Math.floor(editShares.value) : undefined,
  }
  saveTradeTarget(props.thscode, tVal)
  tradeTarget.value = tVal
  editingTarget.value = false
  MessagePlugin.success(t('watchlist.targetSaved'))
}

function handleClearTarget() {
  saveTradeTarget(props.thscode, null)
  tradeTarget.value = null
  editCost.value = undefined
  editStop.value = undefined
  editShares.value = undefined
  editingTarget.value = false
  MessagePlugin.success(t('watchlist.targetCleared'))
}

const costPnlText = computed(() => {
  if (!tradeTarget.value?.cost || !props.quote?.close) return ''
  const diff = ((props.quote.close - tradeTarget.value.cost) / tradeTarget.value.cost) * 100
  return `${diff >= 0 ? '+' : ''}${diff.toFixed(2)}%`
})

const costPnlClass = computed(() => {
  if (!tradeTarget.value?.cost || !props.quote?.close) return ''
  return props.quote.close >= tradeTarget.value.cost ? 'is-up' : 'is-down'
})

const stopLossPnlText = computed(() => {
  if (!tradeTarget.value?.stopLoss || !props.quote?.close) return ''
  const diff = ((tradeTarget.value.stopLoss - props.quote.close) / props.quote.close) * 100
  return `${diff >= 0 ? '+' : ''}${diff.toFixed(2)}%`
})

// ── 历史真实 K 线走势复盘与胜率回测 ─────────────────────────────────
const currentBars = ref<KLineData[]>([])
function onBarsLoaded(bars: KLineData[]) {
  currentBars.value = bars
}
const targetMarketValueText = computed(() => {
  if (!tradeTarget.value?.shares || !props.quote?.close) return '—'
  return (tradeTarget.value.shares * props.quote.close).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
})

const targetPnlAmountText = computed(() => {
  if (!tradeTarget.value?.shares || !tradeTarget.value?.cost || !props.quote?.close) return '—'
  const diff = (props.quote.close - tradeTarget.value.cost) * tradeTarget.value.shares
  return `${diff >= 0 ? '+' : ''}${diff.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
})

const resonanceResult = computed(() => {
  const bars = currentBars.value
  if (!bars || bars.length < 20) return null
  const closes = bars.map(b => b.close).filter((c): c is number => typeof c === 'number' && Number.isFinite(c))
  if (closes.length < 20) return null
  const latestClose = closes[closes.length - 1]

  const avg = (n: number) => {
    const slice = closes.slice(-n)
    return slice.reduce((a, b) => a + b, 0) / slice.length
  }

  // 1. 日线 MA20
  const ma20 = avg(20)
  const daily = latestClose >= ma20

  // 2. 周线 MA5 ≈ 25 根日线均价 (5 周)
  const ma25 = closes.length >= 25 ? avg(25) : ma20
  const weekly = latestClose >= ma25

  // 3. 月线 MA5 ≈ 100 根日线均价 (5 个月)
  const ma100 = closes.length >= 100 ? avg(100) : (closes.length >= 60 ? avg(60) : ma25)
  const monthly = latestClose >= ma100

  if (monthly && weekly && daily) {
    return {
      daily, weekly, monthly,
      label: '三红共振 · 强顺势',
      themeClass: 'is-bull-all',
      desc: '日、周、月三级别均线呈多头共振，大势与短线均处于主升浪区间。',
    }
  }
  if (monthly && weekly && !daily) {
    return {
      daily, weekly, monthly,
      label: '大顺小逆 · 回踩买点',
      themeClass: 'is-bull-pullback',
      desc: '月线与周线趋势保持向上，日线出现短期技术回踩，回抽均线观察试仓机会。',
    }
  }
  if (!monthly && !weekly && daily) {
    return {
      daily, weekly, monthly,
      label: '超跌反弹 · 逆势防守',
      themeClass: 'is-bear-bounce',
      desc: '中长级别仍受均线压制，日线出现短期超跌反弹脉冲，注意高抛止盈防守。',
    }
  }
  if (!monthly && !weekly && !daily) {
    return {
      daily, weekly, monthly,
      label: '三绿空头 · 破位规避',
      themeClass: 'is-bear-all',
      desc: '日、周、月三级别均线破位下行，空头排列，建议严控仓位、谨慎观望。',
    }
  }
  return {
    daily, weekly, monthly,
    label: daily ? '短线转多 · 观察持续' : '中短分歧 · 震荡整理',
    themeClass: daily ? 'is-mixed-up' : 'is-mixed-down',
    desc: '多空均线出现交叉分歧，处于震荡筑底或整理蓄势阶段。',
  }
})

const backtestData = computed(() => computeDiaryOutcomes(diaries.value, currentBars.value))
const diaryOutcomes = computed(() => backtestData.value.outcomes)
const backtestSummary = computed(() => backtestData.value.summary)

const displayName = computed(() => props.name || props.thscode)

function num(v: number | null | undefined): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function changeClass(): string {
  const pct = num(props.quote?.change_pct)
  if (pct === null) return ''
  return pct >= 0 ? 'is-up' : 'is-down'
}

function signed(v: number | null | undefined): string {
  const n = num(v)
  if (n === null) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}`
}

function pct(v: number | null | undefined): string {
  const n = num(v)
  return n === null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}

function verdictLabel(v: DiaryVerdict): string {
  const key = `watchlist.verdict${v.charAt(0).toUpperCase()}${v.slice(1)}`
  return t(key)
}

function stateLabel(state: WatchState): string {
  switch (state) {
    case 'observing': return t('watchlist.stateObserving')
    case 'triggered': return t('watchlist.stateTriggered')
    case 'holding': return t('watchlist.stateHolding')
    case 'dropped': return t('watchlist.stateDropped')
    default: return state
  }
}

/** 这条建议指向哪个状态。'none' 返回 null，即「不提供采纳按钮」。 */
function targetState(v: DiaryVerdict): WatchState | '' {
  return DIARY_VERDICT_TARGET[v] ?? ''
}

/**
 * 模板里真正要用的那个：null 而不是 ''。
 *
 * v-if 与 t() 的类型检查都认不得空串（"" 在 TS 里是 falsy 但不是 null），
 * 在模板里写 `v-if="targetState(...)"` 就会让 stateLabel 收到 `"" | WatchState`
 * 而报错。收敛到一个函数里，模板就不用再关心这个区别。
 */
function acceptState(d: WatchDiary): WatchState | null {
  return targetState(d.verdict) || null
}

async function loadDiaries() {
  if (!props.thscode) return
  diaryLoading.value = true
  try {
    const res = await listDiaries(props.thscode)
    diaries.value = res.data || []
  } catch (e: any) {
    // 读不到就显示成空列表并如实报错，不静默：日记读不出来和「还没写日记」
    // 是两件事，混起来用户会去错的地方查。
    diaries.value = []
    MessagePlugin.error(e?.message || t('watchlist.loadFailed'))
  } finally {
    diaryLoading.value = false
  }
}

async function accept(d: WatchDiary) {
  const to = targetState(d.verdict)
  if (!to) return
  acting.value = `${d.trade_date}:accept`
  try {
    await acceptDiary(d.thscode || props.thscode, d.trade_date, to)
    MessagePlugin.success(t('watchlist.diaryAccepted'))
    // 状态变了，父组件需要重新拉列表。
    emit('close')
  } catch (e: any) {
    MessagePlugin.error(e?.message || t('watchlist.loadFailed'))
  } finally {
    acting.value = ''
  }
}

async function ignore(d: WatchDiary) {
  acting.value = `${d.trade_date}:ignore`
  try {
    await ignoreDiary(d.thscode || props.thscode, d.trade_date)
    MessagePlugin.success(t('watchlist.diaryIgnored'))
  } catch (e: any) {
    MessagePlugin.error(e?.message || t('watchlist.loadFailed'))
  } finally {
    acting.value = ''
  }
}

function askAiAboutDiary(d: WatchDiary) {
  const vLabel = verdictLabel(d.verdict)
  let costContext = ''
  if (tradeTarget.value?.cost && props.quote?.close) {
    const pnl = (((props.quote.close - tradeTarget.value.cost) / tradeTarget.value.cost) * 100).toFixed(2)
    costContext = `\n【我的持仓操盘基准】\n` +
      `- 持仓成本价：¥${tradeTarget.value.cost.toFixed(2)}\n` +
      `- 最新市价：¥${props.quote.close.toFixed(2)} (浮动盈亏: ${Number(pnl) >= 0 ? '+' : ''}${pnl}%)\n` +
      `- 防守止损线：${tradeTarget.value.stopLoss ? `¥${tradeTarget.value.stopLoss.toFixed(2)}` : '未设置'}\n` +
      `请务必结合我的持仓成本与盈亏比，评估是否应收紧止损至保本线、部分止盈或继续持股。\n`
  }

  const prompt = `请帮我针对关注的标的【${displayName.value} (${props.thscode})】展开深度分析与决策推演：\n\n` +
    `【系统观察日记】(行情日: ${d.trade_date})\n` +
    `- 模型建议：${vLabel} (把握度: ${d.confidence}/5)\n` +
    `- 核心依据：${d.reasons || d.body}\n` +
    `- 观察日记全文：\n${d.body}\n` +
    costContext + '\n' +
    `请结合近期技术形态、主力资金动向、板块大盘环境及关键均线支撑位，帮我做进一步复盘，并给出具体的操作应对策略。`
  router.push({
    path: '/platform/creatChat',
    query: { q: prompt },
  })
}

onMounted(() => {
  loadDiaries()
  refreshTradeTarget()
})
// 换一只票就换一份日记与操盘位。
watch(() => props.thscode, () => {
  loadDiaries()
  refreshTradeTarget()
})
const expandedScorecards = ref<Set<string>>(new Set())
function toggleScorecard(tradeDate: string) {
  if (expandedScorecards.value.has(tradeDate)) {
    expandedScorecards.value.delete(tradeDate)
  } else {
    expandedScorecards.value.add(tradeDate)
  }
}

interface ParsedQItem {
  key: string
  category: string
  name: string
  score: number
  reason: string
}

const Q_CONFIG: Record<string, { category: string; name: string }> = {
  q1: { category: '技术', name: '趋势强度' },
  q2: { category: '技术', name: '短期动量' },
  q3: { category: '技术', name: '量能配合' },
  q4: { category: '技术', name: '技术形态' },
  q5: { category: '技术', name: '趋势稳定' },
  q6: { category: '技术', name: '量价协调' },
  q7: { category: '技术', name: '均线支撑' },
  q8: { category: '技术', name: '技术综合' },
  q9: { category: '资金', name: '资金方向' },
  q10: { category: '资金', name: '量价关系' },
  q11: { category: '资金', name: '交易活跃' },
  q12: { category: '资金', name: '资金综合' },
  q13: { category: '基本面', name: '入池理由' },
  q14: { category: '基本面', name: '公司质地' },
  q15: { category: '基本面', name: '观察变化' },
  q16: { category: '宏观', name: '行业风险' },
  q17: { category: '宏观', name: '护城河' },
  q18: { category: '宏观', name: '估值水平' },
}

function parseDiaryDims(d: WatchDiary): { tech: number; fund: number; base: number; macro: number } | null {
  if (!d.scores) return null
  try {
    const parsed = typeof d.scores === 'string' ? JSON.parse(d.scores) : d.scores
    const s = parsed?.scores || parsed
    if (!s || typeof s !== 'object') return null
    const avg = (keys: string[]) => {
      let sum = 0, count = 0
      for (const k of keys) {
        if (typeof s[k] === 'number') { sum += s[k]; count++ }
      }
      return count > 0 ? sum / count : 0
    }
    return {
      tech: avg(['q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q7', 'q8']),
      fund: avg(['q9', 'q10', 'q11', 'q12']),
      base: avg(['q13', 'q14', 'q15']),
      macro: avg(['q16', 'q17', 'q18']),
    }
  } catch {
    return null
  }
}

function parseScoresJson(raw: string | undefined): ParsedQItem[] {
  if (!raw) return []
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    const s = parsed?.scores || parsed
    const r = parsed?.reasons || {}
    if (!s || typeof s !== 'object') return []
    const out: ParsedQItem[] = []
    for (let i = 1; i <= 18; i++) {
      const k = `q${i}`
      if (s[k] !== undefined) {
        const conf = Q_CONFIG[k] || { category: '其他', name: k.toUpperCase() }
        out.push({
          key: k,
          category: conf.category,
          name: conf.name,
          score: Number(s[k]) || 0,
          reason: typeof r[k] === 'string' ? r[k] : '',
        })
      }
    }
    return out
  } catch {
    return []
  }
}

function qScoreClass(score: number): string {
  if (score >= 70) return 'is-high'
  if (score <= 45) return 'is-low'
  return 'is-mid'
}

</script>

<style lang="less" scoped>
.wl-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
  flex: none;
  border-left: 1px solid var(--td-component-stroke);
  padding-left: 12px;
  overflow-y: auto;
  min-width: 360px;
}

.wl-detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.wl-detail__title { display: flex; align-items: baseline; gap: 8px; }
.wl-detail__name { font-size: var(--app-text-lg); font-weight: 600; }
.wl-detail__code { font-size: var(--app-text-sm); opacity: 0.6; }

.wl-detail__close {
  border: none; background: transparent; cursor: pointer;
  color: inherit; opacity: 0.6; padding: 2px;
}
.wl-detail__close:hover { opacity: 1; }

.wl-detail__quote { display: flex; align-items: baseline; gap: 10px; font-size: var(--app-text-md); }
.wl-detail__price { font-size: var(--app-text-2xl); font-weight: 600; }
.wl-detail__date { margin-left: auto; opacity: 0.6; font-size: var(--app-text-sm); }

/*
 * A 股红涨绿跌。
 *
 * 之前这里是 `var(--wl-up)` —— 但 `--wl-up` **谁都没定义过**
 * （注释说"见 Watchlist.vue 的 --wl-up/--wl-down"，而那个文件里并没有），
 * 所以永远走 fallback 硬编码。后果有两个：
 *   1. 深色模式下暗红 #dc2626 配深背景基本看不见（这个面板深色适配原本是 0 处）
 *   2. 想改涨跌色要改 6 个 var() 里的 6 个字面量
 *
 * 现在在根类上定义变量，深色模式整体提亮一档。
 * 浅色与 MarketDashboard.vue 的 --md-up/--md-down 取同一对值，两处一致。
 */
.wl-detail {
  --wl-up: #dc2626;
  --wl-down: #047857;
}

:global(:root[theme-mode='dark']) .wl-detail {
  --wl-up: #f87171;
  --wl-down: #34d399;
}

.is-up { color: var(--wl-up); }
.is-down { color: var(--wl-down); }

.wl-detail__section { display: flex; flex-direction: column; gap: 6px; }
.wl-detail__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.wl-detail__ws-btn {
  font-size: var(--app-text-xs);
  padding: 0 4px;
}
/* HALO 入口比「全功能工作台」更值得一个完整按钮：它是这份面板里唯一一个
   「打开一份独立分析」的出口，而工作台按钮只是放大当前视图。 */
.wl-detail__halo {
  padding: 8px 10px;
  border: 1px solid var(--td-border-level-2-color);
  border-radius: var(--app-radius-sm);
  background: var(--td-bg-color-secondarycontainer);
}
.wl-detail__halo-btn { font-size: var(--app-text-xs); flex: none; }
.wl-detail__section--diary { flex: 1; }
.wl-detail__h3 { margin: 0; font-size: var(--app-text-md); font-weight: 600; opacity: 0.75; }
.wl-detail__hint { font-size: var(--app-text-sm); opacity: 0.6; margin: 0; }

.wl-detail__empty { padding: 12px 0; }
.wl-detail__empty-title { margin: 0 0 4px; font-size: var(--app-text-md); }
.wl-detail__empty-hint { margin: 0; font-size: var(--app-text-sm); opacity: 0.6; line-height: 1.6; }

.wl-diary { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.wl-diary__item {
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-sm); padding: 8px 10px;
}
.wl-diary__head { display: flex; align-items: center; gap: 8px; font-size: var(--app-text-sm); }
.wl-diary__date { opacity: 0.6; }
.wl-diary__conf { margin-left: auto; opacity: 0.55; }

.wl-diary__verdict {
  font-size: var(--app-text-sm); padding: 1px 6px; border-radius: var(--app-radius-xs);
  background: rgba(0, 0, 0, 0.05); font-weight: 600;
}
/* 建议买入偏红、建议移除偏绿，与 A 股涨跌色相反是有意的：
   这里说的是「该做什么」而不是「今天涨没涨」。 */
.wl-diary__verdict.is-buy, .wl-diary__verdict.is-keep { color: var(--wl-up); }
.wl-diary__verdict.is-sell, .wl-diary__verdict.is-exit { color: var(--wl-down); }
.wl-diary__verdict.is-tighten { color: #b45309; }
.wl-diary__verdict.is-hold, .wl-diary__verdict.is-none { color: #475569; }

.wl-diary__body { margin: 6px 0 0; font-size: var(--app-text-md); line-height: 1.65; white-space: pre-wrap; }
.wl-diary__actions { display: flex; gap: 8px; margin-top: 8px; }

.wl-targets-card {
  padding: 8px 10px;
  border: 1px dashed var(--td-border-level-2-color);
  border-radius: var(--app-radius-sm);
  background: var(--td-bg-color-secondarycontainer);
}

.wl-targets-card__view {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.wl-targets-card__info {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: var(--app-text-xs);
}

.wl-targets-item {
  display: flex;
  align-items: center;
  gap: 6px;

  &__k {
    color: var(--td-text-color-secondary);
  }

  &__v {
    font-weight: 600;
    font-family: monospace;
    color: var(--td-brand-color);

    &.is-stop {
      color: var(--td-error-color);
    }
  }

  &__empty {
    color: var(--td-text-color-placeholder);
  }
}

.wl-targets-card__edit-btn {
  font-size: var(--app-text-xs);
  padding: 0 4px;
}

.wl-targets-card__form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.wl-targets-form__row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--app-text-xs);
}

.wl-targets-form__lbl {
  color: var(--td-text-color-secondary);
  flex-shrink: 0;
}

.wl-targets-form__input {
  width: 90px;
}

.wl-targets-form__actions {
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: flex-end;
}

.wl-diary__stats-badge {
  font-size: var(--app-text-xs);
  padding: 2px 8px;
  border-radius: var(--app-radius-pill);
  background: color-mix(in srgb, var(--td-brand-color) 12%, transparent);
  color: var(--td-brand-color);
  font-weight: 500;
  white-space: nowrap;
}

.wl-diary__track-badge {
  font-size: var(--app-text-xs);
  padding: 1px 6px;
  border-radius: var(--app-radius-xs);
  font-family: monospace;
  font-weight: 600;
  margin-left: 4px;

  &.is-win {
    background: color-mix(in srgb, var(--td-brand-color) 14%, transparent);
    color: var(--td-brand-color);
    border: 1px solid color-mix(in srgb, var(--td-brand-color) 35%, transparent);
  }

  &.is-loss {
    background: color-mix(in srgb, var(--td-error-color) 14%, transparent);
    color: var(--td-error-color);
    border: 1px solid color-mix(in srgb, var(--td-error-color) 35%, transparent);
  }

  &.is-neutral {
    background: var(--td-bg-color-component);
    color: var(--td-text-color-secondary);
  }
}
.wl-scorecard {
  margin: 6px 0;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-sm, 6px);
  background: var(--td-bg-color-secondarycontainer, #f9f9f9);
  padding: 8px 10px;

  &__summary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    cursor: pointer;
    user-select: none;
  }

  &__left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  &__badge {
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 4px;
    background: color-mix(in srgb, var(--td-brand-color) 12%, transparent);
    color: var(--td-brand-color);
  }

  &__val {
    font-size: 14px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }

  &__lbl {
    font-size: 11px;
    opacity: 0.85;
  }

  &__rank {
    font-size: 12px;
    font-weight: 600;
    color: var(--td-text-color-secondary, #666);
    background: var(--td-bg-color-container, #fff);
    border: 1px solid var(--td-component-stroke);
    padding: 1px 6px;
    border-radius: 3px;
  }

  &__dim-chips {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-wrap: wrap;
  }

  &__toggle-btn {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    border: none;
    background: transparent;
    cursor: pointer;
    font-size: 11px;
    color: var(--td-brand-color);
    padding: 2px 4px;

    &:hover {
      text-decoration: underline;
    }
  }

  &__details {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px dashed var(--td-component-stroke);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  &__q-row {
    font-size: 12px;
    padding: 4px 6px;
    background: var(--td-bg-color-container, #fff);
    border-radius: 4px;
    border: 1px solid var(--td-border-level-1-color, #f0f0f0);
  }

  &__q-head {
    display: flex;
    align-items: center;
    gap: 6px;

    .q-tag {
      font-size: 10px;
      padding: 1px 4px;
      border-radius: 2px;
      background: var(--td-bg-color-secondarycontainer, #f3f3f3);
      color: var(--td-text-color-secondary, #666);
    }

    .q-name {
      font-weight: 500;
      color: var(--td-text-color-primary, #333);
    }

    .q-score {
      margin-left: auto;
      font-weight: 600;
      font-variant-numeric: tabular-nums;

      &.is-high { color: var(--wl-up); }
      &.is-low { color: var(--wl-down); }
      &.is-mid { color: var(--td-brand-color); }
    }
  }

  &__q-reason {
    margin-top: 2px;
    color: var(--td-text-color-secondary, #666);
    font-size: 11px;
    line-height: 1.4;
  }
}

.wl-dim-chip {
  font-size: 11px;
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--td-bg-color-container, #fff);
  border: 1px solid var(--td-component-stroke);
  color: var(--td-text-color-secondary, #666);
}
.wl-resonance-card {
  padding: 8px 10px;
  border-radius: var(--app-radius-sm);
  background: var(--td-bg-color-secondarycontainer);
  border: 1px solid var(--td-component-stroke);
  margin-bottom: 6px;

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
  }

  &__title {
    font-size: var(--app-text-xs);
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 4px;
    color: var(--td-text-color-secondary);
  }

  &__badge {
    font-size: 11px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 3px;
  }

  &.is-bull-all .wl-resonance-card__badge {
    background: rgba(220, 38, 38, 0.12);
    color: var(--wl-up);
  }
  &.is-bull-pullback .wl-resonance-card__badge {
    background: rgba(0, 82, 217, 0.12);
    color: var(--td-brand-color);
  }
  &.is-bear-bounce .wl-resonance-card__badge {
    background: rgba(237, 123, 47, 0.12);
    color: #ed7b2f;
  }
  &.is-bear-all .wl-resonance-card__badge {
    background: rgba(4, 120, 87, 0.12);
    color: var(--wl-down);
  }
  &.is-mixed-up .wl-resonance-card__badge {
    background: rgba(0, 82, 217, 0.08);
    color: var(--td-brand-color);
  }
  &.is-mixed-down .wl-resonance-card__badge {
    background: rgba(100, 116, 139, 0.12);
    color: var(--td-text-color-secondary);
  }

  &__matrix {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 6px;
    margin-bottom: 6px;
  }

  .matrix-item {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 3px 6px;
    border-radius: 4px;
    background: var(--td-bg-color-container);
    border: 1px solid var(--td-border-level-1-color);
    font-size: 11px;

    .matrix-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
    }

    &.is-bull {
      .matrix-dot { background: var(--wl-up); }
      .matrix-status { color: var(--wl-up); font-weight: 600; margin-left: auto; }
    }
    &.is-bear {
      .matrix-dot { background: var(--wl-down); }
      .matrix-status { color: var(--wl-down); font-weight: 600; margin-left: auto; }
    }
  }

  &__desc {
    margin: 0;
    font-size: 11px;
    color: var(--td-text-color-secondary);
    line-height: 1.4;
  }
}
</style>
