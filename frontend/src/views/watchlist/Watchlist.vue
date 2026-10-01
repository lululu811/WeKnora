<template>
  <main class="watchlist-page">
    <header class="watchlist-header" style="--wails-draggable: drag">
      <div class="watchlist-title-row" style="--wails-draggable: drag">
        <h2 style="--wails-draggable: drag">
          <t-icon name="chart-line" size="24px" />
          {{ t('watchlist.title') }}
        </h2>
        <div class="watchlist-actions" style="--wails-draggable: no-drag">
          <span v-if="lastUpdated" class="watchlist-updated">
            {{ t('watchlist.updatedAt', { time: lastUpdated }) }}
          </span>
          <t-button variant="text" theme="default" size="small" :loading="quotesLoading"
            @click="refreshQuotes()">
            <template #icon><t-icon name="refresh" /></template>
            {{ t('watchlist.refresh') }}
          </t-button>
        </div>
      </div>
      <p class="watchlist-subtitle" style="--wails-draggable: drag">{{ t('watchlist.subtitle') }}</p>
    </header>

    <!-- 添加：输入代码或名称片段 → 联想 → 选中即加入。回车在有候选时直接取第一条，
         因为输入框里已经是「600519」这种可判定的前缀时再点一次纯属多余。 -->
    <div class="watchlist-add">
      <div class="watchlist-add__row">
        <t-input v-model="keyword" class="watchlist-add__input" :placeholder="t('watchlist.addPlaceholder')"
          clearable @enter="handleEnter" @change="handleKeywordChange" @focus="handleKeywordChange" />
        <t-button theme="primary" :disabled="!keyword.trim()" @click="handleEnter">
          {{ t('watchlist.add') }}
        </t-button>
      </div>
      <!-- mousedown.prevent：否则 input 先失焦触发联想收起，点击永远落空 -->
      <ul v-if="suggestions.length" class="watchlist-suggest">
        <li v-for="s in suggestions" :key="s.thscode" class="watchlist-suggest__item"
          @mousedown.prevent="addSymbol(s)">
          <span class="watchlist-suggest__code">{{ s.thscode }}</span>
          <span class="watchlist-suggest__name">{{ s.name }}</span>
        </li>
      </ul>
    </div>

    <!-- 查不到的标的是**显式告知**而不是偷偷少一行：本地库里没有它的行情，
         但用户确实加过它，静默消失会让人以为自己的操作没生效。 -->
    <p v-if="missingCodes.length" class="watchlist-hint">
      {{ t('watchlist.missingHint', { codes: missingCodes.join('、') }) }}
    </p>

    <EmptyState v-if="!loading && !rows.length" icon="chart-line" :title="t('watchlist.empty')"
      :description="t('watchlist.emptyHint')" />

    <t-table v-else row-key="thscode" class="watchlist-table" :data="rows" :columns="columns"
      :loading="loading || quotesLoading" size="medium" hover>
      <template #thscode="{ row }">
        <div class="wl-code">
          <span class="wl-code__code">{{ row.thscode }}</span>
          <span v-if="row.exchange" class="wl-code__exchange">{{ row.exchange }}</span>
        </div>
      </template>

      <template #name="{ row }">
        <span class="wl-name">{{ row.quote?.name || row.name || '—' }}</span>
      </template>

      <!-- 状态徽标本身就是操作入口：点它才展开「合法下一步」。
           不画灰掉的非法项 —— 一个永远点不动的按钮只能教会用户怀疑这个页面。 -->
      <template #state="{ row }">
        <t-dropdown :options="stateOptions(row)" trigger="click" placement="bottom-left" attach="body"
          @click="changeState(row, $event)">
          <button type="button" class="wl-state" :class="`wl-state--${row.state}`"
            :title="t('watchlist.changeState')">
            <span class="wl-state__dot"></span>
            {{ stateLabel(row.state) }}
          </button>
        </t-dropdown>
      </template>

      <!-- 备注就地编辑：点开、回车或失焦即存。放一个常驻输入框会让整张表看起来
           像一份还没填完的表单，也会把"滚动时误触保存"变成常态。 -->
      <template #note="{ row }">
        <div class="wl-note">
          <t-input v-if="editingNote === row.thscode" v-model="noteDraft" class="wl-note__input" size="small"
            :placeholder="t('watchlist.notePlaceholder')" @enter="saveNote(row)" @blur="saveNote(row)" />
          <button v-else type="button" class="wl-note__view" :class="{ 'is-empty': !row.note }"
            :title="t('watchlist.noteEdit')" @click="startEditNote(row)">
            {{ row.note || t('watchlist.notePlaceholder') }}
          </button>
        </div>
      </template>

      <template #close="{ row }">
        <span v-if="num(row.quote?.close) !== null" class="wl-num"
          :class="changeClass(row)">{{ formatPrice(row.quote?.close) }}</span>
        <span v-else class="wl-muted">{{ t('watchlist.noData') }}</span>
      </template>

      <template #change="{ row }">
        <span v-if="num(row.quote?.change_pct) !== null" class="wl-change"
          :class="changeClass(row)">
          {{ formatSigned(row.quote?.change) }}
          <span class="wl-change__pct">{{ formatPct(row.quote?.change_pct) }}</span>
        </span>
        <span v-else class="wl-muted">—</span>
      </template>

      <template #turnover="{ row }">
        <span v-if="num(row.quote?.turnover) !== null" class="wl-num">{{ formatAmount(row.quote?.turnover) }}</span>
        <span v-else class="wl-muted">—</span>
      </template>

      <template #date="{ row }">
        <!-- 停牌/长期无成交的票，最新交易日会明显落后于其余行：这里不隐藏，
             也不把它算成"今天的价格"，只把它标灰并把日期如实写出来。 -->
        <span v-if="row.quote?.date" class="wl-date" :class="{ 'is-stale': isStale(row) }">
          {{ row.quote.date }}
        </span>
        <span v-else class="wl-muted">—</span>
      </template>

      <template #actions="{ row, rowIndex }">
        <div class="wl-actions">
          <t-button variant="text" size="small" :disabled="rowIndex === 0" @click="pinRow(row)">
            {{ t('watchlist.pin') }}
          </t-button>
          <t-button variant="text" theme="danger" size="small" @click="confirmRemove(row)">
            {{ t('watchlist.remove') }}
          </t-button>
        </div>
      </template>
    </t-table>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import EmptyState from '@/components/EmptyState.vue'
