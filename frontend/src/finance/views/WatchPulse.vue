<!--
  「今天谁在动」——自选股的量价异动页（/platform/watch-pulse）。

  这个页面的判断依据全部来自后端 `POST /api/finance/pulse`（python-service，
  见 `api/pulse.ts`）。**前端不重算任何指标**：倍数、放量/缩量、推升/出货都是
  服务端读数，前端只负责把「倍数 → 条长」「涨跌 → 颜色」映射成图。

  三条实现约束，改动前先读：

  1. **量能轴与价格轴正交，不能合成一个红绿信号。**
     条形的长度是量能（multiple）。条形的**颜色**不承担方向语义；墨账体例下
     图表一律墨色，方向只由右边的涨跌幅数字（pct_change，A 股红涨绿跌）讲；
     量能强弱另用「放量/缩量」文字标签表达。一只「缩量上涨」的票在这里就是
     一根墨色短条 + 绿色涨跌幅 + "缩量"标签；若把三者合并成一个红绿信号，
     它会被读成涨或跌的强度，那是本页最容易出的错。

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
    <!--
      embedded = 渲染在个股追踪壳（Tracking.vue）的「谁在动」tab 里。
      标题让给外壳，「重新计算」按钮留着 —— 它是这一屏自己的动作，
      外壳不知道 python-service 的 /api/finance/pulse，藏掉等于把入口丢了。
      默认 false 时行为与合并前完全一致（/platform/watch-pulse 仍可直接进）。
    -->
    <header class="wp-header" :class="{ 'is-embedded': embedded }">
      <h2 v-if="!embedded" class="wp-title">
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
    <!--
      用自己的包裹层而不是直接给 t-loading 加样式：t-loading 的根元素
      (.t-loading__parent) 布局由组件内部决定，scoped 样式压不干净。
      包裹层给确定高度并居中，不依赖 .wp-page → .trk-panel 的 flex 链
      能否解析出高度，页面结构变了也不会退回"spinner 悬在顶部"。
    -->
    <div v-if="loading && !pulse" class="wp-loading">
      <t-loading :loading="true" :show-overlay="false" />
      <p class="wp-loading__text">{{ $t('common.loading') }}</p>
    </div>

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
    <div v-else-if="watchlistError" class="wp-error ledger-block">
      <t-icon name="info-circle" size="16px" />
      <span class="wp-error__msg">{{ t('watchPulse.watchlistError') }}</span>
      <t-button variant="text" size="small" :loading="loading" @click="reload">
        {{ t('watchPulse.retry') }}
      </t-button>
    </div>

    <template v-else>
      <!--
        技术位置条 —— 让本页能回答**为什么**。

        本页原本只说"谁在动"（放量几倍、涨跌几点），而同样是涨 4%：
        站在 250 日线上方 3% 和下方 12% 是两件事。缺了这一条，用户看到
        一片异动只能自己去翻 K 线，而那正是本页想替他省掉的步骤。

        放在异动列表**之上**而不是并入每一行：这一条回答的是"整体处在什么
        位置"，属于结论性的一层；逐行展开会让每行从 4 个字段涨到 10 个，
        而那一屏本来是按"异动强度"排序的，混进均线就排不动了。
      -->
      <TechnicalStrip
        :thscodes="watchCodes"
        :labels="watchLabels"
      />

      <!-- ══════ 元信息 ══════ -->
      <section class="wp-meta ledger-block">
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
      <div v-if="pulseError" class="wp-error wp-error--block ledger-block">
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
                <t-tag
                  size="small"
                  class="wp-tag--volume"
                  :theme="item.volume_state === 'up' ? 'warning' : 'primary'"
                >
                  {{ volumeStateLabel(item.volume_state) }}
                </t-tag>
                <!-- verdict 为 null = 服务端读数不足没给判定。不渲染标签，
                     更不能拿 volume_state 去顶替 —— 那是两个不同的结论。 -->
                <t-tag
                  v-if="item.verdict"
                  size="small"
                  class="wp-tag--verdict"
                  theme="default"
                  variant="light"
                >
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
              class="wp-cal__day ledger-block"
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
import TechnicalStrip from '@/finance/components/watchlist/TechnicalStrip.vue'
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

/**
 * embedded = 渲染在个股追踪壳（Tracking.vue）的「谁在动」tab 里。
 * 只影响页头（藏标题），「重新计算」按钮保留 —— 见模板里的说明。
 */
const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })
const embedded = computed(() => props.embedded)

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

