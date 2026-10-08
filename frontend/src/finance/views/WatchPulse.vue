<!--
  「今天谁在动」——自选股的量价异动页（/platform/watch-pulse）。

  这个页面的判断依据全部来自后端 `POST /api/finance/pulse`（python-service，
  见 `api/pulse.ts`）。**前端不重算任何指标**：倍数、放量/缩量、推升/出货都是
  服务端读数，前端只负责把「倍数 → 条长」「涨跌 → 颜色」映射成图。

  三条实现约束，改动前先读：

  1. **量能轴与价格轴正交，不能合成一个红绿信号。**
     条形的长度是量能（multiple），条形的**颜色**是价格方向（pct_change，A 股
     红涨绿跌）。量能强弱另用「放量/缩量」文字标签表达。一只「缩量上涨」的票
     在这里就是一根短红条 + "缩量"标签 —— 若把两者合并成红绿，它会被读成涨或
     跌的强度，那是本页最容易出的错。

  2. **条长封顶不等于抹掉信息。**
     倍数轴封在 2.5×，超出的部分只画到 cap，但 `isOverflowBar` 会在数字旁标出
     溢出，否则 2.5 倍和 8 倍在图上长得一样，而这两个结论差得远。

  3. **缺席与零是两回事。**
     `missing`（库里没这只 / 日线不足）与 `volume_state: 'normal'`（算出来了，
     就是正常）都**不能**被渲染成空列表 —— 空列表会被读成"今天没人动"。
     所以两者都显式呈现。

  数据链路（两条，见 api/pulse.ts 顶部注释）：
    自选清单 → Go `/api/v1/watchlist`（带鉴权，按 user/tenant 隔离）
    量价异动 → python-service `/api/finance/pulse`（直连）
    财经日历 → python-service `/api/finance/calendar`（直连，失败不阻塞主面板）
