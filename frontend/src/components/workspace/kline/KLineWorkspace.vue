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

        <!-- 双线多空（DEMA10 vs LongBBI） -->
        <span
          class="status-pill"
          :class="latestQuote.aboveYellow && latestQuote.whiteAboveYellow ? 'is-bull' : !latestQuote.aboveYellow ? 'is-bear' : 'is-neutral'"
          :title="`快线 DEMA10(${latestQuote.whiteVal.toFixed(2)}) 与 大哥线 LongBBI(${latestQuote.yellowVal.toFixed(2)}) 的相对位置`"
        >
          {{ !latestQuote.aboveYellow ? '跌破大哥线(严守止损)' : latestQuote.whiteAboveYellow ? '快线在大哥线上(顺大势)' : '碗内回踩(蓄势)' }}
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

      <!-- 周期与复权。这两组是低频操作（切股票时基本不用动），原先和主图/副图
           指标挤在同一条工具栏里，把 15 个按钮顶成两行还看不全。挪到行情条
           右端后，工具栏只剩真正高频的主图/副图切换。 -->
      <div class="quote-strip__right">
        <div class="strip-ctl">
          <span class="strip-ctl__label">周期</span>
          <button
            v-for="(p, idx) in PERIODS"
            :key="p.timespan"
            type="button"
            class="strip-ctl__btn"
            :class="{ 'is-active': periodIdx === idx }"
            @click="periodIdx = idx"
          >
            {{ p.text }}
          </button>
        </div>
        <div class="strip-ctl">
          <span class="strip-ctl__label">复权</span>
          <button
            v-for="opt in ADJUST_OPTIONS"
            :key="opt.value"
            type="button"
            class="strip-ctl__btn"
            :class="{ 'is-active': adjust === opt.value }"
            @click="adjust = opt.value"
          >
            {{ opt.label }}
          </button>
        </div>
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

    <!-- 4. 主图/副图指标控制栏。周期与复权已移进行情条（见上方 quote-strip__right）——
         这两组很少改，却占了工具栏 6 个按钮，把主图/副图挤成两行还看不全。 -->
    <div class="kline-workspace__toolbar">
      <!-- 主图指标 -->
      <div class="toolbar__group">
        <span class="group__label">主图:</span>
        <button
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': mainMode === 'zettaranc' }"
          @click="setMainMode('zettaranc')"
          title="战法核心主图：快线 DEMA10 + 大哥线 LongBBI(14/28/57/114) + BBI牵牛绳(3/6/12/24)。双线判断多空节奏，牵牛绳是多空分界"
        >
          双线+BBI
        </button>
        <button
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': mainMode === 'all' }"
          @click="setMainMode('all')"
          title="战法核心线 + 传统 MA5/10/20。给短期均线做参考，适合看价格与短期成本的相对位置"
        >
          战法+MA
        </button>
        <button
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': mainMode === 'ma' }"
          @click="setMainMode('ma')"
          title="纯传统均线 MA5/10/20/60/120/250，不叠加战法线。最基础的看图方式"
        >
          传统MA
        </button>
        <button
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': mainMode === 'boll' }"
          @click="setMainMode('boll')"
          title="布林带 BOLL(20,2) + 战法信号层。价格触上轨偏强、触下轨偏弱，带宽收窄常预示变盘"
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
          :title="sub.hint"
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
          title="神奇九转：连续 9 根 K 线的变盘倒数。红色数字在上方代表上涨序列、绿色在下方代表下跌序列，走到 9 时变盘概率最高"
          @click="toggleTD9"
        >
          九转序列
        </button>
        <button
          type="button"
          class="toolbar__btn feature-btn"
          :class="{ 'is-active': isPatternsEnabled }"
          title="形态气泡：在 K 线上标出服务端识别出的形态（阳包阴、乌云压顶、十字星、B1建仓波、S1预警、关键K、暴力K），括号内是本图命中的数量"
          @click="togglePatterns"
        >
          形态气泡<span v-if="filteredAnnotations.length > 0" class="feature-count">({{ filteredAnnotations.length }})</span>
        </button>
      </div>

      <div class="toolbar__spacer" />

      <!-- 画线侧栏切换 -->
      <button
        type="button"
        class="toolbar__btn"
        :class="{ 'is-active': isDrawingBarVisible }"
        title="显示/隐藏左侧画线工具栏（斐波那契、波浪、ABCD 等约 35 个画线工具）"
        @click="toggleDrawingBar"
      >
        画线
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
          分析基本面与估值
        </button>
        <button
          type="button"
          class="action-chip"
          @click="handleActionAsk('strategy')"
        >
          测算防守位与试仓策略
        </button>
        <button
          type="button"
          class="action-chip"
          @click="handleActionAsk('report')"
        >
          查阅最新研报与核心逻辑
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
import { useTheme } from '@/composables/useTheme';
import { ZettarancDatafeed, type Adjust } from './datafeed';
import { setZettarancPalette } from './palette';
import { getKlineChartTheme } from './theme';
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

