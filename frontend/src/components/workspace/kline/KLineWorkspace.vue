<template>
  <div class="kline-workspace" :class="{ 'is-dark': isDark, 'hide-drawing-bar': !isDrawingBarVisible }">
    <!-- 1. 顶部股票池候选条 (Picks Bar) -->
    <div class="kline-workspace__picks-bar" v-if="workspace.picks.value.length > 0">
      <div class="picks-bar__label">
        <t-icon name="chart-bar" size="14px" />
        <span>候选池 ({{ workspace.picks.value.length }})</span>
      </div>
      <div class="picks-bar__list" role="tablist">
        <button
          v-for="(pick, idx) in workspace.picks.value"
          :key="`${pick.ticker}-${pick.exchange}-${idx}`"
          type="button"
          class="picks-bar__tab"
          :class="{ 'is-active': workspace.activeIndex.value === idx }"
          role="tab"
          :aria-selected="workspace.activeIndex.value === idx"
          @click="workspace.setActiveIndex(idx)"
        >
          <span class="tab__code">{{ pick.ticker }}</span>
          <span class="tab__name" v-if="pick.name">{{ pick.name }}</span>
          <span class="tab__tag" v-if="pick.pattern">{{ pick.pattern }}</span>
        </button>
      </div>
      <div class="picks-bar__hint">[↑/↓] 键快速切股</div>
    </div>

    <!-- 2. 行情概览与战法状态概括条 (Quote & Z-Status Strip) -->
    <div class="kline-workspace__quote-strip">
      <div class="quote-strip__left">
        <!-- 股票代码与名称（点击可快捷搜索切换） -->
        <button type="button" class="quote__symbol-btn" @click="showSearchModal = !showSearchModal" title="点击搜索切换股票">
          <span class="quote__name">{{ currentStockName }}</span>
          <span class="quote__symbol">{{ currentTicker }}.{{ currentExchange }}</span>
          <t-icon name="search" size="13px" class="search-hint-icon" />
        </button>

        <span
          v-if="latestQuote"
          class="quote__price"
          :class="latestQuote.pctChange >= 0 ? 'is-up' : 'is-down'"
        >
          {{ latestQuote.close.toFixed(2) }}
        </span>
        <span
          v-if="latestQuote"
          class="quote__change-badge"
          :class="latestQuote.pctChange >= 0 ? 'is-up' : 'is-down'"
        >
          {{ latestQuote.pctChange >= 0 ? '+' : '' }}{{ latestQuote.pctChange.toFixed(2) }}%
        </span>
      </div>

      <!-- 实时战法关键特征状态胶囊 -->
      <div class="quote-strip__zstatus" v-if="latestQuote">
        <!-- ZX砖型图状态 -->
        <span
          class="status-pill"
          :class="latestQuote.brickScore > 0 ? 'is-bull' : latestQuote.brickScore < 0 ? 'is-bear' : 'is-neutral'"
          title="同花顺知行砖型图 连续红绿砖数砖战法"
        >
          🧱 ZX砖型: {{ latestQuote.brickText }}
        </span>

        <!-- 白黄线多空 -->
        <span
          class="status-pill"
          :class="latestQuote.aboveYellow && latestQuote.whiteAboveYellow ? 'is-bull' : !latestQuote.aboveYellow ? 'is-bear' : 'is-neutral'"
          :title="`白线(${latestQuote.whiteVal.toFixed(2)}) 与 黄线大哥线(${latestQuote.yellowVal.toFixed(2)})`"
        >
          ⚪🟡 {{ !latestQuote.aboveYellow ? '破黄线(严守止损)' : latestQuote.whiteAboveYellow ? '白在黄上(顺大势)' : '碗内回踩(蓄势)' }}
        </span>

        <!-- BBI牵牛绳 -->
        <span
          class="status-pill"
          :class="latestQuote.aboveBbi ? 'is-bull' : 'is-bear'"
          title="收盘价与BBI多空平衡线关系"
        >
          {{ latestQuote.aboveBbi ? '🐂 站上BBI' : '🐻 跌破BBI' }}
        </span>
      </div>

      <!-- 右侧辅助行情指标 -->
      <div class="quote-strip__metrics" v-if="latestQuote">
        <span class="metric-item">量: <strong>{{ formatVolume(latestQuote.volume) }}</strong></span>
        <span class="metric-item">额: <strong>{{ formatTurnover(latestQuote.turnover) }}</strong></span>
      </div>
    </div>

    <!-- 3. 股票搜索浮层 (Search Popover) -->
    <div v-if="showSearchModal" class="kline-workspace__search-modal" @click.self="showSearchModal = false">
      <div class="search-modal__box">
        <div class="search-modal__header">
          <t-icon name="search" size="16px" />
          <input
            v-model="searchQuery"
            type="text"
            placeholder="输入股票代码/名称/拼音 (如 600519、宁德时代)"
            class="search-modal__input"
            autofocus
            @input="handleSearchInput"
            @keydown.esc="showSearchModal = false"
          />
          <button type="button" class="search-modal__close" @click="showSearchModal = false">
            <t-icon name="close" size="14px" />
          </button>
        </div>
        <div class="search-modal__results">
          <div v-if="isSearching" class="search-loading">正在搜索...</div>
          <div v-else-if="searchResults.length === 0 && searchQuery" class="search-empty">未匹配到相关个股</div>
          <div
            v-for="item in searchResults"
            :key="`${item.ticker}-${item.exchange}`"
            class="search-item"
            @click="selectSymbol(item)"
          >
            <span class="search-item__name">{{ item.name }}</span>
            <span class="search-item__code">{{ item.ticker }}.{{ item.exchange }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 4. 同花顺风格一体化控制栏 (周期/复权/主图/副图/九转/形态/画线/关闭) -->
    <div class="kline-workspace__toolbar">
      <!-- 周期 -->
      <div class="toolbar__group">
        <span class="group__label">周期:</span>
        <button
          v-for="(p, idx) in PERIODS"
          :key="p.timespan"
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': periodIdx === idx }"
          @click="periodIdx = idx"
        >
          {{ p.text }}
        </button>
      </div>

      <div class="toolbar__divider" />

      <!-- 复权 -->
      <div class="toolbar__group">
        <span class="group__label">复权:</span>
        <button
          v-for="opt in ADJUST_OPTIONS"
          :key="opt.value"
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': adjust === opt.value }"
          @click="adjust = opt.value"
        >
          {{ opt.label }}
        </button>
      </div>

      <div class="toolbar__divider" />

      <!-- 主图指标 -->
      <div class="toolbar__group">
        <span class="group__label">主图:</span>
        <button
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': mainMode === 'zettaranc' }"
          @click="setMainMode('zettaranc')"
          title="EMA10白线 + EMA14黄线 + BBI牵牛绳"
        >
          ⚪🟡 白黄+BBI
        </button>
        <button
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': mainMode === 'all' }"
          @click="setMainMode('all')"
          title="战法核心线 + MA5/10/20"
        >
          战法+MA
        </button>
        <button
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': mainMode === 'ma' }"
          @click="setMainMode('ma')"
          title="传统均线"
        >
          传统MA
        </button>
        <button
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': mainMode === 'boll' }"
          @click="setMainMode('boll')"
          title="布林带 BOLL"
        >
          BOLL
        </button>
      </div>

      <div class="toolbar__divider" />

      <!-- 副图指标 -->
      <div class="toolbar__group">
        <span class="group__label">副图:</span>
        <button
          v-for="sub in SUB_INDICATOR_LIST"
          :key="sub.id"
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': subMode === sub.id }"
          @click="switchSubMode(sub.id)"
        >
          {{ sub.label }}
        </button>
      </div>

      <div class="toolbar__divider" />

      <!-- 同花顺专业特性开关：神奇九转 & 形态气泡 -->
      <div class="toolbar__group">
        <button
          type="button"
          class="toolbar__btn feature-btn"
          :class="{ 'is-active': isTD9Enabled }"
          title="开启/关闭神奇九转变盘倒数序列 (1~9)"
          @click="toggleTD9"
        >
          9️⃣ 九转序列
        </button>
        <button
          type="button"
          class="toolbar__btn feature-btn"
          :class="{ 'is-active': isPatternsEnabled }"
          title="开启/关闭 K线形态气泡胶囊 (阳包阴、乌云压顶、十字星、B1/S1等)"
          @click="togglePatterns"
        >
          🏷️ 形态气泡<span v-if="filteredAnnotations.length > 0" class="feature-count">({{ filteredAnnotations.length }})</span>
        </button>
      </div>

      <div class="toolbar__spacer" />

      <!-- 画线侧栏切换 -->
      <button
        type="button"
        class="toolbar__btn"
        :class="{ 'is-active': isDrawingBarVisible }"
        title="显示/隐藏左侧画线工具栏"
        @click="toggleDrawingBar"
      >
        ✏️ 画线
      </button>

      <!-- 关闭工作台 -->
      <button type="button" class="toolbar__icon-btn" :title="t('common.close')" @click="workspace.close()">
        <t-icon name="close" size="16px" />
      </button>
    </div>

    <!-- 5. KLineChart Canvas 容器 (占用主要高度，无任何挤压)
         外包一层 chart-wrap 作为相对定位上下文：空状态必须只盖住图表区域，
         不能用兄弟节点的 inset:0——那会把工具栏和候选池条一起遮掉。 -->
    <div class="kline-workspace__chart-wrap">
      <div ref="chartContainer" class="kline-workspace__chart" />

      <!-- 5b. 无行情空状态
           以前这里什么都不显示，图表就是一块纯黑，用户分不清是"还在加载"、
           "这只票没数据"还是"页面坏了"。实测最常见的原因是代码不存在——模型
           幻觉出的代码段（如把 600487 写成 688487）在本地从未发行。 -->
      <div v-if="noDataSymbol" class="kline-workspace__empty">
        <div class="empty__icon">📉</div>
        <p class="empty__title">本地无 {{ noDataSymbol }} 的行情数据</p>
        <p class="empty__hint">
          该代码不在本地代码表中（v_symbol），或本地行情尚未同步到它。<br />
          如果这是模型提到的代码，它很可能是<b>幻觉出的不存在的代码</b>；<br />
          代码格式为 6 位数字 + 交易所后缀（.SH / .SZ / .BJ）。
        </p>
      </div>
    </div>

    <!-- 7. 底部向 Agent 决策追问快捷条 -->
    <div class="kline-workspace__actions">
      <span class="actions__title">
        <t-icon name="chat" size="14px" />
        <span>继续向 Agent 提问:</span>
      </span>
      <div class="actions__chips">
        <button
          type="button"
          class="action-chip"
          @click="handleActionAsk('valuation')"
        >
          📊 分析基本面与估值
        </button>
        <button
          type="button"
          class="action-chip"
          @click="handleActionAsk('strategy')"
        >
          🛡️ 测算防守位与试仓策略
        </button>
        <button
          type="button"
          class="action-chip"
          @click="handleActionAsk('report')"
        >
          📑 查阅最新研报与核心逻辑
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue';
import { useI18n } from 'vue-i18n';
import { KLineChartPro } from '@klinecharts/pro';
import '@klinecharts/pro/dist/klinecharts-pro.css';
import { useAgentWorkspace } from '@/composables/useAgentWorkspace';
import { ZettarancDatafeed, type Adjust } from './datafeed';
import { getKlineTheme } from './theme';
import { registerZettarancIndicators } from './indicators';
import { fetchAnnotations, type Annotation, PATTERN_CONFIG } from './annotate-api';
import { setGlobalOverlayConfig } from './overlay-drawer';
import { calcDEMA, calcLongBBI, calcBBI, calcZXBrick } from './stock-score';
import type { Period, SymbolInfo, KLineData } from './types';

const { t } = useI18n();
const workspace = useAgentWorkspace();

// 注册同花顺风格与 Zettaranc 扩展指标
registerZettarancIndicators();

const chartContainer = ref<HTMLDivElement | null>(null);

// 当前标的在本地取不到行情时的提示文案（形如 688487.SH），空串表示有数据。
// 由 datafeed 的 onNoData 置位，由 handleDataLoaded 清空——后者必须清，否则
// 换到有数据的票时空状态会残留。
const noDataSymbol = ref('');
const chartInstance = ref<KLineChartPro | null>(null);
let resizeObserver: ResizeObserver | null = null;

const isDark = ref(true); // 专业深色交易终端风
// 左侧画线栏。默认关闭。
//
// 库的实例只暴露 setTheme/setStyles/setPeriod 等 setter，没有运行时的
// drawingBarVisible 开关，也没有 resize 方法——要么重建整个图表（会丢缩放
// 位置并重新取数），要么用 CSS 隐藏后让内部 ResizeObserver 重排。这里选后者：
// 切换后派发一次 window resize，库内部的 observer 会重新量算画布尺寸，
// 否则容器变宽了但 canvas 仍按旧宽度绘制，右侧会留白。
const isDrawingBarVisible = ref(false);

const toggleDrawingBar = () => {
  isDrawingBarVisible.value = !isDrawingBarVisible.value;
  // 等 DOM class 应用后再触发，避免量到切换前的尺寸。
  nextTick(() => {
    window.dispatchEvent(new Event('resize'));
  });
};
const adjust = ref<Adjust>('forward');
const periodIdx = ref(0);

// 神奇九转与形态气泡开关
const isTD9Enabled = ref(true);
const isPatternsEnabled = ref(true);

// 主图模式：战法白黄+BBI、战法+MA、传统均线、布林带
type MainIndicatorMode = 'zettaranc' | 'all' | 'ma' | 'boll';
const mainMode = ref<MainIndicatorMode>('zettaranc');

// 副图模式：双副图(量+ZX砖型推荐)、双副图(量+MACD经典)、单ZX砖型、单成交量、单MACD、单KDJ、RSL
type SubIndicatorMode = 'VOL_AND_BRICK' | 'VOL_AND_MACD' | 'ZX_BRICK' | 'Z_VOL' | 'Z_MACD' | 'Z_KDJ' | 'Z_RSL';
const subMode = ref<SubIndicatorMode>('VOL_AND_BRICK');

const SUB_INDICATOR_LIST = [
  { id: 'VOL_AND_BRICK' as const, label: '📊🧱 量+ZX砖型 (推荐)' },
  { id: 'ZX_BRICK' as const, label: '🧱 ZX砖型图' },
  { id: 'VOL_AND_MACD' as const, label: '📊📈 量+MACD' },
  { id: 'Z_VOL' as const, label: '📊 成交量' },
  { id: 'Z_MACD' as const, label: '📈 MACD' },
  { id: 'Z_KDJ' as const, label: '⚡ KDJ' },
  { id: 'Z_RSL' as const, label: '🎯 RSL强弱' },
];

interface LatestQuoteInfo {
  close: number;
  open: number;
  high: number;
  low: number;
  volume: number;
  turnover: number;
  pctChange: number;
  brickScore: number;
  brickText: string;
  whiteAboveYellow: boolean;
  aboveBbi: boolean;
  aboveYellow: boolean;
  whiteVal: number;
  yellowVal: number;
}

const latestQuote = ref<LatestQuoteInfo | null>(null);
const annotations = ref<Annotation[]>([]);
const enabledPatterns = ref<Set<string>>(new Set(Object.keys(PATTERN_CONFIG)));

// 股票搜索状态
const showSearchModal = ref(false);
const searchQuery = ref('');
const searchResults = ref<Array<{ ticker: string; name: string; exchange: string }>>([]);
const isSearching = ref(false);
let searchDebounceTimer: any = null;

const ADJUST_OPTIONS: Array<{ value: Adjust; label: string }> = [
  { value: 'forward', label: '前复权' },
  { value: 'none', label: '不复权' },
  { value: 'backward', label: '后复权' },
];

const PERIODS: Period[] = [
  { multiplier: 1, timespan: 'day', text: '日K' },
  { multiplier: 1, timespan: 'week', text: '周K' },
  { multiplier: 1, timespan: 'month', text: '月K' },
];

const currentTicker = computed(() => {
  const p = workspace.activePick.value;
  return p ? p.ticker : '600519';
});

const currentExchange = computed(() => {
  const p = workspace.activePick.value;
  return p ? p.exchange : 'SH';
});

const currentStockName = computed(() => {
  const p = workspace.activePick.value;
  return p?.name || currentTicker.value;
});

const filteredAnnotations = computed(() => {
  return annotations.value.filter((ann) => enabledPatterns.value.has(ann.type));
});

const formatVolume = (vol: number) => {
  if (!vol || !Number.isFinite(vol)) return '0';
  if (vol >= 100000000) return (vol / 100000000).toFixed(2) + '亿手';
  if (vol >= 10000) return (vol / 10000).toFixed(2) + '万手';
  return vol.toFixed(0) + '手';
};

const formatTurnover = (amount: number) => {
  if (!amount || !Number.isFinite(amount)) return '0';
  if (amount >= 100000000) return (amount / 100000000).toFixed(2) + '亿';
  if (amount >= 10000) return (amount / 10000).toFixed(2) + '万';
  return amount.toFixed(0);
};

// 计算主图指标列表
const getMainIndicators = () => {
  const list: string[] = [];
  if (mainMode.value === 'zettaranc') {
    list.push('Z_MAIN');
  } else if (mainMode.value === 'all') {
    list.push('MA', 'Z_MAIN');
  } else if (mainMode.value === 'boll') {
    list.push('BOLL', 'Z_SIGNALS');
  } else {
    list.push('MA', 'Z_SIGNALS');
  }
  return list;
};

// 计算副图指标列表（严格控制在1~2个，确保蜡烛图主图饱满）
const getSubIndicators = () => {
  if (subMode.value === 'VOL_AND_BRICK') {
    return ['Z_VOL', 'ZX_BRICK'];
  }
  if (subMode.value === 'VOL_AND_MACD') {
    return ['Z_VOL', 'Z_MACD'];
  }
  if (subMode.value === 'ZX_BRICK') {
    return ['ZX_BRICK'];
  }
  return [subMode.value];
};

const setMainMode = (mode: MainIndicatorMode) => {
  mainMode.value = mode;
  initChart();
};

const switchSubMode = (mode: SubIndicatorMode) => {
  subMode.value = mode;
  initChart();
};

const toggleTD9 = () => {
  isTD9Enabled.value = !isTD9Enabled.value;
  setGlobalOverlayConfig({ showTD9: isTD9Enabled.value });
  window.dispatchEvent(new Event('resize'));
};

const togglePatterns = () => {
  isPatternsEnabled.value = !isPatternsEnabled.value;
  setGlobalOverlayConfig({ showPatterns: isPatternsEnabled.value });
  window.dispatchEvent(new Event('resize'));
};

// 股票搜索处理
const handleSearchInput = () => {
  clearTimeout(searchDebounceTimer);
  const q = searchQuery.value.trim();
  if (!q) {
    searchResults.value = [];
    return;
  }
  searchDebounceTimer = setTimeout(async () => {
    isSearching.value = true;
    try {
      const res = await fetch(`/api/symbols/search?q=${encodeURIComponent(q)}`);
      if (res.ok) {
        const json = await res.json();
        searchResults.value = json.data || [];
      }
    } catch {
      searchResults.value = [];
    } finally {
      isSearching.value = false;
    }
  }, 250);
};

const selectSymbol = (item: { ticker: string; name: string; exchange: string }) => {
  workspace.addOrSwitchPick({
    ticker: item.ticker,
    exchange: item.exchange,
    name: item.name,
  });
  showSearchModal.value = false;
  searchQuery.value = '';
  searchResults.value = [];
};

// 抽取并计算最新行情快照指标
const handleDataLoaded = (dataList: KLineData[]) => {
  if (!dataList || dataList.length === 0) return;
  // 有数据了，清掉上一只票可能残留的空状态。
  noDataSymbol.value = '';
  const lastIdx = dataList.length - 1;
  const last = dataList[lastIdx];
  const prev = dataList.length > 1 ? dataList[dataList.length - 2] : last;

  const close = last.close;
  const prevClose = prev.close;
  const pctChange = prevClose > 0 ? ((close - prevClose) / prevClose) * 100 : 0;

  // 1. 严格依据知识库计算白黄线与BBI
  const dema10 = calcDEMA(dataList, 10);
  const longBbi = calcLongBBI(dataList, [14, 28, 57, 114]);
  const bbiList = calcBBI(dataList);
  const zxBricks = calcZXBrick(dataList);

  const whiteVal = dema10[lastIdx] ?? close;
  const yellowVal = longBbi[lastIdx] ?? close;
  const bbiVal = bbiList[lastIdx] ?? close;
  const brick = zxBricks[lastIdx];

  const whiteAboveYellow = whiteVal >= yellowVal;
  const aboveYellow = close >= yellowVal;
  const aboveBbi = close >= bbiVal;

  let brickText = brick ? brick.countText : '震荡';
  let brickScore = brick ? (brick.direction === 'up' ? brick.stepCount : -brick.stepCount) : 0;

  latestQuote.value = {
    close,
    open: last.open,
    high: last.high,
    low: last.low,
    volume: last.volume ?? 0,
    turnover: last.turnover ?? 0,
    pctChange,
    brickScore,
    brickText,
    whiteAboveYellow,
    aboveBbi,
    aboveYellow,
    whiteVal,
    yellowVal,
  };
};

// 加载形态标注
const loadAnnotations = async () => {
  if (!currentTicker.value || !currentExchange.value) return;
  try {
    const symbolStr = `${currentTicker.value}.${currentExchange.value}`;
    const res = await fetchAnnotations(symbolStr, 120);
    annotations.value = res.annotations || [];
    setGlobalOverlayConfig({ backendAnnotations: annotations.value });
    window.dispatchEvent(new Event('resize'));
  } catch (err) {
    annotations.value = [];
  }
};

// 初始化并渲染 KLineChart Pro
const initChart = () => {
  if (!chartContainer.value) return;

  if (chartInstance.value) {
    chartContainer.value.innerHTML = '';
    chartInstance.value = null;
  }

  const symbol: SymbolInfo = {
    exchange: currentExchange.value,
    market: 'stocks',
    name: currentStockName.value,
    shortName: currentStockName.value,
    ticker: currentTicker.value,
    priceCurrency: 'cny',
    type: 'stock',
  };

  const datafeed = new ZettarancDatafeed({
    adjust: adjust.value,
    onDataLoaded: handleDataLoaded,
    onNoData: (symbol) => {
      noDataSymbol.value = `${symbol.ticker}.${symbol.exchange}`;
    },
  });

  chartInstance.value = new KLineChartPro({
    container: chartContainer.value,
    symbol,
    period: PERIODS[periodIdx.value],
    datafeed,
    styles: getKlineTheme(isDark.value),
    mainIndicators: getMainIndicators(),
    subIndicators: getSubIndicators(),
    periods: PERIODS,
    drawingBarVisible: true,
    theme: isDark.value ? 'dark' : 'light',
  });
};

// 键盘快捷键监听
const handleKeyDown = (e: KeyboardEvent) => {
  const target = e.target as HTMLElement | null;
  const isInput = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
  if (isInput) return;

  if (e.key === 'ArrowDown' || e.key === 'PageDown') {
    e.preventDefault();
    workspace.nextStock();
  } else if (e.key === 'ArrowUp' || e.key === 'PageUp') {
    e.preventDefault();
    workspace.prevStock();
  } else if (e.key === '1') {
    periodIdx.value = 0;
  } else if (e.key === '2') {
    periodIdx.value = 1;
  } else if (e.key === '3') {
    periodIdx.value = 2;
  }
};

// 快捷动作：把指令反哺给 Chat
const handleActionAsk = (type: 'valuation' | 'strategy' | 'report') => {
  const code = `${currentTicker.value}.${currentExchange.value}`;
  const name = workspace.activePick.value?.name ? `(${workspace.activePick.value.name})` : '';

  let prompt = '';
  if (type === 'valuation') {
    prompt = `请结合最新研报与财报数据，深入分析个股 ${code} ${name} 目前的估值水位、主要盈利指标与财务健康度。`;
  } else if (type === 'strategy') {
    prompt = `按照 Z 哥交易体系与当前技术形态，请帮我分析 ${code} ${name} 当前位置的试仓性价比、加仓条件与防守止损位。`;
  } else if (type === 'report') {
    prompt = `请在研报知识库中检索关于 ${code} ${name} 的最新券商研报，梳理机构核心投资逻辑与风险提示。`;
  }

  workspace.sendToChat(prompt);
};

// 监听标的切换重新初始化
watch([currentTicker, currentExchange, adjust, periodIdx], () => {
  nextTick(() => {
    initChart();
    loadAnnotations();
  });
});

onMounted(() => {
  setGlobalOverlayConfig({
    showTD9: isTD9Enabled.value,
    showPatterns: isPatternsEnabled.value,
  });

  nextTick(() => {
    initChart();
    loadAnnotations();
  });

  // 监听容器尺寸自适应变化（拖动分栏滑块时自动调用图表 resize）
  if (chartContainer.value) {
    resizeObserver = new ResizeObserver(() => {
      window.dispatchEvent(new Event('resize'));
    });
    resizeObserver.observe(chartContainer.value);
  }

  window.addEventListener('keydown', handleKeyDown);
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown);
  if (resizeObserver) {
    resizeObserver.disconnect();
    resizeObserver = null;
  }
  if (chartInstance.value && chartContainer.value) {
    chartContainer.value.innerHTML = '';
    chartInstance.value = null;
  }
});
</script>

