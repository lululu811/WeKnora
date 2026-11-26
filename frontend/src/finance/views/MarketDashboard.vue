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

  两种形态，同一份代码：
  - `embedded=false`（/dashboard）：全屏大屏，自己占满视口，顶栏带返回/主题/刷新。
  - `embedded=true`（个股追踪 → 大盘 tab）：塞进侧边栏壳的正常流里，顶栏由外层壳
    提供、`100vh` 换成自适应、@media 收紧规则整段不生效（那套是给固定视口调的）。
    ETF 卡在嵌入态降级成摘要行 —— 全表由「权重 ETF」tab 独占，不在两处各画一次。

  数据链路（与 finance/api/market.ts 的注释对应）：
    指数 / 情绪 / ETF  → python-service `/api/market/snapshot`
    龙虎榜            → python-service `/api/market/dragon-tiger`
    自选股            → Go `/api/v1/watchlist` + python-service `/api/quotes`
-->
<template>
  <div class="md-page" :class="{ 'is-flashing': flashing, 'is-embedded': embedded }">
    <!--
      折线渐变。必须放在 template 内：SFC 里 `</style>` 之后的第二个
      顶层标签会被 vue-loader 当成 custom block 解析（vite 报 import-analysis 语法错），
      所以渐变定义随模板一起走，不能另起一段顶层 svg。
      墨账：折线与面积一律墨色，不再按涨跌染 —— A 股日涨跌幅多在 ±1% 以内，
      整条线染成红绿是把 0.39% 放大成一条心理冲击。方向由数字讲。
      stop-color 用 var(--td-text-color-secondary)，随浅/深色主题自动切换。
    -->
    <svg class="md-gradients" width="0" height="0" aria-hidden="true">
      <defs>
        <linearGradient id="mdGradInk" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="var(--td-text-color-secondary)" stop-opacity=".18" />
          <stop offset="1" stop-color="var(--td-text-color-secondary)" stop-opacity="0" />
        </linearGradient>
      </defs>
    </svg>
    <!-- 数据源降级提示：非空才出现，不占常态布局 -->
    <p v-if="unavailable.length" class="md-notice" role="status">
      {{ $t('marketDashboard.sourceUnavailable', { sources: unavailable.join('、') }) }}
    </p>

    <!-- ══════ 墨账版 · 全屏 /dashboard ══════
         风格 achromatic-ledger（墨账）：chrome 里没有颜色，图表用墨色，
         只在某个数字变了的地方给一个小小的信号色。块与块之间只靠留白和
         字号层级分开 —— 不画卡片、不描边、不用分隔线。

         为什么和下面的嵌入态分成两套标记：/dashboard 是独立全屏路由，一屏
         看完，构图按"读数 + 账目"重排；个股追踪的「大盘」tab 是滚动工位，
         块多且每块自带交互。第二轮已把嵌入态也换成墨账 —— 两边共用同一套
         体例（只密度不同），见 docs/design/dashboard-ledger-restyle.md。 -->
    <template v-if="!embedded">
      <header class="lg-topbar">
        <button type="button" class="lg-icon-btn" :title="$t('marketDashboard.back')"
          :aria-label="$t('marketDashboard.back')" @click="goBack">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M9 2.5 4.5 7 9 11.5" stroke="currentColor" stroke-width="1.6"
              stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
        <h1 class="lg-topbar__title">{{ $t('marketDashboard.title') }}</h1>
        <span class="lg-topbar__date">{{ dateLine }}</span>
        <span class="lg-topbar__spacer" />
        <span class="lg-topbar__clock md-num">{{ $t('marketDashboard.dataTime') }} {{ clock }}</span>
        <span class="lg-state" :class="{ 'is-open': marketState === 'open' }">
          <span class="lg-state__dot" />
          <span>{{ stateLabel }}</span>
        </span>
        <button type="button" class="lg-icon-btn" :title="themeTitle" :aria-label="themeTitle"
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
        <button type="button" class="lg-primary-btn" :disabled="refreshing"
          :title="$t('marketDashboard.refresh')" :aria-label="$t('marketDashboard.refresh')"
          @click="refresh">
          <svg width="13" height="13" viewBox="0 0 14 14" fill="none" aria-hidden="true"
            :class="{ 'is-spin': refreshing }">
            <path d="M12 7A5 5 0 1 1 7 2c1.9 0 3.5 1 4.4 2.6M12 1.4v3.2H8.8" stroke="currentColor"
              stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span>{{ $t('marketDashboard.refresh') }}</span>
        </button>
      </header>

      <main class="lg-board">
        <!-- ── 左栏：读数 · 发现 · 自选股 ── -->
        <section class="lg-col lg-col--read">
          <div class="lg-read">
            <h2 class="lg-read__title">{{ $t('marketDashboard.ledger.readTitle') }}</h2>
            <dl class="lg-read__list">
              <div v-for="r in readRows" :key="r.key" class="lg-read__row">
                <dt>{{ r.label }}</dt>
                <dd class="md-num" :class="r.cls">{{ r.value }}</dd>
              </div>
            </dl>
          </div>

          <div class="lg-block">
            <h2 class="lg-block__title">{{ $t('marketDashboard.ledger.findingTitle') }}</h2>
            <p class="lg-finding">{{ findingMain }}</p>
            <p v-for="(line, i) in findingSubs" :key="i" class="lg-finding__sub">{{ line }}</p>
          </div>

          <div class="lg-block">
            <h2 class="lg-block__title">
              {{ $t('marketDashboard.watchlist.title') }}
              <span v-if="watchlistHasRows" class="lg-block__note md-num">
                {{ $t('marketDashboard.watchlist.count', { n: watchRows.length }) }}
              </span>
            </h2>
            <router-link v-if="watchlistHasRows" class="lg-block__link" to="/platform/watchlist">
              {{ $t('marketDashboard.watchlist.all') }}
            </router-link>

            <div v-if="watchlistEmpty" class="lg-empty">
              <span>{{ $t('marketDashboard.watchlist.empty') }}</span>
              <router-link class="lg-empty__cta" to="/platform/watchlist">
                {{ $t('marketDashboard.watchlist.emptyCta') }}
              </router-link>
            </div>
            <ul v-else-if="watchlistHasRows" class="lg-rows">
              <li v-for="w in watchRows" :key="w.thscode" class="lg-row lg-row--watch">
                <span class="lg-row__name">
                  <template v-if="w.name">{{ w.name }}</template>
                  <span class="lg-row__code md-num">{{ w.thscode }}</span>
                </span>
                <span class="lg-row__num md-num">{{ fmtNum(w.close) }}</span>
                <span class="lg-signal" :class="trendClass(w.change_pct)">
                  <i class="lg-signal__dot" />
                  <span class="md-num">{{ fmtPctSigned(w.change_pct) }}</span>
                </span>
                <span class="lg-row__tag">{{ watchStateLabel(w.state) }}</span>
              </li>
            </ul>
          </div>

          <p class="lg-source">{{ sourceLine }}</p>
        </section>

        <!-- ── 右栏：收盘表 · 情绪明细 · 龙虎榜 / 权重 ETF ── -->
        <section class="lg-col lg-col--book">
          <div class="lg-block">
            <h2 class="lg-block__title">{{ $t('marketDashboard.ledger.title') }}</h2>
            <table class="lg-ledger">
              <colgroup>
                <col />
                <col style="width: 96px" />
                <col style="width: 86px" />
                <col style="width: 84px" />
                <col style="width: 128px" />
              </colgroup>
              <thead>
                <tr>
                  <th>{{ $t('marketDashboard.ledger.col.name') }}</th>
                  <th class="num">{{ $t('marketDashboard.ledger.col.close') }}</th>
                  <th class="num">{{ $t('marketDashboard.ledger.col.change') }}</th>
                  <th class="num">{{ $t('marketDashboard.ledger.col.pct') }}</th>
                  <th class="line">{{ $t('marketDashboard.ledger.col.line') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in ledgerRows" :key="r.thscode" class="lg-ledger__row">
                  <td class="lg-ledger__name">{{ r.name }}</td>
                  <td class="num md-num">{{ r.lastText }}</td>
                  <td class="num md-num">{{ r.changeText }}</td>
                  <td class="num">
                    <span class="lg-signal" :class="r.trend">
                      <i class="lg-signal__dot" />
                      <span class="md-num">{{ r.pctText }}</span>
                    </span>
                  </td>
                  <td class="line">
                    <svg v-if="r.line" class="lg-spark" viewBox="0 0 100 20" preserveAspectRatio="none"
                      aria-hidden="true" focusable="false">
                      <path :d="r.line" fill="none" stroke="currentColor" stroke-width="1.4"
                        stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />
                    </svg>
                    <span v-else class="lg-dash">—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="lg-block">
            <h2 class="lg-block__title">{{ $t('marketDashboard.ledger.detailTitle') }}</h2>
            <ul class="lg-acct">
              <li v-for="r in acctRows" :key="r.key" class="lg-acct__row" :class="{ 'is-signal': r.signal }">
                <span class="lg-acct__k">
                  <i v-if="r.signal" class="lg-acct__sig" />
                  {{ r.label }}
                </span>
                <span class="lg-acct__v md-num" :class="r.cls">{{ r.value }}</span>
                <span class="lg-acct__m">{{ r.note }}</span>
              </li>
            </ul>
          </div>

          <div class="lg-two">
            <div class="lg-block">
              <h2 class="lg-block__title">
                {{ $t('marketDashboard.dragonTiger.title') }}
                <span class="lg-block__note">
                  {{ $t('marketDashboard.ledger.dragonCaption', { n: dragonTop.length, date: dragonDate }) }}
                </span>
              </h2>
              <ul v-if="dragonRows.length" class="lg-rows">
                <li v-for="(r, i) in dragonTop" :key="r.thscode" class="lg-row lg-row--rank">
                  <span class="lg-row__rank md-num">{{ i + 1 }}</span>
                  <span class="lg-row__name">
                    {{ r.name }}
                    <span class="lg-row__tag">{{ r.tag }}</span>
                  </span>
                  <span class="lg-row__num md-num">{{ $t('marketDashboard.dragonTiger.net') }} {{ fmtYi(r.net_value) }}</span>
                </li>
              </ul>
              <p v-else-if="!loading" class="lg-empty">{{ $t('marketDashboard.dragonTiger.empty') }}</p>
            </div>

            <div class="lg-block">
              <h2 class="lg-block__title">
                {{ $t('marketDashboard.etf.title') }}
                <span class="lg-block__note">{{ etfCaption }}</span>
              </h2>
              <ul v-if="etfRows.length" class="lg-rows">
                <li v-for="r in etfTop" :key="r.thscode" class="lg-row lg-row--etf">
                  <span class="lg-row__name">{{ r.name }}</span>
                  <span class="lg-row__num md-num">{{ fmtPrice(r.price) }}</span>
                  <span class="lg-signal" :class="trendClass(r.change_pct)">
                    <i class="lg-signal__dot" />
                    <span class="md-num">{{ fmtPctSigned(r.change_pct) }}</span>
                  </span>
                  <!-- 份额变动是"国家队在不在进出"的唯一证据，价格涨跌只是结果。
                       少了这一列，这块就退回成一张涨跌榜。 -->
                  <span class="lg-signal lg-signal--end" :class="trendClass(r.share_change_pct)">
                    <i class="lg-signal__dot" />
                    <span class="md-num">
                      {{ r.share_change_pct != null ? fmtPctSigned(r.share_change_pct) : r.share_text }}
                    </span>
                  </span>
                  <span class="lg-row__mult md-num">{{ r.multiple_text }}</span>
                </li>
              </ul>
              <p v-else-if="!loading" class="lg-empty">{{ $t('marketDashboard.noData') }}</p>
            </div>
          </div>
        </section>
      </main>
    </template>

    <!-- ══════ 嵌入态（个股追踪 → 大盘 tab）══════
         沿用方向 01「午后的工作室」的卡片版：它是可滚动的工位屏，块多且
         每块自带交互，与 /dashboard 的墨账版是两种不同的用法。 -->
    <template v-else>

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
            <span class="md-index-panel__last md-num">{{ fmtNum(idx.last) }}</span>
            <span class="md-pill md-num" :class="trendClass(idx.change_pct)">
              {{ fmtPct(idx.change_pct) }}
            </span>
          </div>

          <div class="md-index-panel__chart">
            <!-- 只有一个读数画不出线：显示文案，不给一条会骗人的横线 -->
            <p v-if="!idx.line" class="md-tile-empty">{{ idx.emptyText }}</p>
            <svg v-else viewBox="0 0 260 64" preserveAspectRatio="none" role="img"
              :aria-label="`${idx.name} 60日走势`">
              <path :d="idx.area" fill="url(#mdGradInk)" />
              <line v-if="idx.prevY != null" x1="3" x2="257" :y1="idx.prevY" :y2="idx.prevY"
                class="md-chart-prev" vector-effect="non-scaling-stroke" />
              <path :d="idx.line" fill="none" class="md-chart-line" stroke-width="1.6"
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
            <!-- 图例：方向由**点**讲，文字（含数字）一律墨色。
                 竞价条那处图例也是同一套写法 —— 同屏两种写法会让读者以为两套数据。 -->
            <span><i class="md-dot md-breadth__up" />{{ $t('marketDashboard.sentiment.breadth.up') }}
              <b class="md-num">{{ fmtInt(sentiment.breadth.up) }}</b></span>
            <span><i class="md-dot md-breadth__flat" />{{ $t('marketDashboard.sentiment.breadth.flat') }}
              <b class="md-num">{{ fmtInt(sentiment.breadth.flat) }}</b></span>
            <span><i class="md-dot md-breadth__down" />{{ $t('marketDashboard.sentiment.breadth.down') }}
              <b class="md-num">{{ fmtInt(sentiment.breadth.down) }}</b></span>
          </div>
        </div>
      </section>

      <!--
        ── 板块 / 情绪位置 / 期股联动（2026-10-09 新增）──

        位置不是按数据新鲜度排的，是按「这一块回答哪个问题」：

          板块榜      钱往哪个方向走      ← 大盘页真正缺的那一块
          连板梯队    情绪在垒还是在塌
          期股联动    对冲盘有多重（期货比现货先表态）

        三块都只在大盘 tab（embedded）出现。全屏 /dashboard 是一屏看完的速览，
        塞三块进去会把它撑成两屏 —— 而那一屏的用途恰恰是"一眼"。

        `md-full` 让它们在大屏态隐藏、嵌入态整宽显示，与 .md-tile 的栅格并排无关。
      -->
      <div v-if="embedded" class="md-full">
        <!-- 竞价排在板块榜之前：它解释"今天怎么开的"，板块榜解释"钱往哪走"，
             时间上先后、逻辑上因果。 -->
        <AuctionStrip />
        <SectorBoard class="md-full__item" />
        <div class="md-full__pair">
          <LadderHeat />
          <BasisStrip />
        </div>
        <div class="md-full__pair">
          <HotStockRadar />
          <DividendCalendar />
        </div>
      </div>

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
              <!-- name 可能为空（库里存的是代码而非名字，见 watchRows 的说明），
                   空时不渲染这一段，让 thscode 单独当标识，不留一段空白撑出间距 -->
              <template v-if="w.name">{{ w.name }}</template>
              <span class="md-wl-row__code md-num">{{ w.thscode }}</span>
            </span>
            <span class="md-wl-row__price md-num">{{ fmtNum(w.close) }}</span>
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
          <!-- 标题不再写死"前五"：写的是真实条数，改 limit 不会让标题说谎 -->
          <span class="md-tile__caption">{{ dragonCaption }}</span>
        </div>
        <div v-if="dragonRows.length" class="md-lhb-rows">
          <div v-for="(r, i) in dragonRows" :key="r.thscode" class="md-lhb-row">
            <span class="md-lhb-row__rank md-num" :class="{ 'is-first': i === 0 }">{{ i + 1 }}</span>
            <span class="md-lhb-row__name">
              {{ r.name }}
              <span class="md-lhb-row__tag">{{ r.tag }}</span>
            </span>
            <span class="md-lhb-row__amt">
              <span class="md-lhb-row__net md-num">
                {{ $t('marketDashboard.dragonTiger.net') }} {{ fmtYi(r.net_value) }}
              </span>
              <span class="md-lhb-row__org md-num">
                {{ $t('marketDashboard.dragonTiger.org') }}
                <span>{{ fmtYi(r.org_net_value) }}</span>
              </span>
            </span>
          </div>
        </div>
        <!-- 当日无数据要明说，不要留白也不要报错 -->
        <p v-else-if="!loading" class="md-tile-empty">{{ $t('marketDashboard.dragonTiger.empty') }}</p>
      </section>

      <!-- ── 权重 ETF · 大资金动向 ── -->
      <section class="md-tile md-etf" style="--d: 540ms">
        <div class="md-tile__head">
          <span class="md-tile__title">{{ $t('marketDashboard.etf.title') }}</span>
          <span class="md-tile__caption">{{ etfCaption }}</span>
        </div>
        <!--
          嵌入态：全表由「权重 ETF」tab 独占，这张卡降级成一行摘要。
          两处各画一张同样的表不是"更方便"，是同一件事被画两遍 —— 用户切 tab
          会以为内容变了。大屏态（/dashboard）没有那个 tab，所以保持原来的全表。
        -->
        <div v-if="embedded" class="md-etf-summary">
          <div class="md-etf-summary__stats">
            <div class="md-etf-summary__stat">
              <span class="md-etf-summary__k">{{ $t('tracking.etf.signalCount') }}</span>
              <span class="md-etf-summary__v md-num">{{ etfSignalText }}</span>
            </div>
            <div class="md-etf-summary__stat">
              <span class="md-etf-summary__k">{{ $t('tracking.etf.inflowCount') }}</span>
              <span class="md-etf-summary__v md-num" :class="trendClass(etfInflowDelta)">
                {{ etfInflowText }}
              </span>
            </div>
            <div class="md-etf-summary__stat">
              <span class="md-etf-summary__k">{{ $t('tracking.etf.topMove') }}</span>
              <span class="md-etf-summary__v">{{ etfTopMoveText }}</span>
            </div>
          </div>
          <router-link class="md-etf-summary__cta"
            :to="{ path: '/platform/watchlist', query: { tab: 'etf' } }">
            {{ $t('tracking.etf.openWorkspace') }}
          </router-link>
        </div>

        <template v-else>
          <div v-if="etfRows.length" class="md-etf-rows">
            <div v-for="r in etfRows" :key="r.thscode" class="md-etf-row">
              <span class="md-etf-row__name">
                {{ r.name }}
                <span class="md-etf-row__code md-num">{{ r.thscode }}</span>
              </span>
              <span class="md-etf-row__price md-num" :class="trendClass(r.change_pct)">{{ fmtPrice(r.price) }}</span>
              <span class="md-pill md-num" :class="trendClass(r.change_pct)">{{ fmtPct(r.change_pct) }}</span>
              <!-- 份额列是这张卡的主角：季频口径，红=净流入（与价格红涨同一套语义） -->
              <span class="md-etf-row__share md-num" :class="r.share_class">{{ r.share_text }}</span>
              <span
                class="md-etf-row__mult md-num"
                :class="{ 'md-etf-row__mult--hot': r.multiple_hot }"
              >{{ r.multiple_text }}</span>
              <span v-if="r.signal" class="md-etf-row__signal">
                <span class="md-etf-row__dot" />{{ $t('marketDashboard.etf.signal') }}
              </span>
            </div>
          </div>
          <p v-else-if="!loading" class="md-tile-empty">{{ $t('marketDashboard.noData') }}</p>
          <!-- 预留披露持仓（B 线）：数据通了再换成真持仓，这里只占位不误导 -->
          <p class="md-tile__caption md-etf__foot">{{ $t('marketDashboard.etf.holdings') }}</p>
        </template>
      </section>
    </main>
    </template>
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
import { getEtfFlow, type EtfFlowResponse } from '@/finance/api/pulse'
import { fetchQuotes, listWatchlist, type Quote, type WatchState } from '@/finance/api/watchlist'
import AuctionStrip from '@/finance/components/market/AuctionStrip.vue'
import SectorBoard from '@/finance/components/market/SectorBoard.vue'
import LadderHeat from '@/finance/components/market/LadderHeat.vue'
import BasisStrip from '@/finance/components/market/BasisStrip.vue'
import DividendCalendar from '@/finance/components/market/DividendCalendar.vue'
import HotStockRadar from '@/finance/components/market/HotStockRadar.vue'
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
import { displayStockName } from '@/finance/utils/stockDisplayName'

const props = withDefaults(
  defineProps<{
    /**
     * 嵌入态：渲染进个股追踪页的「大盘」tab，而不是 /dashboard 全屏路由。
     * 只改三处 —— 顶栏（交给外层壳）、视口高度约束（交给正常流）、
     * ETF 卡（全表由「权重 ETF」tab 独占，这里降级成摘要行）。
     */
    embedded?: boolean
  }>(),
  { embedded: false },
)
const embedded = computed(() => props.embedded)

const router = useRouter()
const { t } = useI18n()
const { effectiveTheme, setTheme } = useTheme()

/** 龙虎榜取多少条。后端上限 50，写小了就等于自己把榜单截断了。 */
const DRAGON_LIMIT = 50

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

/** 龙虎榜副标题：真实条数 + 交易日。原 i18n 文案写死"前五"，与实际条数脱钩。 */
const dragonCaption = computed(() => {
  const n = dragonRows.value.length
  const d = dragon.value?.trade_date
  return d
    ? t('marketDashboard.dragonTiger.captionCount', { n, date: d })
    : t('marketDashboard.dragonTiger.captionCount', { n, date: DASH })
})

// ── 权重 ETF · 大资金动向 ────────────────────────────────────────────────
//
// 回答的是「国家队是不是在进出宽基 ETF、动的哪只、力度多大」。份额变动才是
// 「进出」的证据 —— 价格涨跌只是结果。这里还有一条口径陷阱：份额来自季报，
// 是**季频**的，所以 caption 必须写清观测日，否则读者会把「+57.81%」读成今天。

/** etf-flow 读数。null = 没拿到（首屏未返回 / 请求失败）→ 下面自动回退。 */
const etfFlow = ref<EtfFlowResponse | null>(null)

/** 成交放大高亮阈值。取自后端 signal 的量能口径，前端只做展示，不在这里复算结论。 */
const ETF_HOT_MULTIPLE = 1.382

interface EtfRow {
  thscode: string
  name: string
  price: number | null
  change_pct: number | null
  /** 份额变动（%），季频。null = 没有上一观测点。 */
  share_change_pct: number | null
  /** 文本与配色在 computed 里定好，模板只做渲染，不再判断口径。 */
  share_text: string
  share_class: string
  multiple_text: string
  multiple_hot: boolean
  signal: boolean
}

const etfRows = computed<EtfRow[]>(() => {
  const items = etfFlow.value?.items ?? []
  if (items.length) {
    // etf-flow 契约里没有涨跌字段 —— 用快照里同代码的 change_pct 补齐，
    // 补不到就只显示价格（fmtPct(null) 给破折号），不硬凑一个数出来。
    const byCode = new Map(etfs.value.map((e) => [e.thscode, e]))
    return items.map((it) => {
      const snap = byCode.get(it.thscode)
      return {
        thscode: it.thscode,
        name: it.name,
        price: it.close ?? snap?.last ?? null,
        change_pct: snap?.change_pct ?? null,
        share_change_pct: it.share_change_pct,
        // 份额为 null 显示「—」，**绝不显示 0%** —— 0% 是"这个季度没动"这个结论，
        // 而 null 是"没有上一观测点可比"，两件事不能混。
        share_text: fmtPct(it.share_change_pct),
        share_class: trendClass(it.share_change_pct),
        multiple_text: fmtMultiple(it.turnover_multiple),
        multiple_hot:
          it.turnover_multiple != null && it.turnover_multiple >= ETF_HOT_MULTIPLE,
        signal: it.signal,
      }
    })
  }
  // 回退：etf-flow 拿不到 → 退回旧版纯价格行（快照），份额列写「未同步」。
  // 静默发生，不弹错误、不影响其他卡片：一张辅助卡不该打断「扫一眼」。
  return etfs.value.map((e) => ({
    thscode: e.thscode,
    name: e.name,
    price: e.last,
    change_pct: e.change_pct,
    share_change_pct: null,
    share_text: t('marketDashboard.etf.unsynced'),
    share_class: 'md-etf-row__share--off',
    multiple_text: DASH,
    multiple_hot: false,
    signal: false,
  }))
})

/** 有份额数据才承诺日期，否则这张卡就只剩价格，不给读者一个空的观测日。 */
const etfCaption = computed(() => {
  const f = etfFlow.value
  const items = f?.items ?? []
  if (!items.length) return t('marketDashboard.etf.captionFallback')
  const total = f?.counts?.total ?? items.length
  // 日期取**份额观测日**（item.trade_date，季频口径），不是响应级的行情日——
  // 行情日是今天，份额最新观测可能是一个季度前，混用会把季频伪装成日频。
  const shareDate = items.map((i) => i.trade_date).filter(Boolean).sort().pop() ?? DASH
  return t('marketDashboard.etf.caption', {
    signal: items.filter((i) => i.signal).length,
    total,
    date: shareDate,
  })
})

// ── 嵌入态 ETF 摘要 ──────────────────────────────────────────────────────
//
// 三个数回答"资金在不在动、往哪个方向、动得最大的是哪只"。这三个是看 ETF 的
// 全部理由，其余的列（现价 / 放量 / 信号）是核对细节时才用的，留在 ETF tab。
//
// 口径陷阱照旧：只看**有上一观测点**的那些。`share_change_pct === null` 是
// "没有可比的上期"，不是"没动"，把它算进"增加/减少"的分母会得出假结论。

/** 有份额观测点的子集 —— 摘要的分母，只能是它。 */
const etfWithShare = computed(() =>
  (etfFlow.value?.items ?? []).filter(
    (i) => i.share_change_pct !== null && i.share_change_pct !== undefined,
  ),
)

const etfSignalText = computed(() => {
  const items = etfFlow.value?.items ?? []
  if (!items.length) return DASH
  return `${items.filter((i) => i.signal).length} / ${etfFlow.value?.counts?.total ?? items.length}`
})

/** 份额净增加的家数。没有可比观测点时返回 null —— 显示「—」而不是 0。 */
const etfInflowCount = computed(() => {
  const withShare = etfWithShare.value
  if (!withShare.length) return null
  return withShare.filter((i) => (i.share_change_pct as number) > 0).length
})

const etfInflowDelta = computed(() => {
  const n = etfInflowCount.value
  if (n === null) return null
  const total = etfWithShare.value.length
  // 净流入为 0 不是"持平"结论，是"这批全在流出"——给一个方向量而不是颜色。
  return n === 0 ? -1 : n / total
})

const etfInflowText = computed(() => {
  const n = etfInflowCount.value
  if (n === null) return DASH
  return `${n} / ${etfWithShare.value.length}`
})

/** 变动幅度最大的那一只（取绝对值最大，不分方向 —— 流出也是"动"）。 */
const etfTopMove = computed(() => {
  const withShare = etfWithShare.value
  if (!withShare.length) return null
  return withShare.reduce((best, cur) =>
    Math.abs((cur.share_change_pct as number)) > Math.abs((best.share_change_pct as number))
      ? cur
      : best,
  )
})

const etfTopMoveText = computed(() => {
  const top = etfTopMove.value
  if (!top) return DASH
  return `${top.name} ${fmtPct(top.share_change_pct)}`
})

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
        // 名字优先取行情源（python-service 的标的表），清单里存的名字只当兜底。
        // 存的名字**可能压根不是名字**：新增自选时若客户端只传了代码，库里存下
        // 的就是 "002859"，那一行会渲染成「002859」+「002859.SZ」两行同文。
        // 规则见 utils/stockDisplayName.ts（与个股追踪页共用同一份判定）。
        name: displayStockName(item.name, item.thscode, q?.name),
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
  // 四路数据各自降级：任一路失败只让对应格子空着。
  // etf-flow 失败时 etfFlow 保持 null，etfRows 自动退回快照的纯价格行。
  const [snap, dt, flow] = await Promise.allSettled([
    getMarketSnapshot(60),
    // 50 = 后端上限（`limit: int = Query(5, ge=1, le=50)`）。原来写死 5 是因为
    // 大屏只放得下 5 行；现在行容器自带 overflow-y:auto（见 .md-lhb-rows 的注释），
    // 大屏不滚页也能看全，嵌入态则并排两列铺开 —— 两种形态都不再截断。
    getDragonTiger(DRAGON_LIMIT),
    getEtfFlow(),
  ])
  if (snap.status === 'fulfilled') snapshot.value = snap.value
  if (dt.status === 'fulfilled') dragon.value = dt.value
  if (flow.status === 'fulfilled') etfFlow.value = flow.value
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

/**
 * 墨账版专用的带符号百分比。
 *
 * 仓库的 fmtPct 用 ASCII 连字符表示负号（"-2.17%"），而同一行的涨跌额用的是
 * 真减号 U+2212（"−26.09"）。两种减号并排出现，右侧对齐的列会参差；等宽
 * 数字里 ASCII 连字符又比数字窄一格。墨账版统一用真减号。
 */
function fmtPctSigned(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)}%`
}

/** 成交放大倍数（如 1.12×）。null 给破折号，理由同 fmtCount。 */
function fmtMultiple(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return DASH
  return `${v.toFixed(2)}×`
}

function watchStateLabel(s: WatchState): string {
  return t(`marketDashboard.watchlist.state.${s}`)
}

// ── 墨账版（全屏 /dashboard）─────────────────────────────────────────────
//
// 这一段只服务 v-if="!embedded" 那条分支。风格是 achromatic-ledger：
// chrome 无色、图表用墨色、涨跌只给一个 6px 小点 + 符号。
// **所有数字都从上面的 computed 派生**，不重新取数、不写死值 ——
// 同一个事实只有一处来源，账目和读数栏不会各算各的。

/** 收盘表墨线的几何。与 market-geometry 的 CHART 同一套归一化，只是画得小。 */
const LEDGER_CHART = { width: 100, height: 20, pad: 2 } as const

interface LedgerRow {
  thscode: string
  name: string
  lastText: string
  changeText: string
  pctText: string
  /** 与其余行同一套 class（md-up / md-down / md-flat），小点才认得出红绿。 */
  trend: string
  line: string
}

/**
 * 收盘表的一行：5 只宽基 + 4 大指数 = 9 行。
 *
 * 涨跌额带真减号（−，U+2212）而不是连字符 —— 后者在等宽数字里会被读成短横。
 * 墨线只有一个点时**不画**（linePath 返回零长度 path）：横线会被读成
 * "这段时间没波动"，而真相是"只有一个读数"。
 */
const ledgerRows = computed<LedgerRow[]>(() =>
  [...tickers.value, ...indices.value].map((m) => {
    const norm = normalize(usableCloses(m.series))
    const last = m.last
    const prev = m.prev_close
    const change = last != null && prev != null ? last - prev : null
    const pct = m.change_pct
    return {
      thscode: m.thscode,
      name: m.name,
      lastText: fmtNum(m.last),
      changeText:
        change == null ? DASH : `${change >= 0 ? '+' : '−'}${Math.abs(change).toFixed(2)}`,
      pctText: fmtPctSigned(pct),
      trend: trendClass(pct),
      line:
        norm.length >= 2
          ? linePath(norm, LEDGER_CHART.width, LEDGER_CHART.height, LEDGER_CHART.pad)
          : '',
    }
  }),
)

/** 触板数：涨停 + 炸板。封板率的分母。任一缺失就整体为 null（不拿 0 顶替）。 */
const touchCount = computed(() => {
  const lu = sentiment.value.limit_up
  const bk = sentiment.value.broken
  if (lu == null || bk == null) return null
  return lu + bk
})

/** 封板率（%）。分母是触板数，不是涨停数 —— 炸板的那部分也算触过板。 */
const sealRatePct = computed(() => {
  const lu = sentiment.value.limit_up
  const total = touchCount.value
  if (lu == null || total == null || total <= 0) return null
  return (lu / total) * 100
})

/**
 * 左栏读数：五个数，不带副说明。
 *
 * 分项说明归右栏账目。同一组数（涨停 / 跌停 / 炸板 / 封板率 / 最高连板）在
 * 这一屏出现三次 —— 读数栏（只给数）、今日发现（串成一句话）、情绪明细
 * （给数 + 分项 + 算式）。三处**都从同一批 computed 派生**，值不会打架；
 * 这是"摘要 + 叙述 + 账目"三种读法，不是把一件事画三遍。
 */
const readRows = computed(() => [
  // 涨停红 / 跌停绿是 **A 股语义**（不是装饰）：这两个数是情绪的两端，
  // 颜色在这里是读数的一部分。别的数字一律墨色，方向靠旁边的小点讲。
  {
    key: 'limitUp',
    label: t('marketDashboard.sentiment.limitUp'),
    value: fmtCount(sentiment.value.limit_up),
    cls: 'md-up',
  },
  {
    key: 'limitDown',
    label: t('marketDashboard.sentiment.limitDown'),
    value: fmtCount(sentiment.value.limit_down),
    cls: 'md-down',
  },
  {
    key: 'broken',
    label: t('marketDashboard.sentiment.broken'),
    value: fmtCount(sentiment.value.broken),
    cls: '',
  },
  {
    key: 'sealRate',
    label: t('marketDashboard.ledger.sealRate'),
    value: sealRatePct.value == null ? DASH : `${sealRatePct.value.toFixed(0)}%`,
    cls: '',
  },
  {
    key: 'maxStreak',
    label: t('marketDashboard.sentiment.maxStreak'),
    value:
      sentiment.value.max_streak == null
        ? DASH
        : `${fmtCount(sentiment.value.max_streak)} ${t('marketDashboard.sentiment.streakUnit')}`,
    cls: '',
  },
])

/**
 * 右栏情绪明细：科目 | 说明 | 金额。
 *
 * 说明列给的是**读数栏给不了的分项**（近 5 日序列、与涨停之比、炸板率、
 * 封板率算式、最高空间板是谁、涨跌家数分项），金额列是那个总数。
 * 最高空间板那一行前面挂全屏唯一一处品牌色小方块。
 */
const acctRows = computed(() => {
  const s = sentiment.value
  const lu5Series = lu5.value.map((p) => String(p.count)).join(' · ')
  const rows: Array<{
    key: string
    label: string
    note: string
    value: string
    signal?: boolean
    cls?: string
  }> = [
    {
      key: 'limitUp',
      label: t('marketDashboard.sentiment.limitUp'),
      note: lu5Series ? t('marketDashboard.ledger.lu5Note', { series: lu5Series }) : '',
      value: fmtCount(s.limit_up),
      cls: 'md-up',
    },
    {
      key: 'limitDown',
      label: t('marketDashboard.sentiment.limitDown'),
      note:
        s.limit_up != null && s.limit_down != null
          ? t('marketDashboard.ledger.downRatio', { up: s.limit_up, down: s.limit_down })
          : '',
      value: fmtCount(s.limit_down),
      cls: 'md-down',
    },
    {
      key: 'broken',
      label: t('marketDashboard.sentiment.broken'),
      note: s.broken_rate != null ? t('marketDashboard.ledger.brokenNote', { rate: s.broken_rate }) : '',
      value: fmtCount(s.broken),
    },
    {
      key: 'sealRate',
      label: t('marketDashboard.ledger.sealRate'),
      note:
        touchCount.value != null
          ? t('marketDashboard.ledger.sealFormula', { up: s.limit_up, total: touchCount.value })
          : '',
      value: sealRatePct.value == null ? DASH : `${sealRatePct.value.toFixed(0)}%`,
    },
    {
      key: 'topBoard',
      label: t('marketDashboard.sentiment.maxStreak'),
      note: s.streak_name || '',
      value:
        s.max_streak == null
          ? DASH
          : `${fmtCount(s.max_streak)} ${t('marketDashboard.sentiment.streakUnit')}`,
      signal: true,
    },
    {
      key: 'breadth',
      label: t('marketDashboard.ledger.upCount'),
      note:
        s.breadth.flat != null || s.breadth.down != null
          ? t('marketDashboard.ledger.breadthNote', {
              flat: fmtInt(s.breadth.flat),
              down: fmtInt(s.breadth.down),
            })
          : '',
      value: fmtInt(s.breadth.up),
    },
  ]
  return rows
})

/**
 * 两个榜单在墨账版里各取前几行。
 *
 * 接口一次给 50 条龙虎榜、18 只 ETF（DRAGON_LIMIT = 50）。现状卡片是**靠固定
 * 高度裁掉**多余的：1440 下露出 6 条龙虎榜、8 只 ETF，DOM 里却是 50 / 18 行。
 * 墨账版没有卡片可裁 —— 每一行都占真高度，所以显式切到能列下的行数，并把
 * caption 换成与实际列出内容相符的口径，免得写着「信号 18/18」却一行都没给。
 */
const DRAGON_TOP = 6
const ETF_TOP = 6
const dragonTop = computed(() => dragonRows.value.slice(0, DRAGON_TOP))
const etfTop = computed(() => etfRows.value.slice(0, ETF_TOP))

/** 龙虎榜的交易日。caption 要说清"这是哪一天的榜"，不能只说"全市场"。 */
const dragonDate = computed(() => dragon.value?.trade_date ?? DASH)

/**
 * 今日发现：一行概况 + 两行分述，全部从数据算。
 *
 * 走向判定用近 5 日涨停家数的首尾比较（lu5 是升序，最早在前、今天在末）。
 * 没有数据时整段为空，不写死任何一句 —— 宁可少一句，不说假话。
 */
const findingMain = computed(() => {
  const s = sentiment.value
  if (s.limit_up == null && s.limit_down == null && s.broken == null) return ''
  return t('marketDashboard.ledger.finding.limit', {
    up: fmtCount(s.limit_up),
    down: fmtCount(s.limit_down),
    broken: fmtCount(s.broken),
  })
})

const findingSubs = computed(() => {
  const s = sentiment.value
  const out: string[] = []
  if (s.breadth.up != null && s.breadth.down != null) {
    out.push(
      t('marketDashboard.ledger.finding.breadth', {
        up: fmtInt(s.breadth.up),
        down: fmtInt(s.breadth.down),
      }),
    )
  }
  if (lu5.value.length >= 2) {
    const from = lu5.value[0].count
    const to = lu5.value[lu5.value.length - 1].count
    const mood = to > from ? 'up' : to < from ? 'down' : 'flat'
    out.push(t(`marketDashboard.ledger.finding.trend.${mood}`, { from, to }))
  }
  return out
})

/** 来源行：数据出处 + 口径日期。没有快照就没有来源，不写死。 */
const sourceLine = computed(() => {
  const d = snapshot.value?.trade_date
  if (!d) return ''
  return t('marketDashboard.ledger.sourceLine', {
    source: t('marketDashboard.ledger.sourceName'),
    date: d,
  })
})
</script>

<style lang="less" scoped>
/* 涨跌色已收敛到全局令牌（--app-quote-up/--app-quote-down，随主题自动切），
   本文件不再定义本地变量，也不再需要暗色覆盖块。 */

/* 渐变定义容器：零尺寸、绝对定位，不参与布局也不可被鼠标命中 */
.md-gradients {
  position: absolute;
  width: 0;
  height: 0;
  pointer-events: none;
}

.md-up { color: var(--app-quote-up); }
.md-down { color: var(--app-quote-down); }
.md-flat { color: var(--td-text-color-placeholder); }

.md-up-stroke { stroke: var(--app-quote-up); }
.md-down-stroke { stroke: var(--app-quote-down); }
.md-up-bg { background: color-mix(in srgb, var(--app-quote-up) 11%, transparent); }
.md-down-bg { background: color-mix(in srgb, var(--app-quote-down) 11%, transparent); }

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
  font-size: var(--app-text-xs);
  font-weight: 600;
  /* 不加字距：中文标签加 0.08em 会散成两截（inspect 的 micro-label 也是这么判的）。 */
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

/* 墨账：块与块之间只靠留白和标题层级分开 —— 不画卡片。
   不描边、不加阴影、不用底色块、不用圆角。
   也没有入场动画：应用屏的动效只做反馈，页面不表演进场。 */
.md-tile {
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: transparent;
  border: 0;
  border-radius: 0;
  padding: 0;
}

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

.md-tile__caption { font-size: var(--app-text-xs); color: var(--td-text-color-placeholder); }

/* 次级链接用次级墨色，不用品牌珊瑚：墨账里珊瑚只留给那一处数据标记。 */
.md-tile__link {
  margin-left: auto;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
  text-decoration: none;
}

.md-tile__link:hover { color: var(--td-text-color-primary); text-decoration: underline; }

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

/* 指数名是这张卡的主标签，绝不收缩；代码是次要信息，挤不下时它先让位。
   （一开始给两边都加 min-width:0 + ellipsis，结果 name 被压成「上证…」，
   等于把「折行」换成「读不出名字」——都不对。
   只给 name 加 nowrap 也只是把折行换成整行横向溢出，同样不对。） */
.md-index-panel__name {
  font-family: var(--app-font-display);
  font-size: var(--app-text-lg);
  font-weight: 600;
  flex-shrink: 0;
  white-space: nowrap;
}

.md-index-panel__code {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 20px 的等宽读数本身不许被压扁 */
.md-index-panel__last {
  margin-left: auto;
  flex-shrink: 0;
  font-size: var(--app-text-3xl);
  font-weight: 700;
  line-height: 1;
}

/* 墨账：涨跌不靠胶囊底色喊，改成 6px 小点 + 墨色数字。
   数字保持墨色 —— 方向由点讲，颜色留给真正需要被看见的场合。 */
.md-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--app-text-xs);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.md-pill::before {
  content: '';
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--td-text-color-disabled);
}

.md-pill.md-up::before { background: var(--app-quote-up); }
.md-pill.md-down::before { background: var(--app-quote-down); }

.md-index-panel__chart {
  flex: 1;
  min-height: 0;
  margin: 6px 0 8px;
  display: flex;
}

.md-index-panel__chart svg { width: 100%; height: 100%; display: block; }

/* 墨账：折线用墨色。方向由数字讲，不给整条线染红绿。 */
.md-chart-line { fill: none; stroke: var(--td-text-color-secondary); }
.md-chart-prev { stroke: var(--td-text-color-disabled); stroke-width: 1; stroke-dasharray: 4 3; }

/* 当日快照条：低—高轨道 + 今开刻度 + 昨收虚线 + 最新点 */
.md-rangebar { display: flex; align-items: center; gap: 8px; }

.md-rangebar__edge {
  font-size: var(--app-text-xs);
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

.md-rangebar__last.md-up { background: var(--app-quote-up); }
.md-rangebar__last.md-down { background: var(--app-quote-down); }
.md-rangebar__last.md-flat { background: var(--td-text-color-disabled); }

.md-index-panel__legend {
  margin-top: 5px;
  font-size: var(--app-text-xs);
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

.md-hero-metric__label { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

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

.md-metric__label { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }
.md-metric__value { font-size: var(--app-text-xl); font-weight: 700; line-height: 1.3; }

.md-metric__unit {
  font-size: var(--app-text-xs);
  font-weight: 400;
  color: var(--td-text-color-placeholder);
  margin-left: 3px;
}

.md-metric__note {
  font-size: var(--app-text-xs);
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
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
  margin-bottom: 6px;
}

.md-lu5__bars {
  /* 必须是**确定高度**，不能只写 min-height：柱子用的是 height:X%，
     父级高度不确定时百分比按 auto 处理，所有柱子塌成 2px，只剩数字悬空。
     flex-basis 给确定值，百分比才有参照物。 */
  /* flex-basis 给 72px（确定值）让柱子那个 height:X% 有参照物 ——
     之前只有 min-height，高度不定时百分比按 auto 处理，所有柱子塌成 2px。
     但不能写死 0 0：卡片更高时多出来的空间会变成图下方的空白，
     所以保留 grow，卡片变高时图跟着长高。 */
  flex: 1 1 72px;
  min-height: 72px;
  display: flex;
  align-items: flex-end;
  gap: 6px;
  /* 基线：柱状图必须落地，否则柱子像浮在空中（评审 round 2 修掉的问题） */
  border-bottom: 1px solid var(--td-component-stroke);
}

/* 墨账：浓淡表用墨阶，不与涨跌红绿抢颜色。今天那根用最浓的一档。 */
.md-lu5__bar {
  flex: 1;
  min-height: 2px;
  border-radius: var(--app-radius-xs) var(--app-radius-xs) 1px 1px;
  background: var(--ledger-ink-2);
  position: relative;
}

.md-lu5__bar.is-last { background: var(--ledger-ink-4); }

.md-lu5__bar b {
  position: absolute;
  top: -16px;
  left: 50%;
  transform: translateX(-50%);
  font-size: var(--app-text-xs);
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

.md-breadth__up { background: var(--app-quote-up); }
.md-breadth__flat { background: var(--td-text-color-disabled); }
.md-breadth__down { background: var(--app-quote-down); }

.md-breadth__legend {
  display: flex;
  gap: 16px;
  margin-top: 6px;
  /* 10.5px —— 评审 round 2 指出 9px 低于可读底线，这里取 theme 里的 --app-text-2xs(10px)
     之上一点点：字体本身 10px，行高 1.4 视觉上约合 10.5px 的可读高度 */
  font-size: var(--app-text-xs);
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
  /* 滚动条占位：8 行超出出现滚动条时不压住最右侧的信号列 */
  scrollbar-gutter: stable;
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
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  display: block;
}

.md-wl-row__price { font-size: var(--app-text-md); text-align: right; }
.md-wl-row .md-pill { justify-self: end; }

.md-badge {
  font-size: var(--app-text-xs);
  padding: 1px 7px;
  border-radius: var(--app-radius-pill);
  border: 1px solid var(--td-component-border);
  color: var(--td-text-color-secondary);
  white-space: nowrap;
}

/* 「已触发」是状态，不是涨跌 —— 用墨阶底 + 墨色字，不占品牌珊瑚
   （全屏版的同一状态是灰字，两视图口径一致；品牌珊瑚全产品只留一处）。 */
.md-badge.is-trigger {
  color: var(--td-text-color-primary);
  border-color: var(--ledger-ink-2);
  background: var(--ledger-ink-2);
  font-weight: 600;
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

/* 榜首用字重强调，不用珊瑚 —— 同上，品牌色只留那一处。 */
.md-lhb-row__rank.is-first { color: var(--td-text-color-primary); font-weight: 600; }
.md-lhb-row__name { font-size: var(--app-text-md); font-weight: 500; min-width: 0; }

.md-lhb-row__tag {
  display: block;
  font-size: var(--app-text-xs);
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

.md-lhb-row__org { font-size: var(--app-text-xs); color: var(--td-text-color-placeholder); }

/* ── ETF · 大资金动向 ── */
.md-etf { grid-column: span 3; }

/* 名称列 minmax(0,1fr) + ellipsis：最长名「沪深300ETF华泰柏瑞」不折行、不撑破栅格。
   新增三列后 gap 由 10 收到 6 —— 列变多了，间距不收就没有留给名称的宽度。 */
.md-etf-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto auto auto;
  align-items: center;
  gap: 6px;
  padding: 5px 2px;
  border-top: 1px solid var(--td-component-stroke);

  &:first-child { border-top: 0; }
}

.md-etf-row__name { font-size: var(--app-text-sm); font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.md-etf-row__code {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  display: block;
}

.md-etf-row__price { font-size: var(--app-text-sm); text-align: right; }

/* 份额列：季频口径。红 = 净流入，沿用本卡 --app-quote-up / --app-quote-down，不另造涨跌色。 */
.md-etf-row__share { font-size: var(--app-text-xs); text-align: right; }

/* 回退态（etf-flow 未同步）：写「未同步」而不是「—」，两者的意思不同 */
.md-etf-row__share--off {
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-xs);
}

.md-etf-row__mult {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  text-align: right;
}

/* 放大倍数 ≥1.382 才抬色 —— 倍数本身不是结论，只是"今天这只比平时活跃" */
.md-etf-row__mult--hot { color: var(--td-brand-color); font-weight: 600; }

.md-etf-row__signal {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: var(--app-text-xs);
  color: var(--td-brand-color);
  white-space: nowrap;
}

/* 信号圆点。复用 .md-dot 的画法，尺寸小一档 —— 8 行里不能喧宾夺主。 */
.md-etf-row__dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--td-brand-color);
}

/* 底部预留行（汇金披露持仓，B 线）。样式沿用 caption，只多一条分隔线。 */
.md-etf__foot {
  margin: 0;
  padding-top: 6px;
  border-top: 1px solid var(--td-component-stroke);
}

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
  /* ETF 从 4 行涨到 8 行：小视口再收一档，否则得靠滚动才看得到第 5 只以后 */
  .md-etf-row { padding: 3px 2px; }
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

/* ══════════════════════════════════════════════════════════════════════════
   嵌入态（个股追踪 → 大盘 tab）

   全屏大屏那套是给"固定视口、一屏看完"调的：100vh + overflow:hidden + 12 栅格 +
   两行网格 + @media(max-height:800px) 逐项收紧。塞进侧边栏壳以后这些全部反过来
   —— 高度由内容定、页面该滚就滚、宽度随侧栏伸缩。所以这里不是"微调"，是把
   固定视口的假设逐条换掉。

   下面每条都写在 `@media (max-height: 800px)` 之后：那段是收紧，嵌入态要把它
   逐项放宽，选择器多带类保证压得住。
   ══════════════════════════════════════════════════════════════════════════ */

.md-page.is-embedded {
  /* 高度交给内容，页面滚。overflow:hidden 留着会把超出部分直接吃掉。 */
  height: auto;
  min-height: 0;
  overflow: visible;
  /* 墨账：块根归零（没有卡片外壳了），内缩就由页面统一给。
     外层壳不给内缩，不给这里的话整块内容会贴到屏幕边缘（edge-text）。 */
  padding: 4px var(--ledger-inset) 20px;
  gap: 12px;
  background: transparent;
}

/* 12 栅格 + 两行等高是给大屏的。嵌入态改成单列顺流：宽度不确定时，
   强行两行等高会让龙虎榜那 50 条永远塞不进第二行的格子里。 */
.md-page.is-embedded .md-board {
  display: flex;
  flex-direction: column;
  /* 墨账：块之间没有边框和底色了，靠留白分组 —— 12px 不够，读起来会糊成一片。 */
  gap: 20px;
  flex: 0 0 auto;
}

.md-page.is-embedded .md-ticker {
  flex: 0 0 auto;
}

/* 新增三块（板块榜 / 连板梯队 / 期股联动）的容器。
   整宽铺满，不参与 .md-board 的 12 栅格 —— 它们自己内部已经排好版了，
   再塞进栅格只会让它们的列宽随旁边卡片的 span 抖动。 */
.md-full {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.md-full__pair {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

/* 窄内容区时梯队与基差改成上下排 —— 侧边栏收起来时两栏会挤成两条竖线。 */
@media (max-width: 1200px) {
  .md-full__pair { grid-template-columns: minmax(0, 1fr); }
}

.md-page.is-embedded .md-index-grid {
  /* 大屏是两列；侧栏壳里宽度更窄，仍两列但卡片会挤 —— 保持 2 列，
     它是"四大指数"这一组的天然分组，换成一列会让它变成四条横幅。 */
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

/* 三张速览卡（自选股 / 龙虎榜 / ETF）在大屏是同一行三等分；
   嵌入态里龙虎榜要放 50 条、ETF 要放摘要，各自需要的行数差一个量级，
   强行并排会让高的那张把另外两张挤成半行。改纵向排列。 */
.md-page.is-embedded .md-wl,
.md-page.is-embedded .md-lhb,
.md-page.is-embedded .md-etf {
  grid-column: 1 / -1;
}

/* 龙虎榜 50 条：两列铺开。这是"缩编全景"这一版的核心 ——
   大屏一列 5 行，嵌入态两列 50 行，不用滚就能扫完一屏两屏。 */
.md-page.is-embedded .md-lhb-rows {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
  align-content: start;
  overflow: visible;
}

.md-page.is-embedded .md-lhb-row {
  padding: 6px 2px;
}

/* 把 @media(max-height:800px) 那几项收紧逐条放宽 —— 它是为"塞进 768px 高的
   固定视口"调的，嵌入态的高度由内容决定，套用它只会白白挤掉信息。 */
.md-page.is-embedded .md-board,
.md-page.is-embedded .md-index-grid {
  gap: 12px;
}

/* 墨账：块根归零，与 6 个子组件的根类一致 —— 所有块的标题才会对齐在同一条左边缘。 */
.md-page.is-embedded .md-tile { padding: 0; }

/* 唯一保留纸面的一块：市场情绪（涨停红 / 跌停绿这两个数）。
   理由有二：
   1) 红绿压在暖纸底上是 4.4:1，差一点点过不了 AA；压在白纸上回到 4.6:1。
   2) 全屏版的「今日读数」也是一块纸 —— 两个视图的读数块长得一样才叫同一套体例。 */
.md-page.is-embedded .md-sentiment {
  background: var(--td-bg-color-container);
  border-radius: var(--app-radius-xl);
  padding: 14px 20px 16px;
}
.md-page.is-embedded .md-tile__head { margin-bottom: 8px; }
.md-page.is-embedded .md-wl-row { padding: 6px 2px; }
.md-page.is-embedded .md-etf-row { padding: 7px 2px; }
.md-page.is-embedded .md-index-panel__chart { margin: 6px 0 8px; }
.md-page.is-embedded .md-lu5 { margin-bottom: 8px; }

/* 自选股第 7 行：那张卡在大屏小视口下是"只留 6 行"，嵌入态没有理由丢一只。 */
.md-page.is-embedded .md-wl-row:nth-child(7) { display: flex; }

/* ── 嵌入态 ETF 摘要 ── */
.md-etf-summary {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.md-etf-summary__stats {
  display: flex;
  gap: 28px;
  flex-wrap: wrap;
}

.md-etf-summary__stat { display: flex; flex-direction: column; gap: 2px; }

.md-etf-summary__k {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
}

/* 摘要的三个数用展示字号，不是正文 —— 它们是这一格的结论，不是明细。 */
.md-etf-summary__v {
  font-size: var(--app-text-lg);
  color: var(--td-text-color-primary);
}

/* 次级链接用次级墨色：珊瑚在暖纸底上只有 3.2:1，而且墨账里品牌色只留给那一处数据标记。 */
.md-etf-summary__cta {
  flex: 0 0 auto;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
  text-decoration: none;
  border-bottom: 1px solid color-mix(in srgb, var(--td-text-color-secondary) 40%, transparent);
}

.md-etf-summary__cta:hover { color: var(--td-text-color-primary); }

/* ══════════════════════════════════════════════════════════════════════
   墨账版 · 全屏 /dashboard
   风格 achromatic-ledger：外壳无色、图表用墨色、只给变化处一个小信号色。
   块与块之间只靠留白和字号层级分开 —— 不画卡片、不描边、不加分隔线。
   这一段只作用于 lg-* 类名，与上面的卡片版互不干扰。
   ══════════════════════════════════════════════════════════════════════ */

.lg-topbar {
  flex: 0 0 40px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 2px;
}

.lg-topbar__title {
  margin: 0;
  font-family: var(--app-font-display);
  font-size: var(--app-text-3xl);
  font-weight: 600;
  line-height: 1.2;
}

.lg-topbar__date { font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }
.lg-topbar__spacer { flex: 1; }
.lg-topbar__clock { font-size: var(--app-text-sm); color: var(--td-text-color-placeholder); }

.lg-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

/* 状态点用中性灰：这一屏唯一的品牌色留给「最高空间板」那一个方块。 */
.lg-state__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--td-text-color-placeholder);
}

.lg-icon-btn {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: var(--app-radius-md);
  background: transparent;
  color: var(--td-text-color-secondary);
  cursor: pointer;
  transition: background-color var(--app-motion-base) ease, color var(--app-motion-base) ease,
    transform 140ms cubic-bezier(0.16, 1, 0.3, 1);

  &:hover {
    background: color-mix(in srgb, var(--td-text-color-primary) 7%, transparent);
    color: var(--td-text-color-primary);
  }

  &:active { transform: scale(0.94); }
  &:disabled { opacity: 0.4; cursor: default; }
}

/* 唯一的主操作：实心墨色（近黑），不是珊瑚 —— 墨账版的强调色只出现一处。 */
.lg-primary-btn {
  height: 30px;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  border-radius: var(--app-radius-md);
  background: var(--td-text-color-primary);
  color: var(--td-bg-color-container);
  font-size: var(--app-text-sm);
  cursor: pointer;
  transition: opacity var(--app-motion-base) ease, transform 140ms cubic-bezier(0.16, 1, 0.3, 1);

  &:hover { opacity: 0.86; }
  &:active { transform: scale(0.97); }
  &:disabled { opacity: 0.4; cursor: default; }
}

.lg-board {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(300px, 31%) minmax(0, 1fr);
  gap: 34px;
}

.lg-col {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 20px;
  overflow: hidden;
}

.lg-block {
  /* flex: none —— 不许被压扁。压扁后内容会溢出到下一块上（实测三块叠在一起）。
     min-height 也不能是 0：column flex 里 min-height:0 等于允许块比内容还矮。 */
  flex: none;
  display: flex;
  flex-direction: column;
}

.lg-block__title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 0 0 10px;
  font-family: var(--app-font-display);
  font-size: var(--app-text-lg);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.lg-block__note {
  font-family: var(--app-font-family);
  font-size: var(--app-text-xs);
  font-weight: 400;
  color: var(--td-text-color-placeholder);
}

.lg-block__link {
  margin-left: auto;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);
  text-decoration: none;

  &:hover { color: var(--td-text-color-primary); text-decoration: underline; }
}

/* ── 左栏：今日读数 ──
   全屏唯一一块**有底色**的地方（纸面，比地面亮一档），用来托住那五个大数。
   「不画卡片」说的是不画带边框 + 阴影的卡片；这里是一张纸，没有边框也没有
   阴影，其余块都直接坐在地面上。 */
.lg-read {
  background: var(--td-bg-color-container);
  border-radius: var(--app-radius-xl);
  padding: 14px 22px 16px;
}

.lg-read__title {
  margin: 0 0 4px;
  font-family: var(--app-font-display);
  font-size: var(--app-text-md);
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.lg-read__list { margin: 0; }

.lg-read__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 0;
}

.lg-read__row dt {
  font-size: var(--app-text-md);
  color: var(--td-text-color-secondary);
}

.lg-read__row dd {
  margin: 0;
  font-size: 30px;
  font-weight: 500;
  line-height: 1.05;
  color: var(--td-text-color-primary);
}

/* 涨停红 / 跌停绿 —— A 股语义，不是装饰。
   这两个数是情绪的两端，颜色在这里是读数的一部分；嵌入态的「市场情绪」块
   也是同一套。规则写在 .md-up/.md-down 之后，用同样的特异度把颜色要回来。 */
.lg-read__row dd.md-up { color: var(--app-quote-up); }
.lg-read__row dd.md-down { color: var(--app-quote-down); }

/* ── 今日发现 ── */
.lg-finding {
  margin: 0 0 8px;
  font-family: var(--app-font-display);
  font-size: var(--app-text-2xl);
  line-height: 1.5;
  color: var(--td-text-color-primary);
}

.lg-finding__sub {
  margin: 0 0 6px;
  font-size: var(--app-text-md);
  line-height: 1.6;
  color: var(--td-text-color-secondary);
}

.lg-source {
  margin: 0;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

/* ── 账目行：收盘表 / 情绪明细 / 榜单共用 ── */
.lg-ledger {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.lg-ledger th {
  padding: 0 0 6px;
  font-size: var(--app-text-xs);
  font-weight: 400;
  color: var(--td-text-color-placeholder);
  text-align: left;
}

.lg-ledger th.num,
.lg-ledger td.num { text-align: right; }
.lg-ledger th.line { text-align: right; }

.lg-ledger td {
  padding: 5px 0;
  font-size: var(--app-text-md);
  vertical-align: baseline;
}

.lg-ledger__name { color: var(--td-text-color-primary); }

.lg-ledger__row:hover {
  background: color-mix(in srgb, var(--td-text-color-primary) 4%, transparent);
}

/* 墨线：图表用墨色，不染色。 */
.lg-spark {
  display: block;
  width: 100%;
  height: 18px;
  color: var(--td-text-color-secondary);
}

.lg-dash { color: var(--td-text-color-disabled); }

/* 涨跌信号：6px 小点 + 带符号的数字，数字本身用墨色。 */
.lg-signal {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  color: var(--td-text-color-primary);
}

.lg-signal .lg-signal__dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--td-text-color-disabled);
}

.lg-signal.md-up .lg-signal__dot { background: var(--app-quote-up); }
.lg-signal.md-down .lg-signal__dot { background: var(--app-quote-down); }

/* 右对齐变体：份额那列用它，数字贴着列的右边。 */
.lg-signal--end { justify-content: flex-end; }

/* 覆盖 .md-up / .md-down / .md-flat 的整段染色 —— 数字要用墨色。 */
.lg-signal.md-up,
.lg-signal.md-down,
.lg-signal.md-flat { color: var(--td-text-color-primary); }

/* ── 情绪明细：科目 | 金额 | 说明 ──
   金额紧跟在科目右边（像账单：项目、金额、摘要），不推到最右 —— 推到最右时
   科目与金额隔了半屏宽，那一行就断成两列，不再是一行账。 */
.lg-acct { margin: 0; }

.lg-acct__row {
  display: grid;
  grid-template-columns: 96px 78px minmax(0, 1fr);
  align-items: baseline;
  gap: 12px;
  padding: 5px 0;
  font-size: var(--app-text-md);
}

.lg-acct__k {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--td-text-color-primary);
}

/* 全屏唯一一处品牌色：最高空间板那行前面的小方块。 */
.lg-acct__sig {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 2px;
  background: var(--td-brand-color);
}

.lg-acct__v {
  text-align: right;
  color: var(--td-text-color-primary);
}

/* 与读数栏同一套：涨停红 / 跌停绿。 */
.lg-acct__v.md-up { color: var(--app-quote-up); }
.lg-acct__v.md-down { color: var(--app-quote-down); }

.lg-acct__m {
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 榜单行 ── */
.lg-rows { margin: 0; }

.lg-row {
  display: grid;
  align-items: baseline;
  gap: 10px;
  padding: 5px 0;
  font-size: var(--app-text-md);
}

.lg-row--watch { grid-template-columns: minmax(0, 1fr) 78px 68px 44px; }
.lg-row--rank { grid-template-columns: 18px minmax(0, 1fr) 104px; }
.lg-row--etf { grid-template-columns: minmax(0, 1fr) 52px 64px 64px 42px; }

/* 倍数是次要量，灰小字右对齐；量能没放大的时候它就是噪声。 */
.lg-row__mult {
  text-align: right;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.lg-row__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--td-text-color-primary);
}

.lg-row__code {
  margin-left: 6px;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.lg-row__tag {
  margin-left: 6px;
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.lg-row__num {
  text-align: right;
  color: var(--td-text-color-primary);
}

.lg-row__rank {
  color: var(--td-text-color-placeholder);
}

.lg-two {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 34px;
  min-height: 0;
}

.lg-empty {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: var(--app-text-md);
  color: var(--td-text-color-placeholder);
}

.lg-empty__cta {
  color: var(--td-text-color-secondary);
  text-decoration: underline;
  text-underline-offset: 3px;
}

/* 矮视口（1366×768）收紧：与上面卡片版那套 @media(max-height:800px) 同一意图，
   只是墨账版要收的是行距和最后一行，不是卡片内边距。收完仍是一屏无滚动。 */
@media (max-height: 800px) {
  .md-page { padding: 10px 16px 12px; gap: 10px; }
  .lg-topbar { flex-basis: 36px; }
  .lg-board { gap: 24px; }
  .lg-col { gap: 16px; }
  .lg-read { padding: 10px 20px 12px; }
  .lg-read__row { padding: 6px 0; }
  .lg-read__row dd { font-size: 26px; }
  .lg-block__title { margin-bottom: 7px; }
  .lg-ledger td { padding: 3px 0; }
  .lg-acct__row { padding: 3px 0; }
  .lg-row { padding: 3px 0; }
  /* 自选股第 7 行在小视口隐藏 —— 与卡片版同一取舍 */
  .lg-row--watch:nth-child(7) { display: none; }
  .lg-row--etf:nth-child(6) { display: none; }
  .lg-row--rank:nth-child(5) { display: none; }
  .lg-finding { font-size: var(--app-text-xl); margin-bottom: 6px; }
  .lg-finding__sub { margin-bottom: 4px; }
}
</style>
