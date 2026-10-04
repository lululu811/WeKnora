<!--
  大盘预览大屏（/dashboard）。

  设计稿：docs/design/market-dashboard/dashboard.html（高保真样稿，逐格对照）
  交接书：docs/design/market-dashboard/HANDOFF.md

  三条实现约束，改动前先读：
  1. **不新增设计变量**。所有颜色/圆角/间距/动效时长都引用 `theme.css` 现有
     变量（`--td-*` / `--app-*`）。涨跌色是唯一例外：设计稿定的浅色
     `--up:#dc2626 / --down:#047857`、深色 `--up:#f87171 / --down:#34d399`
     （深色各提一档，见本文件底部 `.md-up/.md-down`），因为 palette.ts 的深色值
     是在 K 线画布 #181d26 上测的，本页容器是更亮更暖的 #2A231C，原值掉档。
  2. **红涨绿跌**（A股惯例），不跟随欧美配色。
  3. **一屏无滚动**。1440×900 与 1366×768 都要放下，小视口走
     `@media (max-height: 800px)` 收紧（自选股减到 6 行）。

  数据链路（与 finance/api/market.ts 的注释对应）：
    指数 / 情绪 / ETF  → python-service `/api/market/snapshot`
    龙虎榜            → python-service `/api/market/dragon-tiger`
    自选股            → Go `/api/v1/watchlist` + python-service `/api/quotes`
