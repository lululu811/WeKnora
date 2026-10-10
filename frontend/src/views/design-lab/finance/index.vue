<template>
  <div class="finlab" :data-variant="variant">
    <!-- ══════════ 方案说明条（对比页外壳，不带风格倾向） ══════════ -->
    <header class="finlab__bar">
      <div class="finlab__switch" role="radiogroup" aria-label="候选方案">
        <button v-for="v in VARIANTS" :key="v.id" type="button" class="finlab__switch-btn"
          :class="{ 'is-active': variant === v.id }" role="radio" :aria-checked="variant === v.id"
          @click="pick(v.id)">
          <span class="finlab__switch-name">{{ v.name }}</span>
          <span class="finlab__switch-stance">{{ v.stance }}</span>
        </button>
      </div>
      <div class="finlab__meta">
        <p class="finlab__task">{{ current.concept }}</p>
        <p class="finlab__trade">{{ current.trade }}</p>
      </div>
      <button type="button" class="finlab__reload" @click="reload">重新取数</button>
    </header>

    <div v-if="failed.length" class="finlab__alert">
      未取到：{{ failed.join(' / ') }} —— 缺失的格子留空，不用 0 顶替。
    </div>

    <div class="finlab__stage">
      <!-- ══════════ 合并后的侧边栏（方案差异：菜单里还剩什么） ══════════ -->
      <aside class="finlab__menu">
        <p class="finlab__menu-title">菜单（合并后）</p>
        <ul class="finlab__menu-list">
          <li v-for="m in MENU" :key="m.label" class="finlab__menu-item"
            :class="{ 'is-active': m.active }">
            <span class="finlab__menu-dot" />{{ m.label }}
          </li>
        </ul>
        <p class="finlab__menu-note">
          「谁在动」不再是独立菜单项，收进本页 tab。<br />
          /dashboard 全屏大屏入口保留，从「大盘」页头进入。
        </p>
      </aside>

      <!-- ══════════ 个股追踪页 ══════════ -->
      <main class="finlab__page">
        <header class="finlab__pagehead">
          <div class="finlab__title">
            <h1>个股追踪</h1>
            <span class="finlab__sub">{{ snapshot?.trade_date ?? '—' }}</span>
          </div>
          <router-link v-if="variant === 'a' || variant === 'b'" class="finlab__expand" to="/dashboard">
            展开全屏大屏 ↗
          </router-link>
        </header>

        <nav class="finlab__tabs" role="tablist">
          <button v-for="t in tabs" :key="t.key" type="button" class="finlab__tab"
            :class="{ 'is-active': tab === t.key }" role="tab" :aria-selected="tab === t.key"
            @click="setTab(t.key)">
            {{ t.label }}<span v-if="t.badge" class="finlab__tab-badge">{{ t.badge }}</span>
          </button>
          <router-link class="finlab__tab finlab__tab--link" to="/platform/watch-pulse">谁在动 ↗</router-link>
          <router-link class="finlab__tab finlab__tab--link" to="/platform/watchlist">自选股 ↗</router-link>
        </nav>

        <!-- ─────────── 大盘 tab ─────────── -->
        <section v-show="tab === 'market'" class="finlab__body">
          <p v-if="loading" class="finlab__loading">正在取数…</p>
          <!-- 指数条：三个方案完全一致，只抽出来一次 -->
          <div v-if="snapshot?.tickers?.length" class="finlab__tickers">
            <div v-for="t in snapshot.tickers" :key="t.thscode" class="finlab__ticker">
              <span class="finlab__ticker-name">{{ t.name }}</span>
              <span class="finlab__num">{{ fmtNum(t.last, 2) }}</span>
              <span class="finlab__chg" :class="trendClass(t.change_pct)">{{ fmtPct(t.change_pct) }}</span>
            </div>
          </div>

          <!-- ── 方案 A：全屏大屏整体缩编，指数卡 2×2，信息量最大 ── -->
          <template v-if="variant === 'a'">
            <div class="finlab__grid2">
              <article v-for="ix in snapshot?.indices ?? []" :key="ix.thscode" class="finlab__tile">
                <div class="finlab__tilehead">
                  <span class="finlab__tile-name">{{ ix.name }}</span>
                  <span class="finlab__tile-code">{{ ix.thscode }}</span>
                </div>
                <div class="finlab__tile-right">
                  <span class="finlab__num">{{ fmtNum(ix.last, 2) }}</span>
                  <span class="finlab__chg" :class="trendClass(ix.change_pct)">{{ fmtPct(ix.change_pct) }}</span>
                </div>
                <svg class="finlab__spark" viewBox="0 0 220 44" preserveAspectRatio="none">
                  <polyline :points="sparkPoints(ix.series, 220, 44)" fill="none"
                    :stroke="(ix.change_pct ?? 0) >= 0 ? 'var(--md-up)' : 'var(--md-down)'" stroke-width="1.4" />
                </svg>
                <div class="finlab__range">
                  <span>低 {{ fmtNum(ix.low, 2) }}</span>
                  <span>高 {{ fmtNum(ix.high, 2) }}</span>
                </div>
              </article>
            </div>

            <article v-if="sentiment" class="finlab__tile finlab__sentiment">
              <div class="finlab__tilehead"><span class="finlab__tile-name">市场情绪</span>
                <span class="finlab__tile-code">涨跌停池 · 全市场</span>
              </div>
              <div class="finlab__stats">
                <div><span class="finlab__stat-k">涨停</span>
                  <span class="finlab__num is-up">{{ sentiment.limit_up ?? '—' }}</span></div>
                <div><span class="finlab__stat-k">跌停</span>
                  <span class="finlab__num is-down">{{ sentiment.limit_down ?? '—' }}</span></div>
                <div><span class="finlab__stat-k">炸板</span>
                  <span class="finlab__num">{{ sentiment.broken ?? '—' }}</span></div>
                <div><span class="finlab__stat-k">最高连板</span>
                  <span class="finlab__num">{{ sentiment.max_streak ?? '—' }} 板</span></div>
              </div>
              <div class="finlab__trend">
                <div v-for="(p, pi) in sentiment.limit_up_trend" :key="p.trade_date ?? `lu-${pi}`"
                  class="finlab__trend-col">
                  <span class="finlab__trend-v">{{ p.count }}</span>
                  <span class="finlab__trend-bar"
                    :style="{ height: trendBarHeight(p.count) }" />
                </div>
              </div>
            </article>

            <!-- 龙虎榜 50 条，双列 -->
            <article class="finlab__tile">
              <div class="finlab__tilehead">
                <span class="finlab__tile-name">龙虎榜 净买入</span>
                <span class="finlab__tile-code">全市场 · {{ dragonDate ?? '—' }} · 共 {{ dragon.length }} 条 · 双列</span>
              </div>
              <div class="finlab__dt2">
                <div v-for="(r, i) in dragon" :key="r.thscode" class="finlab__dt-row">
                  <span class="finlab__dt-rank">{{ i + 1 }}</span>
                  <span class="finlab__dt-name">{{ r.name }}</span>
                  <span class="finlab__dt-net" :class="trendClass(r.net_value)">{{ fmtMoney(r.net_value) }}</span>
                  <span class="finlab__dt-org">机构 {{ fmtMoney(r.org_net_value) }}</span>
                </div>
              </div>
            </article>

            <EtfBlock :items="etfItems" />
          </template>

          <!-- ── 方案 B：只留三块，一屏看完，龙虎榜与 ETF 全表收进抽屉 ── -->
          <template v-else-if="variant === 'b'">
            <article v-if="sentiment" class="finlab__tile finlab__sentiment">
              <div class="finlab__tilehead"><span class="finlab__tile-name">市场情绪</span>
                <span class="finlab__tile-code">涨跌停池 · 全市场</span>
              </div>
              <div class="finlab__stats">
                <div><span class="finlab__stat-k">涨停</span>
                  <span class="finlab__num is-up">{{ sentiment.limit_up ?? '—' }}</span></div>
                <div><span class="finlab__stat-k">跌停</span>
                  <span class="finlab__num is-down">{{ sentiment.limit_down ?? '—' }}</span></div>
                <div><span class="finlab__stat-k">炸板</span>
                  <span class="finlab__num">{{ sentiment.broken ?? '—' }}</span></div>
                <div><span class="finlab__stat-k">最高连板</span>
                  <span class="finlab__num">{{ sentiment.max_streak ?? '—' }} 板</span></div>
              </div>
              <div class="finlab__breadth">
                <span class="is-up">上涨 {{ sentiment.breadth.up ?? '—' }}</span>
                <span>平盘 {{ sentiment.breadth.flat ?? '—' }}</span>
                <span class="is-down">下跌 {{ sentiment.breadth.down ?? '—' }}</span>
              </div>
            </article>

            <!-- 一行信号速览：三个方案的产出物都是「今天该看什么」，这里最接近它 -->
            <div class="finlab__signalbar">
              <div class="finlab__signal">
                <p class="finlab__signal-k">龙虎榜净买前三</p>
                <ul class="finlab__signal-list">
                  <li v-for="(r, i) in dragonTop" :key="r.thscode">
                    <span class="finlab__dt-rank">{{ i + 1 }}</span>{{ r.name }}
                    <span class="finlab__dt-net" :class="trendClass(r.net_value)">{{ fmtMoney(r.net_value) }}</span>
                  </li>
                </ul>
                <button type="button" class="finlab__drawer-btn" @click="drawer = 'dragon'">
                  看全部 {{ dragon.length }} 条
                </button>
              </div>
              <div class="finlab__signal">
                <p class="finlab__signal-k">权重 ETF 异动</p>
                <p class="finlab__signal-num">
                  <span class="is-up">{{ etfSignalCount }}</span> / {{ etfItems.length }} 只异动
                </p>
                <p class="finlab__signal-k2">份额截至 {{ shareDate ?? '—' }}（季频）</p>
                <button type="button" class="finlab__drawer-btn" @click="drawer = 'etf'">
                  看全表
                </button>
              </div>
            </div>
          </template>

          <!-- ── 方案 C：大盘 tab 只留指数 + 情绪，龙虎榜收成一行摘要，ETF 提到一级 tab ── -->
          <template v-else>
            <div class="finlab__grid2">
              <article v-for="ix in snapshot?.indices ?? []" :key="ix.thscode" class="finlab__tile">
                <div class="finlab__tilehead">
                  <span class="finlab__tile-name">{{ ix.name }}</span>
                  <span class="finlab__tile-code">{{ ix.thscode }}</span>
                </div>
                <div class="finlab__tile-right">
                  <span class="finlab__num">{{ fmtNum(ix.last, 2) }}</span>
                  <span class="finlab__chg" :class="trendClass(ix.change_pct)">{{ fmtPct(ix.change_pct) }}</span>
                </div>
                <svg class="finlab__spark" viewBox="0 0 220 44" preserveAspectRatio="none">
                  <polyline :points="sparkPoints(ix.series, 220, 44)" fill="none"
                    :stroke="(ix.change_pct ?? 0) >= 0 ? 'var(--md-up)' : 'var(--md-down)'" stroke-width="1.4" />
                </svg>
              </article>
            </div>

            <article v-if="sentiment" class="finlab__tile finlab__sentiment">
              <div class="finlab__tilehead"><span class="finlab__tile-name">市场情绪</span>
                <span class="finlab__tile-code">涨跌停池 · 全市场</span>
              </div>
              <div class="finlab__stats">
                <div><span class="finlab__stat-k">涨停</span>
                  <span class="finlab__num is-up">{{ sentiment.limit_up ?? '—' }}</span></div>
                <div><span class="finlab__stat-k">跌停</span>
                  <span class="finlab__num is-down">{{ sentiment.limit_down ?? '—' }}</span></div>
                <div><span class="finlab__stat-k">炸板</span>
                  <span class="finlab__num">{{ sentiment.broken ?? '—' }}</span></div>
                <div><span class="finlab__stat-k">最高连板</span>
                  <span class="finlab__num">{{ sentiment.max_streak ?? '—' }} 板</span></div>
              </div>
            </article>

            <div class="finlab__signalbar finlab__signalbar--one">
              <div class="finlab__signal">
                <p class="finlab__signal-k">龙虎榜净买前三（共 {{ dragon.length }} 条）</p>
                <ul class="finlab__signal-list">
                  <li v-for="(r, i) in dragonTop" :key="r.thscode">
                    <span class="finlab__dt-rank">{{ i + 1 }}</span>{{ r.name }}
                    <span class="finlab__dt-net" :class="trendClass(r.net_value)">{{ fmtMoney(r.net_value) }}</span>
                  </li>
                </ul>
              </div>
              <p class="finlab__signal-hint">ETF 已提为一级 tab →</p>
            </div>
          </template>
        </section>

        <!-- ─────────── 权重 ETF tab ─────────── -->
        <section v-show="tab === 'etf'" class="finlab__body">
          <!-- 方案 C 独有：系统先给一句结论，用户只处理例外 -->
          <div v-if="variant === 'c'" class="finlab__verdict">
            <p class="finlab__verdict-k">本期结论</p>
            <p class="finlab__verdict-text">{{ etfVerdict }}</p>
          </div>

          <div class="finlab__filterbar">
            <div class="finlab__seg" role="radiogroup" aria-label="ETF 分类">
              <button v-for="g in ETF_GROUPS" :key="g.key" type="button" class="finlab__seg-btn"
                :class="{ 'is-active': etfGroup === g.key }" role="radio"
                :aria-checked="etfGroup === g.key" :disabled="g.empty" @click="etfGroup = g.key">
                {{ g.label }}<span class="finlab__seg-n">{{ g.count }}</span>
              </button>
            </div>
            <p class="finlab__filter-note">份额截至 {{ shareDate ?? '—' }} · 季频口径</p>
          </div>

          <EtfBlock :items="etfItems" />
        </section>
      </main>
    </div>

    <!-- ══════════ 方案 B 的下钻抽屉 ══════════ -->
    <div v-if="drawer" class="finlab__drawer" @click.self="drawer = null">
      <div class="finlab__drawer-panel">
        <header class="finlab__drawer-head">
          <h2>{{ drawer === 'dragon' ? `龙虎榜 净买入 · 共 ${dragon.length} 条` : '权重 ETF · 全表' }}</h2>
          <button type="button" class="finlab__drawer-close" @click="drawer = null">✕</button>
        </header>
        <div v-if="drawer === 'dragon'" class="finlab__dt1">
          <div v-for="(r, i) in dragon" :key="r.thscode" class="finlab__dt-row">
            <span class="finlab__dt-rank">{{ i + 1 }}</span>
            <span class="finlab__dt-name">{{ r.name }}</span>
            <span class="finlab__dt-net" :class="trendClass(r.net_value)">{{ fmtMoney(r.net_value) }}</span>
            <span class="finlab__dt-org">机构 {{ fmtMoney(r.org_net_value) }}</span>
          </div>
        </div>
        <EtfBlock v-else :items="etfItems" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * design-lab / finance：菜单合并方案对比页。
 *
 * 三个候选**共享同一份数据、同一个页面骨架**，只有"内容出现在哪里、
 * 要几步、谁在做事"不同 —— 这是 oil-ui-pro 对存量项目改流程的要求：
 * 视觉全部沿用现有暖米色纸张体系，方案之间不比配色。
 *
 * 已锁定的前提（grill 已确认，这里不再作为变量）：
 *   · 菜单合并后只剩「个股追踪」；「谁在动」收进本页 tab。
 *   · /dashboard 全屏大屏保留，不删。
 *   · 龙虎榜按 50 条请求（对比现状的 5 条）。
 *   · ETF 增加分类筛选（现状只有 8 只宽基，行业池尚未扩）。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  useMergeLab, fmtMoney, fmtPct, fmtNum, trendClass, latestShareDate,
  type VariantId,
} from './useMergeLab'
import EtfBlock from './EtfBlock.vue'
import type { EtfFlowItem } from '@/finance/api/pulse'