import {
  addWatchItem,
  fetchQuotes,
  listWatchlist,
  removeWatchItem,
  searchSymbols,
  updateWatchItem,
  WATCH_STATE_TRANSITIONS,
  type Quote,
  type SymbolSuggestion,
  type WatchItem,
  type WatchState,
} from '@/api/watchlist'

const { t } = useI18n()

/** 行情自动刷新的间隔。日线级别的读数，一分钟一次足够。 */
const REFRESH_INTERVAL_MS = 60_000
/** 输入停止多久后才发搜索请求。 */
const SEARCH_DEBOUNCE_MS = 250

interface WatchRow extends WatchItem {
  quote?: Quote
}

const items = ref<WatchItem[]>([])
const quotes = ref<Record<string, Quote>>({})
const loading = ref(false)
const quotesLoading = ref(false)
const keyword = ref('')
const suggestions = ref<SymbolSuggestion[]>([])
const lastUpdated = ref('')

let searchTimer: ReturnType<typeof setTimeout> | null = null
let refreshTimer: ReturnType<typeof setInterval> | null = null

const rows = computed<WatchRow[]>(() =>
  items.value.map((item) => ({ ...item, quote: quotes.value[item.thscode] })),
)

/** 清单里格式合法、但本地没有行情的代码（服务端 missing）。 */
const missingCodes = computed(() =>
  items.value.filter((item) => !quotes.value[item.thscode]).map((item) => item.thscode),
)

/** 最新交易日：用来判断某一行是不是"停在更早的某天"（停牌）。 */
const newestDate = computed(() => {
  let newest = ''
  for (const quote of Object.values(quotes.value)) {
    if (quote.date && quote.date > newest) newest = quote.date
  }
  return newest
})

const columns = computed(() => [
  { colKey: 'thscode', title: t('watchlist.columns.code'), width: 148 },
  { colKey: 'name', title: t('watchlist.columns.name'), minWidth: 140 },
  { colKey: 'state', title: t('watchlist.columns.state'), width: 112 },
  { colKey: 'note', title: t('watchlist.columns.note'), minWidth: 150 },
  { colKey: 'close', title: t('watchlist.columns.price'), width: 110, align: 'right' as const },
  { colKey: 'change', title: t('watchlist.columns.change'), width: 170, align: 'right' as const },
  { colKey: 'turnover', title: t('watchlist.columns.turnover'), width: 110, align: 'right' as const },
  { colKey: 'date', title: t('watchlist.columns.date'), width: 128, align: 'center' as const },
  { colKey: 'actions', title: t('watchlist.columns.actions'), width: 132, align: 'right' as const },
])

