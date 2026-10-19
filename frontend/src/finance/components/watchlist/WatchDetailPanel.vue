<template>
  <!--
    原来这里是 `:style="{ width: `${panelWidth}px` }"`。但 `panelWidth` 是个
    从未被父组件传入的 prop，运行时是 undefined，内联样式渲染成
    `width: undefinedpx` 被浏览器忽略 —— 纯死代码，且一旦有人补上传参就会
    绕开 CSS 里的宽度约束（和 max-width 抢）。宽度统一交给样式表。
  -->
  <aside class="wl-detail">
    <header class="wl-detail__head">
      <div class="wl-detail__title">
        <span class="wl-detail__name">{{ displayName }}</span>
        <span class="wl-detail__code">{{ thscode }}</span>
      </div>
      <button type="button" class="wl-detail__close" :title="t('watchlist.cancel')"
        :aria-label="t('watchlist.cancel')" @click="$emit('close')">
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
              {{ t('watchDetail.sharesMarketValue', { shares: tradeTarget.shares.toLocaleString(), value: targetMarketValueText }) }}
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

    <!-- 盘中异动归因：大涨大跌逻辑依据 (special.duckdb) -->
    <section v-if="anomalyRecords.length" class="wl-detail__section wl-detail__anomaly">
      <div class="wl-detail__section-head">
        <h3 class="wl-detail__h3">
          <t-icon name="chart-bubble" size="14px" style="margin-right: 4px;" />
          {{ t('watchDetail.anomalyTitle') }}
        </h3>
        <span
          class="wl-anomaly__tag"
          :class="anomalyRecords[0].tag_name === '大跌' ? 'is-down' : 'is-up'"
        >
          {{ anomalyRecords[0].tag_name }} · {{ anomalyRecords[0].capture_date }}
        </span>
      </div>
      <div class="wl-anomaly__card">
        <div v-if="anomalyRecords[0].keywords && anomalyRecords[0].keywords.length" class="wl-anomaly__kws">
          <span v-for="kw in anomalyRecords[0].keywords" :key="kw" class="wl-anomaly__kw-pill">
            #{{ kw }}
          </span>
        </div>
        <div class="wl-anomaly__body">
          {{ anomalyRecords[0].analysis_content }}
        </div>
      </div>
    </section>

    <!-- 机构筹码：公募与 ETF 重仓穿透 (fund.duckdb) -->
    <section class="wl-detail__section wl-detail__fund">
      <div class="wl-detail__section-head">
        <h3 class="wl-detail__h3">
          <t-icon name="usergroup" size="14px" style="margin-right: 4px;" />
          {{ t('watchDetail.fundTitle') }}
        </h3>
        <span v-if="fundHoldings.length" class="wl-fund__count-badge">
          {{ t('watchDetail.fundHoldingCount', { count: fundSummary?.total_funds ?? fundHoldings.length }) }}
        </span>
      </div>

      <!-- 机构集中度与增减仓风向标 -->
      <div v-if="fundSummary && fundHoldings.length" class="wl-fund__summary-bar">
        <div class="wl-fund__stat">
          <span class="wl-fund__stat-lbl">{{ t('watchDetail.fundConcentration') }}</span>
          <span class="wl-fund__stat-val">{{ fundSummary.total_hold_ratio ? fundSummary.total_hold_ratio.toFixed(2) + '%' : '—' }}</span>
        </div>
        <div class="wl-fund__stat">
          <span class="wl-fund__stat-lbl">{{ t('watchDetail.fundInstitutionCount') }}</span>
          <span class="wl-fund__stat-val">{{ t('watchDetail.fundInstitutionUnit', { count: fundSummary.total_funds }) }}</span>
        </div>
        <div class="wl-fund__stat">
          <span class="wl-fund__stat-lbl">{{ t('watchDetail.fundFlowTrend') }}</span>
          <span class="wl-fund__stat-val wl-fund__stat-flow">
            <span class="is-up">{{ t('watchDetail.fundIncrease', { count: fundSummary.increase_count }) }}</span>
            <span class="wl-fund__sep">/</span>
            <span class="is-down">{{ t('watchDetail.fundDecrease', { count: fundSummary.decrease_count }) }}</span>
          </span>
        </div>
      </div>

      <p v-if="fundLoading" class="wl-detail__hint">{{ t('watchDetail.fundLoading') }}</p>
      <div v-else-if="!fundHoldings.length" class="wl-detail__empty">
        <p class="wl-detail__empty-hint">{{ t('watchDetail.fundEmpty') }}</p>
      </div>
      <div v-else class="wl-fund__list">
        <div v-for="(fh, idx) in fundHoldings" :key="fh.fund_thscode" class="wl-fund__item">
          <div class="wl-fund__item-rank" :class="{ 'is-top': idx < 3 }">
            #{{ idx + 1 }}
          </div>
          <div class="wl-fund__item-main">
            <div class="wl-fund__name-row">
              <span class="wl-fund__type-badge" :class="fh.fund_category === 'ETF' ? 'is-etf' : 'is-active'">
                {{ fh.fund_category || t('watchDetail.publicFund') }}
              </span>
              <span class="wl-fund__name" :title="fh.fund_name">{{ fh.fund_name }}</span>
            </div>
            <div class="wl-fund__meta-row">
              <span class="wl-fund__code">{{ fh.fund_thscode }}</span>
              <span v-if="fh.manager_name" class="wl-fund__mgr">{{ fh.manager_name }}</span>
            </div>
          </div>
          <div class="wl-fund__item-stats">
            <span class="wl-fund__ratio" :title="$t('watchDetail.fundNetValueRatioTitle')">
              {{ t('watchDetail.fundHoldingRatio') }} <b>{{ fh.hold_ratio ? fh.hold_ratio.toFixed(2) + '%' : '—' }}</b>
            </span>
            <span
              v-if="fh.period_increase_rate_pct !== null && fh.period_increase_rate_pct !== undefined"
              class="wl-fund__chg"
              :class="fh.period_increase_rate_pct >= 0 ? 'is-up' : 'is-down'"
              :title="$t('watchDetail.fundPeriodChangeTitle')"
            >
              {{ t('watchDetail.fundQuarterChange', { value: (fh.period_increase_rate_pct >= 0 ? '+' : '') + fh.period_increase_rate_pct.toFixed(2) }) }}
            </span>
          </div>
        </div>
      </div>
    </section>

    <!-- 分红送转历史 (market.duckdb -> raw_adjustment_events) -->
    <section v-if="dividendRecords.length" class="wl-detail__section wl-detail__dividends">
      <div class="wl-detail__section-head">
        <h3 class="wl-detail__h3">
          <t-icon name="money-circle" size="14px" style="margin-right: 4px;" />
          {{ t('watchDetail.dividendHistory') }}
        </h3>
        <span class="wl-dividend__count-badge">
          {{ t('watchDetail.dividendCount', { count: dividendRecords.length }) }}
        </span>
      </div>
      <div class="wl-dividend__list">
        <div v-for="d in dividendRecords" :key="d.ex_date || ''" class="wl-dividend__item">
          <div class="wl-dividend__top">
            <span class="wl-dividend__date">{{ d.ex_date }}</span>
            <span v-if="d.dividend_yield" class="wl-dividend__yield">{{ t('watchDetail.dividendYield', { value: d.dividend_yield.toFixed(2) }) }}</span>
          </div>
          <div class="wl-dividend__details">
            <span v-if="(d.dividend_per_share ?? 0) > 0" class="wl-dividend__cash">
              {{ t('watchDetail.dividendCash', { value: (d.dividend_per_share ?? 0).toFixed(2) }) }}
            </span>
            <span v-if="(d.per_share_bonus ?? 0) > 0" class="wl-dividend__bonus">
              {{ t('watchDetail.dividendBonus', { value: ((d.per_share_bonus ?? 0) * 10).toFixed(1) }) }}
            </span>
          </div>
        </div>
      </div>
    </section>

    <!-- 资金关注度与热度走势 (special.duckdb -> v_hot_stock_rank_trend) -->
    <section v-if="hotTrend.length" class="wl-detail__section wl-detail__hot-trend">
      <div class="wl-detail__section-head">
        <h3 class="wl-detail__h3">
          <t-icon name="fire" size="14px" style="margin-right: 4px;" />
          {{ t('watchDetail.hotTrendTitle') }}
        </h3>
        <span class="wl-hot__badge">
          {{ t('watchDetail.hotLatestRank', { rank: hotTrend[hotTrend.length - 1].rank }) }}
        </span>
      </div>
      <div class="wl-hot__trend-box">
        <div class="wl-hot__trend-points">
          <div v-for="pt in hotTrend.slice(-6)" :key="pt.date || ''" class="wl-hot__pt">
            <span class="wl-hot__pt-rank">#{{ pt.rank }}</span>
            <span class="wl-hot__pt-date">{{ pt.date?.slice(5) }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 高阶量化：TTM Squeeze 波动挤压与超级趋势 (indicators.duckdb) -->
    <section v-if="squeezeData && squeezeData.squeeze_status !== 'no_data' && squeezeData.squeeze_status !== 'unavailable'" class="wl-detail__section wl-detail__squeeze">
      <div class="wl-detail__section-head">
        <h3 class="wl-detail__h3">
          <t-icon name="dashboard" size="14px" style="margin-right: 4px;" />
          {{ t('watchDetail.quantTitle') }}
        </h3>
        <span class="wl-squeeze__date">{{ squeezeData.latest_date || t('watchDetail.latest') }}</span>
      </div>

      <div class="wl-squeeze__card">
        <!-- 核心状态双列看板 -->
        <div class="wl-squeeze__status-row">
          <!-- TTM Squeeze 状态 -->
          <div class="wl-squeeze__col">
            <span class="wl-squeeze__col-lbl">{{ t('watchDetail.squeezeStatusLabel') }}</span>
            <div class="wl-squeeze__status-pill" :class="squeezePillClass">
              <span class="wl-squeeze__dot" />
              <span>{{ squeezeLabel }}</span>
            </div>
            <span class="wl-squeeze__sub-hint">{{ squeezeSubHint }}</span>
          </div>

          <!-- Supertrend 超级趋势追踪 -->
          <div class="wl-squeeze__col">
            <span class="wl-squeeze__col-lbl">{{ t('watchDetail.supertrendLabel') }}</span>
            <div class="wl-squeeze__status-pill" :class="supertrendPillClass">
              <t-icon :name="squeezeData.supertrend_direction === 1 ? 'arrow-up' : 'arrow-down'" size="14px" />
              <span>{{ supertrendLabel }}</span>
            </div>
            <span class="wl-squeeze__sub-hint">
              {{ t('watchDetail.dynamicStopLine', { value: squeezeData.supertrend_level ? squeezeData.supertrend_level.toFixed(2) : '—' }) }}
            </span>
          </div>
        </div>

        <!-- 动量与风险系数指标网格 -->
        <div class="wl-squeeze__metric-grid">
          <div class="wl-squeeze__metric-cell">
            <span class="wl-squeeze__m-k">{{ t('watchDetail.momentumLabel') }}</span>
            <span
              class="wl-squeeze__m-v"
              :class="(squeezeData.momentum_hist ?? 0) >= 0 ? 'is-up' : 'is-down'"
            >
              {{ (squeezeData.momentum_hist ?? 0) >= 0 ? '+' : '' }}{{ squeezeData.momentum_hist?.toFixed(3) ?? '—' }}
            </span>
          </div>
          <div class="wl-squeeze__metric-cell">
            <span class="wl-squeeze__m-k">{{ t('watchDetail.betaLabel') }}</span>
            <span class="wl-squeeze__m-v">
              {{ squeezeData.beta?.toFixed(2) ?? '—' }}
              <span class="wl-squeeze__beta-tag" :class="betaTagClass">
                {{ betaDesc }}
              </span>
            </span>
          </div>
        </div>
      </div>
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
                  <span class="wl-dim-chip" :title="$t('watchDetail.techScoreTitle', { value: parseDiaryDims(d)!.tech.toFixed(0) })">
                    {{ t('watchDetail.techScore', { value: parseDiaryDims(d)!.tech.toFixed(0) }) }}
                  </span>
                  <span class="wl-dim-chip" :title="$t('watchDetail.fundScoreTitle', { value: parseDiaryDims(d)!.fund.toFixed(0) })">
                    {{ t('watchDetail.fundScore', { value: parseDiaryDims(d)!.fund.toFixed(0) }) }}
                  </span>
                  <span class="wl-dim-chip" :title="$t('watchDetail.fundamentalScoreTitle', { value: parseDiaryDims(d)!.base.toFixed(0) })">
                    {{ t('watchDetail.fundamentalScore', { value: parseDiaryDims(d)!.base.toFixed(0) }) }}
                  </span>
                  <span class="wl-dim-chip" :title="$t('watchDetail.macroScoreTitle', { value: parseDiaryDims(d)!.macro.toFixed(0) })">
                    {{ t('watchDetail.macroScore', { value: parseDiaryDims(d)!.macro.toFixed(0) }) }}
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
                  <span class="q-score" :class="qScoreClass(item.score)">{{ t('watchDetail.scorePoints', { value: item.score }) }}</span>
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
import {
  getStockFundHoldings,
  getStockAnomalyReasons,
  getDividendCalendar,
  getHotStockTrend,
  getSqueezeStatus,
  type FundHoldingItem,
  type FundHoldingsSummary,
  type AnomalyReasonItem,
  type DividendEventItem,
  type HotStockTrendPoint,
  type SqueezeStatusResponse,
} from '@/finance/api/market'

const fundHoldings = ref<FundHoldingItem[]>([])
const fundSummary = ref<FundHoldingsSummary | null>(null)
const fundLoading = ref(false)

const anomalyRecords = ref<AnomalyReasonItem[]>([])

async function loadFundHoldings() {
  if (!props.thscode) return
  fundLoading.value = true
  try {
    const res = await getStockFundHoldings(props.thscode, 10)
    fundHoldings.value = res?.items || []
    fundSummary.value = res?.summary || null
  } catch {
    fundHoldings.value = []
    fundSummary.value = null
  } finally {
    fundLoading.value = false
  }
}

async function loadAnomalyReasons() {
  if (!props.thscode) {
    anomalyRecords.value = []
    return
  }
  try {
    const res = await getStockAnomalyReasons(props.thscode, 2)
    anomalyRecords.value = res?.items || []
  } catch {
    anomalyRecords.value = []
  }
}

const dividendRecords = ref<DividendEventItem[]>([])

async function loadDividends() {
  if (!props.thscode) {
    dividendRecords.value = []
    return
  }
  try {
    const res = await getDividendCalendar({ thscode: props.thscode, limit: 5 })
    dividendRecords.value = res?.items || []
  } catch {
    dividendRecords.value = []
  }
}

const hotTrend = ref<HotStockTrendPoint[]>([])

async function loadHotTrend() {
  if (!props.thscode) {
    hotTrend.value = []
    return
  }
  try {
    const res = await getHotStockTrend(props.thscode, 10)
    hotTrend.value = res?.items || []
  } catch {
    hotTrend.value = []
  }
}

// ── TTM Squeeze 波动挤压 & Supertrend 超级趋势 ────────────────────────
const squeezeData = ref<SqueezeStatusResponse | null>(null)
const squeezeLoading = ref(false)

async function loadSqueeze() {
  if (!props.thscode) {
    squeezeData.value = null
    return
  }
  squeezeLoading.value = true
  try {
    const res = await getSqueezeStatus(props.thscode, 30)
    squeezeData.value = res
  } catch {
    squeezeData.value = null
  } finally {
    squeezeLoading.value = false
  }
}

const squeezeLabel = computed(() => {
  const s = squeezeData.value?.squeeze_status
  if (s === 'on') return t('watchDetail.squeeze.on')
  if (s === 'fired') return t('watchDetail.squeeze.fired')
  return t('watchDetail.squeeze.off')
})

const squeezePillClass = computed(() => {
  const s = squeezeData.value?.squeeze_status
  if (s === 'on') return 'is-squeeze-on'
  if (s === 'fired') return 'is-squeeze-fired'
  return 'is-squeeze-off'
})

const squeezeSubHint = computed(() => {
  const s = squeezeData.value?.squeeze_status
  if (s === 'on') return t('watchDetail.squeezeHint.on')
  if (s === 'fired') return t('watchDetail.squeezeHint.fired')
  return t('watchDetail.squeezeHint.off')
})

const supertrendLabel = computed(() => {
  const dir = squeezeData.value?.supertrend_direction
  if (dir === 1) return t('watchDetail.supertrend.bullish')
  if (dir === -1) return t('watchDetail.supertrend.bearish')
  return t('watchDetail.supertrend.neutral')
})

const supertrendPillClass = computed(() => {
  const dir = squeezeData.value?.supertrend_direction
  if (dir === 1) return 'is-up'
  if (dir === -1) return 'is-down'
  return 'is-neutral'
})

const betaDesc = computed(() => {
  const b = squeezeData.value?.beta
  if (b == null) return '—'
  if (b >= 1.2) return t('watchDetail.beta.high')
  if (b <= 0.8) return t('watchDetail.beta.low')
  return t('watchDetail.beta.base')
})

const betaTagClass = computed(() => {
  const b = squeezeData.value?.beta
  if (b == null) return ''
  if (b >= 1.2) return 'is-high-beta'
  if (b <= 0.8) return 'is-low-beta'
  return 'is-mid-beta'
})

const props = defineProps<{
  thscode: string
  name?: string
  quote?: Quote
  conditions?: WatchCondition[]
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
      label: t('watchDetail.resonance.tripleBullLabel'),
      themeClass: 'is-bull-all',
      desc: t('watchDetail.resonance.tripleBullDesc'),
    }
  }
  if (monthly && weekly && !daily) {
    return {
      daily, weekly, monthly,
      label: t('watchDetail.resonance.bullPullbackLabel'),
      themeClass: 'is-bull-pullback',
      desc: t('watchDetail.resonance.bullPullbackDesc'),
    }
  }
  if (!monthly && !weekly && daily) {
    return {
      daily, weekly, monthly,
      label: t('watchDetail.resonance.oversoldLabel'),
      themeClass: 'is-bear-bounce',
      desc: t('watchDetail.resonance.oversoldDesc'),
    }
  }
  if (!monthly && !weekly && !daily) {
    return {
      daily, weekly, monthly,
      label: t('watchDetail.resonance.tripleBearLabel'),
      themeClass: 'is-bear-all',
      desc: t('watchDetail.resonance.tripleBearDesc'),
    }
  }
  return {
    daily, weekly, monthly,
    label: daily ? t('watchDetail.resonance.mixedBullLabel') : t('watchDetail.resonance.mixedRangeLabel'),
    themeClass: daily ? 'is-mixed-up' : 'is-mixed-down',
    desc: t('watchDetail.resonance.mixedDesc'),
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
  loadFundHoldings()
  loadAnomalyReasons()
  loadDividends()
  loadHotTrend()
  loadSqueeze()
})
// 换一只票就换一份日记与操盘位、机构重仓及异动归因。
watch(() => props.thscode, () => {
  loadDiaries()
  refreshTradeTarget()
  loadFundHoldings()
  loadAnomalyReasons()
  loadDividends()
  loadHotTrend()
  loadSqueeze()
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

const Q_CONFIG: Record<string, () => { category: string; name: string }> = {
  q1: () => ({ category: t('watchDetail.questionCategory.technical'), name: t('watchDetail.question.trendStrength') }),
  q2: () => ({ category: t('watchDetail.questionCategory.technical'), name: t('watchDetail.question.shortMomentum') }),
  q3: () => ({ category: t('watchDetail.questionCategory.technical'), name: t('watchDetail.question.volumeConfirmation') }),
  q4: () => ({ category: t('watchDetail.questionCategory.technical'), name: t('watchDetail.question.technicalPattern') }),
  q5: () => ({ category: t('watchDetail.questionCategory.technical'), name: t('watchDetail.question.trendStability') }),
  q6: () => ({ category: t('watchDetail.questionCategory.technical'), name: t('watchDetail.question.volumePriceSync') }),
  q7: () => ({ category: t('watchDetail.questionCategory.technical'), name: t('watchDetail.question.maSupport') }),
  q8: () => ({ category: t('watchDetail.questionCategory.technical'), name: t('watchDetail.question.technicalComposite') }),
  q9: () => ({ category: t('watchDetail.questionCategory.capital'), name: t('watchDetail.question.capitalDirection') }),
  q10: () => ({ category: t('watchDetail.questionCategory.capital'), name: t('watchDetail.question.volumePriceRelation') }),
  q11: () => ({ category: t('watchDetail.questionCategory.capital'), name: t('watchDetail.question.tradeActivity') }),
  q12: () => ({ category: t('watchDetail.questionCategory.capital'), name: t('watchDetail.question.capitalComposite') }),
  q13: () => ({ category: t('watchDetail.questionCategory.fundamental'), name: t('watchDetail.question.poolReason') }),
  q14: () => ({ category: t('watchDetail.questionCategory.fundamental'), name: t('watchDetail.question.companyQuality') }),
  q15: () => ({ category: t('watchDetail.questionCategory.fundamental'), name: t('watchDetail.question.observationChange') }),
  q16: () => ({ category: t('watchDetail.questionCategory.macro'), name: t('watchDetail.question.industryRisk') }),
  q17: () => ({ category: t('watchDetail.questionCategory.macro'), name: t('watchDetail.question.moat') }),
  q18: () => ({ category: t('watchDetail.questionCategory.macro'), name: t('watchDetail.question.valuationLevel') }),
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
        const conf = Q_CONFIG[k] ? Q_CONFIG[k]() : { category: t('watchDetail.questionCategory.other'), name: k.toUpperCase() }
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
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-md, 8px);
  padding: 14px;
  overflow-y: auto;
  flex: 0 0 460px;
  width: 460px;
  min-width: 420px;
  max-width: 480px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
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
 * 涨跌色已收敛到全局令牌（theme.css 的 --app-quote-up/--app-quote-down，
 * 随主题自动切换），本文件不再定义本地变量，也不再需要暗色覆盖块。
 */
.is-up { color: var(--app-quote-up); }
.is-down { color: var(--app-quote-down); }

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
.wl-diary__verdict.is-buy, .wl-diary__verdict.is-keep { color: var(--app-quote-up); }
.wl-diary__verdict.is-sell, .wl-diary__verdict.is-exit { color: var(--app-quote-down); }
.wl-diary__verdict.is-tighten { color: #b45309; }
.wl-diary__verdict.is-hold, .wl-diary__verdict.is-none { color: #475569; }

.wl-diary__body { margin: 6px 0 0; font-size: var(--app-text-md); line-height: 1.65; white-space: pre-wrap; }
.wl-diary__actions { display: flex; gap: 8px; margin-top: 8px; }

.wl-targets-card {
  padding: 10px 12px;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-sm, 6px);
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

      &.is-high { color: var(--app-quote-up); }
      &.is-low { color: var(--app-quote-down); }
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
    color: var(--app-quote-up);
  }
  &.is-bull-pullback .wl-resonance-card__badge {
    background: color-mix(in srgb, var(--td-brand-color) 12%, transparent);
    color: var(--td-brand-color);
  }
  &.is-bear-bounce .wl-resonance-card__badge {
    background: rgba(237, 123, 47, 0.12);
    color: #ed7b2f;
  }
  &.is-bear-all .wl-resonance-card__badge {
    background: rgba(4, 120, 87, 0.12);
    color: var(--app-quote-down);
  }
  &.is-mixed-up .wl-resonance-card__badge {
    background: color-mix(in srgb, var(--td-brand-color) 8%, transparent);
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
      .matrix-dot { background: var(--app-quote-up); }
      .matrix-status { color: var(--app-quote-up); font-weight: 600; margin-left: auto; }
    }
    &.is-bear {
      .matrix-dot { background: var(--app-quote-down); }
      .matrix-status { color: var(--app-quote-down); font-weight: 600; margin-left: auto; }
    }
  }

  &__desc {
    margin: 0;
    font-size: 11px;
    color: var(--td-text-color-secondary);
    line-height: 1.4;
  }
}

.wl-fund {
  &__count-badge {
    font-size: 11px;
    color: var(--td-brand-color);
    background: rgba(var(--td-brand-color-rgb, 0, 82, 217), 0.08);
    padding: 1px 6px;
    border-radius: 10px;
    margin-left: auto;
  }
  &__summary-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 10px;
    background: var(--td-bg-color-secondarycontainer);
    border-radius: var(--td-radius-small);
    border: 1px solid var(--td-component-stroke);
    margin-top: 4px;
  }
  &__stat {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  &__stat-lbl {
    font-size: 10px;
    color: var(--td-text-color-placeholder);
  }
  &__stat-val {
    font-size: 11px;
    font-weight: 600;
    color: var(--td-text-color-primary);
  }
  &__stat-flow {
    display: flex;
    align-items: center;
    gap: 3px;
  }
  &__sep {
    color: var(--td-text-color-placeholder);
    font-size: 10px;
  }
  &__list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: 6px;
    max-height: 240px;
    overflow-y: auto;
  }
  &__item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    background: var(--td-bg-color-container-hover);
    border-radius: var(--td-radius-small);
    border: 1px solid var(--td-component-stroke);
    font-size: var(--app-text-xs);
  }
  &__item-rank {
    font-family: var(--app-font-display);
    font-size: 11px;
    font-weight: 600;
    color: var(--td-text-color-placeholder);
    flex: none;
    width: 20px;
    text-align: center;

    &.is-top {
      color: var(--td-brand-color);
    }
  }
  &__item-main {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }
  &__name-row {
    display: flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
  }
  &__meta-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  &__type-badge {
    font-size: 9px;
    padding: 0 4px;
    border-radius: 2px;
    flex: none;
    line-height: 1.4;

    &.is-etf {
      background: rgba(var(--td-brand-color-rgb, 0, 82, 217), 0.12);
      color: var(--td-brand-color);
      font-weight: 500;
    }
    &.is-active {
      background: var(--td-bg-color-secondarycontainer);
      color: var(--td-text-color-secondary);
    }
  }
  &__name {
    font-weight: 500;
    color: var(--td-text-color-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  &__code {
    font-size: 10px;
    color: var(--td-text-color-placeholder);
  }
  &__item-stats {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 2px;
    flex: none;
  }
  &__ratio {
    color: var(--td-text-color-secondary);
    font-size: 11px;
    b { color: var(--td-brand-color); font-weight: 600; }
  }
  &__chg {
    font-size: 10px;
    font-family: var(--app-font-display);
    font-weight: 500;
  }
  &__mgr {
    font-size: 10px;
    color: var(--td-text-color-placeholder);
  }
}

.wl-detail__anomaly {
  background: var(--td-bg-color-secondarycontainer);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--td-radius-small);
  padding: 8px 10px;
}