<style lang="less" scoped>
.kline-workspace {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  overflow: hidden;
  position: relative;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: #ffffff;
  color: #1f2937;

  &.is-dark {
    background: #11141a;
    color: #e5e7eb;
  }

  /* 隐藏 @klinecharts/pro 自带的重复 period 顶部栏，使用统一专业控制条 */
  :deep(.klinecharts-pro-period-bar) {
    display: none !important;
  }

  /* 确保 pro 组件填满剩余空间，避免 80vh 引起的垂直压缩或溢出 */
  :deep(.klinecharts-pro) {
    height: 100% !important;
  }

  :deep(.klinecharts-pro-content) {
    height: 100% !important;
  }

  /* 画线工具栏显示/隐藏控制 */
  &.hide-drawing-bar {
    :deep(.klinecharts-pro-drawing-bar) {
      display: none !important;
    }
    :deep(.klinecharts-pro-widget) {
      width: 100% !important;
    }
  }

  /* 隐藏滚动条，呈现原生交易软件质感。
     注意：下面这条 `*` 规则原本带 `!important`，会把 .kline-workspace__toolbar
     和 .picks-bar__list 自己的细滚动条一并吃掉——那两处是 `overflow-x: auto`
     的横向滚动容器，滚动条一没，溢出的按钮就再也点不到了（只能靠触控板横向
     滑动，属于静默失效）。这里把 !important 摘掉，让局部样式各管各的：
     全局普通滚动条仍然隐藏，工具栏/候选池保留可见的细滚动条。 */
  scrollbar-width: none;
  -ms-overflow-style: none;

  * {
    scrollbar-width: none;
    -ms-overflow-style: none;
    &::-webkit-scrollbar {
      display: none;
      width: 0;
      height: 0;
    }
  }

  &::-webkit-scrollbar {
    display: none !important;
    width: 0 !important;
    height: 0 !important;
  }
}