-->
<template>
  <div class="md-page" :class="{ 'is-flashing': flashing }">
    <!--
      折线渐变（涨跌两色）。必须放在 template 内：SFC 里 `</style>` 之后的第二个
      顶层标签会被 vue-loader 当成 custom block 解析（vite 报 import-analysis 语法错），
      所以渐变定义随模板一起走，不能另起一段顶层 svg。
      stop-color 用 var(--md-up)/var(--md-down)，随浅/深色主题自动切换。
    -->
    <svg class="md-gradients" width="0" height="0" aria-hidden="true">
      <defs>
        <linearGradient id="mdGradUp" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="var(--md-up)" stop-opacity=".16" />
          <stop offset="1" stop-color="var(--md-up)" stop-opacity="0" />
        </linearGradient>
        <linearGradient id="mdGradDown" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="var(--md-down)" stop-opacity=".16" />
          <stop offset="1" stop-color="var(--md-down)" stop-opacity="0" />
        </linearGradient>
      </defs>
    </svg>
    <!-- ══════ 顶栏 ══════ -->
    <header class="md-topbar md-tile" style="--d: 0ms">
      <button type="button" class="md-icon-btn" :title="$t('marketDashboard.back')"
        :aria-label="$t('marketDashboard.back')" @click="goBack">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M9 2.5 4.5 7 9 11.5" stroke="currentColor" stroke-width="1.6"
            stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      <span class="md-topbar__title">{{ $t('marketDashboard.title') }}</span>
      <span class="md-topbar__date">{{ dateLine }}</span>
      <span class="md-topbar__spacer" />
      <span class="md-topbar__clock md-num">
        {{ $t('marketDashboard.dataTime') }} {{ clock }}
      </span>
      <span class="md-status-chip" :class="{ 'is-open': marketState === 'open' }">
        <span class="md-status-chip__dot" />
        <span>{{ stateLabel }}</span>
      </span>
      <button type="button" class="md-icon-btn" :title="themeTitle" :aria-label="themeTitle"
        @click="toggleTheme">
        <svg v-if="effectiveTheme !== 'dark'" width="14" height="14" viewBox="0 0 14 14" fill="none"
          aria-hidden="true">
          <circle cx="7" cy="7" r="3" stroke="currentColor" stroke-width="1.4" />
          <path
            d="M7 1v1.6M7 11.4V13M1 7h1.6M11.4 7H13M2.8 2.8l1.1 1.1M10.1 10.1l1.1 1.1M11.2 2.8l-1.1 1.1M3.9 10.1l-1.1 1.1"
            stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
        </svg>
        <svg v-else width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M11.8 8.6A5.2 5.2 0 0 1 5.4 2.2a5.2 5.2 0 1 0 6.4 6.4Z" stroke="currentColor"
            stroke-width="1.4" stroke-linejoin="round" />
        </svg>
      </button>
      <button type="button" class="md-icon-btn" :disabled="refreshing"
        :title="$t('marketDashboard.refresh')" :aria-label="$t('marketDashboard.refresh')"
        @click="refresh">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true"
          :class="{ 'is-spin': refreshing }">
          <path d="M12 7A5 5 0 1 1 7 2c1.9 0 3.5 1 4.4 2.6M12 1.4v3.2H8.8" stroke="currentColor"
            stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
    </header>

    <!-- 数据源降级提示：非空才出现，不占常态布局 -->
    <p v-if="unavailable.length" class="md-notice" role="status">
      {{ $t('marketDashboard.sourceUnavailable', { sources: unavailable.join('、') }) }}
    </p>

    <!-- ══════ 指数快照条 ══════ -->
    <div class="md-ticker md-tile" style="--d: 60ms">
      <span class="md-ticker__label">{{ $t('marketDashboard.tickerLabel') }}</span>
      <span v-for="t in tickers" :key="t.thscode" class="md-ticker__item">
        <span class="md-ticker__name">{{ t.name }}</span>
        <span class="md-ticker__val md-num">{{ fmtNum(t.last) }}</span>
        <span class="md-pill md-num" :class="trendClass(t.change_pct)">
          {{ fmtPct(t.change_pct) }}
        </span>
      </span>
      <span v-if="!tickers.length && !loading" class="md-ticker__empty">{{ $t('marketDashboard.noData') }}</span>
    </div>

    <main class="md-board">
      <!-- ── 四大指数走势（页面主角，2×2） ── -->
      <section class="md-index-grid">
        <article v-for="(idx, i) in indexPanels" :key="idx.thscode" class="md-tile md-index-panel"
          :style="{ '--d': `${120 + i * 60}ms` }">
          <div class="md-index-panel__row">
            <span class="md-index-panel__name">{{ idx.name }}</span>
            <span class="md-index-panel__code md-num">{{ idx.thscode }}</span>
            <span class="md-index-panel__last md-num" :class="trendClass(idx.change_pct)">
              {{ fmtNum(idx.last) }}
            </span>
            <span class="md-pill md-num" :class="trendClass(idx.change_pct)">
              {{ fmtPct(idx.change_pct) }}
            </span>
          </div>

          <div class="md-index-panel__chart">
            <!-- 只有一个读数画不出线：显示文案，不给一条会骗人的横线 -->
            <p v-if="!idx.line" class="md-tile-empty">{{ idx.emptyText }}</p>
            <svg v-else viewBox="0 0 260 64" preserveAspectRatio="none" role="img"
              :aria-label="`${idx.name} 60日走势`">
              <path :d="idx.area" :fill="idx.areaFill" />
              <line v-if="idx.prevY != null" x1="3" x2="257" :y1="idx.prevY" :y2="idx.prevY"
                class="md-chart-prev" vector-effect="non-scaling-stroke" />
              <path :d="idx.line" fill="none" :class="idx.lineClass" stroke-width="1.6"
                stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />
            </svg>
          </div>

          <div class="md-rangebar">
            <span class="md-rangebar__edge md-num">{{ $t('marketDashboard.chartLow', { v: fmtNum(idx.low) }) }}</span>
            <div class="md-rangebar__track">
              <span v-if="idx.prevPos != null" class="md-rangebar__prev"
                :style="{ left: `${idx.prevPos}%` }"
                :title="$t('marketDashboard.prevCloseTitle', { v: fmtNum(idx.prev_close) })" />
              <span v-if="idx.openPos != null" class="md-rangebar__open"
                :style="{ left: `${idx.openPos}%` }" />
              <span v-if="idx.lastPos != null" class="md-rangebar__last"
                :class="trendClass(idx.change_pct)" :style="{ left: `${idx.lastPos}%` }" />
            </div>
            <span class="md-rangebar__edge md-num">{{ $t('marketDashboard.chartHigh', { v: fmtNum(idx.high) }) }}</span>
          </div>

          <div class="md-index-panel__legend">
            {{ $t('marketDashboard.chartLegend', { point: pointLabel }) }}
          </div>
        </article>
      </section>

      <!-- ── 市场情绪 ── -->
      <section class="md-tile md-sentiment" style="--d: 360ms">
        <div class="md-tile__head">
          <span class="md-tile__title">{{ $t('marketDashboard.sentiment.title') }}</span>
          <span class="md-tile__caption">{{ $t('marketDashboard.sentiment.caption') }}</span>
        </div>
        <div class="md-sentiment__hero">
          <div class="md-hero-metric">
            <div class="md-hero-metric__label">{{ $t('marketDashboard.sentiment.limitUp') }}</div>
            <div class="md-hero-metric__value md-num md-up">{{ fmtCount(sentiment.limit_up) }}</div>
          </div>
          <div class="md-hero-metric">
            <div class="md-hero-metric__label">{{ $t('marketDashboard.sentiment.limitDown') }}</div>
            <div class="md-hero-metric__value md-num md-down">{{ fmtCount(sentiment.limit_down) }}</div>
          </div>
        </div>
        <div class="md-sentiment__sub">
          <div class="md-metric">
            <div class="md-metric__label">{{ $t('marketDashboard.sentiment.broken') }}</div>
            <div class="md-metric__value md-num">
              <span>{{ fmtCount(sentiment.broken) }}</span>
              <span v-if="sentiment.broken_rate != null" class="md-metric__unit">
                {{ $t('marketDashboard.sentiment.brokenUnit', { rate: sentiment.broken_rate }) }}
              </span>
            </div>
          </div>
          <div class="md-metric">
            <div class="md-metric__label">{{ $t('marketDashboard.sentiment.maxStreak') }}</div>
            <div class="md-metric__value md-num">
              <span>{{ fmtCount(sentiment.max_streak) }}</span>
              <span class="md-metric__unit">{{ $t('marketDashboard.sentiment.streakUnit') }}</span>
            </div>
            <div class="md-metric__note">{{ sentiment.streak_name || '' }}</div>
          </div>
        </div>

        <div class="md-lu5">
          <div class="md-lu5__label">{{ $t('marketDashboard.sentiment.trendLabel') }}</div>
          <div v-if="lu5.length" class="md-lu5__bars">
            <span v-for="(v, i) in lu5" :key="i" class="md-lu5__bar"
              :class="{ 'is-last': i === lu5.length - 1 }" :style="{ height: `${lu5Heights[i]}%` }">
              <b class="md-num">{{ v.count }}</b>
            </span>
          </div>
        </div>

        <div class="md-breadth">
          <div class="md-breadth__bar">
            <i :style="{ width: `${breadthW.up}%` }" class="md-breadth__up" />
            <i :style="{ width: `${breadthW.flat}%` }" class="md-breadth__flat" />
            <i :style="{ width: `${breadthW.down}%` }" class="md-breadth__down" />
          </div>
          <div class="md-breadth__legend">
            <span><i class="md-dot md-breadth__up" />{{ $t('marketDashboard.sentiment.breadth.up') }}
              <b class="md-num md-up">{{ fmtInt(sentiment.breadth.up) }}</b></span>
            <span><i class="md-dot md-breadth__flat" />{{ $t('marketDashboard.sentiment.breadth.flat') }}
              <b class="md-num">{{ fmtInt(sentiment.breadth.flat) }}</b></span>
            <span><i class="md-dot md-breadth__down" />{{ $t('marketDashboard.sentiment.breadth.down') }}
              <b class="md-num md-down">{{ fmtInt(sentiment.breadth.down) }}</b></span>
          </div>
        </div>
      </section>

      <!-- ── 自选股 ── -->
      <section class="md-tile md-watchlist" style="--d: 420ms">
        <div class="md-tile__head">
          <span class="md-tile__title">{{ $t('marketDashboard.watchlist.title') }}</span>
          <!-- 计数与「全部 →」只在清单真的加载出来且非空时出现：
               空清单显示「0 只」是自相矛盾的（0 就是空），加载失败显示更是撒谎。 -->
          <span v-if="watchlistHasRows" class="md-tile__caption md-num">
            {{ $t('marketDashboard.watchlist.count', { n: watchRows.length }) }}
          </span>
          <router-link v-if="watchlistHasRows" class="md-tile__link" to="/platform/watchlist">
            {{ $t('marketDashboard.watchlist.all') }}
          </router-link>
        </div>

        <!-- 空态：引导去自选页，不放插画也不放解释性长文 -->
        <div v-if="watchlistEmpty" class="md-wl-empty">
          <span class="md-wl-empty__hint">{{ $t('marketDashboard.watchlist.empty') }}</span>
          <router-link class="md-wl-empty__cta" to="/platform/watchlist">
            {{ $t('marketDashboard.watchlist.emptyCta') }}
          </router-link>
        </div>

        <div v-else-if="watchlistHasRows" class="md-wl-rows">
          <div v-for="w in watchRows" :key="w.thscode" class="md-wl-row">
            <span class="md-wl-row__name">
              {{ w.name }}
              <span class="md-wl-row__code md-num">{{ w.thscode }}</span>
            </span>
            <span class="md-wl-row__price md-num" :class="trendClass(w.change_pct)">{{ fmtNum(w.close) }}</span>
            <span class="md-pill md-num" :class="trendClass(w.change_pct)">{{ fmtPct(w.change_pct) }}</span>
            <span class="md-badge" :class="{ 'is-trigger': w.state === 'triggered' }">
              {{ watchStateLabel(w.state) }}
            </span>
          </div>
        </div>
      </section>

      <!-- ── 龙虎榜 ── -->
      <section class="md-tile md-lhb" style="--d: 480ms">
        <div class="md-tile__head">
          <span class="md-tile__title">{{ $t('marketDashboard.dragonTiger.title') }}</span>
          <span class="md-tile__caption">{{ $t('marketDashboard.dragonTiger.caption') }}</span>
        </div>
        <div v-if="dragonRows.length" class="md-lhb-rows">
          <div v-for="(r, i) in dragonRows" :key="r.thscode" class="md-lhb-row">
            <span class="md-lhb-row__rank md-num" :class="{ 'is-first': i === 0 }">{{ i + 1 }}</span>
            <span class="md-lhb-row__name">
              {{ r.name }}
              <span class="md-lhb-row__tag">{{ r.tag }}</span>
            </span>
            <span class="md-lhb-row__amt">
              <span class="md-lhb-row__net md-num" :class="trendClass(r.net_value)">
                {{ $t('marketDashboard.dragonTiger.net') }} {{ fmtYi(r.net_value) }}
              </span>
              <span class="md-lhb-row__org md-num">
                {{ $t('marketDashboard.dragonTiger.org') }}
                <span :class="trendClass(r.org_net_value)">{{ fmtYi(r.org_net_value) }}</span>
              </span>
            </span>
          </div>
        </div>
        <!-- 当日无数据要明说，不要留白也不要报错 -->
        <p v-else-if="!loading" class="md-tile-empty">{{ $t('marketDashboard.dragonTiger.empty') }}</p>
      </section>

      <!-- ── 权重 ETF ── -->
      <section class="md-tile md-etf" style="--d: 540ms">
        <div class="md-tile__head">
          <span class="md-tile__title">{{ $t('marketDashboard.etf.title') }}</span>
          <span class="md-tile__caption">{{ $t('marketDashboard.etf.caption') }}</span>
        </div>
        <div v-if="etfs.length" class="md-etf-rows">
          <div v-for="e in etfs" :key="e.thscode" class="md-etf-row">
            <span class="md-etf-row__name">
              {{ e.name }}
              <span class="md-etf-row__code md-num">{{ e.thscode }}</span>
            </span>
            <span class="md-etf-row__price md-num" :class="trendClass(e.change_pct)">{{ fmtPrice(e.last) }}</span>
            <span class="md-pill md-num" :class="trendClass(e.change_pct)">{{ fmtPct(e.change_pct) }}</span>
          </div>
        </div>
        <p v-else-if="!loading" class="md-tile-empty">{{ $t('marketDashboard.noData') }}</p>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  getDragonTiger,
  getMarketSnapshot,
  type DragonTigerRow,
  type MarketEtf,
  type MarketIndex,
  type MarketSentiment,
} from '@/finance/api/market'
import { fetchQuotes, listWatchlist, type Quote, type WatchState } from '@/finance/api/watchlist'
import { useTheme } from '@/composables/useTheme'
import {
  CHART,
  areaPath,
  barHeights,
  breadthWidths,
  linePath,
  normalize,
  prevCloseY,
  rangePosition,
  toYi,
  trendOf,
  usableCloses,
} from './market-geometry'