-->
<template>
  <main class="wp-page">
    <!-- ══════ 标题行 ══════ -->
    <header class="wp-header">
      <h2 class="wp-title">
        <t-icon name="chart-bar" size="24px" />
        {{ t('watchPulse.title') }}
      </h2>
      <div class="wp-header__actions">
        <t-button
          variant="outline"
          theme="default"
          size="small"
          :loading="loading"
          @click="reload"
        >
          <template #icon><t-icon name="refresh" /></template>
          {{ t('watchPulse.recalc') }}
        </t-button>
      </div>
    </header>

    <!-- ══════ 加载中 ══════ -->
    <t-loading v-if="loading && !pulse" :loading="true" :show-overlay="false" class="wp-loading" />

    <!-- ══════ 自选为空 → 引导，不发请求 ══════ -->
    <EmptyState
      v-else-if="!watchlistError && watchlist.length === 0"
      class="wp-empty"
      icon="star"
      :title="t('watchPulse.empty.title')"
      :description="t('watchPulse.empty.desc')"
    >
      <t-button theme="primary" size="small" @click="goWatchlist">
        {{ t('watchPulse.empty.cta') }}
      </t-button>
    </EmptyState>

    <!-- ══════ 自选清单拉取失败 ══════ -->
    <div v-else-if="watchlistError" class="wp-error">
      <t-icon name="info-circle" size="16px" />
      <span class="wp-error__msg">{{ t('watchPulse.watchlistError') }}</span>
      <t-button variant="text" size="small" :loading="loading" @click="reload">
        {{ t('watchPulse.retry') }}
      </t-button>
    </div>

    <template v-else>
      <!-- ══════ 元信息 ══════ -->
      <section class="wp-meta">
        <p class="wp-meta__line">
          {{ t('watchPulse.meta.quote', pulseMeta) }}
        </p>
        <p class="wp-meta__line wp-meta__line--sub">
          {{ t('watchPulse.meta.coverage', pulseMeta) }}
        </p>

        <!-- 缺失清单：显式列出，不静默吞掉 -->
        <div v-if="missing.length" class="wp-missing">
          <button
            type="button"
            class="wp-missing__toggle"
            :aria-expanded="missingOpen"
            @click="missingOpen = !missingOpen"
          >
            <t-icon
              name="chevron-down"
              size="14px"
              class="wp-missing__chevron"
              :class="{ 'is-open': missingOpen }"
            />
            {{ t('watchPulse.missing.title', { n: missing.length }) }}
          </button>
          <ul v-if="missingOpen" class="wp-missing__list">
            <li v-for="m in missing" :key="m.thscode" class="wp-missing__item">
              <code class="wp-missing__code">{{ m.thscode }}</code>
              <span class="wp-missing__reason">{{ m.reason }}</span>
            </li>
          </ul>
        </div>
      </section>

      <!-- ══════ pulse 失败：整页错误态，不降级成空列表 ══════ -->
      <div v-if="pulseError" class="wp-error wp-error--block">
        <t-icon name="info-circle" size="16px" />
        <span class="wp-error__msg">{{ pulseError }}</span>
        <t-button variant="text" size="small" :loading="loading" @click="reload">
          {{ t('watchPulse.retry') }}
        </t-button>
      </div>

      <template v-else-if="pulse">
        <!-- ══════ 主体：异动行 ══════ -->
        <section v-if="activeItems.length" class="wp-list">
          <div
            v-for="(item, idx) in activeItems"
            :key="item.code"
            class="wp-row"
            :class="trendClass(item.pct_change)"
          >
            <!-- 左：名称 + 库内代码 -->
            <div class="wp-row__head">
              <span class="wp-row__name" :title="displayName(item)">{{ displayName(item) }}</span>
              <code class="wp-row__code">{{ item.code }}</code>
            </div>

            <!-- 中：条形 + 刻度标尺 -->
            <div class="wp-row__bar">
              <div class="wp-track">
                <span
                  v-for="tick in PULSE_TICKS"
                  :key="tick.value"
                  class="wp-tick"
                  :class="{ 'wp-tick--pivot': tick.value === 1 }"
                  :style="{ left: tickPositionPercent(tick.value) + '%' }"
                />
                <span
                  class="wp-bar"
                  :style="{ width: barWidthPercent(item.multiple) + '%' }"
                />
              </div>
              <!-- 刻度文字只画在第一行：每行都画会糊成一片灰。 -->
              <div v-if="idx === 0" class="wp-ruler">
                <span
                  v-for="tick in PULSE_TICKS"
                  :key="tick.value"
                  class="wp-ruler__label"
                  :class="{ 'wp-ruler__label--pivot': tick.value === 1 }"
                  :style="{ left: tickPositionPercent(tick.value) + '%' }"
                >
                  {{ tickLabel(tick) }}
                </span>
              </div>
            </div>

            <!-- 右：倍数 | 涨跌% | 两枚标签 -->
            <div class="wp-row__stats">
              <span class="wp-stat wp-stat--mult">
                {{ formatMultiple(item.multiple) }}
                <t-icon
                  v-if="isOverflowBar(item.multiple)"
                  name="more-horizontal"
                  size="12px"
                  class="wp-stat__overflow"
                  :title="t('watchPulse.overflowHint')"
                />
              </span>
              <span class="wp-stat wp-stat--pct">{{ formatPct(item.pct_change) }}</span>
              <span class="wp-row__tags">
                <t-tag size="small" :theme="item.volume_state === 'up' ? 'warning' : 'primary'">
                  {{ volumeStateLabel(item.volume_state) }}
                </t-tag>
                <!-- verdict 为 null = 服务端读数不足没给判定。不渲染标签，
                     更不能拿 volume_state 去顶替 —— 那是两个不同的结论。 -->
                <t-tag v-if="item.verdict" size="small" theme="default" variant="light">
                  {{ item.verdict }}
                </t-tag>
              </span>
            </div>
          </div>
        </section>

        <!-- 全部 normal：不是"没有数据"，是"今天量能都正常"，话要说清楚 -->
        <EmptyState
          v-else-if="normalItems.length"
          class="wp-empty"
          compact
          icon="chart-line"
          :title="t('watchPulse.allNormal.title', { n: normalItems.length })"
          :description="t('watchPulse.allNormal.desc')"
        />

        <!-- 算出来了但一行都没有：与"拉取失败"是两回事，措辞也不同 -->
        <EmptyState
          v-else
          class="wp-empty"
          icon="chart-line"
          :title="t('watchPulse.noItems.title')"
          :description="t('watchPulse.noItems.desc')"
        />

        <!-- ══════ normal 行：默认折叠 ══════ -->
        <section v-if="normalItems.length" class="wp-normal">
          <button
            type="button"
            class="wp-normal__toggle"
            :aria-expanded="normalOpen"
            @click="normalOpen = !normalOpen"
          >
            <t-icon
              name="chevron-down"
              size="14px"
              class="wp-normal__chevron"
              :class="{ 'is-open': normalOpen }"
            />
            {{ t('watchPulse.normal.title', { n: normalItems.length }) }}
          </button>
          <div v-if="normalOpen" class="wp-normal__rows">
            <div
              v-for="item in normalItems"
              :key="item.code"
              class="wp-row wp-row--compact"
              :class="trendClass(item.pct_change)"
            >
              <div class="wp-row__head">
                <span class="wp-row__name">{{ displayName(item) }}</span>
                <code class="wp-row__code">{{ item.code }}</code>
              </div>
              <div class="wp-row__bar">
                <div class="wp-track">
                  <span
                    v-for="tick in PULSE_TICKS"
                    :key="tick.value"
                    class="wp-tick"
                    :class="{ 'wp-tick--pivot': tick.value === 1 }"
                    :style="{ left: tickPositionPercent(tick.value) + '%' }"
                  />
                  <span class="wp-bar" :style="{ width: barWidthPercent(item.multiple) + '%' }" />
                </div>
              </div>
              <div class="wp-row__stats">
                <span class="wp-stat wp-stat--mult">{{ formatMultiple(item.multiple) }}</span>
                <span class="wp-stat wp-stat--pct">{{ formatPct(item.pct_change) }}</span>
              </div>
            </div>
          </div>
        </section>

        <!-- ══════ 页脚：后面几天 ══════ -->
        <footer class="wp-cal">
          <h3 class="wp-cal__title">
            <t-icon name="layers" size="15px" />
            {{ t('watchPulse.cal.title') }}
          </h3>

          <!-- 日历失败不阻塞主面板：整条横条收起，主面板照常。 -->
          <p v-if="calError" class="wp-cal__error">{{ t('watchPulse.cal.failed') }}</p>

          <div v-else-if="calendarGroups.length" class="wp-cal__strip">
            <div
              v-for="group in calendarGroups"
              :key="group.date"
              class="wp-cal__day"
            >
              <div class="wp-cal__when">
                <span class="wp-cal__rel">{{ relativeLabel(group.date) }}</span>
                <code class="wp-cal__date">{{ group.date }}</code>
              </div>
              <div class="wp-cal__events">
                <span
                  v-for="ev in group.events.slice(0, CAL_MAX_TITLES)"
                  :key="`${group.date}-${ev.title}`"
                  class="wp-cal__event"
                  :title="ev.title"
                >
                  {{ ev.title }}
                </span>
                <!-- 超出的折叠成计数，不逐条堆出来把页脚撑长。 -->
                <span
                  v-if="group.events.length > CAL_MAX_TITLES"
                  class="wp-cal__more"
                  :title="group.events.slice(CAL_MAX_TITLES).map((e) => e.title).join(' / ')"
                >
                  {{ t('watchPulse.cal.more', { n: group.events.length - CAL_MAX_TITLES }) }}
                </span>
              </div>
            </div>
          </div>

          <p v-else class="wp-cal__empty">{{ t('watchPulse.cal.empty') }}</p>

          <p v-if="calendarCount > 0" class="wp-cal__total">
            {{ t('watchPulse.cal.total', { n: calendarCount }) }}
          </p>
        </footer>
      </template>
    </template>
  </main>
