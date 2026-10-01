<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="stock-citation-float"
      :class="{ 'placement-below': isPlacementBelow }"
      :style="{ top: `${top}px`, left: `${computedLeft}px` }"
      @mouseenter="$emit('enter')"
      @mouseleave="$emit('leave')"
    >
      <!-- 头部：代码、名称、最新价与涨跌幅 -->
      <div class="stock-float__header">
        <div class="stock-float__title-row">
          <span class="stock-float__name">{{ stockName }}</span>
          <span class="stock-float__code">{{ thscode }}</span>
        </div>
        <div v-if="quote" class="stock-float__quote-row">
          <span
            class="stock-float__price"
            :class="quote.pctChange == null ? '' : quote.pctChange >= 0 ? 'is-up' : 'is-down'"
          >
            {{ quote.close.toFixed(2) }}
          </span>
          <!-- pctChange 为 null 表示只有一根 K 线、算不出涨跌幅。
               此时**不显示任何百分比**：旧实现会兜底成 0，又因为 0 >= 0 套上红色，
               界面呈现为「+0.00%」——一个看起来很确定、实则"没有数据"的数字。 -->
          <span
            v-if="quote.pctChange != null"
            class="stock-float__change"
            :class="quote.pctChange >= 0 ? 'is-up' : 'is-down'"
          >
            {{ quote.pctChange >= 0 ? '+' : '' }}{{ quote.pctChange.toFixed(2) }}%
          </span>
          <span v-else class="stock-float__change is-unknown">涨跌幅无数据</span>
        </div>
      </div>

      <!-- 评分卡片：五分制战法持股星级与标签 -->
      <div v-if="loading" class="stock-float__loading">
        <div class="stock-float__spinner" />
        <span>正在分析战法指标与量化结构...</span>
      </div>

      <!-- 查不到 / 数据不足：显式说明，绝不渲染空白卡片 -->
      <div v-else-if="loadError" class="stock-float__notice is-error">
        {{ loadError }}
      </div>

      <div v-else-if="insufficient" class="stock-float__notice is-warn">
        <p class="notice__title">数据不足，暂不给战法评级</p>
        <p v-if="scoreResultOrNull" class="notice__desc">{{ scoreResultOrNull }}</p>
      </div>

      <template v-else-if="scoreResult">
        <div class="stock-float__score-card" :style="{ borderColor: scoreResult.themeColor }">
          <div class="score-card__stars">
            <span
              v-for="s in 5"
              :key="s"
              class="star-item"
              :class="{ 'is-active': s <= scoreResult.score }"
              :style="{ color: s <= scoreResult.score ? scoreResult.themeColor : '#cbd5e1' }"
            >★</span>
          </div>
          <span
            class="score-card__badge"
            :style="{ backgroundColor: `${scoreResult.themeColor}22`, color: scoreResult.themeColor }"
          >
            {{ scoreResult.ratingText }}
          </span>
        </div>

        <!-- 估值：PE / PB / PS 三个口径。任一为 null 单独显示「—」，
             不用 0 顶替 —— 亏损股的 PE 为负、破净股的 PB 为 0，
             拿 0 填进去会让「算不出来」和「真的等于零」长得一样。 -->
        <div v-if="valuation" class="stock-float__valuation">
          <span class="valuation__label">估值</span>
          <span class="valuation__item">
            <span class="valuation__key">PE<small>TTM</small></span>
            <span class="valuation__num">{{ fmtRatio(valuation.peTtm, '亏损') }}</span>
          </span>
          <span class="valuation__item">
            <span class="valuation__key">PB<small>MRQ</small></span>
            <span class="valuation__num">{{ fmtRatio(valuation.pbMrq) }}</span>
          </span>
          <span class="valuation__item">
            <span class="valuation__key">PS<small>TTM</small></span>
            <span class="valuation__num">{{ fmtRatio(valuation.psTtm) }}</span>
          </span>
          <span v-if="valuation.snapshotDate" class="valuation__asof">
            {{ valuation.snapshotDate }}
          </span>
        </div>

        <!-- 资金面：近 30 个自然日的异动统计。窗口内全空是**结论**
             （这只票近期没异动），不是数据缺失，所以整块不渲染。 -->
        <div v-if="capital" class="stock-float__capital">
          <span class="capital__label">资金面<small>近30日</small></span>
          <span class="capital__tags">
            <span
              v-if="capital.limitUpCount > 0"
              class="capital__chip is-up"
              :title="capital.lastLimitUpDate
                ? `最近涨停 ${capital.lastLimitUpDate}${capital.maxContinueDays > 1 ? ` · 最高 ${capital.maxContinueDays} 连板` : ''}`
                : undefined"
            >
              涨停 ×{{ capital.limitUpCount }}<template v-if="capital.maxContinueDays > 1"> · {{ capital.maxContinueDays }}板</template>
            </span>
            <span v-if="capital.limitBreakCount > 0" class="capital__chip is-break">
              炸板 ×{{ capital.limitBreakCount }}
            </span>
            <span v-if="capital.dragonCount > 0" class="capital__chip is-dragon">
              龙虎榜 ×{{ capital.dragonCount }}<template v-if="capital.dragonNetValue != null"> · 净{{ capital.dragonNetValue >= 0 ? '买' : '卖' }}{{ fmtYi(capital.dragonNetValue) }}</template>
            </span>
            <span v-if="capital.hotRank != null" class="capital__chip is-hot">
              热度榜 第{{ capital.hotRank }}名
            </span>
          </span>
        </div>

        <!-- 板块归属：industry / region / concept 各取前几个，
             tszs（同花顺特色指数，一票二十多个）只在计数里体现。 -->
        <div v-if="shownSectors.length" class="stock-float__sectors">
          <span
            v-for="(s, i) in shownSectors"
            :key="`${s.tag}-${s.name}-${i}`"
            class="sector-chip"
            :class="`is-${s.tag}`"
          >{{ s.name }}</span>
          <span v-if="hiddenSectors > 0" class="sector-chip is-more" :title="`另有 ${hiddenSectors} 个特色指数标签未展示`">
            +{{ hiddenSectors }}
          </span>
        </div>

        <!-- 区间表现：全部由已持有的 300 根 OHLCV 算出，零新增网络请求。
             任一项算不出就整项不显示，不用 0 顶替。 -->
        <div v-if="hasRange" class="stock-float__range">
          <span v-for="r in rangeItems" :key="r.label" class="range-item">
            <span class="range-item__label">{{ r.label }}</span>
            <span
              class="range-item__value"
              :class="r.value >= 0 ? 'is-up' : 'is-down'"
            >{{ r.value >= 0 ? '+' : '' }}{{ r.value.toFixed(2) }}%</span>
          </span>
          <span v-if="volRatio != null" class="range-item">
            <span class="range-item__label">量比</span>
            <span class="range-item__value is-plain">{{ volRatio.toFixed(2) }}</span>
          </span>
          <span v-if="amplitude != null" class="range-item">
            <span class="range-item__label">振幅</span>
            <span class="range-item__value is-plain">{{ amplitude.toFixed(2) }}%</span>
          </span>
        </div>

        <!-- 数据可信度：把「算得出多少」如实摊开。口径退化时这里会写明，
             避免用户把折算出来的线值当成标准四均线。最后一行是本次所有
             数字的出处表名 —— 悬浮框里每个数字都能追回到源表。 -->
        <div class="stock-float__quality">
          <span class="quality__tag">
            {{ scoreResult.quality.bars }} 根 K 线 · 截至 {{ scoreResult.quality.asOf }}
          </span>
          <span
            v-for="(note, i) in scoreResult.quality.notes"
            :key="i"
            class="quality__note"
          >{{ note }}</span>
          <span v-if="sourceLine" class="quality__source" :title="sourceLineTitle">
            {{ sourceLine }}
          </span>
          <span v-if="profileUnavailable.length" class="quality__note">
            以下数据源本次不可用：{{ profileUnavailable.join('、') }}
          </span>
        </div>

        <!-- 核心战法速览提炼 -->
        <div class="stock-float__tactics">
          <div
            v-for="(point, idx) in scoreResult.bulletPoints"
            :key="idx"
            class="tactic-item"
          >
            <span class="tactic-dot" :style="{ backgroundColor: scoreResult.themeColor }" />
            <span class="tactic-text">{{ point }}</span>
          </div>
        </div>
      </template>

      <!-- 底部操作栏 -->
      <div class="stock-float__footer">
        <span class="stock-float__hint">同花顺知行量化指标引擎</span>
        <button
          type="button"
          class="stock-float__action-btn"
          @click="handleOpenWorkspace"
        >
          进入完整K线工作台 →
        </button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { fetchKline, computeChange, KlineFetchError } from './kline-cache';