const router = useRouter()
const { t } = useI18n()
const { effectiveTheme, setTheme } = useTheme()

const loading = ref(true)
const refreshing = ref(false)
const flashing = ref(false)
const snapshot = ref<Awaited<ReturnType<typeof getMarketSnapshot>> | null>(null)
const dragon = ref<Awaited<ReturnType<typeof getDragonTiger>> | null>(null)
const watchItems = ref<{ thscode: string; name: string; state: WatchState }[]>([])
const quotes = ref<Record<string, Quote>>({})

// ── 派生数据 ────────────────────────────────────────────────────────────

const unavailable = computed(() => snapshot.value?.unavailable ?? [])

/** 盘中/盘后/非交易日。用真实交易日历推断，不用 URL 参数（样稿的 ?state= 是演示态）。 */
const marketState = computed<'open' | 'closed' | 'holiday'>(() => {
  const now = new Date()
  const day = now.getDay()
  // 周末必然无交易；法定节假日本地没有日历数据，只能靠"最近交易日是不是今天"判断。
  if (day === 0 || day === 6) return 'holiday'
  const mins = now.getHours() * 60 + now.getMinutes()
  const AM_OPEN = 9 * 60 + 30
  const AM_CLOSE = 11 * 60 + 30
  const PM_OPEN = 13 * 60
  const PM_CLOSE = 15 * 60
  if (mins >= AM_OPEN && mins < AM_CLOSE) return 'open'
  if (mins >= PM_OPEN && mins < PM_CLOSE) return 'open'
  // 交易日 9:30 之前算盘后（承接上一交易日收盘）；这里保守地只把 15:00 之后判为盘后，
  // 午休（11:30~13:00）仍算盘中——那时最新价确实是活的。
  if (mins >= PM_CLOSE) return 'closed'
  if (mins < AM_OPEN) return 'closed'
  return 'open'
})