/* 顶部股票池标签条 */
.kline-workspace__picks-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: rgba(0, 0, 0, 0.03);
  border-bottom: 1px solid var(--td-component-stroke, #e7e7e7);
  overflow-x: auto;
  flex-shrink: 0;
  scrollbar-width: none;

  /* 候选池条与工具栏、容器根共用同一个深色面。之前这里是 #161b24，夹在
     #11141a 的工具栏和 #11141a 的容器之间，横条之间会露出一道色差接缝——
     两条紧挨着的横栏用不同底色，比任何"元素太多"都更显乱。 */
  .is-dark & {
    background: #11141a;
    border-bottom-color: #232a36;
  }

  .picks-bar__label {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
    opacity: 0.8;
  }

  .picks-bar__list {
    display: flex;
    gap: 6px;
    flex: 1;
    overflow-x: auto;
    scrollbar-width: thin;
    scrollbar-color: rgba(148, 163, 184, 0.25) transparent;

    &::-webkit-scrollbar {
      height: 3px;
    }
    &::-webkit-scrollbar-track {
      background: transparent;
    }
    &::-webkit-scrollbar-thumb {
      background: rgba(148, 163, 184, 0.25);
      border-radius: 3px;
    }
    &:hover::-webkit-scrollbar-thumb {
      background: rgba(100, 116, 139, 0.5);
    }
  }

  .picks-bar__tab {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    /* 与 .toolbar__btn 同一套 token，两者并排时不该有尺寸差。 */
    padding: 4px 10px;
    border-radius: 6px;
    border: 1px solid var(--td-component-stroke, #d1d5db);
    background: transparent;
    color: inherit;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.15s ease;

    .is-dark & {
      border-color: #333d4d;
    }

    &:hover {
      border-color: var(--td-brand-color, #0052d9);
    }

    &.is-active {
      background: var(--td-brand-color, #0052d9);
      border-color: var(--td-brand-color, #0052d9);
      color: #ffffff;
      font-weight: 500;
    }

    .tab__code {
      font-family: monospace;
    }

    .tab__tag {
      font-size: 10px;
      padding: 0 4px;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.2);
    }
  }

  .picks-bar__hint {
    font-size: 10px;
    opacity: 0.5;
    white-space: nowrap;
  }
}

/* 顶部最新行情与战法状态概括条 */
.kline-workspace__quote-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: rgba(0, 0, 0, 0.02);
  border-bottom: 1px solid var(--td-component-stroke, #e7e7e7);
  flex-shrink: 0;
  gap: 12px;

  .is-dark & {
    background: #141820;
    border-bottom-color: #232a36;
  }

  .quote-strip__left {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }

  .quote__symbol-btn {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    background: transparent;
    border: none;
    color: inherit;
    cursor: pointer;
    padding: 2px 4px;
    border-radius: 4px;
    transition: background 0.15s ease;

    &:hover {
      background: rgba(128, 128, 128, 0.15);

      .search-hint-icon {
        opacity: 1;
        color: var(--td-brand-color, #0052d9);
      }
    }

    .quote__name {
      font-size: 14px;
      font-weight: 700;
    }

    .quote__symbol {
      font-size: 11px;
      color: var(--td-text-color-placeholder, #9ca3af);
      font-family: monospace;
    }

    .search-hint-icon {
      opacity: 0.4;
      transition: all 0.15s ease;
    }
  }

  .quote__price {
    font-size: 16px;
    font-weight: 700;
    font-family: monospace;

    &.is-up {
      color: #ef4444;
    }
    &.is-down {
      color: #10b981;
    }
  }

  .quote__change-badge {
    font-size: 11px;
    font-weight: 600;
    font-family: monospace;
    padding: 1px 6px;
    border-radius: 4px;

    &.is-up {
      background: rgba(239, 68, 68, 0.18);
      color: #ef4444;
    }
    &.is-down {
      background: rgba(16, 185, 129, 0.18);
      color: #10b981;
    }
  }

  .quote-strip__zstatus {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    white-space: nowrap;
  }

  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
    white-space: nowrap !important;
    flex-shrink: 0;
    border: 1px solid transparent;

    &.is-bull {
      background: rgba(239, 68, 68, 0.14);
      color: #ef4444;
      border-color: rgba(239, 68, 68, 0.3);
    }
    &.is-bear {
      background: rgba(16, 185, 129, 0.14);
      color: #10b981;
      border-color: rgba(16, 185, 129, 0.3);
    }
    &.is-neutral {
      background: rgba(107, 114, 128, 0.14);
      color: #9ca3af;
      border-color: rgba(107, 114, 128, 0.3);
    }
  }

  .quote-strip__metrics {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 11px;
    color: var(--td-text-color-placeholder, #9ca3af);
    margin-left: auto;
    white-space: nowrap;
    flex-shrink: 0;

    strong {
      color: inherit;
      font-family: monospace;
    }
  }
}

/* 股票快速搜索浮层 */
.kline-workspace__search-modal {
  position: absolute;
  top: 42px;
  left: 12px;
  z-index: 100;
  width: 320px;
  background: var(--td-bg-color-container, #ffffff);
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  border: 1px solid var(--td-component-stroke, #e7e7e7);
  overflow: hidden;

  .is-dark & {
    background: #1c222c;
    border-color: #374151;
  }

  .search-modal__header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--td-component-stroke, #e7e7e7);

    .is-dark & {
      border-bottom-color: #2d3644;
    }
  }

  .search-modal__input {
    flex: 1;
    border: none;
    outline: none;
    background: transparent;
    color: inherit;
    font-size: 12px;
  }

  .search-modal__close {
    background: transparent;
    border: none;
    cursor: pointer;
    color: inherit;
    opacity: 0.6;
    &:hover { opacity: 1; }
  }

  .search-modal__results {
    max-height: 240px;
    overflow-y: auto;
    padding: 4px 0;
  }

  .search-loading,
  .search-empty {
    padding: 12px;
    text-align: center;
    font-size: 11px;
    color: var(--td-text-color-placeholder, #9ca3af);
  }

  .search-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 7px 12px;
    font-size: 12px;
    cursor: pointer;
    transition: background 0.15s ease;

    &:hover {
      background: rgba(59, 130, 246, 0.15);
    }

    .search-item__name {
      font-weight: 500;
    }

    .search-item__code {
      font-family: monospace;
      color: var(--td-text-color-placeholder, #9ca3af);
      font-size: 11px;
    }
  }
}

/* 综合控制工具栏 */
.kline-workspace__toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--td-component-stroke, #e7e7e7);
  background: var(--td-bg-color-container, #ffffff);
  flex-shrink: 0;
  overflow-x: auto;
  /* 这条必须留着：21 个按钮在窄宽度下会溢出，滚动条是唯一的可发现提示。
     去掉后溢出部分只能靠触控板盲滑，等于静默失效。 */
  scrollbar-width: thin;
  scrollbar-color: rgba(148, 163, 184, 0.25) transparent;

  &::-webkit-scrollbar {
    height: 3px;
  }
  &::-webkit-scrollbar-track {
    background: transparent;
  }
  &::-webkit-scrollbar-thumb {
    background: rgba(148, 163, 184, 0.25);
    border-radius: 3px;
  }
  &:hover::-webkit-scrollbar-thumb {
    background: rgba(100, 116, 139, 0.5);
  }

  .is-dark & {
    background: #11141a;
    border-bottom-color: #232a36;
  }

  .toolbar__group {
    display: flex;
    align-items: center;
    gap: 3px;
    white-space: nowrap;
  }

  .group__label {
    font-size: 11px;
    font-weight: 600;
    color: var(--td-text-color-placeholder, #9ca3af);
    margin-right: 2px;
  }

  .toolbar__divider {
    width: 1px;
    height: 14px;
    background: var(--td-component-stroke, #e7e7e7);
    margin: 0 4px;
    flex-shrink: 0;

    .is-dark & {
      background: #2b3341;
    }
  }

  .toolbar__btn {
    /* 体量对齐平台 chip（components/chat/MentionedStocksBar.vue）：
       6px 圆角 / 4px 10px 内边距 / 12px 字号。之前这里是 3px / 2px 7px / 11px，
       比平台小一圈，K线面板和左侧聊天区并排时会显得"缩了一号"。 */
    padding: 4px 10px;
    font-size: 12px;
    border-radius: 6px;
    border: 1px solid var(--td-component-stroke, #d1d5db);
    background: transparent;
    color: inherit;
    cursor: pointer;
    transition: all 0.15s ease;
    white-space: nowrap;

    .is-dark & {
      border-color: #333d4d;
    }

    &:hover {
      border-color: var(--td-brand-color, #0052d9);
    }

    &.is-active {
      background: var(--td-brand-color, #0052d9);
      border-color: var(--td-brand-color, #0052d9);
      color: #ffffff;
      font-weight: 500;
    }

    &.feature-btn {
      font-weight: 500;
      &.is-active {
        background: rgba(59, 130, 246, 0.2);
        border-color: rgba(59, 130, 246, 0.6);
        color: #60a5fa;
      }

      .feature-count {
        margin-left: 2px;
        font-size: 10px;
        opacity: 0.85;
        font-family: monospace;
      }
    }
  }

  .toolbar__spacer {
    flex: 1;
  }

  .toolbar__icon-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    border: none;
    background: transparent;
    color: inherit;
    cursor: pointer;
    border-radius: 4px;

    &:hover {
      background: rgba(128, 128, 128, 0.15);
    }
  }
}

/* KLineChart Canvas 容器 */
/* chart-wrap 只负责建立相对定位上下文，让空状态能精确盖在图表区域上；
   真正的 flex 伸缩与尺寸约束仍由内层 .kline-workspace__chart 承担，
   这样 klinecharts 量到的容器高度与改动前完全一致。 */
.kline-workspace__chart-wrap {
  position: relative;
  display: flex;
  /* flex: 1 不能省。根容器是 flex-direction: column，包一层之后 chart-wrap
     成了直接 flex 子元素；少了它就按内容高度塌陷成 0，内层 canvas 量到 0 高
     只画得出坐标轴、画不出 K 线（2026-09-27 实测：日期轴在、蜡烛全无）。 */
  flex: 1;
  min-height: 0;
  width: 100%;
}

.kline-workspace__chart {
  flex: 1;
  min-height: 0;
  width: 100%;
  position: relative;
}

/* 无行情空状态：绝对定位盖在图表之上，不参与 flex 布局，
   所以出现/消失都不会改变 canvas 的尺寸，不会触发重排。 */
.kline-workspace__empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  text-align: center;
  pointer-events: none;
  /* 半透明遮罩而非实心色块：下面的 canvas 仍在，能看出"图表区域在这"，
     只是没有数据。 */
  background: rgba(17, 20, 26, 0.82);

  .empty__icon {
    font-size: 32px;
    opacity: 0.6;
    line-height: 1;
  }

  .empty__title {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: #e5e7eb;
  }

  .empty__hint {
    margin: 0;
    max-width: 420px;
    font-size: 12px;
    line-height: 1.7;
    color: #8b93a3;

    b {
      color: #d1d5db;
      font-weight: 600;
    }
  }
}

/* 底部决策追问快捷卡片 */
.kline-workspace__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  border-top: 1px solid var(--td-component-stroke, #e7e7e7);
  background: rgba(0, 0, 0, 0.02);
  flex-shrink: 0;

  .is-dark & {
    border-top-color: #232a36;
    background: #141820;
  }

  .actions__title {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    font-weight: 500;
    color: var(--td-text-color-placeholder, #9ca3af);
    white-space: nowrap;
  }

  .actions__chips {
    display: flex;
    gap: 8px;
    overflow-x: auto;
  }

  .action-chip {
    padding: 3px 9px;
    font-size: 11px;
    border-radius: 12px;
    border: 1px solid var(--td-brand-color, #0052d9);
    background: rgba(0, 82, 217, 0.08);
    color: var(--td-brand-color, #0052d9);
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.15s ease;

    .is-dark & {
      border-color: #3b82f6;
      background: rgba(59, 130, 246, 0.15);
      color: #93c5fd;
    }

    &:hover {
      background: var(--td-brand-color, #0052d9);
      color: #ffffff;

      .is-dark & {
        background: #3b82f6;
        color: #ffffff;
      }
    }
  }
}
</style>