import {
  calcStockHoldingScore,
  calcDataQuality,
  type StockScoreResult,
  type DataQuality,
} from './stock-score';
import {
  normalizeProfile,
  pickSectors,
  getCachedProfile,
  setCachedProfile,
  type StockProfile,
} from './stock-profile';

const props = defineProps<{
  visible: boolean;
  top: number;
  left: number;
  thscode: string;
  name?: string;
}>();

const emit = defineEmits<{
  (e: 'enter'): void;
  (e: 'leave'): void;
  (e: 'open-workspace', payload: { ticker: string; exchange: string; name: string }): void;
}>();

const loading = ref(false);
const scoreResult = ref<StockScoreResult | null>(null);
const quote = ref<{ close: number; pctChange: number | null } | null>(null);
const resolvedName = ref('');
/** 加载失败的原因。用于渲染「查不到」而不是渲染一张空白卡片。 */
const loadError = ref<string>('');
/** 数据是否不足以给出战法评级（区别于「评级很差」）。 */
const insufficient = ref(false);

/**
 * 画像（资金面 / 估值 / 板块）。**独立于 K 线请求**，两条链路各自降级：
 * 画像挂了不影响战法评级，K 线挂了画像照样显示 —— 两块信息没有依赖关系，
 * 不该让一个失败拖垮另一个。
 */