</template>

<script setup lang="ts">
/**
 * 「今天谁在动」页面的数据编排。
 *
 * 三次请求的容错等级刻意不同（理由见文件头 + api/pulse.ts）：
 *   watchlist 失败 → 整页错误态；为空 → 空态引导；都**不**打 pulse。
 *   pulse 失败     → 整页错误态（绝不降级成空列表）。
 *   calendar 失败  → 只收起页脚横条。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import EmptyState from '@/components/EmptyState.vue'
import { listWatchlist, type WatchItem } from '@/finance/api/watchlist'
import {
  getFinanceCalendar,
  getWatchPulse,
  type CalendarEvent,
  type PulseItem,
  type PulseVolumeState,
  type WatchPulseResponse,
} from '@/finance/api/pulse'
import {
  PULSE_TICKS,
  barWidthPercent,
  formatMultiple,
  formatPct,
  groupEventsByDate,
  isOverflowBar,
  relativeDayLabel,
  tickPositionPercent,
  toBareCode,
  todayYmd,
} from '@/finance/utils/pulseScale'

const { t } = useI18n()
const router = useRouter()

/** 日历横条每格最多直接显示几条事件标题，超出折叠成「另有 N 条」。 */
const CAL_MAX_TITLES = 2

const loading = ref(false)
const watchlistError = ref(false)
const pulseError = ref('')
const calError = ref(false)
const missingOpen = ref(false)
/** normal 默认折叠：它是"今天没异动"的证据，不是主角。 */
const normalOpen = ref(false)