// 技术位置条的两个入参。
//
// **必须传带后缀的 thscode**，不能传裸码：`indicators.duckdb` 的 key 是
// `600519.SH` 这种形态，传裸码一条都匹配不上 —— 而那个接口查不到时返回的是
// 空字典，不是错误，界面会安安静静地什么都不显示，看起来像"没有数据"而不是
// "你传错了"。
const watchCodes = computed(() => watchlist.value.map((w) => w.thscode))
const watchLabels = computed(() => {
  const map: Record<string, string> = {}
  for (const w of watchlist.value) map[w.thscode] = w.name
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
/*
 * 墨账体例（achromatic-ledger）—— 规范见 src/finance/styles/ledger.less 文件头：
 *   1. 无框块：块与块之间只靠留白与标题层级分开，不画卡片；
 *   2. 图表用墨色：条形是图表，一律墨色，不按涨跌给整条染色；
 *   3. 浓淡表用墨阶：--ledger-ink-1…5，最浓一档 --ledger-ink-max 配
 *      --ledger-on-ink-max（这两个令牌随主题翻，别写死颜色）。
 *
 * 涨跌红绿（--app-quote-up/down）是 A 股语义，**只出现在涨跌幅数字上**；
 * 状态标记（放量/缩量、推升/出货）走墨阶；品牌珊瑚在本页不出现。
 */
.wp-page {
  /*
   * 页面根**不是**"卡片"，所以不挂 .ledger-block —— 那个类会把 padding 归零，
   * 而这一层的 padding 是整页的留白。块之间的距离由各块自己的 margin 给。
   */

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

/* 嵌入态：标题让给外壳，这一行只剩右对齐的「重新计算」，压成一条窄条。 */
.wp-header.is-embedded {
  justify-content: flex-end;
}

.wp-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  /* 块标题：展示字 + 600 字重，跟参考稿的标题层级一个口径。 */
  font-family: var(--app-font-display);
  font-size: var(--app-text-xl);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.wp-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 加载态：整块居中，且带一行文案。
   原来只有一个孤零零的 spinner 顶在上方，下面几百像素全白 ——
   读起来像"页面坏了"，而不是"正在加载"。给确定高度而不是靠 flex 链撑满，
   避免上层高度解析失败时退回原状。 */
.wp-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 320px;
  padding: 32px 0;
}

.wp-loading__text {
  margin: 0;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
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
  /* 今天的口径是这一块的结论，给墨色正文级；下面那行退一档。 */
  color: var(--td-text-color-primary);
}

.wp-meta__line--sub {
  margin-top: 4px;
  color: var(--td-text-color-secondary);
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
  /* 次级动作：墨账里用文字链接的分量，不用更浅的 placeholder（对比度只有 2.5:1）。 */
  color: var(--td-text-color-secondary);
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
  color: var(--td-text-color-secondary);
  line-height: 1.7;
}

.wp-missing__code {
  margin-right: 8px;
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 代码/数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
}

// ── 错误态 ──────────────────────────────────────────────────────────
.wp-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding: 10px 0;
  /*
   * 错误态是**告警**，不是数据块：墨账里不画卡片（无底色 / 无描边 / 无圆角，
   * 由全局 .ledger-block 归零），只用错误色把这一行标出来。
   * 错误红不能借涨跌色：涨跌绿是"下跌"，错误红是"出错"，两套语义不同。
   */
  color: var(--td-error-color);
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
  /* 表格的横线用淡墨阶，比容器的描边灰更像"账本上的栏线"。 */
  border-top: 1px solid var(--ledger-ink-2);
}

.wp-row {
  display: grid;
  // 左列固定宽度，条形列吃掉剩余空间，统计列固定 —— 三列对齐后条形才能横向比较。
  // 统计列 232px：倍数(58) + 涨跌幅(62) + 两枚标签(92) + 两个 10px 间隙 = 232，
  // 少了会顶出右边界（原来 210px，标签被挤出内容区）。
  grid-template-columns: 168px minmax(0, 1fr) 232px;
  align-items: center;
  gap: 16px;
  padding: 12px 4px;
  border-bottom: 1px solid var(--ledger-ink-2);
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
  color: var(--td-text-color-secondary);
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 代码/数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
}

// 条形轨道 = 量程尺。未填充的一段是极淡的墨（--ledger-ink-1）——
// 容器的描边灰在这里看着像一根空心管子，不像尺子。
.wp-track {
  position: relative;
  height: 14px;
  border-radius: 3px;
  background: var(--ledger-ink-1);
}

/*
 * 条是**图表**：墨账里一律墨色，不按涨跌给整条染色。
 * 条长讲的是量能倍数，价格方向由右边的涨跌幅数字讲 —— 给整条染色会把
 * "缩量上涨"读成"跌"（见文件头约束 1）。
 */
.wp-bar {
  position: absolute;
  left: 0;
  top: 0;
  height: 100%;
  min-width: 2px;
  border-radius: 3px;
  background: var(--td-text-color-secondary);
  transition: width 0.2s ease;
}

// 刻度线用墨色；z-index 让它压在条形上面，跨过去仍读得出量程位置。
.wp-tick {
  position: absolute;
  top: -2px;
  bottom: -2px;
  width: 1px;
  z-index: 1;
  background: var(--td-text-color-primary);
  opacity: 0.45;
}

// 中枢（1.0）加重：它是"今天算出来多少倍"的基准线。
.wp-tick--pivot {
  width: 2px;
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
  color: var(--td-text-color-secondary);
  white-space: nowrap;
}

.wp-ruler__label--pivot {
  color: var(--td-text-color-primary);
  font-weight: 600;
}

.wp-row__stats {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.wp-stat {
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/*
 * 倍数是**量能**，不是价格方向 —— 墨色，不跟涨跌红绿。
 * （红绿只留给 .wp-stat--pct 那一列，见下面。）
 */
.wp-stat--mult {
  min-width: 58px;
  text-align: right;
  font-size: var(--app-text-md, 14px);
  font-weight: 600;
  color: var(--td-text-color-primary);
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
  min-width: 92px;
  justify-content: flex-end;
}

/*
 * 状态标记走墨阶：t-tag 自带的主题色（primary = 品牌珊瑚 / warning = 橙）
 * 是装饰色 —— 墨账里珊瑚全产品只出现一处，"放量/缩量""推升/出货"这类标记
 * 只能用墨阶。方向由左边的涨跌幅数字讲。
 */
.wp-row__tags :deep(.t-tag) {
  background: var(--ledger-ink-1);
  color: var(--td-text-color-secondary);
  border-radius: 3px;
}

/*
 * 量能状态（放量/缩量）是这一行最该被看见的标记 → 走墨阶**最浓一档**，
 * 字色用 --ledger-on-ink-max（这两个令牌随主题翻，不写死颜色）。
 */
.wp-row__tags :deep(.wp-tag--volume) {
  background: var(--ledger-ink-max);
  color: var(--ledger-on-ink-max);
  font-weight: 600;
}

// 价格方向染色（红涨绿跌）。**只染涨跌幅数字** —— 量能那一列是墨色，
// 状态标记走墨阶。见文件头约束 1。
.wp-row.is-up .wp-stat--pct {
  color: var(--app-quote-up);
}

.wp-row.is-down .wp-stat--pct {
  color: var(--app-quote-down);
}

.wp-row.is-flat .wp-stat--pct {
  color: var(--td-text-color-secondary);
}

// ── normal 折叠区 ───────────────────────────────────────────────────
.wp-normal {
  margin-top: 14px;
}

.wp-normal__rows {
  margin-top: 4px;
  border-top: 1px solid var(--ledger-ink-2);
}

// ── 页脚日历 ────────────────────────────────────────────────────────
.wp-cal {
  flex-shrink: 0;
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px solid var(--ledger-ink-2);
}

.wp-cal__title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 10px;
  font-family: var(--app-font-display);
  font-size: var(--app-text-md, 14px);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.wp-cal__strip {
  display: flex;
  gap: 0;
  overflow-x: auto;
  padding-bottom: 4px;
}

/*
 * 日历格不是卡片：去掉描边 / 圆角 / 底色（全局 .ledger-block 归零），
 * 改用一条淡墨竖线分栏 —— 这条横着滚的条子里，竖线比留白更能分开相邻两天。
 */
.wp-cal__day {
  flex-shrink: 0;
  min-width: 168px;
  max-width: 240px;
  padding: 6px 0 6px 14px;
  border-left: 1px solid var(--ledger-ink-2);
}

.wp-cal__when {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 4px;
}

/*
 * 相对日期是 chrome，不是数据 → 墨色。
 * （原来用品牌珊瑚，既破了"珊瑚全产品只出现一处"，对比度也只有 3.4:1。）
 */
.wp-cal__rel {
  font-size: var(--app-text-sm);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.wp-cal__date {
  font-size: 11px;
  color: var(--td-text-color-secondary);
  font-family: var(--app-font-family-mono); /* taste-skill-ignore: 代码/数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums） */
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
  color: var(--td-text-color-secondary);
}

.wp-cal__error,
.wp-cal__empty {
  margin: 0;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

.wp-cal__total {
  margin: 10px 0 0;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}
</style>