const profile = ref<StockProfile | null>(null);
const profileUnavailable = ref<string[]>([]);

const valuation = computed(() => profile.value?.valuation ?? null);
const capital = computed(() => profile.value?.capital ?? null);
const shownSectors = computed(() => pickSectors(profile.value?.sectors ?? []).shown);
const hiddenSectors = computed(() => pickSectors(profile.value?.sectors ?? []).hidden);

/**
 * 本次展示的所有数字的出处，压成一行显示在数据可信度区。
 * 不逐个数字挂 tooltip —— 320px 的卡里逐个标注会碎成渣，
 * 底部一行统一交代来源，鼠标悬停给出完整表名。
 */
const sourceLine = computed(() => {
  const parts: string[] = ['行情 market.v_daily_qfq'];
  const v = valuation.value;
  if (v) parts.push(`估值 ${v.source}`);
  if (capital.value) {
    const s = capital.value.sources;
    if (s.limit_up) parts.push(`涨停 ${s.limit_up}`);
    if (s.dragon_tiger) parts.push(`龙虎榜 ${s.dragon_tiger}`);
    if (s.hot) parts.push(`热度 ${s.hot}`);
  }
  if (shownSectors.value.length) parts.push('板块 index.v_index_constituents');
  return parts.join(' · ');
});

const sourceLineTitle = computed(() =>
  sourceLine.value.split(' · ').map((p) => {
    const [label, table] = p.split(' ');
    return table ? `${label} ← ${table}` : label;
  }).join('\n'),
);

/**
 * 比率格式化。null 一律显示「—」，绝不用 0 顶替。
 * 只有 PE 为负有明确行业共识（亏损），显示「亏损」；
 * PB/PS 为负是净资产/营收为负，属异常而非亏损，照实显示负数。
 */
function fmtRatio(v: number | null, lossLabel?: string): string {
  if (v == null) return '—';
  if (lossLabel != null && v < 0) return lossLabel;
  return v.toFixed(2);
}

/** 金额转成「亿 / 万」，龙虎榜净买额动辄上亿。 */
function fmtYi(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e8) return `${(abs / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(abs / 1e4).toFixed(0)}万`;
  return abs.toFixed(0);
}

/**
 * 悬浮是高频交互，同一只票反复 hover 很常见，且**必须有 symbol 守卫**——
 * 慢响应会把当前 hover 的另一只票的数据覆盖掉。这里用 requestSeq 做版本号：
 * 只有最后一次请求的结果允许写进 state。
 *
 * 行情本身不再自带缓存：统一走 `fetchKline`（见 kline-cache.ts）。那份缓存按
 * (symbol, period, adjust, limit) 索引并且做在途去重，因此 hover 卡片、顶部
 * 对比条、K 线工作台三处对同一只票只会产生一次网络请求——此前它们各有一份
 * 互不可见的缓存，hover 完再点开工作台仍要重新拉一次。
 */