const watchlist = ref<WatchItem[]>([])
const pulse = ref<WatchPulseResponse | null>(null)
const calendarEvents = ref<CalendarEvent[]>([])

/** 本次口径的"今天"。取一次即可：跨零点重算会连带把 trade_date 换掉。 */
const today = ref(todayYmd())

/** 裸码 → 名称。两侧用同一个 `toBareCode` 归一（规则分叉会错配到别的票上）。 */
const nameByCode = computed(() => {
  const map = new Map<string, string>()
  for (const w of watchlist.value) map.set(toBareCode(w.thscode), w.name)
  return map
})

/**
 * 名称来自 watchlist，**回退到代码本身**而不是空串。
 * 自选里有、但这次 pulse 没回显的票，代码就是唯一还能给人看的标识。
 */
function displayName(item: PulseItem): string {
  return nameByCode.value.get(toBareCode(item.thscode)) || item.code
}

/** 后端没能算出来的那几只。显式呈现，不并进列表也不静默丢掉。 */
const missing = computed(() => pulse.value?.missing ?? [])

/** 后端已按 abs(multiple-1) 降序，这里只做筛选，**不重排**。 */
const activeItems = computed(() =>
  (pulse.value?.items ?? []).filter((i) => i.volume_state === 'up' || i.volume_state === 'down'),
)
const normalItems = computed(() =>
  (pulse.value?.items ?? []).filter((i) => i.volume_state === 'normal'),
)

/** 元信息插值。缺 coverage 时给 0 —— 这里是"本次没算出来几只"，0 是正确答案。 */
const pulseMeta = computed(() => {
  const p = pulse.value
  return {
    date: p?.trade_date || '—',
    included: p?.coverage?.included ?? 0,
    total: p?.coverage?.total ?? 0,
    volumeUp: p?.coverage?.volume_up ?? 0,
    volumeDown: p?.coverage?.volume_down ?? 0,
  }
})

const calendarGroups = computed(() => groupEventsByDate(calendarEvents.value))
const calendarCount = computed(() => calendarEvents.value.length)

/** A 股红涨绿跌。0 归 neutral：平盘不该被染成"涨"。 */
function trendClass(pct: number): string {
  if (!Number.isFinite(pct) || pct === 0) return 'is-flat'
  return pct > 0 ? 'is-up' : 'is-down'
}

/** 量能用文字标签表达，不染色 —— 见文件头约束 1。 */
function volumeStateLabel(state: PulseVolumeState): string {
  if (state === 'up') return t('watchPulse.state.up')
  if (state === 'down') return t('watchPulse.state.down')
  return t('watchPulse.state.normal')
}

/** 中枢（1.0）那条刻度标注为"中枢"，另两条标注读数。 */
function tickLabel(tick: (typeof PULSE_TICKS)[number]): string {
  return t(tick.key)
}

/** 相对标签；日期格式非法时退回显示绝对日期，不留空白。 */
function relativeLabel(date: string): string {
  const rel = relativeDayLabel(date, today.value)
  if (!rel) return date
  if (rel.key === 'watchPulse.cal.inDays') return t(rel.key, { days: rel.days })
  if (rel.key === 'watchPulse.cal.daysAgo') return t(rel.key, { days: rel.days })
  return t(rel.key)
}

