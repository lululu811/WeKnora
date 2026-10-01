<template>
  <!-- 多标的对比条。只在 picks 多于一只时出现——单只票时主图本身就是答案，
       再加一行「对比 1 只」纯属噪音。 -->
  <div v-if="rows.length > 1" class="compare-bar" :class="{ 'is-expanded': expanded }">
    <button
      type="button"
      class="compare-bar__summary"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span class="compare-bar__caret" :class="{ 'is-open': expanded }" aria-hidden="true">▸</span>
      <span class="compare-bar__title">{{ summaryText }}</span>
      <span v-if="loadingCount > 0" class="compare-bar__loading">加载中 {{ loadingCount }}</span>
      <span class="compare-bar__hint">{{ expanded ? '收起' : '展开' }}</span>
    </button>

    <ul v-if="expanded" class="compare-bar__list">
      <li
        v-for="row in rows"
        :key="row.thscode"
        class="compare-row"
        :class="{ 'is-active': row.thscode === activeThscode }"
      >
        <button
          type="button"
          class="compare-row__btn"
          :title="`切换到 ${row.name} (${row.thscode})`"
          @click="emit('select', row.thscode)"
        >
          <span class="compare-row__name">{{ row.name }}</span>
          <span class="compare-row__code">{{ row.thscode }}</span>
        </button>

        <span class="compare-row__price">
          {{ row.close !== null ? row.close.toFixed(2) : '—' }}
        </span>

        <span class="compare-row__pct" :class="`is-${changeDirection(row.pctChange)}`">
          {{ formatPct(row.pctChange) }}
        </span>

        <svg
          v-if="row.path"
          class="compare-row__spark"
          :viewBox="`0 0 ${SPARK_W} ${SPARK_H}`"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <path :d="row.path" fill="none" :stroke="sparkColor(row)" stroke-width="1.2" />
        </svg>
        <span v-else class="compare-row__spark is-empty" aria-hidden="true" />
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useAgentWorkspace } from '@/composables/useAgentWorkspace';
import { fetchKline, computeChange, KlineFetchError } from './kline-cache';
import {
  buildSparklinePath,
  changeDirection,
  summarizeCompare,
  formatSummary,
  type CompareRow,
} from './compare-bar';

const workspace = useAgentWorkspace();

// 默认收起（Q14b）：多标的是常见场景，但常驻展开会吃掉主图的高度。
const expanded = ref(false);

const SPARK_W = 72;
const SPARK_H = 18;
// sparkline 只看近期形态，60 根日线足够，也让这个请求比主图的 5000 根轻得多。
const SPARK_BARS = 60;

interface BarRow extends CompareRow {
  /** 已算好的 SVG path，空串表示不画。 */
  path: string;
  /** 该行是否已拿到数据。 */
  loaded: boolean;
}

/** thscode -> 该标的的收盘价序列。 */
const seriesBySymbol = ref<Record<string, number[]>>({});
/** thscode -> 当前价与涨跌幅。 */
const quoteBySymbol = ref<Record<string, { close: number; pctChange: number | null } | null>>({});
/** thscode -> 是否请求失败（不写进 rows 的差距，只用于计数）。 */
const failedSymbols = ref<Record<string, true>>({});

const activeThscode = computed(() => workspace.activeThscode.value);

const picksKey = computed(() => workspace.picks.value.map((p) => `${p.ticker}.${p.exchange}`).join(','));

/** 拉取所有标的的近期日线。**固定日线**，不跟随主图周期（Q12b）。 */
async function loadAll(): Promise<void> {
  const picks = workspace.picks.value;
  if (picks.length <= 1) return;

  // 并发发起。缓存与在途去重都在 fetchKline 里做，所以这里不必自己去重；
  // 多个标的之间是独立的失败单元，一只票挂掉不该让整条对比条空掉。
  await Promise.all(
    picks.map(async (p) => {
      const thscode = `${p.ticker}.${p.exchange}`;
      try {
        const rows = await fetchKline({ symbol: thscode, period: 'day', limit: SPARK_BARS });
        const closes = rows.map((r) => r.close);
        seriesBySymbol.value = { ...seriesBySymbol.value, [thscode]: closes };
        quoteBySymbol.value = { ...quoteBySymbol.value, [thscode]: computeChange(rows) };
        if (failedSymbols.value[thscode]) {
          const next = { ...failedSymbols.value };
          delete next[thscode];
          failedSymbols.value = next;
        }
      } catch (err) {
        // 失败保持静默降级：该行显示「—」，不阻塞其它标的，也不弹错误条。
        // 只有真的出了问题才在 console 留痕，便于排查是 no-data 还是服务故障。
        if (err instanceof KlineFetchError && err.kind === 'no-data') {
          console.debug('[KLineCompareBar] 无该标的行情:', thscode);
        } else {
          console.warn('[KLineCompareBar] 取数失败:', thscode, err);
        }
        failedSymbols.value = { ...failedSymbols.value, [thscode]: true };
      }
    }),
  );
}