// ─────────────────── 候选方案定义 ───────────────────
const VARIANTS: Array<{ id: VariantId; name: string; stance: string; concept: string; trade: string }> = [
  {
    id: 'a',
    name: 'A · 缩编全景',
    stance: '专用',
    concept: '全屏大屏整体缩进侧边栏壳，一次给全；代价是大盘 tab 要往下滚。',
    trade: '拿到全量，但「扫一眼」要滚一屏。',
  },
  {
    id: 'b',
    name: 'B · 三块速览',
    stance: '顺手',
    concept: '大盘 tab 只留指数条、情绪、一行信号速览；龙虎榜 50 条与 ETF 全表收进抽屉。',
    trade: '一屏看完；看全量多点一次。',
  },
  {
    id: 'c',
    name: 'C · ETF 提级',
    stance: '专用 + 自动',
    concept: 'ETF 提到一级 tab 并先给结论，大盘 tab 退成指数 + 情绪 + 龙虎榜一行摘要。',
    trade: '结论先给，少读；大盘 tab 变薄。',
  },
]

/** 合并后的侧边栏。三个方案在这一点上相同 —— 差异不在菜单，在页内。 */
const MENU = [
  { label: '新对话', active: false },
  { label: '知识库', active: false },
  { label: '公众号', active: false },
  { label: '产物', active: false },
  { label: '智能体', active: false },
  { label: '工具箱', active: false },
  { label: '个股追踪', active: true },
  { label: '共享空间', active: false },
  { label: '系统设置', active: false },
]

