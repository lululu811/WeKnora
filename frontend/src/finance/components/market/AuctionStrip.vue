<!--
  集合竞价（大盘工作台 · 大盘 tab，盘前信号）。

  收盘复盘回答"今天发生了什么"，这一块补上"**今天怎么开的**"。它是全市场
  唯一能比昨天更早拿到读数的时刻 —— 5471 只票在 9:15–9:25 各报一次价，
  开盘前十分钟高低开的家数、谁直接顶在涨停上就全知道了。

  两块内容，理由不同，分开展示：

    分布（全市场 5471 只）  「今天多少只高开」是统计，不设任何门槛也成立。
    异动榜（过市值门槛）    「哪几只异动」必须过门槛，否则排前面全是几亿市值
                           小盘票的量比倍数，那是流动性噪音不是资金信号。

  默认只显示分布与风向标，异动榜按需展开 —— 大盘 tab 本身已经很长，
  再加一张默认展开的表会把需要往下滚的长度又多推一屏。

  口径：`stage` 目前恒为 `final`（9:25 撮合完成的终态），库里的 `live`
  采集没有在跑，所以标题里写明是终态而不是"实时"。
-->
<template>
  <article v-if="data?.date" class="auc ledger-block">
    <header class="auc__head">
      <div class="auc__title-row">
        <span class="auc__title">{{ $t('marketDashboard.auction.title') }}</span>
        <span class="auc__caption">
          {{ $t('marketDashboard.auction.caption', { date: data.date }) }}
        </span>
      </div>
      <button v-if="data.movers?.length" type="button" class="auc__toggle"
        :aria-expanded="expanded" @click="expanded = !expanded">
        {{ expanded ? $t('common.collapse') : $t('marketDashboard.auction.expand') }}
      </button>
    </header>

    <!-- 分布：一条按家数分段的横条 + 数字。没报价单列，不并进任何一边 -->
    <div v-if="breadth" class="auc__breadth">
      <div class="auc__bar" :title="$t('marketDashboard.auction.barTitle')">
        <span class="auc__seg auc__seg--up" :style="{ flexGrow: breadth.up }" />
        <span class="auc__seg auc__seg--flat" :style="{ flexGrow: breadth.flat }" />
        <span class="auc__seg auc__seg--down" :style="{ flexGrow: breadth.down }" />
        <span class="auc__seg auc__seg--none" :style="{ flexGrow: breadth.no_quote }" />
      </div>
      <div class="auc__legend">
        <span class="auc__lg auc__lg--up">
          {{ $t('marketDashboard.auction.up', { n: breadth.up }) }}
        </span>
        <span class="auc__lg auc__lg--flat">
          {{ $t('marketDashboard.auction.flat', { n: breadth.flat }) }}
        </span>
        <span class="auc__lg auc__lg--down">
          {{ $t('marketDashboard.auction.down', { n: breadth.down }) }}
        </span>
        <span v-if="breadth.no_quote" class="auc__lg auc__lg--none">
          {{ $t('marketDashboard.auction.noQuote', { n: breadth.no_quote }) }}
        </span>
        <span class="auc__lg">
          {{ $t('marketDashboard.auction.limitOpen', { up: breadth.limit_up_open, down: breadth.limit_down_open }) }}
        </span>
      </div>
    </div>

    <!-- 短线风向标：带行业标签的少量代表，直接告诉你今天哪个方向被抢 -->
    <div v-if="data.benchmark?.length" class="auc__bench">
      <span class="auc__bench-k">{{ $t('marketDashboard.auction.benchmark') }}</span>
      <span v-for="b in data.benchmark" :key="b.thscode" class="auc__chip">
        <b class="auc__chip-name">{{ b.name }}</b>
        <span class="auc__chip-pct" :class="trendClass(b.auction_pct)">{{ fmtPct(b.auction_pct) }}</span>
        <em v-for="t in b.tags" :key="t" class="auc__tag">{{ t }}</em>
      </span>
    </div>

    <div v-if="expanded && data.movers?.length" class="auc__table">
      <div class="auc__tr auc__tr--head">
        <span>{{ $t('marketDashboard.auction.col.name') }}</span>
        <span class="is-num">{{ $t('marketDashboard.auction.col.open') }}</span>
        <span class="is-num">{{ $t('marketDashboard.auction.col.volRatio') }}</span>
        <span class="is-num">{{ $t('marketDashboard.auction.col.cap') }}</span>
      </div>
      <div v-for="m in data.movers" :key="m.thscode" class="auc__tr">
        <span class="auc__name">
          {{ m.name }}<span class="auc__code">{{ m.thscode }}</span>
        </span>
        <span class="is-num" :class="trendClass(m.auction_pct)">{{ fmtPct(m.auction_pct) }}</span>
        <span class="is-num">{{ m.volume_ratio === null ? '—' : `${m.volume_ratio.toFixed(1)}×` }}</span>
        <span class="is-num">{{ fmtCap(m.float_market_cap) }}</span>
      </div>
      <p class="auc__foot">
        {{ $t('marketDashboard.auction.foot', { cap: Math.round((data.min_float_cap ?? 0) / 1e8) }) }}
      </p>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { get } from '@/utils/request'