/** 徽标文案。`triggered` 只能被买点触发写入，前端只读不提供入口。 */
function stateLabel(state: string): string {
  switch (state) {
    case 'observing':
      return t('watchlist.stateObserving')
    case 'triggered':
      return t('watchlist.stateTriggered')
    case 'holding':
      return t('watchlist.stateHolding')
    case 'dropped':
      return t('watchlist.stateDropped')
    default:
      // 认不出的值原样显示：编一个漂亮的名字会让"服务端多了个状态、前端还没跟上"
      // 这件事彻底隐形。
      return state
  }
}

/**
 * 当前状态能去的下一步，只有这些。
 *
 * 「回到观察中」在列表里叫「标回观察中」——同一个状态，在徽标上是位置（观察中），
 * 在菜单里是动作（标回）——菜单项读起来必须是一个能做决定的操作。
 */
function stateOptions(row: WatchRow) {
  const next = WATCH_STATE_TRANSITIONS[row.state] ?? []
  return next.map((state) => ({
    value: state,
    content: state === 'observing' ? t('watchlist.markObserving') : stateLabel(state),
  }))
}

async function changeState(row: WatchRow, data: unknown) {
  const next = (data as { value?: string })?.value
  if (!next || next === row.state) return
  try {
    await updateWatchItem(row.thscode, { state: next as WatchState })
    MessagePlugin.success(t('watchlist.stateSaved'))
    await loadItems()
  } catch (error: any) {
    MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  }
}

/** 正在就地编辑备注的那一行的 thscode；'' 表示没有在编辑。 */
const editingNote = ref('')
const noteDraft = ref('')
let noteSaving = false

function startEditNote(row: WatchRow) {
  editingNote.value = row.thscode
  noteDraft.value = row.note || ''
}

/**
 * 保存备注。回车和失焦都会走到这里，所以入口先做幂等判断：回车已经把编辑态
 * 关掉了，随后 input 卸载触发的 blur 必须安静地什么也不做，否则会打两次请求。
 */
async function saveNote(row: WatchRow) {
  if (editingNote.value !== row.thscode || noteSaving) return
  const next = noteDraft.value.trim()
  editingNote.value = ''
  if (next === (row.note || '')) return
  noteSaving = true
  try {
    await updateWatchItem(row.thscode, { note: next })
    MessagePlugin.success(t('watchlist.noteSaved'))
    await loadItems()
  } catch (error: any) {
    MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  } finally {
    noteSaving = false
  }
}

/** 缺数据一律返回 null：0 是"真的等于零"，不能拿它顶替"查不到"。 */
function num(v: number | null | undefined): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function formatPrice(v: number | null | undefined): string {
  const n = num(v)
  return n === null ? '—' : n.toFixed(2)
}