let requestSeq = 0;

/** 名称缓存：与行情无关的另一类资源，且只有这张卡片在用，留在本地。 */
const nameCache = new Map<string, string>();

/**
 * 画像请求的超时。行情那份超时已经搬进 kline-cache（跟着它自己的 AbortController
 * 走），这里只服务 `/api/stock-profile`——两条链路各有各的超时，不能共用一个常数。
 */
const PROFILE_TIMEOUT = 8000;

const stockName = computed(() => {
  if (props.name) return props.name;
  if (resolvedName.value) return resolvedName.value;
  return props.thscode.split('.')[0] || props.thscode;
});

const isPlacementBelow = computed(() => props.top < 240);

/** 数据不足时也保留可信度快照——scoreResult 为 null 时提示文案要靠它。 */
const dataQualitySnapshot = ref<DataQuality | null>(null);

/**
 * 区间表现的可展示项。只收录**算得出来**的项——旧实现会把 5/20/60 日涨跌
 * 全部按 0 渲染，于是"没有 20 日数据的新股"和"20 日真的没涨"长得一样。
 */
const rangeItems = computed(() => {
  const r = scoreResult.value?.range;
  if (!r) return [] as Array<{ label: string; value: number }>;
  const out: Array<{ label: string; value: number }> = [];
  if (r.ret5 != null) out.push({ label: '5日', value: r.ret5 });
  if (r.ret20 != null) out.push({ label: '20日', value: r.ret20 });
  if (r.ret60 != null) out.push({ label: '60日', value: r.ret60 });
  return out;
});
const hasRange = computed(() => rangeItems.value.length > 0);
const volRatio = computed(() => scoreResult.value?.range.volRatio ?? null);
const amplitude = computed(() => scoreResult.value?.range.amplitude ?? null);

/** 数据不足时把具体原因写出来：K 线根数 + 为什么算不出评级。 */
const scoreResultOrNull = computed(() => {
  if (scoreResult.value || insufficient.value !== true) return '';
  const q = dataQualitySnapshot.value;
  if (!q) return '';
  const reasons: string[] = [];
  if (q.bars < 24) reasons.push(`仅有 ${q.bars} 根 K 线，牵牛绳所需的 24 根不足`);
  if (q.yellowDegraded) reasons.push(`大哥线需要 114 根才能算出标准四均线`);
  return reasons.join('；');
});

const computedLeft = computed(() => {
  if (typeof window === 'undefined') return props.left;
  return Math.max(165, Math.min(window.innerWidth - 165, props.left));
});

const loadProfile = async (symbolStr: string, seq: number) => {
  const isStale = () => seq !== requestSeq;

  const cached = getCachedProfile(symbolStr);
  if (cached) {
    profile.value = cached;
    profileUnavailable.value = cached.unavailable;
    return;
  }

  // 独立 controller：画像超时不能连带 abort 掉 K 线请求
  const ctl = new AbortController();
  const timer = window.setTimeout(() => ctl.abort(), PROFILE_TIMEOUT);
  try {
    const r = await fetch(`/api/stock-profile?symbol=${encodeURIComponent(symbolStr)}`, {
      signal: ctl.signal,
    });
    const json = await r.json();
    if (isStale()) return;
    if (json?.code === 0) {
      const p = normalizeProfile(json, symbolStr);
      setCachedProfile(symbolStr, p);
      profile.value = p;
      profileUnavailable.value = p.unavailable;
    }
    // code !== 0 不写 state：保留 null，卡片上那三块自然不渲染。
    // 后端已经做了「查不到就返回 null + 记 unavailable」的处理，
    // 这里不需要也不能替它编内容。
  } catch (err: any) {
    if (isStale()) return;
    if (err?.name !== 'AbortError') {
      console.warn('[StockCitationFloat] failed to load stock profile:', err);
    }
    // 静默失败：画像是增强信息，缺了不该在卡片上留错误条
  } finally {
    window.clearTimeout(timer);
  }
};