const route = useRoute()
const router = useRouter()
const variant = ref<VariantId>((route.query.variant as VariantId) || 'a')
watch(() => route.query.variant, (v) => { if (v === 'a' || v === 'b' || v === 'c') variant.value = v })
function pick(id: VariantId) {
  variant.value = id
  router.replace({ query: { ...route.query, variant: id } })
}

const current = computed(() => VARIANTS.find((v) => v.id === variant.value)!)

// tab 也进地址参数：对比页要能直接指向某个候选的某一屏（?variant=c&tab=etf），
// 否则每个候选只能停在默认的大盘 tab，ETF 那半屏没法单独比较。
const tab = ref<'market' | 'etf'>(route.query.tab === 'etf' ? 'etf' : 'market')
watch(() => route.query.tab, (v) => { tab.value = v === 'etf' ? 'etf' : 'market' })
function setTab(next: 'market' | 'etf') {
  tab.value = next
  router.replace({ query: { ...route.query, tab: next === 'market' ? undefined : next } })
}
const drawer = ref<null | 'dragon' | 'etf'>(null)
const etfGroup = ref<'broad' | 'sector'>('broad')

const tabs = computed(() => {
  if (variant.value === 'c') {
    return [
      { key: 'market' as const, label: '大盘', badge: '' },
      { key: 'etf' as const, label: '权重 ETF', badge: `${etfItems.value.length}` },
    ]
  }
  return [
    { key: 'market' as const, label: '大盘', badge: '' },
    { key: 'etf' as const, label: '权重 ETF', badge: `${etfItems.value.length}` },
  ]
})