/** 非交易日：数据日期不是今天。盘后/盘中数据日期都应该是今天。 */
const isHoliday = computed(() => {
  if (marketState.value === 'holiday') return true
  const d = snapshot.value?.trade_date
  if (!d) return false
  const today = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return d !== `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`
})

const stateLabel = computed(() => {
  if (isHoliday.value) return t('marketDashboard.state.holiday')
  return t(`marketDashboard.state.${marketState.value}`)
})

/** 盘中叫「最新」，其余（盘后/非交易日）叫「收盘」——那一刻的读数是收盘价。 */
const pointLabel = computed(() =>
  marketState.value === 'open' && !isHoliday.value
    ? t('marketDashboard.latestLabel')
    : t('marketDashboard.closedLabel'),
)

const dateLine = computed(() => {
  const d = snapshot.value?.trade_date
  if (!d) return ''
  const dt = new Date(`${d}T00:00:00`)
  if (Number.isNaN(dt.getTime())) return d
  const week = ['日', '一', '二', '三', '四', '五', '六'][dt.getDay()]
  const base = `${dt.getFullYear()}年${dt.getMonth() + 1}月${dt.getDate()}日 周${week}`
  return isHoliday.value ? `${base} · ${t('marketDashboard.holidayNote')}` : base
})

/**
 * 数据时间。
 *
 * 本地库只到「交易日」这一粒度 —— 没有分钟级快照可读。所以这里显示**该交易日的
 * 收盘时刻（15:00）**，而不是浏览器当前时间：后者会把周五的收盘价标成
 * "周一 10:23"，读起来像实时行情，那是误导。
 * 盘中也没有更细的数据可给，这一格的价值是"这批数据属于哪一天"，不是"现在几点"。
 */
const clock = computed(() => '15:00:00')

const themeTitle = computed(() =>
  effectiveTheme.value === 'dark'
    ? t('marketDashboard.themeToLight')
    : t('marketDashboard.themeToDark'),
)

const indices = computed<MarketIndex[]>(() => snapshot.value?.indices ?? [])
const tickers = computed<MarketIndex[]>(() => snapshot.value?.tickers ?? [])
const etfs = computed<MarketEtf[]>(() => snapshot.value?.etfs ?? [])
const dragonRows = computed<DragonTigerRow[]>(() => dragon.value?.data ?? [])

const EMPTY_SENTIMENT: MarketSentiment = {
  trade_date: null,
  limit_up: null,
  limit_down: null,
  broken: null,
  broken_rate: null,
  max_streak: null,
  streak_name: null,
  limit_up_trend: [],
  breadth: { up: null, flat: null, down: null },
}
const sentiment = computed<MarketSentiment>(() => snapshot.value?.sentiment ?? EMPTY_SENTIMENT)

const lu5 = computed(() => sentiment.value.limit_up_trend ?? [])
const lu5Heights = computed(() => barHeights(lu5.value.map((p) => p.count)))
const breadthW = computed(() => breadthWidths(sentiment.value.breadth))

/** 一个指数格的绘制数据。曲线 / 昨收线 / 快照条点位全部在这里算。 */
interface IndexPanel {
  thscode: string
  name: string
  last: number | null
  change_pct: number | null
  high: number | null
  low: number | null
  prev_close: number | null
  line: string
  area: string
  prevY: number | null
  lineClass: string
  areaFill: string
  openPos: number | null
  lastPos: number | null
  prevPos: number | null
  emptyText: string
}

