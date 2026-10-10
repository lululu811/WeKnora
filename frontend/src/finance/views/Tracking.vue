<!--
  个股追踪（/platform/watchlist）—— 菜单合并后的唯一金融入口。

  2026-10-09 之前，金融相关的入口散在三处：侧边栏「个股追踪」「谁在动」两项，
  外加一个**不在菜单里**的 /dashboard 全屏大屏（返回按钮只在大屏内部）。
  收盘后想扫一遍大盘、看谁在动、再看自选股，要在三个入口之间来回切，
  而且没有任何一处能告诉你"刚才那份数据是什么时候的"。

  现在合成一页四个 tab：
    大盘     → MarketDashboard 的 embedded 形态（指数/情绪/龙虎榜/ETF 摘要）
    自选股   → Watchlist 本体（原来这一页的全部内容）
    谁在动   → WatchPulse 本体（原来那一个菜单项）
    权重 ETF → EtfWorkspace，池子扩到 宽基 8 + 行业 10，带分组筛选

  三条约定：

  1. **/dashboard 不删。** 大屏是给"一屏看完、不滚"设计的，与这一页的目标不同。
     页头保留「展开全屏」入口，进去仍是原来那个大屏。
  2. **tab 进地址参数。** `?tab=etf`。刷新、收藏、别人发链接都要能落到同一屏，
     否则合并的意义（一个入口）会在刷新后丢掉。
  3. **三个子页面都不重写。** 它们只多了一个 `embedded` 开关把页头让给外壳，
     自己的逻辑一行没改 —— 合并的是入口，不是把三段业务重新焊一遍。
-->
<template>
  <main class="trk-page">
    <header class="trk-head" style="--wails-draggable: drag">
      <div class="trk-head__row">
        <h2 class="trk-title" style="--wails-draggable: drag">
          <t-icon name="watchlist" size="22px" />
          {{ $t('menu.watchlist') }}
        </h2>

        <div class="trk-head__actions" style="--wails-draggable: no-drag">
          <!--
            「展开全屏」只在大盘 tab 有意义：另两个 tab 是列表页，没有大屏形态。
            放在页头而不是 tab 里，是因为它是"换一种尺寸看同一份数据"，
            不是"看另一块数据" —— 后者才归 tab。
          -->
          <a
            v-if="tab === 'market'"
            class="trk-expand"
            href="/dashboard"
            target="_blank"
            rel="noopener"
          >
            {{ $t('tracking.openFullscreen') }}
            <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <path d="M5 2H2v3M9 2h3v3M9 12h3V9M5 12H2V9" stroke="currentColor"
                stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </a>
        </div>
      </div>

      <nav class="trk-tabs" role="tablist" :aria-label="$t('tracking.tabsLabel')">
        <button
          v-for="t in TABS"
          :key="t.key"
          type="button"
          class="trk-tab"
          :class="{ 'is-active': tab === t.key }"
          role="tab"
          :aria-selected="tab === t.key"
          @click="setTab(t.key)"
        >
          <t-icon :name="t.icon" size="16px" />
          <span>{{ $t(t.labelKey) }}</span>
        </button>
      </nav>
    </header>

    <!--
      懒挂载，不是四个页面同时挂载。

      用 v-show 的话，`Watchlist` / `WatchPulse` 会在用户看「大盘」时照样发请求 ——
      自选股清单、事件流、排名，以及 K 线工作台的订阅。三个页面加起来的请求量不小，
      而其中两个的数据在这一屏根本看不见。

      KeepAlive 包住，切走再切回来时选中行、滚动位置、已加载的数据都还在，
      所以懒挂载的代价（重新挂载 → 重新取数）只发生在第一次进入某个 tab 时。
    -->
    <KeepAlive>
      <section v-if="tab === 'market'" class="trk-panel">
        <MarketDashboard embedded />
      </section>
      <section v-else-if="tab === 'watchlist'" class="trk-panel">
        <WatchlistView embedded />
      </section>
      <section v-else-if="tab === 'pulse'" class="trk-panel">
        <WatchPulseView embedded />
      </section>
      <section v-else class="trk-panel">
        <EtfWorkspace />
      </section>
    </KeepAlive>
  </main>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import MarketDashboard from '@/finance/views/MarketDashboard.vue'
import WatchlistView from '@/finance/views/Watchlist.vue'
import WatchPulseView from '@/finance/views/WatchPulse.vue'
import EtfWorkspace from '@/finance/components/etf/EtfWorkspace.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

type TabKey = 'market' | 'watchlist' | 'pulse' | 'etf'

/** 兜底用 market：合并后的默认落点就是"扫一眼大盘"。 */
const TAB_KEYS: TabKey[] = ['market', 'watchlist', 'pulse', 'etf']

function isTabKey(v: unknown): v is TabKey {
  return typeof v === 'string' && (TAB_KEYS as string[]).includes(v)
}

const tab = computed<TabKey>(() => {
  const q = route.query.tab
  return isTabKey(q) ? q : 'market'
})

function setTab(next: TabKey) {
  if (next === tab.value) return
  router.replace({ query: { ...route.query, tab: next } })
}

const TABS = [
  { key: 'market' as const, labelKey: 'tracking.tab.market', icon: 'dashboard' },
  { key: 'watchlist' as const, labelKey: 'tracking.tab.watchlist', icon: 'star' },
  { key: 'pulse' as const, labelKey: 'tracking.tab.pulse', icon: 'chart-bar' },
  { key: 'etf' as const, labelKey: 'tracking.tab.etf', icon: 'money' },
]
</script>

<style scoped lang="less">
.trk-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100%;
}

.trk-head {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.trk-head__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.trk-title {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--app-font-display);
  font-size: var(--app-text-2xl);
}

.trk-head__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.trk-expand {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-link);
  text-decoration: none;
  border-bottom: 1px solid color-mix(in srgb, var(--td-text-color-link) 36%, transparent);
}

/* ── tab 条 ── */

.trk-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.trk-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-sm);
  cursor: pointer;
}

.trk-tab.is-active {
  color: var(--td-text-color-primary);
  font-weight: 600;
  border-bottom-color: var(--td-brand-color);
}

.trk-tab__badge {
  padding: 0 6px;
  border-radius: var(--td-radius-round);
  background: var(--td-bg-color-component);
  font-size: var(--app-text-xs);
  font-weight: 400;
}

.trk-panel {
  min-width: 0;
  flex: 1 1 auto;
  min-height: 0;
}
</style>
