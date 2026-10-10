<template>
  <main class="watchlist-page">
    <!--
      embedded = 渲染在个股追踪壳的「自选股」tab 里，页头由 Tracking.vue 提供。
      这时**只藏标题和副标题**，动作按钮留着 —— 刷新 / 浏览工作区 / 组合诊断
      是这一屏自己的事，壳不知道它们存在，藏掉等于把入口丢了。
    -->
    <header class="watchlist-header" :class="{ 'is-embedded': embedded }" style="--wails-draggable: drag">
      <div class="watchlist-title-row" style="--wails-draggable: drag">
        <h2 v-if="!embedded" style="--wails-draggable: drag">
          <t-icon name="chart-line" size="24px" />
          {{ t('watchlist.title') }}
        </h2>
        <div class="watchlist-actions" style="--wails-draggable: no-drag">
          <span v-if="lastUpdated" class="watchlist-updated">
            {{ t('watchlist.updatedAt', { time: lastUpdated }) }}
          </span>
          <t-button variant="text" theme="default" size="small" :loading="quotesLoading"
            @click="refreshAll()">
            <template #icon><t-icon name="refresh" /></template>
            {{ t('watchlist.refresh') }}
          </t-button>
          <t-button
            variant="outline"
            theme="default"
            size="small"
            :disabled="rows.length === 0"
            @click="openFullWorkspace()"
          >
            <template #icon><t-icon name="chart-line" /></template>
            {{ t('watchlist.browseWorkspace') }}
          </t-button>
          <t-button
            variant="outline"
            theme="primary"
            size="small"
            :disabled="rows.length === 0"
            @click="openPortfolioDiagnosis"
          >
            <template #icon><t-icon name="dashboard" /></template>
            {{ t('watchlist.portfolioDiagnosis') }}
          </t-button>
        </div>
      </div>
      <p v-if="!embedded" class="watchlist-subtitle" style="--wails-draggable: drag">{{ t('watchlist.subtitle') }}</p>
    </header>

    <!--
      概览条 —— 一行文字，不是五张卡。

      早前是 5 张等宽卡片，而 5 个数字里通常 3 个是 0（今日触发 0、当前持仓 0、
      已归档 0）。零值占着最贵的首屏，真正的决策信息（哪只要动手）被挤到下面。
      现在压成一行：非 0 的加粗上色，0 的淡下去，仍然可点（切分类）。
      数字不再是主角，"今天有几只需要动手"才是。
    -->
    <div class="watchlist-metrics" v-if="rows.length">
      <button
        class="wl-metric"
        :class="{ 'is-active': activeTab === 'all' }"
        @click="activeTab = 'all'"
      >
        <span class="wl-metric__label">{{ t('watchlist.metricTotal') }}</span>
        <span class="wl-metric__val">{{ rows.length }}</span>
      </button>

      <button
        class="wl-metric wl-metric--triggered"
        :class="{ 'is-active': activeTab === 'triggered', 'is-zero': countTriggered === 0 }"
        @click="activeTab = 'triggered'"
      >
        <t-icon name="notification-filled" size="13px" />
        <span class="wl-metric__label">{{ t('watchlist.metricTriggered') }}</span>
        <span class="wl-metric__val">{{ countTriggered }}</span>
      </button>

      <button
        class="wl-metric wl-metric--holding"
        :class="{ 'is-active': activeTab === 'holding', 'is-zero': countHolding === 0 }"
        @click="activeTab = 'holding'"
      >
        <span class="wl-state__dot is-holding"></span>
        <span class="wl-metric__label">{{ t('watchlist.metricHolding') }}</span>
        <span class="wl-metric__val">{{ countHolding }}</span>
      </button>

      <button
        class="wl-metric wl-metric--observing"
        :class="{ 'is-active': activeTab === 'observing', 'is-zero': countObserving === 0 }"
        @click="activeTab = 'observing'"
      >
        <span class="wl-state__dot is-observing"></span>
        <span class="wl-metric__label">{{ t('watchlist.metricObserving') }}</span>
        <span class="wl-metric__val">{{ countObserving }}</span>
      </button>

      <button
        class="wl-metric wl-metric--dropped"
        :class="{ 'is-active': activeTab === 'dropped', 'is-zero': countDropped === 0 }"
        @click="activeTab = 'dropped'"
      >
        <span class="wl-state__dot is-dropped"></span>
        <span class="wl-metric__label">{{ t('watchlist.metricDropped') }}</span>
        <span class="wl-metric__val">{{ countDropped }}</span>
      </button>
    </div>

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

    <!-- 行业分组标签 -->
    <div class="wl-industry-groups" v-if="rows.length && industryGroups.length > 1">
      <button
        class="wl-industry-tag"
        :class="{ 'is-active': !activeIndustry }"
        @click="activeIndustry = ''"
      >
        {{ t('watchlist.allIndustries') }}
        <span class="wl-industry-tag__count">{{ rows.length }}</span>
      </button>
      <button
        v-for="group in industryGroups"
        :key="group.industry"
        class="wl-industry-tag"
        :class="{ 'is-active': activeIndustry === group.industry }"
        @click="activeIndustry = group.industry"
      >
        {{ group.industry }}
        <span class="wl-industry-tag__count">{{ group.count }}</span>
      </button>
    </div>

    <!-- 查不到的标的是**显式告知**而不是偷偷少一行：本地库里没有它的行情，
         但用户确实加过它，静默消失会让人以为自己的操作没生效。 -->
    <p v-if="missingCodes.length" class="watchlist-hint">
      {{ t('watchlist.missingHint', { codes: missingCodes.join('、') }) }}
    </p>

    <EmptyState v-if="!loading && !rows.length" icon="chart-line" :title="t('watchlist.empty')"
      :description="t('watchlist.emptyHint')" />
    <div v-else class="wl-body">
      <!--
        条件挂在**外层**的 .wl-body 上而不是 t-table 自己身上：v-else 必须紧贴
        上面的 v-if，中间不能插元素（包括注释）。曾经把它挂在 table 上、外面
        再包一层 div，vue-tsc 不报错，vite 构建时才炸 "v-else has no
        adjacent v-if"。
      -->
      <div class="wl-body__table">
        <div class="watchlist-tabs" v-if="rows.length">
          <t-radio-group v-model="activeTab" variant="default-filled" size="small">
            <t-radio-button value="all">
              <span class="wl-tab">
                <svg class="wl-tab__icon" viewBox="0 0 14 14" aria-hidden="true">
                  <path d="M2.5 3.5h9M2.5 7h9M2.5 10.5h6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
                </svg>
                {{ t('watchlist.tabAll') }} ({{ rows.length }})
              </span>
            </t-radio-button>
            <t-radio-button value="triggered">
              <!-- has-count 的样式此前根本没有定义，这个高亮一直是死的 -->
              <span class="wl-tab wl-tab--triggered" :class="{ 'has-count': countTriggered > 0 }">
                <svg class="wl-tab__icon" viewBox="0 0 14 14" aria-hidden="true">
                  <circle cx="7" cy="7" r="4.2" stroke="currentColor" stroke-width="1.3" />
                  <circle cx="7" cy="7" r="1.4" fill="currentColor" />
                  <path d="M7 .9v2.2M7 10.9v2.2M.9 7h2.2M10.9 7h2.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
                </svg>
                {{ t('watchlist.tabTriggered') }} ({{ countTriggered }})
              </span>
            </t-radio-button>
            <t-radio-button value="holding">
              <span class="wl-tab">
                <svg class="wl-tab__icon" viewBox="0 0 14 14" aria-hidden="true">
                  <rect x="1.6" y="3.6" width="10.8" height="8" rx="1.4" stroke="currentColor" stroke-width="1.3" />
                  <path d="M5.2 3.6V2.8a1 1 0 011-1h1.6a1 1 0 011 1v.8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
                  <path d="M1.6 7.4h10.8" stroke="currentColor" stroke-width="1.3" />
                </svg>
                {{ t('watchlist.tabHolding') }} ({{ countHolding }})
              </span>
            </t-radio-button>
            <t-radio-button value="observing">
              <span class="wl-tab">
                <svg class="wl-tab__icon" viewBox="0 0 14 14" aria-hidden="true">
                  <path d="M1.2 7S3.7 3.1 7 3.1 12.8 7 12.8 7 10.3 10.9 7 10.9 1.2 7 1.2 7z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
                  <circle cx="7" cy="7" r="1.8" stroke="currentColor" stroke-width="1.3" />
                </svg>
                {{ t('watchlist.tabObserving') }} ({{ countObserving }})
              </span>
            </t-radio-button>
            <t-radio-button value="dropped">
              <span class="wl-tab">
                <svg class="wl-tab__icon" viewBox="0 0 14 14" aria-hidden="true">
                  <rect x="1.6" y="3.2" width="10.8" height="8.4" rx="1.3" stroke="currentColor" stroke-width="1.3" />
                  <path d="M1.6 5.9h10.8M5.3 3.2v2.7" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
                </svg>
                {{ t('watchlist.tabDropped') }} ({{ countDropped }})
              </span>
            </t-radio-button>
          </t-radio-group>
        </div>
        <!-- 持仓资产全景看板 (仅在持仓 Tab 激活且有持仓时呈现) -->
        <div v-if="activeTab === 'holding' && portfolioHoldings.length" class="wl-portfolio-banner">
          <div class="wl-portfolio-banner__item">
            <span class="lbl">{{ t('watchlist.portfolioMarketValue') }}</span>
            <span class="val mono">¥{{ portfolioStats.totalMarketValue.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</span>
          </div>
          <div class="wl-portfolio-banner__item" v-if="portfolioStats.totalCostValue > 0">
            <span class="lbl">{{ t('watchlist.portfolioTotalPnl') }}</span>
            <span class="val mono" :class="portfolioStats.totalPnl >= 0 ? 'is-up' : 'is-down'">
              {{ portfolioStats.totalPnl >= 0 ? '+' : '' }}¥{{ portfolioStats.totalPnl.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
              <small>({{ portfolioStats.totalPnlPct >= 0 ? '+' : '' }}{{ portfolioStats.totalPnlPct.toFixed(2) }}%)</small>
            </span>
          </div>
          <div class="wl-portfolio-banner__item">
            <span class="lbl">{{ t('watchlist.portfolioCount') }}</span>
            <span class="val">{{ portfolioStats.holdingCount }} 只</span>
          </div>
          <div class="wl-portfolio-banner__item is-warn" v-if="portfolioStats.stopLossAlertCount > 0">
            <span class="lbl">⚠️ {{ t('watchlist.portfolioStopAlerts') }}</span>
            <span class="val">{{ portfolioStats.stopLossAlertCount }} 只触发止损</span>
          </div>
        </div>
        <t-table row-key="thscode" class="watchlist-table" :data="sortedRows" :columns="columns"
          :loading="loading || quotesLoading" size="medium" hover
          :sort="sortConfig" @sort-change="handleSortChange"
          :row-class-name="rowClassName" @row-click="onRowClick">
      <template #score="{ row }">
          <!--
            评分 + 当日名次。null 显示「—」而不是 0 —— "今天没评分"和"评了 0 分"
            是两件事，后者会让人以为这只票很差。
          -->
          <div v-if="row.final_score != null" class="wl-score-cell">
            <span class="wl-score" :class="scoreClass(row.final_score)">
              {{ row.final_score.toFixed(1) }}
            </span>
            <span v-if="row.rank" class="wl-rank md-num">#{{ row.rank }}</span>
          </div>
          <span v-else class="wl-score-none">—</span>
        </template>

        <template #thscode="{ row }">
        <div class="wl-code">
          <span class="wl-code__code">{{ row.thscode }}</span>
          <span v-if="row.exchange" class="wl-code__exchange">{{ row.exchange }}</span>
        </div>
      </template>

      <template #name="{ row }">
        <div class="wl-name-cell">
          <!-- 名字优先取行情源；清单里存的可能压根是代码（见 utils/stockDisplayName）。
               识别不出真名时退回 thscode，不显示「—」也不显示伪装成名字的代码。 -->
          <span class="wl-name">{{ displayStockName(row.name, row.thscode, row.quote?.name) || row.thscode }}</span>
          <!-- 「今日触发」只认事件流。条件行上的 last_satisfied 只说明"此刻满不满足"，
               分不清"今天刚跨过"和"早就一直满足"；事件是跨过那一刻留下的、
               之后任何一轮评估都覆盖不掉的证据。note 就是机器写下的原因。 -->
          <span v-if="todayTriggerNotes(row).length" class="wl-fired"
            :title="todayTriggerNotes(row).join('\n')">
            <t-icon name="notification-filled" size="12px" />
            {{ t('watchlist.triggeredToday') }}
          </span>
        </div>
      </template>

      <!-- 行业列：一级行业，没有则显示板块类型 -->
      <template #industry="{ row }">
        <span class="wl-industry" v-if="industryMap[row.thscode]">
          {{ industryMap[row.thscode].level1 }}
        </span>
        <span class="wl-industry wl-industry--board" v-else-if="isBoard(row)">
          {{ t('watchlist.board') }}
        </span>
        <span class="wl-industry wl-industry--unknown" v-else>—</span>
      </template>

      <!-- 技术信号汇总：金叉/死叉/砖型等 -->
      <template #signals="{ row }">
        <div class="wl-signals">
          <span v-if="rowSignals(row).length" class="wl-signal-list">
            <span
              v-for="sig in rowSignals(row)"
              :key="sig.type"
              class="wl-signal"
              :class="`is-${sig.kind}`"
              :title="sig.label"
            >
              {{ sig.icon }}
            </span>
          </span>
          <span v-else class="wl-muted">—</span>
        </div>
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
        <div class="wl-close-cell">
          <span v-if="num(row.quote?.close) !== null" class="wl-num"
            :class="changeClass(row)">{{ formatPrice(row.quote?.close) }}</span>
          <span v-else class="wl-muted">{{ t('watchlist.noData') }}</span>
          <!-- 持仓成本与浮动盈亏胶囊 -->
          <span v-if="rowTargetPnl(row)" class="wl-cost-pill" :class="rowTargetPnl(row)!.pnlClass"
            :title="`持仓成本: ¥${rowTargetPnl(row)!.cost.toFixed(2)}，浮动盈亏: ${rowTargetPnl(row)!.pnlText}`">
            成本 ¥{{ rowTargetPnl(row)!.cost.toFixed(2) }} ({{ rowTargetPnl(row)!.pnlText }})
          </span>
          <!-- 跌破止损预警 -->
          <span v-if="rowTargetStopAlert(row)" class="wl-stop-pill"
            :title="`当前价格已触及/跌破预设止损价 ¥${rowTargetStopAlert(row)!.stopLoss.toFixed(2)}`">
            ⚠️ {{ t('watchlist.stopBreached') }}
          </span>
        </div>
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
          <t-button variant="text" size="small" @click="openConditions(row)">
            {{ t('watchlist.cond') }}
          </t-button>
          <t-button variant="text" size="small" :disabled="rowIndex === 0" @click="pinRow(row)">
            {{ t('watchlist.pin') }}
          </t-button>
          <t-button variant="text" theme="danger" size="small" @click="confirmRemove(row)">
            {{ t('watchlist.remove') }}
          </t-button>
        </div>
      </template>
    </t-table>
      </div>

      <!-- 详情面板：选中一行后右侧滑出，内含 K 线与该票的观察日记。
           挂在表格外面而不是展开行里，是因为图表需要一个稳定尺寸的容器，
           展开行的高度由内容决定，画布量到 0 高时 klinecharts 不会画任何东西。 -->
      <WatchDetailPanel
        v-if="selectedRow"
        :thscode="selectedRow.thscode"
        :name="displayName(selectedRow)"
        :quote="selectedRow.quote"
        :conditions="conditionsByThscode[selectedRow.thscode] || []"
        @close="selectedThscode = ''"
        @open-workspace="openFullWorkspace(selectedRow)"
        @open-conditions="openConditions(selectedRow)"
        @open-halo="openHaloReport(selectedRow)"
      />
    </div>

    <!-- HALO 年报报告。挂在这一层而不是详情面板内部，是为了让面板的选中态与
         弹窗解耦：面板可以随选中行切换，弹窗则在自己关闭前保持当前标的。
         与 K 线工作台里那个 HaloReportDialog 是同一组件、同一数据源，只是入口
         深度不同（这里是 2 步，工作台里是 5 步）。 -->
    <HaloReportDialog
      v-if="haloThscode"
      v-model:visible="haloVisible"
      :thscode="haloThscode"
      :name="haloName"
    />

    <!-- 条件面板。用 dialog 而不是 popover：里面有两个 t-select，下拉渲染到 body，
         挂在 popover 里会被"点击外部"判成关闭，选中值的一瞬间面板就没了。 -->
    <t-dialog v-model:visible="condDialogVisible" :header="condDialogTitle" :footer="false" width="480px"
      :close-on-overlay-click="false" dialog-class-name="wl-cond-dialog" @close="closeConditions">
      <div class="wl-cond">
        <p class="wl-cond__hint">{{ t('watchlist.condHint') }}</p>

        <t-loading v-if="condLoading" size="small" class="wl-cond__loading" />

        <ul v-else-if="conditions.length" class="wl-cond__list">
          <li v-for="c in conditions" :key="c.id" class="wl-cond-chip"
            :class="`wl-cond-chip--${conditionState(c)}`" :title="conditionEvalTitle(c)">
            <span class="wl-cond-chip__expr">{{ conditionExpr(c) }}</span>
            <span class="wl-cond-chip__state">{{ conditionStateLabel(c) }}</span>
            <button type="button" class="wl-cond-chip__del" :title="t('watchlist.condDelete')"
              :aria-label="`${t('watchlist.condDelete')}: ${conditionExpr(c)}`" @click="deleteCondition(c)">
              <t-icon name="close" size="14px" />
            </button>
          </li>
        </ul>
        <p v-else class="wl-cond__empty">{{ t('watchlist.condEmpty') }}</p>

        <div class="wl-cond-presets">
          <span class="wl-cond-presets__title"> {{ t('watchlist.commonPresets') }}: </span>
          <button type="button" class="wl-preset-btn" @click="applyPreset('ma20')">
            {{ t('watchlist.presetCondMa20') }}
          </button>
          <button type="button" class="wl-preset-btn" @click="applyPreset('drop3')">
            {{ t('watchlist.presetCondDrop3') }}
          </button>
          <button type="button" class="wl-preset-btn" @click="applyPreset('vol2')">
            {{ t('watchlist.presetCondVolume2') }}
          </button>
        </div>

        <div class="wl-cond-add">
          <t-select class="wl-cond-add__field" :value="condField" :options="fieldOptions"
            :aria-label="t('watchlist.condField')" @change="setCondField" />
          <t-select class="wl-cond-add__op" :value="condOp" :options="opOptions"
            :aria-label="t('watchlist.condOp')" :title="t('watchlist.condOp')" @change="setCondOp" />
          <t-input-number :value="condValue" class="wl-cond-add__value" :placeholder="t('watchlist.condValue')"
            :aria-label="t('watchlist.condValue')" @change="setCondValue" />
          <t-button theme="primary" size="small" :loading="condSubmitting" :disabled="!canSubmitCondition"
            @click="submitCondition">
            {{ t('watchlist.condAdd') }}
          </t-button>
        </div>
      </div>
    </t-dialog>

    <!-- 全功能 K 线工作台模态 -->
    <t-dialog
      v-model:visible="fullWorkspaceVisible"
      :header="fullWorkspaceTitle"
      :footer="false"
      width="94vw"
      top="3vh"
      dialog-class-name="wl-workspace-dialog"
      destroy-on-close
    >
      <div class="wl-workspace-modal-body">
        <KLineWorkspace v-if="fullWorkspaceVisible" />
      </div>
    </t-dialog>

    <!-- 自选池组合诊断全景模态 -->
    <t-dialog
      v-model:visible="diagnosisVisible"
      :header="t('watchlist.diagnosisTitle')"
      :footer="false"
      width="680px"
      dialog-class-name="wl-diag-dialog"
    >
      <div class="wl-diag-body">
        <div class="wl-diag-metrics">
          <div class="wl-diag-metric-card">
            <span class="wl-diag-metric-card__lbl"> {{ t('watchlist.totalTracked') }} </span>
            <span class="wl-diag-metric-card__val">{{ t('watchlist.diagCount', { count: rows.length }) }}</span>
          </div>
          <div class="wl-diag-metric-card">
            <span class="wl-diag-metric-card__lbl"> {{ t('watchlist.currentPortfolio') }} </span>
            <span class="wl-diag-metric-card__val">{{ t('watchlist.diagCount', { count: countHolding }) }}</span>
          </div>
          <div class="wl-diag-metric-card is-triggered">
            <span class="wl-diag-metric-card__lbl"> {{ t('watchlist.todayAlerts') }} </span>
            <span class="wl-diag-metric-card__val">{{ t('watchlist.diagCount', { count: countTriggered }) }}</span>
          </div>
          <div class="wl-diag-metric-card">
            <span class="wl-diag-metric-card__lbl"> {{ t('watchlist.watchPool') }} </span>
            <span class="wl-diag-metric-card__val">{{ t('watchlist.diagCount', { count: countObserving }) }}</span>
          </div>
        </div>

        <div class="wl-diag-summary">
          <h4 class="wl-diag-h4"> {{ t('watchlist.portfolioOverview') }} </h4>
          <div class="wl-diag-tags">
            <div
              v-for="r in rows.slice(0, 10)"
              :key="r.thscode"
              class="wl-diag-tag"
              :class="r.quote && (r.quote.change_pct ?? 0) >= 0 ? 'is-up' : 'is-down'"
            >
              <span class="wl-diag-tag__name">{{ displayName(r) }}</span>
              <span class="wl-diag-tag__pct">
                {{ r.quote ? `${(r.quote.change_pct ?? 0) >= 0 ? '+' : ''}${(r.quote.change_pct ?? 0).toFixed(2)}%` : '—' }}
              </span>
            </div>
            <span v-if="rows.length > 10" class="wl-diag-more">{{ t('watchlist.diagMore', { count: rows.length }) }}</span>
          </div>
        </div>

        <div class="wl-diag-prompt-box">
          <p class="wl-diag-prompt-box__desc">
            {{ t('watchlist.diagDesc') }}
          </p>
          <t-button theme="primary" size="large" block class="wl-diag-launch-btn" @click="launchAiPortfolioReport">
            <template #icon><t-icon name="chat" /></template>
            {{ t('watchlist.startAiDiagnosis') }}
          </t-button>
        </div>
      </div>
    </t-dialog>
  </main>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import EmptyState from '@/components/EmptyState.vue'
import WatchDetailPanel from '@/finance/components/watchlist/WatchDetailPanel.vue'
import HaloReportDialog from '@/finance/components/kline/HaloReportDialog.vue'
// 异步导入：这个组件（含 klinecharts）约 274 KB / 78 KB gzip，而它只在这个页面
// 的全功能工作台模态里用得到（见模板里的 v-if="fullWorkspaceVisible"）。静态导入
// 会把它塞进本路由的 chunk，于是每次打开 /platform/watchlist 都要多下 5.6 倍的
// 体积，哪怕从不打开那个模态。components/workspace/registry.ts 早就是这个写法，
// 这里跟上。
const KLineWorkspace = defineAsyncComponent(
  () => import('@/finance/components/kline/KLineWorkspace.vue'),
)
import { registerStockName } from '@/finance/utils/stockMentions'
import { displayStockName } from '@/finance/utils/stockDisplayName'
import { provideAgentWorkspace } from '@/finance/composables/useAgentWorkspace'
import { withLaunchAgent } from '@/utils/launchAgent'
import { useSettingsStore } from '@/stores/settings'
import { provideChatKLinePanel } from '@/finance/composables/useChatKLinePanel'
import {
  addCondition,
  addWatchItem,
  fetchQuotes,
  listConditions,
  listEvents,
  listWatchlist,
  removeCondition,
  removeWatchItem,
  searchSymbols,
  updateWatchItem,
  WATCH_STATE_TRANSITIONS,
  type ConditionField,
  type ConditionOp,
  type Quote,
  type SymbolSuggestion,
  type WatchCondition,
  type WatchEvent,
  listRanking,
  type WatchItem,
  type WatchState,
  fetchIndustryMap,
  type IndustryInfo,
} from '@/finance/api/watchlist'

const { t } = useI18n()
const router = useRouter()
const settingsStore = useSettingsStore()

/**
 * embedded = 渲染在个股追踪壳（Tracking.vue）的「自选股」tab 里。
 * 只影响页头：标题与副标题交给外壳，刷新/浏览工作台/组合诊断三个动作仍然留着 ——
 * 它们是这一屏自己的事，外壳不知道它们存在，藏掉等于把入口丢了。
 * 默认 false 时行为与合并前完全一致（/platform/watchlist 仍可直接进）。
 */
const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })
const embedded = computed(() => props.embedded)

// 注入 AgentWorkspace 上下文以供全功能 K 线工作台模态使用
const agentWorkspace = provideAgentWorkspace()
provideChatKLinePanel(agentWorkspace)

const fullWorkspaceVisible = ref(false)
const fullWorkspaceTitle = ref('')

/**
 * HALO 报告弹窗的状态。
 *
 * 标的存成独立变量而不是直接读 selectedRow：面板的选中态会随用户在列表里点
 * 别的行而变，弹窗不该跟着漂 —— 一旦打开就锁定打开时的那只票。
 */
const haloVisible = ref(false)
const haloThscode = ref('')

const haloName = computed(() => {
  const r = rows.value.find((x) => x.thscode === haloThscode.value)
  return r ? displayName(r) : ""
})

function openHaloReport(row: WatchRow) {
  haloThscode.value = row.thscode
  haloVisible.value = true
}

function openFullWorkspace(row?: WatchRow) {
  const currentList = sortedRows.value.length > 0 ? sortedRows.value : rows.value
  if (!currentList.length) return
  const targetRow = row || currentList[0]
  const picks = currentList.map((r) => {
    const parts = r.thscode.split('.')
    return {
      ticker: parts[0] || r.thscode,
      exchange: parts[1] || 'SH',
      name: displayName(r),
    }
  })
  const initialIdx = Math.max(0, currentList.findIndex((r) => r.thscode === targetRow.thscode))
  const name = displayName(targetRow)
  fullWorkspaceTitle.value = `${name} (${targetRow.thscode})`
  agentWorkspace.open('kline', picks, initialIdx)
  fullWorkspaceVisible.value = true
}

/**
 * 工作台底部那些问法（形态研判、HALO 六维/七维、治理事实）走这里开新对话。
 *
 * **必须把当前 agent 一起带走**。新会话用哪个 agent 取自 settings.selectedAgentId，
 * 而这个值是会漂的：从会话页跳到 creatChat 时，会话页的 onBeforeRouteLeave 会
 * restoreDefaultsIfSnapshotted()，把 settings 换成「进入会话前的全局默认」——
 * 也就是 settings.ts 里的 builtin-quick-answer。那种情况下用户点的是
 * 「用 halo.analyze 取六维」，落到新会话却变成 RAG 管线，工具进不了 tool schema，
 * 模型只能回一句「检索材料中没有相关信息」。
 *
 * 落点把 agent 挂在 query 上，由 creatChat 在路由守卫之后应用（见
 * applyLaunchAgent / utils/launchAgent.ts）。
 */
agentWorkspace.sendToChatCallback.value = (text: string) => {
  fullWorkspaceVisible.value = false
  router.push({
    path: '/platform/creatChat',
    query: withLaunchAgent({ q: text }, settingsStore.selectedAgentId),
  })
}

// ── 自选池组合诊断全景 ───────────────────────────────────────────────
import { getTradeTarget } from '@/finance/utils/tradeTargets'

const diagnosisVisible = ref(false)

function openPortfolioDiagnosis() {
  diagnosisVisible.value = true
}

// ── 行业分组 ──────────────────────────────────────────────
/** thscode → 行业信息 */
const industryMap = ref<Record<string, IndustryInfo>>({})
/** 当前选中的行业过滤，空串 = 全部 */
const activeIndustry = ref('')

interface IndustryGroup {
  industry: string
  count: number
}

const industryGroups = computed<IndustryGroup[]>(() => {
  const counts = new Map<string, number>()
  for (const row of rows.value) {
    const info = industryMap.value[row.thscode]
    if (info?.level1) {
      counts.set(info.level1, (counts.get(info.level1) ?? 0) + 1)
    }
  }
  return Array.from(counts.entries())
    .map(([industry, count]) => ({ industry, count }))
    .sort((a, b) => b.count - a.count)
})

/** 行业过滤后的行 */
const industryFilteredRows = computed(() => {
  if (!activeIndustry.value) return rows.value
  return rows.value.filter((r) => {
    const info = industryMap.value[r.thscode]
    return info?.level1 === activeIndustry.value
  })
})

function isBoard(row: WatchRow): boolean {
  return row.exchange === 'TI'
}

async function loadIndustries() {
  const codes = rows.value.map((r) => r.thscode)
  if (!codes.length) return
  try {
    const res = await fetchIndustryMap(codes)
    if (res.code === 0 && res.data) {
      industryMap.value = { ...industryMap.value, ...res.data }
    }
  } catch {
    // 静默失败，行业是增强项
  }
}

// ── 技术信号汇总 ───────────────────────────────────────────────
interface TechSignal {
  type: string
  kind: 'bull' | 'bear' | 'neutral'
  icon: string
  label: string
}

/** 从行情数据计算简单技术信号 */
function rowSignals(row: WatchRow): TechSignal[] {
  const q = row.quote
  if (!q) return []
  const signals: TechSignal[] = []

  // 涨跌信号
  if (q.change_pct != null) {
    if (q.change_pct >= 5) {
      signals.push({ type: 'surge', kind: 'bull', icon: '🔴', label: `大涨 +${q.change_pct.toFixed(1)}%` })
    } else if (q.change_pct <= -5) {
      signals.push({ type: 'drop', kind: 'bear', icon: '🟢', label: `大跌 ${q.change_pct.toFixed(1)}%` })
    }
  }

  // 成交量信号（成交额突增）
  if (q.turnover != null && q.turnover > 50_0000_0000) {
    signals.push({ type: 'highVol', kind: 'bull', icon: '📊', label: '放量' })
  }

  return signals
}

function launchAiPortfolioReport() {
  diagnosisVisible.value = false
  const holdingList = rows.value.filter(r => r.state === 'holding').map(r => {
    const target = getTradeTarget(r.thscode)
    let pnlStr = ''
    if (target?.cost && r.quote?.close) {
      const pnl = (((r.quote.close - target.cost) / target.cost) * 100).toFixed(2)
      pnlStr = ` (持仓成本 ¥${target.cost.toFixed(2)}, 当前浮动盈亏: ${Number(pnl) >= 0 ? '+' : ''}${pnl}%)`
      if (target.shares) {
        const mv = (target.shares * r.quote.close).toFixed(2)
        const diffMoney = ((r.quote.close - target.cost) * target.shares).toFixed(2)
        pnlStr += ` [持股: ${target.shares}股, 市值: ¥${mv}, 浮动盈亏额: ${Number(diffMoney) >= 0 ? '+' : ''}¥${diffMoney}]`
      }
    }
    return `- ${displayName(r)} (${r.thscode}): 现价 ¥${r.quote?.close?.toFixed(2) ?? '—'}, 日内涨跌 ${r.quote?.change_pct ? `${r.quote.change_pct >= 0 ? '+' : ''}${r.quote.change_pct.toFixed(2)}%` : '—'}${pnlStr}`
  })

  const triggeredList = rows.value.filter(r => (todayTriggerNotesByCode.value[r.thscode]?.length ?? 0) > 0).map(r => {
    const notes = todayTriggerNotes(r).join('; ')
    return `- ${displayName(r)} (${r.thscode}): 触发预警【${notes}】(现价 ¥${r.quote?.close?.toFixed(2) ?? '—'})`
  })

  const observingList = rows.value.filter(r => r.state === 'observing').slice(0, 8).map(r => {
    return `- ${displayName(r)} (${r.thscode}): 现价 ¥${r.quote?.close?.toFixed(2) ?? '—'}, 日内涨跌 ${r.quote?.change_pct ? `${r.quote.change_pct >= 0 ? '+' : ''}${r.quote.change_pct.toFixed(2)}%` : '—'}, 关注理由: ${r.note || '无'}`
  })

  const prompt = `请帮我针对当前自选池（共 ${rows.value.length} 只标的）进行一次全景组合诊断与复盘研判：\n\n` +
    `【当前持仓组合】(共 ${countHolding.value} 只)\n` +
    (holdingList.length > 0 ? holdingList.join('\n') : '暂无持仓标的') + '\n\n' +
    `【今日触发预警/买点标的】(共 ${countTriggered.value} 只)\n` +
    (triggeredList.length > 0 ? triggeredList.join('\n') : '今日暂无触发标的') + '\n\n' +
    `【重点观察池候选】(共 ${countObserving.value} 只)\n` +
    (observingList.length > 0 ? observingList.join('\n') : '暂无观察标的') + '\n\n' +
    `请结合当前大盘与行业主线轮动环境，输出一份专业的《自选池盘后大盘点与组合攻防策略》：\n` +
    `1. 【板块暴露与市场主线】当前自选池标的集中在哪些行业题材？资金是否在其主线方向？\n` +
    `2. 【持仓攻防与买卖点】针对当前持仓标的及其实际浮盈/浮亏，哪些建议上移止损保本？哪些出现分歧需要止盈或减仓？\n` +
    `3. 【重点异动机会】对今日触发预警和观察池中异动的标的，判断突破真实性与介入胜率；\n` +
    `4. 【总仓位与交易节奏建议】给出明晰的仓位配比与明日开盘应对策略。`

  router.push({
    path: '/platform/creatChat',
    query: withLaunchAgent({ q: prompt }, settingsStore.selectedAgentId),
  })
}

/** 状态分流 Tab */
const activeTab = ref<'all' | 'triggered' | 'holding' | 'observing' | 'dropped'>('all')

/** 缓存每个标的的条件列表供 K 线图与详情面板使用 */
const conditionsByThscode = ref<Record<string, WatchCondition[]>>({})

async function fetchConditionsForSymbol(thscode: string) {
  if (!thscode) return
  try {
    const res = await listConditions(thscode)
    if (res.data) {
      conditionsByThscode.value[thscode] = res.data
    }
  } catch {}
}

/** 预设条件快速应用 */
function applyPreset(type: 'ma20' | 'drop3' | 'vol2') {
  if (type === 'ma20') {
    condField.value = 'close_vs_ma20'
    condOp.value = 'below'
    condValue.value = 0
  } else if (type === 'drop3') {
    condField.value = 'pct_change'
    condOp.value = 'below'
    condValue.value = -3.0
  } else if (type === 'vol2') {
    condField.value = 'volume_ratio'
    condOp.value = 'above'
    condValue.value = 2.0
  }
}

/** 行情自动刷新的间隔。日线级别的读数，一分钟一次足够。 */
const REFRESH_INTERVAL_MS = 60_000
/** 输入停止多久后才发搜索请求。 */
const SEARCH_DEBOUNCE_MS = 250

interface WatchRow extends WatchItem {
  quote?: Quote
  /**
   * 评分（0-100）与当日总排名，来自 `listRanking`。
   *
   * 这两个字段**不在 WatchItem 上** —— 服务端把评分存在日记（WatchDiary）里，
   * 清单接口不返回。所以这里单独取一次排行再按 thscode 合并，让表格能按
   * 评分排序。评分为 null = 该票当天没有评分作业，不是 0 分。
   */
  final_score?: number | null
  rank?: number | null
  /** 排行里的模型建议（buy/sell/…），同样只在有日记时才有。 */
  verdict?: string | null
}

const items = ref<WatchItem[]>([])
const quotes = ref<Record<string, Quote>>({})
const loading = ref(false)
const quotesLoading = ref(false)
const keyword = ref('')
const suggestions = ref<SymbolSuggestion[]>([])
const lastUpdated = ref('')

/**
 * 评分索引：thscode → { score, rank, verdict }。
 *
 * 单独一个接口（`listRanking`），与清单接口并行发出。取不到就留空 —— 表格
 * 照常显示，只是没有评分列、排序退回按代码。评分是增强项，不该让它拖垮整页。
 */
const scoreIndex = ref<Record<string, { score: number | null; rank: number | null; verdict: string | null }>>({})

let searchTimer: ReturnType<typeof setTimeout> | null = null
let refreshTimer: ReturnType<typeof setInterval> | null = null

const rows = computed<WatchRow[]>(() =>
  items.value.map((item) => {
    const s = scoreIndex.value[item.thscode]
    return {
      ...item,
      quote: quotes.value[item.thscode],
      final_score: s?.score ?? null,
      rank: s?.rank ?? null,
      verdict: s?.verdict ?? null,
    }
  }),
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

/**
 * 活动流。整条流一次拉回来，只在本地筛出「条件触发」那一种 kind 用于徽标 ——
 * 按 kind 分别请求只会把同一次查询拆成两次。
 */
const events = ref<WatchEvent[]>([])

/**
 * 今日触发的 note，按 thscode 归集。
 *
 * "今日" = 行情里最新的交易日（newestDate），不是浏览器当前日期 —— 周末、
 * 节假日、本地行情还没更新时，页面上所有人的"最新"都该是同一个交易日。
 * 拿不到行情（newestDate 为空）就一条都不标：宁可不说，也不能猜。
 */
const todayTriggerNotesByCode = computed<Record<string, string[]>>(() => {
  const day = newestDate.value
  const grouped: Record<string, string[]> = {}
  if (!day) return grouped
  for (const event of events.value) {
    if (event.kind !== 'condition_triggered') continue
    // 比的是**判定所属交易日**，不是 created_at：created_at 是落库那一刻（D+1 早上），
    // 而它报告的交易日是 D。拿 created_at 比会让这个徽章在真实运行里永远不亮。
    if (!event.eval_date || event.eval_date !== day) continue
    if (!event.note) continue
    if (!grouped[event.thscode]) grouped[event.thscode] = []
    grouped[event.thscode].push(event.note)
  }
  return grouped
})

function todayTriggerNotes(row: WatchRow): string[] {
  return todayTriggerNotesByCode.value[row.thscode] ?? []
}

const countTriggered = computed(
  () => rows.value.filter((r) => (todayTriggerNotesByCode.value[r.thscode]?.length ?? 0) > 0).length,
)
const countHolding = computed(() => rows.value.filter((r) => r.state === 'holding').length)
const portfolioHoldings = computed(() => {
  return rows.value.filter((r) => r.state === 'holding')
})

const portfolioStats = computed(() => {
  let totalMarketValue = 0
  let totalCostValue = 0
  let totalShares = 0
  const holdingCount = portfolioHoldings.value.length
  let stopLossAlertCount = 0

  for (const r of portfolioHoldings.value) {
    const target = getTradeTarget(r.thscode)
    const close = r.quote?.close
    if (close && target?.stopLoss && close <= target.stopLoss) {
      stopLossAlertCount++
    }
    if (close && target?.shares && target.shares > 0) {
      const mv = close * target.shares
      totalMarketValue += mv
      totalShares += target.shares
      if (target.cost && target.cost > 0) {
        totalCostValue += target.cost * target.shares
      }
    }
  }

  const totalPnl = totalCostValue > 0 ? totalMarketValue - totalCostValue : 0
  const totalPnlPct = totalCostValue > 0 ? (totalPnl / totalCostValue) * 100 : 0

  return {
    holdingCount,
    totalMarketValue,
    totalCostValue,
    totalPnl,
    totalPnlPct,
    stopLossAlertCount,
    hasSharesData: totalShares > 0,
  }
})
const countObserving = computed(() => rows.value.filter((r) => r.state === 'observing').length)
const countDropped = computed(() => rows.value.filter((r) => r.state === 'dropped').length)

/**
 * 状态过滤 + 行业过滤。两层过滤顺序：先按状态分桶（tab），再按行业筛选。
 */
const filteredRows = computed(() => {
  let list: WatchRow[]
  switch (activeTab.value) {
    case 'triggered':
      list = rows.value.filter((r) => (todayTriggerNotesByCode.value[r.thscode]?.length ?? 0) > 0)
      break
    case 'holding':
      list = rows.value.filter((r) => r.state === 'holding')
      break
    case 'observing':
      list = rows.value.filter((r) => r.state === 'observing')
      break
    case 'dropped':
      list = rows.value.filter((r) => r.state === 'dropped')
      break
    default:
      list = rows.value
  }
  // 行业过滤
  if (activeIndustry.value) {
    list = list.filter((r) => {
      const info = industryMap.value[r.thscode]
      return info?.level1 === activeIndustry.value
    })
  }
  return list
})
/**
 * 默认按评分降序 —— 这就是原来那块独立的「今日评分排行」，并进了表格。
 *
 * 之前排行和表格是两块，排行里的票在表格里又出现一遍，用户要判断
 * "哪只要动手"得先看排行、再往下扫表格找同一只票。现在只有一份数据，
 * 排序即结论。
 */
const sortConfig = ref<{ sortBy: string; descending: boolean } | null>({
  sortBy: 'score',
  descending: true,
})

function handleSortChange(sort: any) {
  sortConfig.value = sort || null
}

const sortedRows = computed(() => {
  const list = [...filteredRows.value]
  if (!sortConfig.value || !sortConfig.value.sortBy) return list
  const { sortBy, descending } = sortConfig.value
  return list.sort((a, b) => {
    let va: number | string | null | undefined
    let vb: number | string | null | undefined
    if (sortBy === 'close') {
      va = a.quote?.close
      vb = b.quote?.close
    } else if (sortBy === 'change') {
      va = a.quote?.change_pct
      vb = b.quote?.change_pct
    } else if (sortBy === 'turnover') {
      va = a.quote?.turnover
      vb = b.quote?.turnover
    } else if (sortBy === 'thscode') {
      va = a.thscode
      vb = b.thscode
    } else if (sortBy === 'score') {
      va = a.final_score
      vb = b.final_score
    }
    if (va == null && vb == null) return 0
    if (va == null) return 1
    if (vb == null) return -1
    if (typeof va === 'string' && typeof vb === 'string') {
      return descending ? vb.localeCompare(va) : va.localeCompare(vb)
    }
    return descending ? Number(vb) - Number(va) : Number(va) - Number(vb)
  })
})

function rowTargetPnl(row: WatchRow): { cost: number; pnlText: string; pnlClass: string } | null {
  if (row.state !== 'holding' || !row.quote?.close) return null
  const target = getTradeTarget(row.thscode)
  if (!target?.cost) return null
  const diff = ((row.quote.close - target.cost) / target.cost) * 100
  let extraText = ''
  if (target.shares) {
    const pnlVal = (row.quote.close - target.cost) * target.shares
    extraText = ` · ${pnlVal >= 0 ? '+' : ''}¥${Math.round(pnlVal).toLocaleString()}`
  }
  return {
    cost: target.cost,
    pnlText: `${diff >= 0 ? '+' : ''}${diff.toFixed(2)}%${extraText}`,
    pnlClass: diff >= 0 ? 'is-up' : 'is-down',
  }
}

function rowTargetStopAlert(row: WatchRow): { stopLoss: number } | null {
  if (row.state !== 'holding' || !row.quote?.close) return null
  const target = getTradeTarget(row.thscode)
  if (!target?.stopLoss) return null
  if (row.quote.close <= target.stopLoss) {
    return { stopLoss: target.stopLoss }
  }
  return null
}


/** 条件面板当前对着哪一行（只存代码 + 展示名，避免行情刷新后握着过期对象）。 */
const condThscode = ref('')
const condLabel = ref('')
const condDialogVisible = ref(false)
const conditions = ref<WatchCondition[]>([])
const condLoading = ref(false)
const condSubmitting = ref(false)
const condField = ref<ConditionField>('price')
const condOp = ref<ConditionOp>('above')
/** undefined（而非 null）表示"还没填"：与 t-input-number 的 modelValue 类型对齐，
 *  清空输入框不会悄悄变成一个 0。 */
const condValue = ref<number | undefined>(undefined)

const condDialogTitle = computed(() =>
  condLabel.value
    ? `${t('watchlist.condPanelTitle')} · ${condLabel.value}`
    : t('watchlist.condPanelTitle'),
)

/** 字段下拉的文案。value 走的是契约里的机器名，label 才是给人看的。 */
const fieldOptions = computed(() => [
  { value: 'price', label: t('watchlist.condFieldPrice') },
  { value: 'pct_change', label: t('watchlist.condFieldPct') },
  { value: 'volume_ratio', label: t('watchlist.condFieldVolumeRatio') },
  { value: 'close_vs_ma20', label: t('watchlist.condFieldMa20') },
])

/** 比较方向直接用 `>` / `<` 两个符号，不做翻译：它是数学记号，五个语种里
 *  都对同一个人说同一件事，翻译成「大于/高于」反而会让人去找符号在哪。 */
const opOptions = computed(() => [
  { value: 'above', label: '>' },
  { value: 'below', label: '<' },
])

/** 数值允许为负（涨跌幅 -3.2 是合法条件），所以只挡 null/NaN，不挡 0 与负数。 */
const canSubmitCondition = computed(() => num(condValue.value) !== null)

const columns = computed(() => [
  {
    colKey: 'score',
    title: t('watchlist.columns.score'),
    width: 92,
    sorter: true,
    align: 'right' as const,
  },
  { colKey: 'thscode', title: t('watchlist.columns.code'), width: 148, sorter: true },
  { colKey: 'name', title: t('watchlist.columns.name'), minWidth: 140 },
  { colKey: 'industry', title: t('watchlist.columns.industry'), width: 110 },
  { colKey: 'signals', title: t('watchlist.columns.signals'), width: 140 },
  { colKey: 'state', title: t('watchlist.columns.state'), width: 112 },
  { colKey: 'note', title: t('watchlist.columns.note'), minWidth: 150 },
  { colKey: 'close', title: t('watchlist.columns.price'), width: 130, align: 'right' as const, sorter: true },
  { colKey: 'change', title: t('watchlist.columns.change'), width: 170, align: 'right' as const, sorter: true },
  { colKey: 'turnover', title: t('watchlist.columns.turnover'), width: 110, align: 'right' as const, sorter: true },
  { colKey: 'date', title: t('watchlist.columns.date'), width: 128, align: 'center' as const },
  { colKey: 'actions', title: t('watchlist.columns.actions'), width: 190, align: 'right' as const },
])


/**
 * 评分着色。评分是"关注度"不是"涨跌"，所以**不用红绿** ——
 * 红绿在这个页面已经被"涨/跌"占用了，用在这里会让人以为
 * "高分=今天涨得多"。改用深浅：>=70 强调，<50 淡下去。
 */
function scoreClass(score: number): string {
  if (score >= 70) return 'is-high'
  if (score >= 50) return 'is-mid'
  return 'is-low'
}

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

/** 正在看详情的那一行（存代码而不是行对象：行情每分钟刷新，行对象会换）。 */
const selectedThscode = ref('')

const selectedRow = computed<WatchRow | null>(
  () => rows.value.find((r) => r.thscode === selectedThscode.value) || null,
)

/** 点行开详情，再点同一行关闭。 */
function onRowClick({ row }: { row: WatchRow }) {
  if (selectedThscode.value === row.thscode) {
    selectedThscode.value = ''
  } else {
    selectedThscode.value = row.thscode
    void fetchConditionsForSymbol(row.thscode)
  }
}

/** 选中行的高亮。空选中不返回任何类，避免"什么都没选也有高亮"。 */
function rowClassName({ row }: { row: WatchRow }) {
  return row.thscode === selectedThscode.value ? 'is-selected' : ''
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
 *
 * 与表格那一列共用 utils/stockDisplayName 的判定：清单里存的可能压根是代码
 * （新增时只传了代码），那种"名字"不能当名字用。
 */
function displayName(row: WatchRow): string {
  return displayStockName(row.name, row.thscode, row.quote?.name) || row.thscode
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

/**
 * 条件的三态。**`null` 是"还没判定过"，不是"不满足"** —— 把两者折叠成
 * 一个灰色状态，就是把「尚无法判定」误报成「已确认不满足」，而这两句话让
 * 人做的决定完全不同：前者是"再等等"，后者是"这事没发生"。
 */
function evalLiveCondition(c: WatchCondition): boolean | null {
  const q = quotes.value[c.thscode]
  if (!q) return null
  if (c.field === 'price' && q.close !== null) {
    return c.op === 'above' ? q.close > c.value : q.close < c.value
  }
  if (c.field === 'pct_change' && q.change_pct !== null) {
    return c.op === 'above' ? q.change_pct > c.value : q.change_pct < c.value
  }
  return null
}

function conditionState(c: WatchCondition): 'met' | 'unmet' | 'live_met' | 'live_unmet' | 'unknown' {
  if (c.last_satisfied === null) {
    const live = evalLiveCondition(c)
    if (live === true) return 'live_met'
    if (live === false) return 'live_unmet'
    return 'unknown'
  }
  return c.last_satisfied ? 'met' : 'unmet'
}

function conditionStateLabel(c: WatchCondition): string {
  switch (conditionState(c)) {
    case 'live_met':
      return t('watchlist.condStateLiveMet')
    case 'live_unmet':
      return t('watchlist.condStateLiveUnmet')
    case 'met':
      return t('watchlist.condStateMet')
    case 'unmet':
      return t('watchlist.condStateUnmet')
    default:
      return t('watchlist.condStateUnknown')
  }
}

/** 字段的展示名。认不出的字段原样露出，理由同 stateLabel。 */
function conditionFieldLabel(field: ConditionField): string {
  switch (field) {
    case 'price':
      return t('watchlist.condFieldPrice')
    case 'pct_change':
      return t('watchlist.condFieldPct')
    case 'volume_ratio':
      return t('watchlist.condFieldVolumeRatio')
    case 'close_vs_ma20':
      return t('watchlist.condFieldMa20')
    default:
      return field
  }
}

/** 条件的可读表达式，例如「价格 > 1235.00」。 */
function conditionExpr(c: WatchCondition): string {
  const value = num(c.value)
  const shown = value === null ? '—' : value.toFixed(2)
  return `${conditionFieldLabel(c.field)} ${c.op === 'above' ? '>' : '<'} ${shown}`
}

/**
 * 悬浮提示补上"上次判定是哪天"：只看「尚无法判定」分不出是刚添加还没跑过，
 * 还是这只票的历史数据一直不够 —— 后者是本地数据问题，值得用户去查。
 */
function conditionEvalTitle(c: WatchCondition): string {
  if (c.last_satisfied === null) {
    const live = evalLiveCondition(c)
    if (live !== null) {
      return '基于最新行情实时计算；正式事件归档与推送将于隔夜任务（08:30）执行'
    }
    return t('watchlist.condNeverEval')
  }
  return c.last_eval_date
    ? t('watchlist.condEvalAt', { date: c.last_eval_date })
    : t('watchlist.condNeverEval')
}

function setCondField(value: unknown) {
  condField.value = value as ConditionField
}

function setCondOp(value: unknown) {
  condOp.value = value as ConditionOp
}

/**
 * t-input-number 的 change 值是 `number | string`（清空时是 `''`）。收敛成
 * 「合法数字或 undefined」：清空必须回到"未填写"，而 0 和负数都是合法条件值
 * （涨跌幅 -3.2 就是），不能当空处理。
 */
function setCondValue(value: unknown) {
  if (value === null || value === undefined || value === '') {
    condValue.value = undefined
    return
  }
  const parsed = typeof value === 'number' ? value : Number(value)
  condValue.value = num(parsed) ?? undefined
}

function openConditions(row: WatchRow) {
  condThscode.value = row.thscode
  condLabel.value = displayName(row)
  condField.value = 'price'
  condOp.value = 'above'
  condValue.value = undefined
  conditions.value = []
  condDialogVisible.value = true
  void loadConditions(row.thscode)
}

function closeConditions() {
  condThscode.value = ''
  condLabel.value = ''
  conditions.value = []
}

async function loadConditions(thscode: string) {
  condLoading.value = true
  try {
    const res = await listConditions(thscode)
    // 请求在飞的时候用户可能已经翻到另一行：迟到的响应只能丢掉，
    // 否则面板会显示上一条标的的条件。
    if (condThscode.value !== thscode) return
    conditions.value = res.data ?? []
    conditionsByThscode.value[thscode] = res.data ?? []
  } catch (error: any) {
    MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  } finally {
    if (condThscode.value === thscode) condLoading.value = false
  }
}

async function submitCondition() {
  const value = num(condValue.value)
  const thscode = condThscode.value
  if (value === null || !thscode || condSubmitting.value) return
  condSubmitting.value = true
  try {
    const res = await addCondition(thscode, { field: condField.value, op: condOp.value, value })
    // created=false：同一条条件已经在了（双击「添加」会走到这里）。报成
    // 「已添加」会让用户以为自己多加了一条。
    MessagePlugin.success(res.created ? t('watchlist.condAdded') : t('watchlist.condExists'))
    condValue.value = undefined
    await loadConditions(thscode)
    conditionsByThscode.value[thscode] = conditions.value
  } catch (error: any) {
    MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  } finally {
    condSubmitting.value = false
  }
}

async function deleteCondition(c: WatchCondition) {
  const thscode = condThscode.value
  if (!thscode) return
  try {
    await removeCondition(thscode, c.id)
    MessagePlugin.success(t('watchlist.condDeleted'))
    await loadConditions(thscode)
    conditionsByThscode.value[thscode] = conditions.value
  } catch (error: any) {
    MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  }
}

/** 拉活动流。它只服务于「今日触发」徽标：失败就静默不标，为一个附加提示
 *  弹错误框只会打断主线行情。 */
async function loadEvents() {
  try {
    const res = await listEvents({ limit: 200 })
    events.value = res.data ?? []
  } catch {
    events.value = []
  }
}

async function loadItems() {
  loading.value = true
  try {
    const res = await listWatchlist()
    items.value = res.data ?? []
    for (const item of items.value) {
      if (item.thscode && item.name) {
        registerStockName(item.thscode, item.name)
      }
    }
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
    for (const q of Object.values(quotes.value)) {
      if (q.thscode && q.name) {
        registerStockName(q.thscode, q.name)
      }
    }
    lastUpdated.value = new Date().toLocaleTimeString()
  } catch (error: any) {
    if (!silent) MessagePlugin.error(error?.message || t('watchlist.loadFailed'))
  } finally {
    if (!silent) quotesLoading.value = false
  }
}

/** 手动刷新：行情和活动流一起补齐（「今日触发」徽标也依赖后者）。 */
/**
 * 取当日评分排行，按 thscode 建索引供表格排序用。
 *
 * 失败不抛：评分是增强项，取不到时表格只是没有评分列，
 * 不该让整页因为一个附加接口挂掉。
 */
async function loadScores() {
  try {
    const res = await listRanking({ limit: 200 })
    const idx: Record<string, { score: number | null; rank: number | null; verdict: string | null }> = {}
    for (const r of res.data || []) {
      idx[r.thscode] = {
        score: r.final_score ?? null,
        rank: r.rank ?? null,
        verdict: r.verdict ?? null,
      }
    }
    scoreIndex.value = idx
  } catch {
    scoreIndex.value = {}
  }
}

async function refreshAll() {
  await Promise.all([refreshQuotes(), loadEvents(), loadScores()])
}

async function reloadAll() {
  await loadItems()
  await refreshAll()
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

function handleRankingSelect(thscode: string) {
  selectedThscode.value = thscode
  const targetRow = rows.value.find((r) => r.thscode === thscode)
  if (targetRow && activeTab.value !== 'all' && activeTab.value !== targetRow.state) {
    activeTab.value = 'all'
  }
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
  if (document.visibilityState === 'visible') {
    void refreshQuotes(true)
    // 活动流也补一次：触发只在新交易日发生一次，页面恰好开着的时候
    // 不能等下一次手动刷新才看到「今日触发」。
    void loadEvents()
  }
}

onMounted(async () => {
  await reloadAll()
  // 行业数据是增强项，加载失败不影响主流程
  await loadIndustries()
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

/* 嵌入态：标题让给外壳，这一行只剩右对齐的动作按钮，压成一条窄条。
   不写 `justify-content: flex-end` 的话，按钮会贴到左边、和 tab 条的
   「大盘」重叠 —— 外壳的标题在上一行，视觉上仍是一条标题区。 */
.watchlist-header.is-embedded .watchlist-title-row {
  justify-content: flex-end;
}

// 表格与详情面板并排。表格这一侧必须 min-width: 0，否则 t-table 的内容宽度
// 会把 flex 容器撑破，右侧面板被挤出视口——这是 flex 子项的默认行为
// （min-width:auto），不显式归零就一定会发生。
.wl-body {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  flex: 1;
  min-height: 0;
}

.wl-body__table {
  flex: 1;
  min-width: 0;
}

// 选中行：左侧一条竖线而不是整行底色。整行底色会盖掉 hover 与涨跌色的
// 可读性，一条竖线只表达"正在看这只"，不与状态色抢注意力。
:deep(.watchlist-table .is-selected > td:first-child) {
  box-shadow: inset 3px 0 0 var(--wl-up);
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
  border-radius: var(--app-radius-sm);
  box-shadow: var(--td-shadow-2);
}

.watchlist-suggest__item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 8px;
  border-radius: var(--app-radius-xs);
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
  border-radius: var(--app-radius-sm);
  background: var(--td-bg-color-secondarycontainer);
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-sm);
}

/* 行业分组标签 */
.wl-industry-groups {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: 12px 0 8px;
}

.wl-industry-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-pill);
  background: transparent;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
  cursor: pointer;
  transition: all var(--app-motion-fast) ease;

  &:hover {
    border-color: var(--td-brand-color);
    color: var(--td-brand-color);
  }

  &.is-active {
    background: var(--td-brand-color);
    border-color: var(--td-brand-color);
    color: #ffffff;
  }

  &__count {
    font-family: var(--app-font-family-mono);
    font-size: var(--app-text-2xs);
    opacity: 0.8;
  }
}

/* 行业列 */
.wl-industry {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-secondary);

  &--board {
    color: var(--td-text-color-placeholder);
    font-style: italic;
  }

  &--unknown {
    color: var(--td-text-color-placeholder);
  }
}

/* 技术信号 */
.wl-signals {
  display: flex;
  align-items: center;
  gap: 4px;
}

.wl-signal-list {
  display: inline-flex;
  gap: 3px;
}

.wl-signal {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 4px;
  font-size: 12px;
  cursor: help;

  &.is-bull {
    background: rgba(239, 68, 68, 0.12);
  }

  &.is-bear {
    background: rgba(16, 185, 129, 0.12);
  }

  &.is-neutral {
    background: rgba(107, 114, 128, 0.12);
  }
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
  min-width: 0;
  overflow: hidden;
  color: var(--td-text-color-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
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
  border-radius: var(--app-radius-xs);
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
.wl-portfolio-banner {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 14px;
  margin-bottom: 8px;
  background: var(--td-bg-color-secondarycontainer);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-sm);
  flex-wrap: wrap;

  &__item {
    display: flex;
    align-items: baseline;
    gap: 6px;
    font-size: var(--app-text-sm);

    .lbl {
      color: var(--td-text-color-secondary);
      font-size: var(--app-text-xs);
    }

    .val {
      font-weight: 600;
      color: var(--td-text-color-primary);

      &.is-up { color: var(--wl-up, #dc2626); }
      &.is-down { color: var(--wl-down, #047857); }

      small {
        font-size: var(--app-text-xs);
        margin-left: 2px;
      }
    }

    &.is-warn .val {
      color: var(--wl-up, #dc2626);
      font-weight: 700;
    }
  }
}

.wl-close-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.wl-cost-pill {
  font-size: 11px;
  line-height: 1.2;
  padding: 1px 4px;
  border-radius: 2px;
  background: var(--td-bg-color-secondarycontainer, #f3f3f3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;

  &.is-up {
    color: var(--wl-up, #dc2626);
    background: rgba(220, 38, 38, 0.08);
  }

  &.is-down {
    color: var(--wl-down, #047857);
    background: rgba(4, 120, 87, 0.08);
  }
}

.wl-stop-pill {
  font-size: 10px;
  line-height: 1.2;
  padding: 1px 4px;
  border-radius: 2px;
  background: rgba(220, 38, 38, 0.12);
  color: var(--wl-up, #dc2626);
  font-weight: 600;
  white-space: nowrap;
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

.wl-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* 「今日触发」：这一天真的跨过了一个用户自己设的条件 —— 这张表上唯一会
   导致"被通知"的事实，给它最强的视觉重量，并让 note（为什么触发）可悬浮查看。 */
.wl-fired {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 3px;
  padding: 1px 7px;
  border: 1px solid color-mix(in srgb, var(--td-warning-color) 55%, transparent);
  border-radius: var(--app-radius-pill);
  background: color-mix(in srgb, var(--td-warning-color) 14%, transparent);
  color: var(--td-warning-color);
  font-size: var(--app-text-xs);
  line-height: 17px;
  white-space: nowrap;
  cursor: help;
}

.wl-cond {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.wl-cond__hint {
  margin: 0;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-sm);
  line-height: 20px;
}

.wl-cond__loading {
  display: flex;
  justify-content: center;
  padding: 16px 0;
}

.wl-cond__list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.wl-cond__empty {
  margin: 0;
  color: var(--td-text-color-placeholder);
  font-size: var(--app-text-sm);
}

/* 三种判定状态不只靠颜色区分：已满足是实色描边、未满足是灰描边、尚无法判定
   是虚线。色觉障碍下也要能一眼看懂 —— 而且"虚线 = 还不知道"比任何配色都直观。 */
.wl-cond-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 4px 3px 9px;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-pill);
  background: var(--td-bg-color-secondarycontainer);
  color: var(--td-text-color-primary);
  font-size: var(--app-text-sm);
  line-height: 18px;
  white-space: nowrap;

  &__expr {
    font-family: monospace;
  }

  &__state {
    color: var(--td-text-color-secondary);
    font-size: var(--app-text-xs);
  }

  &__del {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: var(--td-text-color-placeholder);
    cursor: pointer;

    &:hover {
      background: var(--td-error-color-1);
      color: var(--td-error-color);
    }
  }

  &--met, &--live_met {
    border-color: color-mix(in srgb, var(--td-brand-color) 45%, transparent);
    background: color-mix(in srgb, var(--td-brand-color) 10%, transparent);

    .wl-cond-chip__state {
      color: var(--td-brand-color);
    }
  }

  &--unmet, &--live_unmet {
    border-color: var(--td-component-stroke);
    background: var(--td-bg-color-secondarycontainer);
  }

  &--unknown {
    border-style: dashed;
    color: var(--td-text-color-secondary);
  }
}

.wl-cond-add {
  display: flex;
  align-items: center;
  gap: 8px;
}

.wl-cond-add__field {
  flex: 1 1 auto;
  min-width: 0;
}

.wl-cond-add__op {
  flex: 0 0 64px;
}

.wl-cond-add__value {
  flex: 0 0 96px;
}

.wl-cond-presets {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: var(--app-text-xs);

  &__label {
    color: var(--td-text-color-secondary);
    flex-shrink: 0;
  }
}

.wl-preset-btn {
  padding: 2px 8px;
  border: 1px dashed var(--td-border-level-2-color);
  border-radius: var(--app-radius-pill);
  background: transparent;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
  cursor: pointer;
  transition: all var(--app-motion-fast) ease;

  &:hover {
    border-color: var(--td-brand-color);
    color: var(--td-brand-color);
    background: color-mix(in srgb, var(--td-brand-color) 8%, transparent);
  }
}

/*
 * 概览条 —— 一行文字，不是五张卡。
 *
 * 早前是 5 张等宽卡片（min-width 120px + 大号数字），而 5 个数字里通常 3 个是 0。
 * 零值占着最贵的首屏，真正的决策信息被挤到下面。
 * 现在压成一行：非 0 加粗上色，0 淡下去，仍然可点（切分类）。
 */
.watchlist-metrics {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin: 14px 0 10px;
  padding: 3px;
  border-radius: var(--app-radius-pill);
  background: var(--td-bg-color-secondarycontainer);
  width: fit-content;
}

.wl-metric {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 11px;
  border: 0;
  border-radius: var(--app-radius-pill);
  background: transparent;
  cursor: pointer;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
  transition: background var(--app-motion-fast) ease, color var(--app-motion-fast) ease;

  &:hover { background: var(--td-bg-color-container); }

  &.is-active {
    background: var(--td-bg-color-container);
    color: var(--td-text-color-primary);
    box-shadow: var(--td-shadow-1);
  }

  /* 0 值：淡下去但仍可点。不隐藏 —— 隐藏会让用户忘了这个分类存在。 */
  &.is-zero { opacity: 0.5; }

  &__val {
    font-weight: 600;
    font-family: var(--app-font-family-mono);
    color: var(--td-text-color-primary);
    font-variant-numeric: tabular-nums;
  }

  &.is-zero &__val { font-weight: 400; color: var(--td-text-color-placeholder); }

  /* 今日触发：唯一需要"叫人"的分类，非 0 时用警示色 */
  &--triggered:not(.is-zero) {
    color: var(--td-warning-color);
    .wl-metric__val { color: var(--td-warning-color); }
  }
}

/* ── 表格里的评分列 ──
   评分是"关注度"不是"涨跌"，所以不用红绿（红绿在这个页面已被涨/跌占用，
   用在这里会让人以为"高分=今天涨得多"），改用深浅。 */
.wl-score-cell { display: inline-flex; align-items: baseline; gap: 5px; }

.wl-score {
  font-family: var(--app-font-family-mono);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--td-text-color-primary);

  &.is-high { font-size: var(--app-text-md); }
  &.is-mid { color: var(--td-text-color-secondary); }
  &.is-low { color: var(--td-text-color-placeholder); font-weight: 400; }
}

.wl-rank {
  font-size: var(--app-text-2xs);
  color: var(--td-text-color-placeholder);
}

/* 「今天没评分」是 null，不是 0 分 —— 显示破折号，别让它看起来像 0。 */
.wl-score-none { color: var(--td-text-color-placeholder); }


.watchlist-tabs {
  margin-bottom: 12px;

  /* TDesign 的 filled 分段控件给外层 .t-radio-group 上了 padding + 一层底色，
     而选中态的 __bg-block 又内缩 2px。结果就是整行首尾各漏出一条底色——
     截图里那两道「白边」。这里把外层 padding 归零、底色透明，让选中底色
     紧贴按钮边界。 */
  :deep(.t-radio-group--filled) {
    padding: 0;
    background-color: transparent;
  }

  :deep(.t-radio-group--filled .t-radio-group__bg-block) {
    left: 0;
    top: 0;
    height: 100%;
  }

  :deep(.t-radio-group--filled .t-radio-button) {
    border-radius: var(--app-radius-sm);
  }
}

/* chip 内部排版：图标随字号走，与文字基线对齐 */
.wl-tab {
  display: inline-flex;
  align-items: center;
  gap: 4px;

  &__icon {
    width: 14px;
    height: 14px;
    flex-shrink: 0;
    opacity: 0.75;
  }

  /* 今日有触发时给一点警示色。这个 class 一直在模板里绑着，
     样式却从来没写过——高亮是死的。 */
  &--triggered.has-count {
    color: var(--td-warning-color);
    font-weight: 500;

    .wl-tab__icon {
      opacity: 1;
    }
  }
}

:deep(.wl-workspace-dialog) {
  .t-dialog__body {
    padding: 0;
    height: 84vh;
    overflow: hidden;
  }
}

.wl-workspace-modal-body {
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.wl-diag-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 8px 0;
}

.wl-diag-metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.wl-diag-metric-card {
  display: flex;
  flex-direction: column;
  // grid 拉伸让 4 张卡等高，但卡内若顶对齐，标签换行数不同的卡其数值会错位
  // （en 下 "Current Portfolio" 换行、"Today Alerts" 不换行）。
  // space-between 把数值钉到卡底，让 4 个数值共用一条基线。
  justify-content: space-between;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--td-border-level-1-color);
  border-radius: var(--app-radius-sm);
  background: var(--td-bg-color-secondarycontainer);

  &__lbl {
    font-size: var(--app-text-xs);
    color: var(--td-text-color-secondary);
    // 标签允许折行，但不让长 token 在词中间断开
    overflow-wrap: break-word;
  }

  &__val {
    font-size: var(--app-text-lg);
    font-weight: 600;
    font-family: monospace;
    color: var(--td-text-color-primary);
  }

  &.is-triggered {
    border-color: color-mix(in srgb, var(--td-warning-color) 45%, transparent);
    background: color-mix(in srgb, var(--td-warning-color) 8%, var(--td-bg-color-container));
    .wl-diag-metric-card__val {
      color: var(--td-warning-color);
    }
  }
}

.wl-diag-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.wl-diag-h4 {
  margin: 0;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

.wl-diag-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.wl-diag-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  border-radius: var(--app-radius-xs);
  border: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-container);
  font-size: var(--app-text-xs);
  font-family: monospace;

  &__name {
    color: var(--td-text-color-primary);
  }

  &__pct {
    font-weight: 600;
  }

  &.is-up {
    color: var(--wl-up);
    border-color: color-mix(in srgb, var(--wl-up) 35%, transparent);
  }

  &.is-down {
    color: var(--wl-down);
    border-color: color-mix(in srgb, var(--wl-down) 35%, transparent);
  }
}

.wl-diag-more {
  font-size: var(--app-text-xs);
  color: var(--td-text-color-placeholder);
}

.wl-diag-prompt-box {
  padding: 12px;
  border-radius: var(--app-radius-sm);
  background: color-mix(in srgb, var(--td-brand-color) 6%, var(--td-bg-color-container));
  border: 1px solid color-mix(in srgb, var(--td-brand-color) 25%, transparent);
  display: flex;
  flex-direction: column;
  gap: 12px;

  &__desc {
    margin: 0;
    font-size: var(--app-text-sm);
    line-height: 1.6;
    color: var(--td-text-color-primary);
  }
}
</style>