const loadStockData = async (symbolStr: string) => {
  if (!symbolStr) return;
  const seq = ++requestSeq;
  const isStale = () => seq !== requestSeq;

  loading.value = true;
  scoreResult.value = null;
  quote.value = null;
  resolvedName.value = '';
  loadError.value = '';
  insufficient.value = false;
  dataQualitySnapshot.value = null;
  profile.value = null;
  profileUnavailable.value = [];

  const controller = new AbortController();
  const ticker = symbolStr.split('.')[0];

  // 画像与 K 线并行发出。画像有自己的 AbortController 与 stale 守卫，
  // 慢响应只会晚到，不会串台；失败时只清自己那三块。
  loadProfile(symbolStr, seq);

  // 名称：命中缓存就不再发请求；失败时静默回退到代码，不阻塞卡片。
  if (!props.name && ticker && !nameCache.has(ticker)) {
    fetch(`/api/symbols/search?q=${encodeURIComponent(ticker)}`)
      .then((r) => r.json())
      .then((json) => {
        const n = json?.data?.[0]?.name;
        if (n) nameCache.set(ticker, n);
      })
      .catch(() => {});
  }
  if (!props.name && nameCache.has(ticker)) resolvedName.value = nameCache.get(ticker) as string;

  try {
    // 共享缓存：与对比条、工作台复用同一次请求。limit 取 300 是因为卡片只需要
    // 近一年多的数据来算评分；工作台要 5000 根，两者 key 不同、互不干扰。
    const dataList = await fetchKline({ symbol: symbolStr, period: 'day', limit: 300 });

    if (isStale()) return;

    const change = computeChange(dataList);
    if (!change) {
      loadError.value = '查不到该标的的行情数据';
      return;
    }
    quote.value = { close: change.close, pctChange: change.pctChange };

    dataQualitySnapshot.value = calcDataQuality(dataList);

    const scored = calcStockHoldingScore(dataList);
    if (scored) {
      scoreResult.value = scored;
    } else {
      // 返回 null 表示数据不足以给评级——必须显式告诉用户，
      // 不能退化成一张「什么都没说」的空白卡片。
      insufficient.value = true;
    }
  } catch (err: unknown) {
    if (isStale()) return;
    // 两类失败指向完全不同的排查方向，必须分开说：
    //   no-data  = 本地没有这只票的行情（多半是模型编的代码），用户换一只就行；
    //   transient = 链路/服务故障（502、超时、非 JSON），数据本身可能是好的。
    // 混成一句「查询失败」会让服务挂掉被误读成"这只票没数据"。
    if (err instanceof KlineFetchError) {
      loadError.value = err.kind === 'no-data' ? '查不到该标的的行情数据' : `行情查询失败：${err.message}`;
    } else {
      loadError.value = `行情查询失败${err instanceof Error && err.message ? `：${err.message}` : ''}`;
    }
    console.warn('[StockCitationFloat] failed to load stock kline data:', err);
  } finally {
    if (!isStale()) loading.value = false;
  }
};

watch(
  () => [props.visible, props.thscode],
  ([vis, code]) => {
    if (vis && code) {
      loadStockData(code as string);
    }
  },
  { immediate: true },
);

const handleOpenWorkspace = () => {
  const [ticker, exchange] = props.thscode.split('.');
  emit('open-workspace', {
    ticker: ticker || props.thscode,
    exchange: exchange || 'SH',
    name: stockName.value,
  });
};
</script>