const indexPanels = computed<IndexPanel[]>(() =>
  indices.value.map((idx) => {
    const closes = usableCloses(idx.series)
    const norm = normalize(closes)
    const drawable = norm.length >= 2
    const rising = trendOf(idx.change_pct) !== 'down'
    return {
      thscode: idx.thscode,
      name: idx.name,
      last: idx.last,
      change_pct: idx.change_pct,
      high: idx.high,
      low: idx.low,
      prev_close: idx.prev_close,
      line: drawable ? linePath(norm, CHART.width, CHART.height, CHART.pad) : '',
      area: drawable ? areaPath(norm, CHART.width, CHART.height, CHART.pad) : '',
      prevY: drawable ? prevCloseY(norm, CHART.height, CHART.pad) : null,
      lineClass: rising ? 'md-chart-line md-up-stroke' : 'md-chart-line md-down-stroke',
      areaFill: rising ? 'url(#mdGradUp)' : 'url(#mdGradDown)',
      openPos: rangePosition(idx.open, idx.low, idx.high),
      lastPos: rangePosition(idx.last, idx.low, idx.high),
      prevPos: rangePosition(idx.prev_close, idx.low, idx.high),
      emptyText: t('marketDashboard.indexUnavailable'),
    }
  }),
)

// ── 自选股 ──────────────────────────────────────────────────────────────

interface WatchRow {
  thscode: string
  name: string
  state: WatchState
  close: number | null
  change_pct: number | null
}

const watchRows = computed<WatchRow[]>(() =>
  watchItems.value
    .slice(0, 7)
    .map((item) => {
      const q = quotes.value[item.thscode]
      return {
        thscode: item.thscode,
        name: item.name,
        state: item.state,
        close: q?.close ?? null,
        change_pct: q?.change_pct ?? null,
      }
    }),
)

/**
 * 空态只在**清单确实加载成功且确实为空**时出现。
 *
 * 清单接口失败时 watchLoaded 保持 false —— 那时页面既不显示"还没有自选股"
 * （会把一次网络失败说成"你没有自选股"），也不显示"0 只 / 全部 →"
 * （一个空清单不该有计数和跳转链接）。
 */
const watchlistEmpty = computed(() => watchLoaded.value && watchItems.value.length === 0)
const watchLoaded = ref(false)
/** 头部计数 / 「全部 →」/ 行列表的共同门槛：加载成功且清单非空。 */
const watchlistHasRows = computed(() => watchLoaded.value && watchRows.value.length > 0)

// ── 取数 ────────────────────────────────────────────────────────────────

let flashTimer: ReturnType<typeof setTimeout> | null = null

/** 刷新时全屏数字起伏一次（状态切换动效）。reduced-motion 下由 CSS 关闭动画。 */
function flash() {
  flashing.value = true
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { flashing.value = false }, 500)
}

async function loadWatchlist() {
  try {
    const res = await listWatchlist()
    const items = (res.data ?? []).filter((i) => i.state !== 'dropped')
    watchItems.value = items.map((i) => ({ thscode: i.thscode, name: i.name, state: i.state }))
    watchLoaded.value = true
    if (items.length) {
      // 一次批量取价（上限 200），不要逐行请求
      const res2 = await fetchQuotes(items.map((i) => i.thscode))
      quotes.value = res2.data ?? {}
    }
  } catch {
    // 清单拿不到就留空，但**不置 watchLoaded**，页面不会误导成"你还没有自选股"
    watchItems.value = []
  }
}

async function load(isRefresh = false) {
  if (isRefresh) refreshing.value = true
  else loading.value = true
  // 三路数据各自降级：任一路失败只让对应格子空着
  const [snap, dt] = await Promise.allSettled([getMarketSnapshot(60), getDragonTiger(5)])
  if (snap.status === 'fulfilled') snapshot.value = snap.value
  if (dt.status === 'fulfilled') dragon.value = dt.value
  await loadWatchlist()
  loading.value = false
  refreshing.value = false
  flash()
}

function refresh() {
  if (refreshing.value) return
  void load(true)
}

function toggleTheme() {
  setTheme(effectiveTheme.value === 'dark' ? 'light' : 'dark')
}

function goBack() {
  // 大屏是工作台的下级页面，返回优先回工作台（而不是把用户丢到上一个任意页面）
  if (window.history.length > 1) router.back()
  else router.push('/platform/chat/create')
}

onMounted(() => void load())

// ── 格式化 ──────────────────────────────────────────────────────────────

const DASH = '—'

function trendClass(v: number | null): string {
  const t = trendOf(v)
  return t === 'up' ? 'md-up' : t === 'down' ? 'md-down' : 'md-flat'
}