import { fmtPct, trendClass } from '@/finance/utils/format'

interface AuctionBreadth {
  no_quote: number
  up: number
  down: number
  flat: number
  limit_up_open: number
  limit_down_open: number
  total: number
}
interface AuctionResponse {
  ok: boolean
  date: string | null
  stage: string | null
  breadth: AuctionBreadth | null
  benchmark: Array<{ thscode: string; name: string; auction_pct: number | null; tags: string[] }>
  movers: Array<{
    thscode: string
    name: string
    auction_pct: number | null
    volume_ratio: number | null
    turnover_pct: number | null
    float_market_cap: number | null
  }>
  min_float_cap?: number
  unavailable: string[]
  reason?: string
}

const { t } = useI18n()
const data = ref<AuctionResponse | null>(null)
const expanded = ref(false)

onMounted(async () => {
  try {
    data.value = await get<AuctionResponse>('/api/market/auction', { params: { limit: 20 } })
  } catch {
    data.value = null
  }
})

const breadth = computed(() => data.value?.breadth ?? null)

/** 元 → 亿。null 显示「—」（没报出竞价价的票确实存在）。 */
function fmtCap(v: number | null): string {
  return v === null || v === undefined ? '—' : `${(v / 1e8).toFixed(0)}亿`
}
</script>

<style scoped lang="less">
/*
 * 墨账体例（achromatic-ledger）—— 规范见 src/finance/styles/ledger.less 文件头：
 *   1. 无框块：块与块之间只靠留白与标题层级分开，不画卡片；
 *   2. 图表用墨色：分布条是条形图，一律墨色，不按涨跌给整条染色；
 *   3. 浓淡表用墨阶：--ledger-ink-1…5。
 * 涨跌红绿是金融语义，只出现在数字上，保留。
 */
.auc {
  /* 块根：去掉卡片外壳（底色 / 描边 / 圆角 / 阴影）；块距由父级 grid 的 gap 给。 */
}

.is-up { color: var(--app-quote-up); }
.is-down { color: var(--app-quote-down); }
.is-flat { color: var(--td-text-color-placeholder); }

.auc__head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; margin-bottom: 10px;
}
.auc__title-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.auc__title { font-family: var(--app-font-display); font-size: var(--app-text-md); }
.auc__caption { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }

/* 「展开异动榜」是次级动作：墨账里用文字链接，不画按钮框，也不占品牌珊瑚。 */
.auc__toggle {
  border: 0;
  background: transparent;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
  padding: 2px 4px;
  cursor: pointer;

  &:hover {
    color: var(--td-text-color-primary);
    text-decoration: underline;
  }
}

/* ---- 分布条 ---- */
/* 条是图表：墨账里用墨色分段，不按涨跌给整条染色。
   浓淡只分三档 —— 浓＝有方向的报价（高开 / 低开）、淡＝平开、无底色＝没报价；
   方向交给下面那行数字（红 / 绿）讲。 */
.auc__bar { display: flex; height: 10px; border-radius: 2px; overflow: hidden; background: var(--ledger-ink-1); max-width: 720px; }
.auc__seg { display: block; height: 100%; }
/* 分布条是**分类分段**（高开 / 平开 / 低开），不是连续量 —— 段的位置就是标签，
   三段都变成灰阶就得分不清哪段是哪段。红绿在这里是 A 股语义，与紧邻的涨跌家数条
   （.md-breadth，同为分类分段）必须用同一套。 */