<style lang="less" scoped>
.stock-citation-float {
  position: fixed;
  z-index: 10050;
  width: 320px;
  max-width: 90vw;
  background: var(--td-bg-color-container, #ffffff);
  border: 1px solid var(--td-component-stroke, #e2e8f0);
  border-radius: 8px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.18), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: var(--td-text-color-primary, #1e293b);
  transform: translate(-50%, -100%) translateY(-10px);
  pointer-events: auto;
  animation: floatFadeIn 0.15s cubic-bezier(0.16, 1, 0.3, 1);

  &.placement-below {
    transform: translate(-50%, 14px);
    animation: floatFadeInBelow 0.15s cubic-bezier(0.16, 1, 0.3, 1);
  }

  :root[theme-mode="dark"] & {
    background: #181d26;
    border-color: #2e3846;
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
    color: #f1f5f9;
  }
}

@keyframes floatFadeIn {
  from {
    opacity: 0;
    transform: translate(-50%, -100%) translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translate(-50%, -100%) translateY(-10px);
  }
}

@keyframes floatFadeInBelow {
  from {
    opacity: 0;
    transform: translate(-50%, 4px);
  }
  to {
    opacity: 1;
    transform: translate(-50%, 14px);
  }
}

.stock-float__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--td-component-stroke, #f1f5f9);
  padding-bottom: 8px;

  :root[theme-mode="dark"] & {
    border-bottom-color: #28303d;
  }
}

.stock-float__title-row {
  display: flex;
  align-items: baseline;
  gap: 6px;

  .stock-float__name {
    font-size: 15px;
    font-weight: 700;
  }

  .stock-float__code {
    font-size: 12px;
    color: var(--td-text-color-placeholder, #94a3b8);
    font-family: monospace;
  }
}

.stock-float__quote-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-family: monospace;
  font-weight: 700;

  .stock-float__price {
    font-size: 14px;
    &.is-up { color: #ef4444; }
    &.is-down { color: #10b981; }
  }

  .stock-float__change {
    font-size: 12px;
    &.is-up { color: #ef4444; }
    &.is-down { color: #10b981; }
  }
}

.stock-float__loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 0;
  font-size: 12px;
  color: var(--td-text-color-secondary, #64748b);
  justify-content: center;

  .stock-float__spinner {
    width: 14px;
    height: 14px;
    border: 2px solid rgba(0, 82, 217, 0.2);
    border-top-color: var(--td-brand-color, #0052d9);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.stock-float__score-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  border-radius: 6px;
  border-left: 3px solid #ef4444;
  background: rgba(0, 0, 0, 0.02);

  :root[theme-mode="dark"] & {
    background: rgba(255, 255, 255, 0.03);
  }

  .score-card__stars {
    display: flex;
    gap: 2px;
    font-size: 14px;
    line-height: 1;
  }

  .score-card__badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 4px;
  }
}

/* 查不到 / 数据不足：显式说明。旧实现在这两种情况下都不渲染任何内容，
   用户只看到一张空卡片，无法区分「加载中」「没数据」和「页面坏了」。 */
.stock-float__notice {
  padding: 10px 12px;
  border-radius: var(--app-radius-sm);
  font-size: var(--app-text-sm);
  line-height: 1.6;

  &.is-error {
    background: rgba(220, 38, 38, 0.08);
    color: #b91c1c;
  }

  &.is-warn {
    background: rgba(180, 83, 9, 0.08);
    color: #92400e;
  }

  :root[theme-mode="dark"] & {
    &.is-error { background: rgba(220, 38, 38, 0.15); color: #fca5a5; }
    &.is-warn { background: rgba(180, 83, 9, 0.15); color: #fcd34d; }
  }

  .notice__title {
    margin: 0 0 4px;
    font-weight: 600;
  }

  .notice__desc {
    margin: 0;
    opacity: 0.85;
  }
}

/* 估值：三个口径横排，右对齐让小数点成列 */
.stock-float__valuation {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 5px 8px;
  border-radius: var(--app-radius-xs);
  background: rgba(0, 0, 0, 0.02);
  font-size: var(--app-text-xs);

  :root[theme-mode="dark"] & {
    background: rgba(255, 255, 255, 0.03);
  }
}

.valuation__label,
.capital__label {
  color: var(--td-text-color-placeholder);
  flex-shrink: 0;

  small {
    font-size: var(--app-text-2xs);
    margin-left: 2px;
    opacity: 0.8;
  }
}

.valuation__item {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
}

.valuation__key {
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-2xs);

  small {
    font-size: var(--app-text-2xs);
    vertical-align: super;
  }
}

.valuation__num {
  font-family: monospace;
  font-weight: 600;
  font-size: var(--app-text-sm);
}

.valuation__asof {
  margin-left: auto;
  font-family: monospace;
  font-size: var(--app-text-2xs);
  color: var(--td-text-color-placeholder);
}

/* 资金面：异动徽章 */
.stock-float__capital {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 5px 8px;
  border-radius: var(--app-radius-xs);
  background: rgba(0, 0, 0, 0.02);
  font-size: var(--app-text-xs);

  :root[theme-mode="dark"] & {
    background: rgba(255, 255, 255, 0.03);
  }
}

.capital__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
}

.capital__chip {
  padding: 1px 6px;
  border-radius: var(--app-radius-xs);
  font-size: var(--app-text-2xs);
  font-weight: 600;
  white-space: nowrap;
  background: rgba(100, 116, 139, 0.12);
  color: var(--td-text-color-secondary);

  &.is-up {
    background: rgba(220, 38, 38, 0.12);
    color: #b91c1c;
  }

  &.is-break {
    background: rgba(180, 83, 9, 0.12);
    color: #92400e;
  }

  &.is-dragon {
    background: rgba(2, 132, 199, 0.12);
    color: #0369a1;
  }

  &.is-hot {
    background: rgba(180, 83, 9, 0.12);
    color: #92400e;
  }

  :root[theme-mode="dark"] & {
    &.is-up { background: rgba(239, 68, 68, 0.18); color: #fca5a5; }
    &.is-break,
    &.is-hot { background: rgba(251, 191, 36, 0.16); color: #fcd34d; }
    &.is-dragon { background: rgba(56, 189, 248, 0.16); color: #7dd3fc; }
  }
}

/* 板块归属 chip */
.stock-float__sectors {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.sector-chip {
  padding: 1px 6px;
  border-radius: var(--app-radius-xs);
  font-size: var(--app-text-2xs);
  line-height: 1.6;
  border: 1px solid transparent;
  white-space: nowrap;

  /* 行业是主归属，视觉权重最高 */
  &.is-industry {
    background: var(--td-brand-color-light);
    color: var(--td-brand-color);
    font-weight: 600;
  }

  &.is-region {
    background: rgba(100, 116, 139, 0.12);
    color: var(--td-text-color-secondary);
  }

  &.is-cn_concept {
    background: rgba(0, 0, 0, 0.03);
    color: var(--td-text-color-placeholder);
    border-color: var(--td-component-stroke);
  }

  &.is-more {
    background: transparent;
    color: var(--td-text-color-placeholder);
    border-color: var(--td-component-stroke);
    cursor: help;
  }

  :root[theme-mode="dark"] & {
    &.is-industry { background: rgba(0, 164, 255, 0.16); color: #7cb8ff; }
    &.is-cn_concept { background: rgba(255, 255, 255, 0.04); border-color: #2e3846; }
    &.is-region { background: rgba(255, 255, 255, 0.06); }
  }
}

.quality__source {
  margin-top: 1px;
  color: var(--td-text-color-placeholder);
  cursor: help;
  word-break: break-all;
}

/* 区间表现：只渲染算得出来的项 */
.stock-float__range {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  padding: 6px 8px;
  border-radius: var(--app-radius-xs);
  background: rgba(0, 0, 0, 0.02);
  font-size: var(--app-text-xs);

  :root[theme-mode="dark"] & {
    background: rgba(255, 255, 255, 0.03);
  }
}

.range-item {
  display: inline-flex;
  gap: 3px;
}

.range-item__label {
  color: var(--td-text-color-placeholder);
}

.range-item__value {
  font-family: monospace;
  font-weight: 600;

  &.is-up { color: #dc2626; }
  &.is-down { color: #047857; }
  &.is-plain { color: inherit; }

  :root[theme-mode="dark"] & {
    &.is-up { color: #ef4444; }
    &.is-down { color: #10b981; }
  }
}

/* 数据可信度：把口径退化写在脸上 */
.stock-float__quality {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--app-text-2xs);
  line-height: 1.5;
  color: var(--td-text-color-placeholder);
}

.quality__tag {
  font-family: monospace;
}

.quality__note {
  color: #b45309;

  :root[theme-mode="dark"] & {
    color: #fcd34d;
  }
}

.stock-float__tactics {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 0;
}

.tactic-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--td-text-color-secondary, #475569);

  :root[theme-mode="dark"] & {
    color: #cbd5e1;
  }

  .tactic-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-top: 6px;
  }

  .tactic-text {
    flex: 1;
  }
}

.stock-float__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid var(--td-component-stroke, #f1f5f9);

  :root[theme-mode="dark"] & {
    border-top-color: #28303d;
  }

  .stock-float__hint {
    font-size: 10px;
    color: var(--td-text-color-placeholder, #94a3b8);
  }

  .stock-float__action-btn {
    border: none;
    background: var(--td-brand-color, #0052d9);
    color: #ffffff;
    font-size: 11px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.15s ease;

    &:hover {
      opacity: 0.9;
      transform: translateY(-1px);
    }

    &:active {
      transform: translateY(0);
    }
  }
}
</style>