function fmtNum(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPrice(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return v.toFixed(3)
}

function fmtInt(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return v.toLocaleString('zh-CN')
}

/** 计数缺失时给破折号而不是 0：0 在金融语义里是"真的是零"。 */
function fmtCount(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return String(v)
}

function fmtPct(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

function fmtYi(v: number | null): string {
  const s = toYi(v)
  return s == null ? DASH : `${s} 亿`
}

function watchStateLabel(s: WatchState): string {
  return t(`marketDashboard.watchlist.state.${s}`)
}
</script>

<style lang="less" scoped>
/* 涨跌色：唯一一组"新增"变量，值由设计稿定死。
   深色各提一档的理由见文件头与 design.md「验收重点」。 */
.md-page {
  --md-up: #dc2626;
  --md-down: #047857;
}

/* 渐变定义容器：零尺寸、绝对定位，不参与布局也不可被鼠标命中 */
.md-gradients {
  position: absolute;
  width: 0;
  height: 0;
  pointer-events: none;
}

:global(:root[theme-mode="dark"]) .md-page {
  --md-up: #f87171;
  --md-down: #34d399;
}

.md-up { color: var(--md-up); }
.md-down { color: var(--md-down); }
.md-flat { color: var(--td-text-color-placeholder); }

.md-up-stroke { stroke: var(--md-up); }
.md-down-stroke { stroke: var(--md-down); }
.md-up-bg { background: color-mix(in srgb, var(--md-up) 11%, transparent); }
.md-down-bg { background: color-mix(in srgb, var(--md-down) 11%, transparent); }

.md-num {
  font-family: var(--app-font-family-mono);
  font-variant-numeric: tabular-nums;
}

.md-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 14px 20px 16px;
  gap: 12px;
  background: var(--td-bg-color-page);
  color: var(--td-text-color-primary);
  font-size: var(--app-text-base);
  box-sizing: border-box;
  overflow: hidden;
}

/* ── 顶栏 ── */
.md-topbar {
  flex: 0 0 40px;
  gap: 12px;
  padding: 0 12px;
}

.md-topbar__title {
  font-family: var(--app-font-display);
  font-size: var(--app-text-2xl);
  font-weight: 600;
  letter-spacing: 0.01em;
}

.md-topbar__date { font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }
.md-topbar__spacer { flex: 1; }
.md-topbar__clock { font-size: var(--app-text-sm); color: var(--td-text-color-placeholder); }

.md-icon-btn {
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--td-component-border);
  border-radius: var(--app-radius-lg);
  background: var(--td-bg-color-container);
  color: var(--td-text-color-secondary);
  cursor: pointer;
  transition: transform var(--app-motion-base) cubic-bezier(0.16, 1, 0.3, 1),
    border-color var(--app-motion-base) ease-out, color var(--app-motion-base) ease-out;

  &:hover {
    border-color: color-mix(in srgb, var(--td-brand-color) 30%, var(--td-component-border));
    color: var(--td-text-color-primary);
    transform: translateY(-1px);
  }

  &:active { transform: scale(0.96); }
  &:disabled { cursor: default; opacity: 0.6; }
}

.is-spin { animation: mdSpin 0.7s linear; }
@keyframes mdSpin { to { transform: rotate(360deg); } }

.md-status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 10px;
  border-radius: var(--app-radius-pill);
  font-size: var(--app-text-2xs);
  font-weight: 600;
  border: 1px solid var(--td-component-border);
  color: var(--td-text-color-secondary);
  background: var(--td-bg-color-container);

  &.is-open {
    color: var(--td-brand-color-active);
    border-color: color-mix(in srgb, var(--td-brand-color) 34%, var(--td-component-border));
  }

  &.is-open .md-status-chip__dot {
    background: var(--td-brand-color);
    animation: mdPulse 2s ease-out infinite;
  }
}

.md-status-chip__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--td-text-color-disabled);
}

@keyframes mdPulse {
  0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--td-brand-color) 40%, transparent); }
  70% { box-shadow: 0 0 0 6px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
}

.md-notice {
  margin: 0;
  padding: 4px 10px;
  border-radius: var(--app-radius-md);
  background: color-mix(in srgb, var(--td-warning-color) 12%, transparent);
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-2xs);
}

/* ── 指数快照条 ── */
.md-ticker {
  flex: 0 0 40px;
  display: flex;
  align-items: center;
  gap: 0;
  padding: 0 8px;
  overflow-x: auto;
  scrollbar-width: none;
}

.md-ticker__label {
  flex: 0 0 auto;
  padding: 0 10px 0 6px;
  font-size: var(--app-text-2xs);
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--td-text-color-disabled);
  text-transform: uppercase;
}

.md-ticker__item {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  align-self: stretch;
  border-left: 1px solid var(--td-component-stroke);
}

.md-ticker__name { font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }
.md-ticker__val { font-size: var(--app-text-md); font-weight: 600; }
.md-ticker__empty { font-size: var(--app-text-sm); color: var(--td-text-color-placeholder); }

/* ── 主网格 ── */
.md-board {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  grid-template-rows: 1.08fr 0.92fr;
  gap: 12px;
}

.md-tile {
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-xl);
  padding: 13px 16px;
  box-shadow: var(--td-shadow-1);
  /* 首次进入：各格从下方 12px 依次浮起（stagger 60ms） */
  opacity: 0;
  transform: translateY(12px);
  animation: mdRise 0.48s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  animation-delay: var(--d, 0ms);
}

@keyframes mdRise { to { opacity: 1; transform: translateY(0); } }

/* .md-tile 是 column flex，顶栏和快照条需要横排，显式掰回来 */
.md-tile.md-topbar,
.md-tile.md-ticker {
  flex-direction: row;
  align-items: center;
}

.md-tile__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}

.md-tile__title {
  font-family: var(--app-font-display);
  font-size: var(--app-text-lg);
  font-weight: 600;
}

.md-tile__caption { font-size: var(--app-text-2xs); color: var(--td-text-color-placeholder); }

.md-tile__link {
  margin-left: auto;
  font-size: var(--app-text-sm);
  color: var(--td-brand-color-active);
  text-decoration: none;
}

