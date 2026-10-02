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

    <!-- K 线。选行即看图是本面板存在的理由，所以它常驻在日记上面。 -->
    <section class="wl-detail__section">
      <h3 class="wl-detail__h3">{{ t('watchlist.detailChart') }}</h3>
      <WatchKLineChart :thscode="thscode" :name="displayName" />
    </section>

    <section class="wl-detail__section wl-detail__section--diary">
      <h3 class="wl-detail__h3">{{ t('watchlist.detailDiary') }}</h3>

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
          </div>
        </li>
      </ul>
    </section>
  </aside>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
  type WatchDiary,
  type WatchState,
} from '@/api/watchlist'

const props = defineProps<{
  thscode: string
  name?: string
  quote?: Quote
  panelWidth?: number
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const { t } = useI18n()

const diaries = ref<WatchDiary[]>([])
const diaryLoading = ref(false)
/** 正在提交的那一篇（`日期:动作`），用来只禁用对应的那两个按钮。 */
const acting = ref('')

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

onMounted(loadDiaries)
// 换一只票就换一份日记。不加 immediate 是因为 onMounted 已经拉过。
watch(() => props.thscode, loadDiaries)
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
.wl-diary__model { margin: 4px 0 0; font-size: var(--app-text-2xs); opacity: 0.5; }
.wl-diary__actions { display: flex; gap: 8px; margin-top: 8px; }
</style>