const { snapshot, dragon, dragonDate, flow, loading, failed, reload } = useMergeLab()

const sentiment = computed(() => snapshot.value?.sentiment ?? null)
const etfItems = computed<EtfFlowItem[]>(() => flow.value?.items ?? [])
const shareDate = computed(() => latestShareDate(etfItems.value))
const dragonTop = computed(() => dragon.value.slice(0, 3))
const etfSignalCount = computed(() => etfItems.value.filter((i) => i.signal).length)

/**
 * 行业池当前为空 —— 这是真状态，不是占位。后端 etf_pool.yaml 只有 8 只宽基，
 * 扩池没做之前「行业」这一档就是 0，按钮置灰。方案对比必须把数据的这个洞
 * 显示出来，否则挑完方案才发现池子是空的。
 */
const ETF_GROUPS = computed(() => [
  { key: 'broad' as const, label: '宽基', count: etfItems.value.length, empty: false },
  { key: 'sector' as const, label: '行业', count: 0, empty: true },
])

const etfVerdict = computed(() => {
  const items = etfItems.value
  if (!items.length) return '池内无数据。'
  const withShare = items.filter((i) => i.share_change_pct !== null && i.share_change_pct !== undefined)
  if (!withShare.length) return '池内尚无上一观测点，本期无法判断资金方向。'
  const inflow = withShare.filter((i) => (i.share_change_pct as number) > 0)
  const top = [...withShare].sort((a, b) => (b.share_change_pct as number) - (a.share_change_pct as number))[0]
  const signalled = items.filter((i) => i.signal)
  return `${withShare.length} 只有份额观测点，其中 ${inflow.length} 只份额增加；`
    + `变动最大的是${top.name}（${fmtPct(top.share_change_pct)}）。`
    + (signalled.length ? `本期 ${signalled.length} 只触发异动标记。` : '本期无异动标记。')
})