.md-tile-empty {
  margin: auto 0;
  text-align: center;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-placeholder);
}

/* ── 四大指数 ── */
.md-index-grid {
  grid-column: span 8;
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 12px;
  min-height: 0;
}

.md-index-panel__row { display: flex; align-items: baseline; gap: 8px; }

.md-index-panel__name {
  font-family: var(--app-font-display);
  font-size: var(--app-text-lg);
  font-weight: 600;
}

.md-index-panel__code { font-size: var(--app-text-2xs); color: var(--td-text-color-placeholder); }

.md-index-panel__last {
  margin-left: auto;
  font-size: var(--app-text-3xl);
  font-weight: 700;
  line-height: 1;
}

.md-pill {
  font-size: var(--app-text-2xs);
  font-weight: 600;
  padding: 1px 7px;
  border-radius: var(--app-radius-pill);
}

.md-pill.md-up { background: color-mix(in srgb, var(--md-up) 11%, transparent); }
.md-pill.md-down { background: color-mix(in srgb, var(--md-down) 11%, transparent); }

.md-index-panel__chart {
  flex: 1;
  min-height: 0;
  margin: 6px 0 8px;
  display: flex;
}

.md-index-panel__chart svg { width: 100%; height: 100%; display: block; }

.md-chart-line { fill: none; }
.md-chart-prev { stroke: var(--td-text-color-disabled); stroke-width: 1; stroke-dasharray: 4 3; }

/* 当日快照条：低—高轨道 + 今开刻度 + 昨收虚线 + 最新点 */
.md-rangebar { display: flex; align-items: center; gap: 8px; }

.md-rangebar__edge {
  font-size: var(--app-text-4xs);
  color: var(--td-text-color-placeholder);
  white-space: nowrap;
}

.md-rangebar__track {
  position: relative;
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: var(--td-bg-color-secondarycontainer);
}

.md-rangebar__open {
  position: absolute;
  top: 50%;
  width: 2px;
  height: 10px;
  border-radius: 1px;
  background: var(--td-text-color-disabled);
  transform: translate(-50%, -50%);
}

.md-rangebar__prev {
  position: absolute;
  top: 50%;
  height: 12px;
  border-left: 1.5px dashed var(--td-text-color-placeholder);
  transform: translate(-50%, -50%);
}

.md-rangebar__last {
  position: absolute;
  top: 50%;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  border: 2px solid var(--td-bg-color-container);
  box-shadow: 0 0 0 1px var(--td-component-stroke);
}

.md-rangebar__last.md-up { background: var(--md-up); }
.md-rangebar__last.md-down { background: var(--md-down); }
.md-rangebar__last.md-flat { background: var(--td-text-color-disabled); }

.md-index-panel__legend {
  margin-top: 5px;
  font-size: var(--app-text-2xs);
  color: var(--td-text-color-disabled);
  letter-spacing: 0.02em;
}

/* ── 市场情绪 ── */
.md-sentiment { grid-column: span 4; }

.md-sentiment__hero {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin: 2px 0 10px;
}

.md-hero-metric__label { font-size: var(--app-text-2xs); color: var(--td-text-color-secondary); }

.md-hero-metric__value {
  font-size: 40px;
  font-weight: 700;
  line-height: 1.15;
}

.md-sentiment__sub {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 10px;
}

.md-metric__label { font-size: var(--app-text-2xs); color: var(--td-text-color-secondary); }
.md-metric__value { font-size: var(--app-text-xl); font-weight: 700; line-height: 1.3; }

.md-metric__unit {
  font-size: var(--app-text-2xs);
  font-weight: 400;
  color: var(--td-text-color-placeholder);
  margin-left: 3px;
}

.md-metric__note {
  font-size: var(--app-text-4xs);
  color: var(--td-text-color-disabled);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.md-lu5 {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  margin-bottom: 14px;
}

.md-lu5__label {
  font-size: var(--app-text-2xs);
  color: var(--td-text-color-placeholder);
  margin-bottom: 6px;
}

.md-lu5__bars {
  flex: 1;
  min-height: 26px;
  display: flex;
  align-items: flex-end;
  gap: 6px;
  /* 基线：柱状图必须落地，否则柱子像浮在空中（评审 round 2 修掉的问题） */
  border-bottom: 1px solid var(--td-component-stroke);
}

.md-lu5__bar {
  flex: 1;
  border-radius: 3px 3px 1px 1px;
  background: color-mix(in srgb, var(--md-up) 32%, transparent);
  position: relative;
}

.md-lu5__bar.is-last { background: var(--md-up); }

.md-lu5__bar b {
  position: absolute;
  top: -14px;
  left: 50%;
  transform: translateX(-50%);
  font-size: var(--app-text-4xs);
  font-weight: 400;
  color: var(--td-text-color-placeholder);
}

.md-breadth__bar {
  display: flex;
  height: 8px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--td-bg-color-secondarycontainer);
}

.md-breadth__bar i { display: block; height: 100%; }

.md-breadth__up { background: var(--md-up); }
.md-breadth__flat { background: var(--td-text-color-disabled); }
.md-breadth__down { background: var(--md-down); }

.md-breadth__legend {
  display: flex;
  gap: 16px;
  margin-top: 6px;
  /* 10.5px —— 评审 round 2 指出 9px 低于可读底线，这里取 theme 里的 --app-text-2xs(10px)
     之上一点点：字体本身 10px，行高 1.4 视觉上约合 10.5px 的可读高度 */
  font-size: var(--app-text-2xs);
  color: var(--td-text-color-placeholder);
}