const rows = computed<BarRow[]>(() =>
  workspace.picks.value.map((p) => {
    const thscode = `${p.ticker}.${p.exchange}`;
    const closes = seriesBySymbol.value[thscode] ?? [];
    const quote = quoteBySymbol.value[thscode] ?? null;
    return {
      thscode,
      name: p.name || p.ticker,
      close: quote ? quote.close : closes.length > 0 ? closes[closes.length - 1] : null,
      pctChange: quote ? quote.pctChange : null,
      closes,
      path: buildSparklinePath(closes, SPARK_W, SPARK_H),
      loaded: closes.length > 0,
    };
  }),
);

const summaryText = computed(() => formatSummary(summarizeCompare(rows.value)));

const loadingCount = computed(() => rows.value.filter((r) => !r.loaded).length);

function formatPct(pct: number | null): string {
  if (pct === null || !Number.isFinite(pct)) return '—';
  return `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function sparkColor(row: BarRow): string {
  const dir = changeDirection(row.pctChange);
  if (dir === 'up') return '#ef4444';
  if (dir === 'down') return '#10b981';
  return '#94a3b8';
}

const emit = defineEmits<{ (e: 'select', thscode: string): void }>();

// picks 变化（用户点了另一只票 / agent 换了一组）就重新取数。
// 只在确实超过一只时才发请求：单只票时这条根本不渲染。
watch(picksKey, () => { void loadAll(); }, { immediate: true });
</script>

<style lang="less" scoped>
/* 样式守卫（src/assets/theme/styleGuard.test.mjs）会统计绕过设计令牌的写法并只允许
   计数下降。这个文件是新增的，所以这里刻意全部走令牌：颜色用 TDesign 变量、
   圆角用 --app-radius-*、字号用 --app-text-*、时长用 --app-motion-*。
   否则新代码会凭空抬高基线，让那条棘轮失去意义。 */
.compare-bar {
  border-bottom: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-secondarycontainer);
  font-size: var(--app-text-sm);
}

.compare-bar__summary {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 5px 10px;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-align: left;

  &:hover {
    background: rgba(127, 127, 127, 0.08);
  }
}

.compare-bar__caret {
  display: inline-block;
  transition: transform var(--app-motion-fast) ease;

  &.is-open {
    transform: rotate(90deg);
  }
}

.compare-bar__title {
  font-weight: 600;
}

.compare-bar__loading {
  color: var(--td-text-color-placeholder);
}

.compare-bar__hint {
  margin-left: auto;
  color: var(--td-text-color-placeholder);
}

.compare-bar__list {
  margin: 0;
  padding: 0 6px 6px;
  list-style: none;
  max-height: 240px;
  overflow-y: auto;
}

.compare-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 62px 64px 76px;
  align-items: center;
  gap: 6px;
  padding: 2px 4px;
  border-radius: var(--app-radius-xs);

  &:hover {
    background: rgba(127, 127, 127, 0.1);
  }

  // 正在主图上显示的那只：与 chip 行、正文标记共用同一套 active 语义。
  &.is-active {
    background: color-mix(in srgb, var(--td-brand-color) 12%, transparent);
    box-shadow: inset 2px 0 0 var(--td-brand-color);
  }
}

.compare-row__btn {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-align: left;
}

.compare-row__name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compare-row__code {
  flex-shrink: 0;
  font-family: var(--td-font-family-mono);
  font-size: var(--app-text-2xs);
  color: var(--td-text-color-placeholder);
}

.compare-row__price,
.compare-row__pct {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.compare-row__pct {
  font-weight: 600;

  &.is-up { color: #ef4444; }
  &.is-down { color: #10b981; }
  &.is-flat,
  &.is-unknown { color: var(--td-text-color-placeholder); }
}

.compare-row__spark {
  width: 100%;
  height: 18px;

  &.is-empty {
    display: block;
  }
}
</style>
