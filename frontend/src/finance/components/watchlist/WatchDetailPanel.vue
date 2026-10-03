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
            <span v-else class="wl-targets-item__empty">未设置</span>
          </div>
          <div class="wl-targets-item">
            <span class="wl-targets-item__k">{{ t('watchlist.targetStop') }}:</span>
            <span v-if="tradeTarget?.stopLoss" class="wl-targets-item__v is-stop">
              ¥{{ tradeTarget.stopLoss.toFixed(2) }} ({{ stopLossPnlText }})
            </span>
            <span v-else class="wl-targets-item__empty">未设置</span>
          </div>
        </div>
        <t-button size="small" variant="text" theme="primary" class="wl-targets-card__edit-btn" @click="startEditTarget">
          <template #icon><t-icon name="edit" /></template>
          {{ tradeTarget?.cost || tradeTarget?.stopLoss ? t('common.edit') || '修改' : t('watchlist.setTarget') }}
        </t-button>
      </div>

      <div v-else class="wl-targets-card__form">
        <div class="wl-targets-form__row">
          <span class="wl-targets-form__lbl">成本:</span>
          <t-input-number
            v-model="editCost"
            :decimal-places="2"
            :min="0"
            :step="0.1"
            size="small"
            placeholder="成本价"
            class="wl-targets-form__input"
          />
          <span class="wl-targets-form__lbl">止损:</span>
          <t-input-number
            v-model="editStop"
            :decimal-places="2"
            :min="0"
            :step="0.1"
            size="small"
            placeholder="止损线"
            class="wl-targets-form__input"
          />
        </div>
        <div class="wl-targets-form__actions">
          <t-button size="small" theme="primary" @click="handleSaveTarget">{{ t('common.save') || '保存' }}</t-button>
          <t-button size="small" variant="text" @click="handleClearTarget">{{ t('common.clear') || '清空' }}</t-button>
          <t-button size="small" variant="text" @click="editingTarget = false">{{ t('common.cancel') || '取消' }}</t-button>
        </div>
      </div>
    </div>

    <!-- K 线。选行即看图是本面板存在的理由，所以它常驻在日记上面。 -->
    <section class="wl-detail__section">
      <div class="wl-detail__section-head">
        <h3 class="wl-detail__h3">{{ t('watchlist.detailChart') }}</h3>
        <t-button size="small" variant="text" theme="primary" class="wl-detail__ws-btn" @click="$emit('open-workspace')">
          <template #icon><t-icon name="fullscreen" /></template>
          {{ t('watchlist.fullWorkspace') }}
        </t-button>
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
        <span v-if="backtestSummary.totalBuys > 0" class="wl-diary__stats-badge" title="基于过去真实K线复盘：买点后5日最高涨幅达标率">
          买点胜率 {{ backtestSummary.winRate }}% ({{ backtestSummary.wins }}/{{ backtestSummary.totalBuys }}) · 冲高+{{ backtestSummary.avgMaxGain }}%
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

function refreshTradeTarget() {
  tradeTarget.value = getTradeTarget(props.thscode)
  editCost.value = tradeTarget.value?.cost
  editStop.value = tradeTarget.value?.stopLoss
  editingTarget.value = false
}

function startEditTarget() {
  editCost.value = tradeTarget.value?.cost
  editStop.value = tradeTarget.value?.stopLoss
  editingTarget.value = true
}

function handleSaveTarget() {
  const tVal: TradeTarget = {
    cost: editCost.value && editCost.value > 0 ? editCost.value : undefined,
    stopLoss: editStop.value && editStop.value > 0 ? editStop.value : undefined,
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
</script>

<style lang="less" scoped>
.wl-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
  flex: none;
  border-left: 1px solid var(--td-component-stroke-color);
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

/* A 股红涨绿跌，与表格里同一对色值（见 Watchlist.vue 的 --wl-up/--wl-down）。 */
.is-up { color: var(--wl-up, #dc2626); }
.is-down { color: var(--wl-down, #047857); }

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
.wl-detail__section--diary { flex: 1; }
.wl-detail__h3 { margin: 0; font-size: var(--app-text-md); font-weight: 600; opacity: 0.75; }
.wl-detail__hint { font-size: var(--app-text-sm); opacity: 0.6; margin: 0; }

.wl-detail__empty { padding: 12px 0; }
.wl-detail__empty-title { margin: 0 0 4px; font-size: var(--app-text-md); }
.wl-detail__empty-hint { margin: 0; font-size: var(--app-text-sm); opacity: 0.6; line-height: 1.6; }

.wl-diary { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.wl-diary__item {
  border: 1px solid var(--td-component-stroke-color);
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
.wl-diary__verdict.is-buy, .wl-diary__verdict.is-keep { color: #dc2626; }
.wl-diary__verdict.is-sell, .wl-diary__verdict.is-exit { color: #047857; }
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
</style>