/** 从 axios 抛出的对象里挖出人话；挖不到就给通用文案，不显示 `[object Object]`。 */
function errorText(err: unknown, fallbackKey: string): string {
  const anyErr = err as { message?: unknown; error?: { message?: unknown } }
  const raw = anyErr?.error?.message ?? anyErr?.message
  return typeof raw === 'string' && raw.trim() ? raw : t(fallbackKey)
}

function goWatchlist() {
  router.push({ name: 'watchlist' })
}

/**
 * 拉一轮。watchlist 失败/为空时**不再调 pulse**：空 codes 调了既拿不到东西，
 * 又会被渲染成"今天没人动"，那是个假结论。
 */
async function reload() {
  loading.value = true
  today.value = todayYmd()
  try {
    let items: WatchItem[]
    try {
      const res = await listWatchlist()
      items = res?.data ?? []
      watchlistError.value = false
    } catch (err) {
      watchlistError.value = true
      pulseError.value = errorText(err, 'watchPulse.watchlistError')
      pulse.value = null
      watchlist.value = []
      return
    }

    watchlist.value = items

    if (items.length === 0) {
      pulse.value = null
      pulseError.value = ''
      return
    }

    try {
      const res = await getWatchPulse(items.map((w) => toBareCode(w.thscode)))
      pulse.value = res
      pulseError.value = ''
    } catch (err) {
      pulse.value = null
      pulseError.value = errorText(err, 'watchPulse.pulseError')
    }
  } finally {
    loading.value = false
  }
}

/** 日历独立加载：失败只收起页脚横条，主面板不依赖它。 */
async function loadCalendar() {
  calError.value = false
  try {
    const res = await getFinanceCalendar(7)
    calendarEvents.value = res?.events ?? []
  } catch {
    calendarEvents.value = []
    calError.value = true
  }
}

onMounted(() => {
  void reload()
  void loadCalendar()
})
</script>

<style lang="less" scoped>
.wp-page {
  // A 股约定：红涨绿跌。同一对色值与 Watchlist.vue 的 --wl-up/--wl-down 一致，
  // 两个页面不能各定一套，否则同一个涨跌在两处显示成两种颜色。
  --wp-up: #dc2626;
  --wp-down: #047857;
  --wp-up-soft: rgba(220, 38, 38, 0.16);
  --wp-down-soft: rgba(4, 120, 87, 0.16);

  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 20px 24px 28px;
  overflow-y: auto;
}

.wp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
}

.wp-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: var(--app-text-xl);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.wp-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.wp-loading {
  padding: 32px 0;
}

.wp-empty {
  padding: 32px 0;
}

// ── 元信息 ──────────────────────────────────────────────────────────
.wp-meta {
  flex-shrink: 0;
  margin-top: 14px;
}

.wp-meta__line {
  margin: 0;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

.wp-meta__line--sub {
  margin-top: 4px;
  color: var(--td-text-color-placeholder);
}

.wp-missing {
  margin-top: 6px;
}

.wp-missing__toggle,
.wp-normal__toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-placeholder);
}

.wp-missing__toggle:hover,
.wp-normal__toggle:hover {
  color: var(--td-text-color-primary);
}

.wp-missing__chevron,
.wp-normal__chevron {
  transition: transform 0.15s ease;

  &.is-open {
    transform: rotate(180deg);
  }
}

.wp-missing__list {
  margin: 4px 0 0;
  padding-left: 22px;
}

.wp-missing__item {
  font-size: var(--app-text-sm);
  color: var(--td-text-color-placeholder);
  line-height: 1.7;
}

.wp-missing__code {
  margin-right: 8px;
  font-family: var(--app-font-mono, ui-monospace, monospace);
}

// ── 错误态 ──────────────────────────────────────────────────────────
.wp-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: var(--td-radius-medium, 6px);
  background: var(--td-error-color-light, var(--wp-down-soft));
  color: var(--td-text-color-primary);
}

.wp-error--block {
  margin-top: 20px;
}

.wp-error__msg {
  flex: 1;
  min-width: 0;
  font-size: var(--app-text-sm);
}

// ── 主体行 ──────────────────────────────────────────────────────────
.wp-list {
  display: flex;
  flex-direction: column;
  margin-top: 18px;
  border-top: 1px solid var(--td-component-stroke);
}