function formatSigned(v: number | null | undefined): string {
  const n = num(v)
  if (n === null) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}`
}

function formatPct(v: number | null | undefined): string {
  const n = num(v)
  if (n === null) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}

function formatAmount(v: number | null | undefined): string {
  const n = num(v)
  if (n === null) return '—'
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return n.toFixed(0)
}

/**
 * 这一行在**对话里**该叫什么。
 *
 * 行情返回的名称最权威（ST 前缀、更名都会反映），清单里存的那份只是本地无行情
 * 时的回退；两者都空时退回代码本身 —— 弹窗里写「确认把「—」移出自选？」是在让
 * 用户对着一团墨迹点确认，而代码至少能指出是哪一行。
 */
function displayName(row: WatchRow): string {
  return row.quote?.name || row.name || row.thscode
}

function changeClass(row: WatchRow): string {
  const pct = num(row.quote?.change_pct)
  if (pct === null) return ''
  return pct >= 0 ? 'is-up' : 'is-down'
}

function isStale(row: WatchRow): boolean {
  const date = row.quote?.date
  return Boolean(date && newestDate.value && date !== newestDate.value)
}

async function loadItems() {
  loading.value = true
  try {
    const res = await listWatchlist()
    items.value = res.data ?? []
  } catch (error: any) {
    MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  } finally {
    loading.value = false
  }
}

/** 拉一遍清单里所有标的的行情。`silent` 用于后台自动刷新（不闪 loading、不弹错）。 */
async function refreshQuotes(silent = false) {
  const codes = items.value.map((item) => item.thscode)
  if (!codes.length) {
    quotes.value = {}
    return
  }
  if (!silent) quotesLoading.value = true
  try {
    const res = await fetchQuotes(codes)
    quotes.value = res.data ?? {}
    lastUpdated.value = new Date().toLocaleTimeString()
  } catch (error: any) {
    if (!silent) MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  } finally {
    if (!silent) quotesLoading.value = false
  }
}

async function reloadAll() {
  await loadItems()
  await refreshQuotes()
}

function handleKeywordChange() {
  if (searchTimer) clearTimeout(searchTimer)
  const q = keyword.value.trim()
  if (!q) {
    suggestions.value = []
    return
  }
  searchTimer = setTimeout(async () => {
    try {
      const res = await searchSymbols(q)
      suggestions.value = res.data ?? []
    } catch {
      // 联想失败不报错：用户完全可以手输完整代码后点「添加」，
      // 为一个辅助功能弹错误提示只会打断主线操作。
      suggestions.value = []
    }
  }, SEARCH_DEBOUNCE_MS)
}

async function handleEnter() {
  const q = keyword.value.trim()
  if (!q) return
  // 已经有候选：回车 = 取第一条。输入框里是精确代码时，第一条就是它。
  if (suggestions.value.length) {
    await addSymbol(suggestions.value[0])
    return
  }
  if (searchTimer) clearTimeout(searchTimer)
  try {
    const res = await searchSymbols(q)
    suggestions.value = res.data ?? []
  } catch {
    suggestions.value = []
  }
  if (!suggestions.value.length) {
    MessagePlugin.warning(t('watchlist.symbolNotFound', { q }))
    return
  }
  if (suggestions.value.length === 1) {
    await addSymbol(suggestions.value[0])
    return
  }
  // 多候选就让用户从列表里挑，不替他猜。
}

async function addSymbol(s: SymbolSuggestion) {
  try {
    const res = await addWatchItem({ thscode: s.thscode, name: s.name, exchange: s.exchange })
    MessagePlugin.success(res.created ? t('watchlist.added') : t('watchlist.alreadyWatched'))
    keyword.value = ''
    suggestions.value = []
    await reloadAll()
  } catch (error: any) {
    MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  }
}

function confirmRemove(row: WatchRow) {
  const dialog = DialogPlugin.confirm({
    header: t('watchlist.removeConfirmTitle'),
    body: t('watchlist.removeConfirmBody', { name: displayName(row) }),
    confirmBtn: { content: t('watchlist.remove'), theme: 'danger' as const },
    cancelBtn: t('watchlist.cancel'),
    theme: 'warning',
    onConfirm: async () => {
      try {
        await removeWatchItem(row.thscode)
        MessagePlugin.success(t('watchlist.removed'))
        await reloadAll()
      } catch (error: any) {
        MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
      } finally {
        dialog.destroy()
      }
    },
    onClose: () => dialog.destroy(),
  })
}

/**
 * 置顶。
 *
 * 用「当前最小 sort_order - 1」而不是重排整个列表：一次请求即可，且不依赖
 * 列表里其它行是否被赋过 sort_order（全部为 0 的默认态下，重排需要先写 N 行
 * 才能让"交换两行的值"有意义）。代价是 sort_order 会往负数漂，无实际影响。
 */
async function pinRow(row: WatchRow) {
  let min = 0
  for (const item of items.value) {
    if (item.sort_order < min) min = item.sort_order
  }
  try {
    await updateWatchItem(row.thscode, { sort_order: min - 1 })
    MessagePlugin.success(t('watchlist.pinned'))
    await loadItems()
  } catch (error: any) {
    MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  }
}

/**
 * 切回本页时立刻补一次：后台标签页里 setInterval 会被浏览器节流甚至暂停，
 * 只靠定时器会出现"切回来还是十分钟前的价"。
 */
function handleVisibility() {
  if (document.visibilityState === 'visible') void refreshQuotes(true)
}

onMounted(async () => {
  await reloadAll()
  refreshTimer = setInterval(() => {
    if (document.visibilityState === 'visible') void refreshQuotes(true)
  }, REFRESH_INTERVAL_MS)
  document.addEventListener('visibilitychange', handleVisibility)
})

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
  if (refreshTimer) clearInterval(refreshTimer)
  document.removeEventListener('visibilitychange', handleVisibility)
})
</script>

<style lang="less" scoped>
.watchlist-page {
  // A 股约定：红涨绿跌。与工作台 quote 条用的是同一对色值
  // （见 components/workspace/kline/KLineWorkspace.vue）。
  --wl-up: #dc2626;
  --wl-down: #047857;
  --wl-up-soft: rgba(220, 38, 38, 0.12);
  --wl-down-soft: rgba(4, 120, 87, 0.12);

  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 20px 24px 24px;
  overflow-y: auto;
}

.watchlist-header {
  flex-shrink: 0;
}

.watchlist-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;

  h2 {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    font-size: var(--app-text-xl);
    font-weight: 600;
    color: var(--td-text-color-primary);
  }
}

.watchlist-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.watchlist-updated {
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-sm);
}

.watchlist-subtitle {
  margin: 6px 0 0;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-md);
}

.watchlist-add {
  position: relative;
  margin: 18px 0 4px;
  max-width: 520px;
}

.watchlist-add__row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.watchlist-add__input {
  flex: 1;
}

.watchlist-suggest {
  position: absolute;
  z-index: 20;
  top: calc(100% + 4px);
  left: 0;
  right: 76px;
  max-height: 280px;
  margin: 0;
  padding: 4px;
  overflow-y: auto;
  list-style: none;
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-border-level-1-color);
  border-radius: 6px;
  box-shadow: var(--td-shadow-2);
}

.watchlist-suggest__item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 8px;
  border-radius: 4px;
  cursor: pointer;

  &:hover {
    background: var(--td-bg-color-container-hover);
  }
}

.watchlist-suggest__code {
  min-width: 92px;
  color: var(--td-text-color-primary);
  font-family: monospace;
  font-size: var(--app-text-md);
}

.watchlist-suggest__name {
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-sm);
}

.watchlist-hint {
  margin: 12px 0 0;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--td-bg-color-secondarycontainer);
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-sm);
}

.watchlist-table {
  margin-top: 16px;
}

.wl-code {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.wl-code__code {
  color: var(--td-text-color-primary);
  font-family: monospace;
}

.wl-code__exchange {
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-xs);
}

.wl-name {
  color: var(--td-text-color-primary);
}

.wl-num {
  font-family: monospace;
  color: var(--td-text-color-primary);

  &.is-up {
    color: var(--wl-up);
  }

  &.is-down {
    color: var(--wl-down);
  }
}

.wl-change {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: monospace;

  &.is-up {
    color: var(--wl-up);
  }

  &.is-down {
    color: var(--wl-down);
  }
}

.wl-change__pct {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: var(--app-text-xs);

  .is-up & {
    background: var(--wl-up-soft);
  }

  .is-down & {
    background: var(--wl-down-soft);
  }
}

.wl-date {
  color: var(--td-text-color-secondary);
  font-family: monospace;
  font-size: var(--app-text-sm);

  &.is-stale {
    color: var(--td-text-color-placeholder);
  }
}

.wl-muted {
  color: var(--td-text-color-placeholder);
}

/* 状态徽标。颜色只区分「要不要多看一眼」，不做涨跌语义 —— 这里的绿意是
   「已放弃」，跟行情列的红涨绿跌不是一回事，所以不复用那对色值。 */
.wl-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 9px;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-pill);
  background: var(--td-bg-color-secondarycontainer);
  color: var(--td-text-color-primary);
  font-size: var(--app-text-xs);
  line-height: 18px;
  cursor: pointer;
  white-space: nowrap;
  transition: border-color var(--app-motion-fast) ease, background var(--app-motion-fast) ease;

  &:hover {
    border-color: var(--td-brand-color);
  }

  .wl-state__dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
    opacity: 0.75;
  }

  &.wl-state--observing {
    color: var(--td-text-color-secondary);
  }

  /* 已触发买点：需要用户处理，给最强的视觉重量。 */
  &.wl-state--triggered {
    border-color: color-mix(in srgb, var(--td-warning-color) 55%, transparent);
    background: color-mix(in srgb, var(--td-warning-color) 14%, transparent);
    color: var(--td-warning-color);
  }

  &.wl-state--holding {
    border-color: color-mix(in srgb, var(--td-brand-color) 45%, transparent);
    background: color-mix(in srgb, var(--td-brand-color) 10%, transparent);
    color: var(--td-brand-color);
  }

  &.wl-state--dropped {
    color: var(--td-text-color-placeholder);
  }
}

.wl-note {
  display: flex;
  align-items: center;
  min-height: 24px;
}

/* 未编辑态是一个"长得像文本的按钮"：整格可点，键盘也能到。 */
.wl-note__view {
  display: block;
  width: 100%;
  max-width: 100%;
  padding: 2px 6px;
  border: 1px dashed transparent;
  border-radius: var(--app-radius-xs);
  background: transparent;
  color: var(--td-text-color-primary);
  font: inherit;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: text;

  &:hover {
    border-color: var(--td-component-stroke);
  }

  &.is-empty {
    color: var(--td-text-color-placeholder);
  }
}

.wl-note__input {
  width: 100%;
}

.wl-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
}
</style>