/** 近 60 日收盘序列 → SVG polyline 点串。跳过 null 点（不用 0 顶替断点）。 */
function sparkPoints(series: Array<{ close: number | null }> | undefined, w: number, hgt: number): string {
  if (!series?.length) return ''
  const vals = series.map((p) => p.close).filter((c): c is number => c !== null && c !== undefined)
  if (vals.length < 2) return ''
  const min = Math.min(...vals); const max = Math.max(...vals)
  const span = max - min || 1
  const step = w / Math.max(1, vals.length - 1)
  return vals
    .map((v, i) => `${(i * step).toFixed(1)},${(hgt - ((v - min) / span) * (hgt - 4) - 2).toFixed(1)}`)
    .join(' ')
}

function trendBarHeight(count: number): string {
  const trend = sentiment.value?.limit_up_trend ?? []
  const max = Math.max(1, ...trend.map((p) => p.count))
  return `${Math.max(2, (count / max) * 100)}%`
}
</script>

<style scoped>
/* 视觉全部沿用大盘预览的暖米色纸张体系（MarketDashboard.vue:736 起），
   不新造配色：--md-up 红 / --md-down 绿随深色模式切换。 */
.finlab {
  --md-up: #dc2626;
  --md-down: #047857;
  min-height: 100%;
  background: #faf7f2;
  color: #2c2622;
  font-family: var(--app-font-family-base, system-ui, sans-serif);
  padding: 16px 20px 40px;
}
.is-up { color: var(--md-up); }
.is-down { color: var(--md-down); }
.is-flat { color: #8a8078; }
.finlab__num { font-family: var(--app-font-family-mono, ui-monospace, monospace); font-variant-numeric: tabular-nums; }

/* ── 说明条 ── */
.finlab__bar { display: flex; gap: 20px; align-items: flex-start; justify-content: space-between;
  background: #fffdf9; border: 1px solid #e8ded0; border-radius: 10px; padding: 12px 16px; margin-bottom: 14px; }
.finlab__switch { display: flex; gap: 8px; flex: 0 0 auto; }
.finlab__switch-btn { display: flex; flex-direction: column; gap: 2px; align-items: flex-start;
  padding: 7px 12px; border-radius: 7px; border: 1px solid #e0d5c4; background: #fdfaf5;
  cursor: pointer; color: #6b6058; }
.finlab__switch-btn.is-active { background: #2c2622; border-color: #2c2622; color: #fdfaf5; }
.finlab__switch-name { font-size: 13px; font-weight: 600; }
.finlab__switch-stance { font-size: 11px; opacity: .72; }
.finlab__meta { flex: 1 1 auto; }
.finlab__task { margin: 0; font-size: 13px; font-weight: 600; color: #2c2622; }
.finlab__trade { margin: 3px 0 0; font-size: 12px; color: #8a8078; }
.finlab__reload { border: 1px solid #e0d5c4; background: #fdfaf5; border-radius: 6px;
  padding: 6px 12px; cursor: pointer; font-size: 12px; color: #6b6058; }
.finlab__alert { background: #fff4e5; border: 1px solid #f0d9b5; color: #8a5a12;
  padding: 8px 14px; border-radius: 8px; font-size: 12px; margin-bottom: 12px; }

/* ── 舞台：菜单 + 页面 ── */
.finlab__stage { display: grid; grid-template-columns: 176px 1fr; gap: 16px; align-items: start; }
.finlab__menu { background: #fffdf9; border: 1px solid #e8ded0; border-radius: 10px; padding: 12px; }
.finlab__menu-title { margin: 0 0 8px; font-size: 11px; color: #a3988d; letter-spacing: .04em; }
.finlab__menu-list { list-style: none; margin: 0; padding: 0; }
.finlab__menu-item { display: flex; align-items: center; gap: 8px; padding: 7px 9px;
  border-radius: 6px; font-size: 13px; color: #6b6058; }
.finlab__menu-item.is-active { background: #f2e9dc; color: #2c2622; font-weight: 600; }
.finlab__menu-dot { width: 5px; height: 5px; border-radius: 50%; background: #cfc3b2; }
.finlab__menu-item.is-active .finlab__menu-dot { background: #2c2622; }
.finlab__menu-note { margin: 12px 0 0; font-size: 11px; line-height: 1.6; color: #a3988d; }

/* ── 页头与 tab ── */
.finlab__page { background: #fffdf9; border: 1px solid #e8ded0; border-radius: 10px; padding: 16px 18px 20px; }
.finlab__pagehead { display: flex; align-items: center; justify-content: space-between; }
.finlab__title { display: flex; align-items: baseline; gap: 10px; }
.finlab__title h1 { margin: 0; font-family: var(--app-font-display, system-ui); font-size: 19px; }
.finlab__sub { font-size: 12px; color: #a3988d; }
.finlab__expand { font-size: 12px; color: #8a6d3b; text-decoration: none; border-bottom: 1px dashed #c9b58a; }
.finlab__tabs { display: flex; gap: 4px; margin: 12px 0 16px; border-bottom: 1px solid #eee3d4; }
.finlab__tab { border: 0; background: none; padding: 8px 12px; font-size: 13px; color: #8a8078;
  cursor: pointer; border-bottom: 2px solid transparent; text-decoration: none; }
.finlab__tab.is-active { color: #2c2622; font-weight: 600; border-bottom-color: #2c2622; }
.finlab__tab--link { color: #a3988d; font-size: 12px; }
.finlab__tab-badge { display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 8px;
  background: #f2e9dc; font-size: 11px; font-weight: 400; color: #6b6058; }

/* ── 内容块 ── */
.finlab__body { display: flex; flex-direction: column; gap: 14px; }
.finlab__tickers { display: flex; gap: 26px; padding-bottom: 12px; border-bottom: 1px solid #f0e7da;
  flex-wrap: wrap; }
.finlab__ticker { display: flex; align-items: baseline; gap: 8px; }
.finlab__ticker-name { font-size: 12px; color: #8a8078; }
.finlab__num { font-size: 14px; }
.finlab__chg { font-size: 12px; font-family: var(--app-font-family-mono, ui-monospace, monospace); }
.finlab__grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.finlab__tile { background: #fdfaf5; border: 1px solid #eee3d4; border-radius: 9px; padding: 12px 14px; }
.finlab__tilehead { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
.finlab__tile-name { font-family: var(--app-font-display, system-ui); font-size: 14px; }
.finlab__tile-code { font-size: 11px; color: #a3988d; }
.finlab__tile-right { display: flex; align-items: baseline; gap: 10px; justify-content: flex-end;
  font-family: var(--app-font-display, system-ui); }
.finlab__tile-right .finlab__num { font-size: 20px; }
.finlab__spark { width: 100%; height: 44px; margin-top: 6px; display: block; }
.finlab__range { display: flex; justify-content: space-between; font-size: 11px; color: #a3988d; }

.finlab__stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.finlab__stat-k { display: block; font-size: 11px; color: #a3988d; margin-bottom: 2px; }
.finlab__stats .finlab__num { font-size: 22px; }
.finlab__breadth { display: flex; gap: 16px; margin-top: 10px; font-size: 12px; }
.finlab__trend { display: flex; align-items: flex-end; gap: 6px; height: 56px; margin-top: 10px; }
.finlab__trend-col { display: flex; flex-direction: column; justify-content: flex-end; align-items: center;
  height: 100%; flex: 1; gap: 3px; }
.finlab__trend-v { font-size: 10px; color: #a3988d; }
.finlab__trend-bar { width: 100%; background: color-mix(in srgb, var(--md-up) 42%, transparent); border-radius: 3px 3px 0 0; }

/* ── 龙虎榜 ── */
.finlab__dt1 { max-height: 60vh; overflow-y: auto; }
.finlab__dt2 { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 20px; max-height: 46vh; overflow-y: auto; }
.finlab__dt-row { display: grid; grid-template-columns: 22px 1fr auto auto; gap: 8px;
  align-items: baseline; padding: 5px 0; border-bottom: 1px dashed #f0e7da; font-size: 12px; }
.finlab__dt-rank { color: #b3a795; font-family: var(--app-font-family-mono, ui-monospace, monospace); }
.finlab__dt-name { color: #2c2622; }
.finlab__dt-net { font-family: var(--app-font-family-mono, ui-monospace, monospace); }
.finlab__dt-org { font-size: 11px; color: #a3988d; }

/* ── ETF ── */
.finlab__etf-head, .finlab__etf-row { display: grid;
  grid-template-columns: 1.6fr .8fr 1fr .6fr .5fr; gap: 8px; align-items: baseline; }
.finlab__etf-head { font-size: 11px; color: #a3988d; padding-bottom: 6px; border-bottom: 1px solid #f0e7da; }
.finlab__etf-row { padding: 7px 0; border-bottom: 1px dashed #f0e7da; font-size: 12px; }
.finlab__etf-name { display: flex; align-items: center; gap: 6px; }
.finlab__dot { width: 5px; height: 5px; border-radius: 50%; background: var(--md-up); display: inline-block; }
.finlab__etf-sig { font-size: 11px; color: #a3988d; }
.finlab__filterbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.finlab__seg { display: flex; gap: 4px; }
.finlab__seg-btn { border: 1px solid #e0d5c4; background: #fdfaf5; border-radius: 6px;
  padding: 5px 12px; font-size: 12px; color: #6b6058; cursor: pointer; }
.finlab__seg-btn.is-active { background: #2c2622; border-color: #2c2622; color: #fdfaf5; }
.finlab__seg-btn:disabled { opacity: .45; cursor: not-allowed; }
.finlab__seg-n { margin-left: 6px; font-size: 11px; opacity: .7; }
.finlab__filter-note { margin: 0; font-size: 11px; color: #a3988d; }

/* ── 方案 B 的信号速览行 ── */
.finlab__signalbar { display: grid; grid-template-columns: 1.6fr 1fr; gap: 12px; }
.finlab__signalbar--one { grid-template-columns: 1.6fr 1fr; }
.finlab__signal { background: #fdfaf5; border: 1px solid #eee3d4; border-radius: 9px; padding: 12px 14px; }
.finlab__signal-k { margin: 0 0 6px; font-size: 11px; color: #a3988d; }
.finlab__signal-k2 { margin: 2px 0 0; font-size: 11px; color: #a3988d; }
.finlab__signal-num { margin: 0; font-size: 24px; font-family: var(--app-font-display, system-ui); }
.finlab__signal-list { list-style: none; margin: 0 0 8px; padding: 0; }
.finlab__signal-list li { display: grid; grid-template-columns: 22px 1fr auto; gap: 8px;
  padding: 4px 0; font-size: 13px; }
.finlab__signal-hint { align-self: center; font-size: 12px; color: #8a6d3b; }
.finlab__drawer-btn { border: 1px solid #e0d5c4; background: #fffdf9; border-radius: 6px;
  padding: 5px 12px; font-size: 12px; color: #6b6058; cursor: pointer; }
.finlab__verdict { background: #f6f1e6; border-left: 3px solid #c9b58a; border-radius: 0 8px 8px 0;
  padding: 11px 14px; margin-bottom: 12px; }
.finlab__verdict-k { margin: 0 0 4px; font-size: 11px; color: #a3988d; }
.finlab__verdict-text { margin: 0; font-size: 13px; line-height: 1.6; }

/* ── 抽屉 ── */
.finlab__drawer { position: fixed; inset: 0; background: rgba(44, 38, 34, .28); z-index: 90;
  display: flex; justify-content: flex-end; }
.finlab__drawer-panel { width: min(620px, 90vw); background: #fffdf9; height: 100%; overflow-y: auto;
  padding: 18px 22px; box-shadow: -8px 0 24px rgba(44, 38, 34, .12); }
.finlab__drawer-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.finlab__drawer-head h2 { margin: 0; font-size: 15px; font-family: var(--app-font-display, system-ui); }
.finlab__drawer-close { border: 0; background: none; font-size: 16px; cursor: pointer; color: #8a8078; }
</style>