.auc__seg--up { background: var(--app-quote-up); }
.auc__seg--flat { background: var(--td-component-stroke); }
.auc__seg--down { background: var(--app-quote-down); }
/* 没报价：既不是涨也不是跌，用中性斜纹，不能并进任何一边。
   这是**图案**不是渐变填充（hatch 表示"这一段没有报价"），与"平盘"必须能区分。
   lint 的 gradient 规则认不出这个区别，所以在这一行显式豁免。 */
.auc__seg--none {
  background: repeating-linear-gradient(45deg, var(--ledger-ink-2) 0 3px, transparent 3px 6px); /* taste-skill-ignore: 斜纹=无报价的语义图案 */
}

/* 图例：方向由**点**讲，文字（含数字）一律墨色 —— 与同屏涨跌家数那条图例
   是同一套写法。原先整段染色，既和邻处不一致，染红的数字压在暖纸底上
   也只有 4.4:1。 */
.auc__legend { display: flex; gap: 14px; margin-top: 6px; flex-wrap: wrap; font-size: var(--app-text-xs); }
.auc__lg { color: var(--td-text-color-secondary); display: inline-flex; align-items: center; gap: 5px; }
.auc__lg--up, .auc__lg--down, .auc__lg--flat, .auc__lg--none { color: var(--td-text-color-secondary); }
.auc__lg--up::before, .auc__lg--down::before, .auc__lg--flat::before, .auc__lg--none::before {
  content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%; flex: none;
}
.auc__lg--up::before { background: var(--app-quote-up); }
.auc__lg--down::before { background: var(--app-quote-down); }
.auc__lg--flat::before { background: var(--td-text-color-disabled); }
.auc__lg--none::before { background: var(--ledger-ink-3); }

/* ---- 风向标 ---- */
/* 风向标是一排密集数字（涨停幅度等），保留白底：表面用色阶分开，不用线和阴影。
   红字压暖纸底只有 4.4:1，压白纸回到 4.6:1。 */
.auc__bench {
  display: flex; align-items: baseline; gap: 10px; margin-top: 10px; flex-wrap: wrap;
  background: var(--td-bg-color-container);
  border-radius: var(--app-radius-md);
  padding: 8px 14px 10px;
}
.auc__bench-k { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }
.auc__chip { display: inline-flex; align-items: baseline; gap: 6px; font-size: var(--app-text-sm); }
.auc__chip-name { color: var(--td-text-color-primary); }
.auc__chip-pct { font-family: var(--app-font-family-mono); font-size: var(--app-text-xs);; /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums，见 market-dashboard/HANDOFF.md） */ }
/* 行业标签是 chrome，不是数据：用中性底色 + 墨色字，不与涨跌红绿抢颜色。 */
.auc__tag {
  /* 11px 是可读下限：10px 的标签在暖纸底上既读不清也过不了对比度。 */
  font-style: normal; font-size: var(--app-text-xs);
  padding: 1px 5px; border-radius: 3px;
  background: var(--td-bg-color-secondarycontainer); color: var(--td-text-color-secondary);
}

/* ---- 异动榜 ---- */
.auc__table { margin-top: 12px; max-width: 640px; }
/* 行不是小卡片：去掉描边圆角，只留一条淡墨横线分栏。 */
.auc__tr {
  display: grid; grid-template-columns: minmax(0, 1fr) 84px 88px 84px;
  gap: 10px; align-items: baseline; padding: 6px 0;
  border-bottom: 1px solid var(--ledger-ink-2);
  font-size: var(--app-text-sm);
}
.auc__tr--head { font-size: var(--app-text-xs); color: var(--td-text-color-secondary); padding-bottom: 4px; }
.is-num { font-family: var(--app-font-family-mono); font-variant-numeric: tabular-nums; text-align: right;; /* taste-skill-ignore: 数字用等宽是本产品既定约定（--app-font-family-mono + tabular-nums，见 market-dashboard/HANDOFF.md） */ }
.auc__name { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.auc__code { font-family: var(--app-font-family-mono); font-size: var(--app-text-xs); color: var(--td-text-color-secondary); }
.auc__foot { margin: 8px 0 0; font-size: var(--app-text-xs); color: var(--td-text-color-placeholder); }
</style>