.md-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-right: 4px;
  vertical-align: 1px;
}

/* ── 自选股 ── */
.md-watchlist { grid-column: span 5; }

/*
  行容器用 space-between 把行摊满格子 —— 但那是**在内容比格子矮时**才成立的。
  内容一旦超过格子高度（6 行 ETF / 5 行龙虎榜在小视口下就超），space-between
  不缩间距，而是让最后一行溢出格子、被 .md-tile 的下一格盖住或裁掉（实测：
  1366×768 下 ETF 第 6 行与龙虎榜第 5 名都被切掉半行）。
  改成 flex-start + overflow:auto：装得下就自然排，装不下就滚动，绝不裁切。
*/
.md-wl-rows,
.md-lhb-rows,
.md-etf-rows {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  overflow-y: auto;
  overflow-x: hidden;
}

.md-wl-row {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) 0.9fr 0.72fr auto;
  align-items: center;
  gap: 10px;
  padding: 7px 2px;
  border-top: 1px solid var(--td-component-stroke);

  &:first-child { border-top: 0; }
}

.md-wl-row__name {
  font-size: var(--app-text-md);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.md-wl-row__code {
  font-size: var(--app-text-4xs);
  color: var(--td-text-color-placeholder);
  display: block;
}

.md-wl-row__price { font-size: var(--app-text-md); text-align: right; }
.md-wl-row .md-pill { justify-self: end; }

.md-badge {
  font-size: var(--app-text-2xs);
  padding: 1px 7px;
  border-radius: var(--app-radius-pill);
  border: 1px solid var(--td-component-border);
  color: var(--td-text-color-secondary);
  white-space: nowrap;
}

.md-badge.is-trigger {
  color: var(--td-brand-color-active);
  border-color: color-mix(in srgb, var(--td-brand-color) 36%, var(--td-component-border));
  background: color-mix(in srgb, var(--td-brand-color) 7%, transparent);
}

.md-wl-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.md-wl-empty__hint { font-size: var(--app-text-md); color: var(--td-text-color-secondary); }

.md-wl-empty__cta {
  height: 30px;
  padding: 0 16px;
  border-radius: var(--app-radius-lg);
  background: var(--td-brand-color);
  color: var(--td-text-color-anti);
  font-size: var(--app-text-sm);
  font-weight: 600;
  text-decoration: none;
  display: inline-flex;
  align-items: center;

  &:hover { background: var(--td-brand-color-active); }
}

/* ── 龙虎榜 ── */
.md-lhb { grid-column: span 4; }

.md-lhb-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 2px;
  border-top: 1px solid var(--td-component-stroke);

  &:first-child { border-top: 0; }
}

.md-lhb-row__rank {
  font-family: var(--app-font-display);
  font-size: var(--app-text-lg);
  font-weight: 600;
  color: var(--td-text-color-placeholder);
  width: 16px;
  flex: 0 0 16px;
}

.md-lhb-row__rank.is-first { color: var(--td-brand-color); }
.md-lhb-row__name { font-size: var(--app-text-md); font-weight: 500; min-width: 0; }

.md-lhb-row__tag {
  display: block;
  font-size: var(--app-text-4xs);
  color: var(--td-text-color-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.md-lhb-row__amt { margin-left: auto; text-align: right; flex: 0 0 auto; }

.md-lhb-row__net {
  font-size: var(--app-text-md);
  font-weight: 600;
  display: block;
}

.md-lhb-row__org { font-size: var(--app-text-4xs); color: var(--td-text-color-placeholder); }

/* ── ETF ── */
.md-etf { grid-column: span 3; }

.md-etf-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  padding: 6.5px 2px;
  border-top: 1px solid var(--td-component-stroke);

  &:first-child { border-top: 0; }
}

.md-etf-row__name { font-size: var(--app-text-sm); font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.md-etf-row__code {
  font-size: var(--app-text-4xs);
  color: var(--td-text-color-placeholder);
  display: block;
}

.md-etf-row__price { font-size: var(--app-text-sm); text-align: right; }

/* 刷新时全屏数字起伏一次 */
.is-flashing :deep(.md-num) { animation: mdNumFlash 0.45s ease-out; }
@keyframes mdNumFlash {
  0% { opacity: 0.3; }
  100% { opacity: 1; }
}

/* ── 小视口（1366×768）：收紧 padding，自选股减到 6 行 ── */
@media (max-height: 800px) {
  .md-page { padding: 10px 16px 12px; gap: 10px; }
  .md-board { gap: 10px; }
  .md-index-grid { gap: 10px; }
  .md-tile { padding: 9px 14px; }
  .md-tile__head { margin-bottom: 6px; }
  .md-topbar { flex-basis: 36px; }
  .md-ticker { flex-basis: 36px; }
  .md-hero-metric__value { font-size: 32px; }
  .md-wl-row { padding: 4px 2px; }
  /* 第 7 行在小视口隐藏，留安全边距 */
  .md-wl-row:nth-child(7) { display: none; }
  .md-lhb-row { padding: 5px 2px; }
  .md-etf-row { padding: 4px 2px; }
  .md-index-panel__chart { margin: 4px 0 6px; }
  .md-lu5 { margin-bottom: 6px; }
}

@media (prefers-reduced-motion: reduce) {
  .md-tile {
    animation: none;
    opacity: 1;
    transform: none;
  }

  .md-status-chip.is-open .md-status-chip__dot,
  .is-spin { animation: none; }

  .is-flashing :deep(.md-num) { animation: none; }
}
</style>