// 跟随平台主题。此前这里写死 true（"专业深色交易终端风"），结果是 K线面板与
// 浅色聊天区并排时主题割裂——这是"左右样式不统一"里最难改的那一半。
//
// 跟随而不是加独立开关：平台本身已有 light/dark/system 三态，K线再自带一套
// 开关只会制造"两边不同步"的第二真相源。
const { effectiveTheme } = useTheme();
const isDark = computed(() => effectiveTheme.value === 'dark');
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

// 主图模式：双线+BBI、战法+MA、传统均线、布林带
type MainIndicatorMode = 'zettaranc' | 'all' | 'ma' | 'boll';
const mainMode = ref<MainIndicatorMode>('zettaranc');

// 副图模式：双副图(量+ZX砖型推荐)、双副图(量+MACD经典)、单ZX砖型、单成交量、单MACD、单KDJ、RSL
type SubIndicatorMode = 'VOL_AND_BRICK' | 'VOL_AND_MACD' | 'ZX_BRICK' | 'Z_VOL' | 'Z_MACD' | 'Z_KDJ' | 'Z_RSL';
const subMode = ref<SubIndicatorMode>('VOL_AND_BRICK');

// 副图选项。hint 是悬停说明——之前 7 个副图按钮一个 tooltip 都没有，
// 新指标加进来时没人能靠界面搞清楚它算什么，只能一个个试。
const SUB_INDICATOR_LIST = [
  {
    id: 'VOL_AND_BRICK' as const,
    label: '量+ZX砖型 (推荐)',
    hint: '成交量 + 同花顺知行砖型图。砖型把连续同向的 K 线合并成一块，块数代表趋势强度：4 块以上为强势。推荐作为默认副图。',
  },
  {
    id: 'ZX_BRICK' as const,
    label: 'ZX砖型图',
    hint: '仅砖型图，不带成交量。适合专注看多空节奏；减号标记回调、止字标记止跌。',
  },
  {
    id: 'VOL_AND_MACD' as const,
    label: '量+MACD',
    hint: '成交量 + MACD。DIF/DEA 金叉死叉会打标记，红柱绿柱表示动能强弱，适合判断趋势转折。',
  },
  {
    id: 'Z_VOL' as const,
    label: '成交量',
    hint: '成交量柱 + MA5/MA10 均量线。放量上涨代表资金进场，缩量回调代表抛压不重。',
  },
  {
    id: 'Z_MACD' as const,
    label: 'MACD',
    hint: 'MACD (12,26,9)。DIF 上穿 DEA 为金叉、下穿为死叉，柱状体表示动能变化速度。',
  },
  {
    id: 'Z_KDJ' as const,
    label: 'KDJ',
    hint: 'KDJ 随机指标 (9,3,3)。K/D 在 20 以下为超卖区、80 以上为超买区，金叉死叉会打标记。',
  },
  {
    id: 'Z_RSL' as const,
    label: 'RSL强弱',
    hint: '相对强弱线，3 日与 21 日两个周期。RSL 向上表示这只票强于大盘，适合在同板块内比强弱。',
  },
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

  // 1. 严格依据知识库计算双线（DEMA10 / LongBBI）与 BBI
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

  setZettarancPalette(isDark.value);

  chartInstance.value = new KLineChartPro({
    container: chartContainer.value,
    symbol,
    period: PERIODS[periodIdx.value],
    datafeed,
    // 画布**固定深色**，不跟随平台主题。这是"浅色外壳 + 深色画布"的关键。
    //
    // 为什么不让画布跟着变浅：Z_MAIN 的第一条线是 `#FFFFFF` 的"白线"(DEMA 10)，
    // 模式名就叫「白黄+BBI」。浅底上白线直接隐形，而改成深色又会让"白黄"这个
    // 叫法名不副实——那套白线/黄线/牵牛绳是策略词汇的一部分，不该因为换了个
    // 配色就改口径。深色画布还有个实际好处：红绿 K 线、形态气泡在深底上的
    // 对比度本来就比浅底高，改浅反而更难读。
    //
    // 外壳（工具栏、候选池条、行情条、底部快捷条）由 .is-dark 这个 CSS class
    // 画布跟随平台主题。切调色板必须发生在建实例之前：自定义指标是在
    // chartInstance 构建期间注册的，它们把 PAL.* 抄进 styles 配置，事后改
    // PAL 不会回溯已注册的指标。
    styles: getKlineChartTheme(isDark.value),
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
//
// 关键在于**别让用户替 agent 做上下文整理**。原版提示词只给了代码和名字，
// 把界面上已经算好的东西（ZX砖型状态、双线多空、BBI 位置、形态标注）全都丢掉
// 了，agent 只能从零重新查一遍。现在把这些结论直接写进提示词，并点名该用哪个
// 工具——这 20 个金融工具在 UI 上没有勾选框，agent 只能靠工具描述知道它们存在。
const handleActionAsk = (type: 'valuation' | 'strategy' | 'report') => {
  const code = `${currentTicker.value}.${currentExchange.value}`;
  const name = workspace.activePick.value?.name ? `(${workspace.activePick.value.name})` : '';
  // 数据还没加载出来时 latestQuote 是 null，此时只给代码，不编造形态结论。
  const q = latestQuote.value;

  // 界面上已有的形态结论，原样带给 agent，省掉它重复推导。
  const screenContext = q
    ? [
        q.brickText ? `ZX砖型图显示：${q.brickText}` : '',
        q.aboveBbi ? '收盘价站上 BBI 多空平衡线' : '收盘价跌破 BBI 多空平衡线',
        q.aboveYellow
          ? (q.whiteAboveYellow ? '快线 DEMA10 在大哥线 LongBBI 之上（顺大势）' : '价格在大哥线之上但快线在下方（碗内回踩）')
          : '价格已跌破大哥线 LongBBI',
      ].filter(Boolean).join('；')
    : '（K线数据尚未加载完成，请先自行拉取行情）';

  let prompt = '';
  if (type === 'valuation') {
    prompt = `分析个股 ${code} ${name} 的基本面与估值水位。图表当前状态：${screenContext}。\n\n` +
      `请依次完成：\n` +
      `1. 用 hithink.finance.financial.indicator.detail 取最近 4 期财务指标（ROE、毛利率、净利率、资产负债率、流动比率、净利润现金含量），判断盈利能力与偿债能力的趋势方向；\n` +
      `2. 用 hithink.finance.financial.statement.cashflow 看经营活动现金流净额与净利润是否匹配——长期背离说明利润质量存疑；\n` +
      `3. 用 hithink.finance.financial.valuation.snapshot 取 pe_ttm / pe_mrq / pb_mrq / ps_ttm / pcf_ttm，结合行业平均水平判断估值水位是偏高还是偏低；\n` +
      `4. 用 hithink.finance.index.sector.membership 查它所属的行业与概念板块，说明该拿哪个板块做估值对标。\n\n` +
      `最后给出结论：这家公司当前的基本面质地如何，估值是贵还是便宜，值不值得买，以及最关键的风险点。`;
  } else if (type === 'strategy') {
    prompt = `按 Z 哥交易体系评估 ${code} ${name} 当前的操作策略。图表当前状态：${screenContext}。\n\n` +
      `请完成：\n` +
      `1. 用 hithink.finance.analysis.levels 找出关键支撑位与压力位，给出防守止损位（跌破哪个价位必须走）；\n` +
      `2. 用 hithink.finance.analysis.trend 确认当前趋势方向，用 hithink.finance.analysis.volume 判断放量还是缩量；\n` +
      `3. 用 hithink.finance.special.limit_up_pool 查最近是否上过涨停板、是否有连板，判断资金关注度；\n` +
      `4. 结合上面的双线与 BBI 位置，判断当前处于「可试仓」「等回踩」还是「该观望」；\n` +
      `5. 给出具体的试仓比例、加仓触发条件、止损位和目标位。\n\n` +
      `要求给出明确的操作建议，不要模棱两可。`;
  } else if (type === 'report') {
    prompt = `综合 ${code} ${name} 的多源信息做一次完整研判。图表当前状态：${screenContext}。\n\n` +
      `请覆盖四个方面：\n` +
      `1. 行业与题材：用 hithink.finance.index.sector.membership 查所属板块，再用 sector.constituents 列出同板块可比公司，指出这只票在板块内的相对位置；\n` +
      `2. 资金面：用 hithink.finance.special.dragon_tiger.list 查龙虎榜记录与机构净买入，用 limit_up_pool 查涨停与封板情况；\n` +
      `3. 基本面速览：用 hithink.finance.financial.indicator.detail 取 ROE、毛利率、净利润现金含量三项核心指标；\n` +
      `4. 研报观点：在研报知识库中检索该票的最新券商研报，梳理机构核心逻辑与风险提示。\n\n` +
      `最后给出一句话结论：这只票当前的核心矛盾是什么。`;
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

watch(isDark, () => {
  nextTick(() => {
    initChart();
  });
});

// 平台主题切换 → 重建图表。
//
// 画布跟随平台主题后，CSS 不够用了：KLineChart 的 styles（画布底、网格、坐标轴、
// tooltip）和自定义指标线色都得重新算。库的实例只暴露 setTheme/setStyles，没有
// "重算已注册指标配色" 的接口，所以整体重建最不容易漏。

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

  /* 画布底色由 @klinecharts/pro 的 CSS 变量控制，不在它的 Styles 类型里——
     getKlineChartTheme() 配的 grid / candle / crosshair 全都管不到它，canvas 又是
     clearRect 透明绘制的，所以真正露出来的是这里这个变量的值。浅色下不覆盖就是
     纯白 #ffffff，画布再怎么调也是白的（2026-09-27 实测：像素值 #FFFFFF）。
     这里连同文字/边框色一起换成暖米体系，浅色下才不会和暖底打架。 */
  :deep(.klinecharts-pro) {
    --klinecharts-pro-background-color: #FAF7F0;
    --klinecharts-pro-popover-background-color: #FFFDF8;
    --klinecharts-pro-text-color: #2A2520;
    --klinecharts-pro-text-second-color: #6B6259;
    --klinecharts-pro-border-color: #DDD5C6;
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

  /* 周期/复权控件：跟着行情条走的小号分段按钮。沿用 toolbar__btn 的 6px 圆角
     与 12px 字号，让它看起来和工具栏是同一套控件，只是尺寸小一号。 */
  .quote-strip__right {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
  }

  .strip-ctl {
    display: flex;
    align-items: center;
    gap: 3px;
  }

  .strip-ctl__label {
    font-size: 11px;
    color: var(--td-text-color-placeholder, #9ca3af);
    margin-right: 2px;
  }

  .strip-ctl__btn {
    padding: 2px 7px;
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
    }
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
   这样 klinecharts 量到的容器高度与改动前完全一致。

   flex: 1 不能省。根容器是 flex-direction: column，包一层之后 chart-wrap
   成了直接 flex 子元素；少了它就按内容高度塌陷成 0，内层 canvas 量到 0 高
   只画得出坐标轴、画不出 K 线（2026-09-27 实测：日期轴在、蜡烛全无）。

   底色必须跟着画布主题走：写死深色时白底画布四周会露出一圈黑边。 */
.kline-workspace__chart-wrap {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  width: 100%;
  background: #FAF7F0;
}

.is-dark &.kline-workspace__chart-wrap {
  background: #11141a;
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
     只是没有数据。底色跟着画布主题走，否则白底下会糊成一片深灰。 */
  background: rgba(250, 247, 240, 0.88);

  .empty__icon {
    font-size: 32px;
    opacity: 0.6;
    line-height: 1;
  }

  .empty__title {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: #2A2520;
  }

  .empty__hint {
    margin: 0;
    max-width: 420px;
    font-size: 12px;
    line-height: 1.7;
    color: #6B6259;

    b {
      color: #4A4239;
      font-weight: 600;
    }
  }
}

/* 空状态的深色覆盖。基础规则按浅色画布写（平台默认浅色），深色下整体翻转。 */
.is-dark &.kline-workspace__empty {
  background: rgba(17, 20, 26, 0.86);

  .empty__title {
    color: #e5e7eb;
  }

  .empty__hint {
    color: #8b93a3;

    b {
      color: #d1d5db;
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