.wp-row {
  display: grid;
  // 左列固定宽度，条形列吃掉剩余空间，统计列固定 —— 三列对齐后条形才能横向比较。
  grid-template-columns: 168px minmax(0, 1fr) 210px;
  align-items: center;
  gap: 16px;
  padding: 12px 4px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.wp-row--compact {
  padding: 8px 4px;
}

.wp-row__head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.wp-row__name {
  font-size: var(--app-text-md, 14px);
  font-weight: 500;
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wp-row__code {
  font-size: var(--app-text-xs, 12px);
  color: var(--td-text-color-placeholder);
  font-family: var(--app-font-mono, ui-monospace, monospace);
}

// 条形轨道：高度固定，三条刻度线贯穿所有行才能横向比对。
.wp-track {
  position: relative;
  height: 14px;
  border-radius: 3px;
  background: var(--td-component-stroke);
}

.wp-bar {
  position: absolute;
  left: 0;
  top: 0;
  height: 100%;
  min-width: 2px;
  border-radius: 3px;
  background: var(--td-text-color-placeholder);
  transition: width 0.2s ease;
}

.wp-tick {
  position: absolute;
  top: -2px;
  bottom: -2px;
  width: 1px;
  background: var(--td-bg-color-container);
  opacity: 0.85;
}

// 中枢（1.0）加重：它是"今天算出来多少倍"的基准线。
.wp-tick--pivot {
  width: 2px;
  background: var(--td-text-color-secondary);
  opacity: 1;
}

.wp-ruler {
  position: relative;
  height: 16px;
  margin-top: 2px;
}

.wp-ruler__label {
  position: absolute;
  transform: translateX(-50%);
  font-size: 11px;
  line-height: 16px;
  color: var(--td-text-color-placeholder);
  white-space: nowrap;
}

.wp-ruler__label--pivot {
  color: var(--td-text-color-secondary);
  font-weight: 600;
}

.wp-row__stats {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.wp-stat {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.wp-stat--mult {
  min-width: 58px;
  text-align: right;
  font-size: var(--app-text-md, 14px);
  font-weight: 600;
}

.wp-stat--pct {
  min-width: 62px;
  text-align: right;
  font-size: var(--app-text-sm);
}

.wp-stat__overflow {
  margin-left: 2px;
  vertical-align: middle;
}

.wp-row__tags {
  display: flex;
  gap: 4px;
  min-width: 88px;
  justify-content: flex-end;
}

// 价格方向染色（红涨绿跌）。**只染价格**，量能强弱靠标签 —— 见文件头约束 1。
.wp-row.is-up {
  .wp-bar {
    background: var(--wp-up);
  }
  .wp-stat {
    color: var(--wp-up);
  }
}

.wp-row.is-down {
  .wp-bar {
    background: var(--wp-down);
  }
  .wp-stat {
    color: var(--wp-down);
  }
}

// ── normal 折叠区 ───────────────────────────────────────────────────
.wp-normal {
  margin-top: 14px;
}

.wp-normal__rows {
  margin-top: 4px;
  border-top: 1px solid var(--td-component-stroke);
}

// ── 页脚日历 ────────────────────────────────────────────────────────
.wp-cal {
  flex-shrink: 0;
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px solid var(--td-component-stroke);
}

.wp-cal__title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 10px;
  font-size: var(--app-text-md, 14px);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.wp-cal__strip {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.wp-cal__day {
  flex-shrink: 0;
  min-width: 168px;
  max-width: 240px;
  padding: 8px 10px;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--td-radius-medium, 6px);
  background: var(--td-bg-color-container);
}

.wp-cal__when {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 4px;
}

.wp-cal__rel {
  font-size: var(--app-text-sm);
  font-weight: 600;
  color: var(--td-brand-color);
}

.wp-cal__date {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
  font-family: var(--app-font-mono, ui-monospace, monospace);
}

.wp-cal__events {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.wp-cal__event {
  font-size: var(--app-text-xs, 12px);
  color: var(--td-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wp-cal__more {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}

.wp-cal__error,
.wp-cal__empty {
  margin: 0;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-placeholder);
}

.wp-cal__total {
  margin: 10px 0 0;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}
</style>