.wl-anomaly {
  &__tag {
    font-size: 11px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 3px;

    &.is-up {
      color: var(--app-quote-up);
      background: rgba(227, 77, 89, 0.1);
    }
    &.is-down {
      color: var(--app-quote-down);
      background: rgba(43, 164, 113, 0.1);
    }
  }

  &__card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: 6px;
  }

  &__kws {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  &__kw-pill {
    font-size: 10px;
    color: var(--td-brand-color);
    background: rgba(var(--td-brand-color-rgb, 0, 82, 217), 0.08);
    padding: 1px 6px;
    border-radius: 3px;
  }

  &__body {
    font-size: 11px;
    line-height: 1.5;
    color: var(--td-text-color-primary);
    white-space: pre-line;
    max-height: 120px;
    overflow-y: auto;
  }
}

.wl-detail__dividends {
  .wl-dividend__count-badge {
    font-size: 11px;
    color: var(--td-brand-color, #0052d9);
    background: rgba(var(--td-brand-color-rgb, 0, 82, 217), 0.08);
    padding: 1px 6px;
    border-radius: 4px;
    font-weight: 500;
  }

  .wl-dividend__list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: 6px;
  }

  .wl-dividend__item {
    background: var(--app-bg-secondary, rgba(0, 0, 0, 0.02));
    border: 1px solid var(--app-border, rgba(0, 0, 0, 0.06));
    border-radius: 4px;
    padding: 6px 8px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .wl-dividend__top {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .wl-dividend__date {
    font-size: 11px;
    font-family: var(--app-font-mono, monospace);
    color: var(--td-text-color-secondary);
  }

  .wl-dividend__yield {
    font-size: 10px;
    font-family: var(--app-font-mono, monospace);
    font-weight: 600;
    color: #059669;
    background: rgba(16, 185, 129, 0.1);
    padding: 0 4px;
    border-radius: 3px;
  }

  .wl-dividend__details {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
  }

  .wl-dividend__cash {
    color: var(--td-text-color-primary);
    font-family: var(--app-font-mono, monospace);
  }

  .wl-dividend__bonus {
    color: #b45309;
    font-family: var(--app-font-mono, monospace);
    font-weight: 500;
  }
}

.wl-detail__hot-trend {
  .wl-hot__badge {
    font-size: 11px;
    font-family: var(--app-font-mono, monospace);
    color: #ef4444;
    background: rgba(239, 68, 68, 0.08);
    padding: 1px 6px;
    border-radius: 4px;
    font-weight: 600;
  }

  .wl-hot__trend-box {
    background: var(--app-bg-secondary, rgba(0, 0, 0, 0.02));
    border: 1px solid var(--app-border, rgba(0, 0, 0, 0.06));
    border-radius: 4px;
    padding: 8px 10px;
    margin-top: 6px;
  }

  .wl-hot__trend-points {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
  }

  .wl-hot__pt {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
  }

  .wl-hot__pt-rank {
    font-size: 11px;
    font-family: var(--app-font-mono, monospace);
    font-weight: 700;
    color: #ef4444;
  }

  .wl-hot__pt-date {
    font-size: 9px;
    font-family: var(--app-font-mono, monospace);
    color: var(--td-text-color-secondary);
  }
}

.wl-detail__squeeze {
  .wl-squeeze__date {
    font-size: 11px;
    font-family: var(--app-font-mono, monospace);
    color: var(--td-text-color-secondary);
  }

  .wl-squeeze__card {
    background: var(--app-bg-secondary, rgba(0, 0, 0, 0.02));
    border: 1px solid var(--app-border, rgba(0, 0, 0, 0.06));
    border-radius: 6px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .wl-squeeze__status-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }

  .wl-squeeze__col {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .wl-squeeze__col-lbl {
    font-size: 11px;
    color: var(--td-text-color-secondary);
  }

  .wl-squeeze__status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
    width: fit-content;

    &.is-squeeze-on {
      background: color-mix(in srgb, #e11d48 14%, transparent);
      color: #e11d48;
      border: 1px solid color-mix(in srgb, #e11d48 30%, transparent);

      .wl-squeeze__dot {
        background: #e11d48;
        box-shadow: 0 0 6px #e11d48;
      }
    }

    &.is-squeeze-fired {
      background: color-mix(in srgb, #059669 14%, transparent);
      color: #059669;
      border: 1px solid color-mix(in srgb, #059669 30%, transparent);

      .wl-squeeze__dot {
        background: #059669;
        box-shadow: 0 0 6px #059669;
      }
    }

    &.is-squeeze-off {
      background: var(--td-bg-color-secondarycontainer);
      color: var(--td-text-color-secondary);
      border: 1px solid var(--td-component-stroke);

      .wl-squeeze__dot {
        background: var(--td-text-color-placeholder);
      }
    }

    &.is-up {
      background: color-mix(in srgb, var(--app-quote-up) 12%, transparent);
      color: var(--app-quote-up);
    }

    &.is-down {
      background: color-mix(in srgb, var(--app-quote-down) 12%, transparent);
      color: var(--app-quote-down);
    }

    &.is-neutral {
      background: var(--td-bg-color-secondarycontainer);
      color: var(--td-text-color-secondary);
    }
  }

  .wl-squeeze__dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
  }

  .wl-squeeze__sub-hint {
    font-size: 10px;
    color: var(--td-text-color-placeholder);
    line-height: 1.3;
  }

  .wl-squeeze__metric-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    padding-top: 8px;
    border-top: 1px dashed var(--td-component-stroke);
  }

  .wl-squeeze__metric-cell {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .wl-squeeze__m-k {
    font-size: 10px;
    color: var(--td-text-color-secondary);
  }

  .wl-squeeze__m-v {
    font-size: 12px;
    font-family: var(--app-font-mono, monospace);
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .wl-squeeze__beta-tag {
    font-size: 10px;
    font-weight: normal;
    padding: 1px 4px;
    border-radius: 3px;

    &.is-high-beta {
      background: color-mix(in srgb, #9333ea 12%, transparent);
      color: #9333ea;
    }
    &.is-low-beta {
      background: color-mix(in srgb, #0284c7 12%, transparent);
      color: #0284c7;
    }
    &.is-mid-beta {
      background: var(--td-bg-color-secondarycontainer);
      color: var(--td-text-color-secondary);
    }
  }
}
</style>
