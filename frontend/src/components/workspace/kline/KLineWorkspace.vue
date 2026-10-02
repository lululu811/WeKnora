<template>
  <div class="kline-workspace" :class="{ 'is-dark': isDark }">
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
          v-for="preset in MAIN_PRESETS"
          :key="preset.id"
          type="button"
          class="toolbar__btn"
          :class="{ 'is-active': mainMode === preset.id }"
          :title="preset.hint"
          @click="setMainMode(preset.id)"
        >
          {{ preset.label }}
        </button>
      </div>

      <div class="toolbar__divider" />

      <!-- 副图指标 -->
      <div class="toolbar__group">
        <span class="group__label">副图:</span>
        <button
          v-for="sub in SUB_PRESETS"
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
        <LayerFilterDropdown
          :selection="bubbleSelection"
          @toggle="(v) => (bubbleSelection = toggleOption(bubbleSelection, v))"
          @set-all="(all) => (bubbleSelection = all ? selectAllOptions() : clearAllOptions(bubbleOptions))"
          v-model:enabled="isPatternsEnabled"
          :open="openLayerPanel === 'bubbles'"
          @update:open="(v) => (openLayerPanel = v ? 'bubbles' : null)"
          title="形态气泡"
          :options="bubbleOptions"
          empty-text="本图没有识别到形态"
          hint="按类型勾选。战法标注来自 Z 哥战法，蜡烛形态来自 TA-Lib。括号里是「勾中/全部」。"
        >
          <button
            type="button"
            class="toolbar__btn feature-btn"
            :class="{ 'is-active': isPatternsEnabled && bubbleOnCount > 0 }"
            title="形态气泡：在 K 线上标出识别出的蜡烛形态。点开可以按类型勾选要显示哪几种"
          >
            形态气泡<span class="feature-count">({{ bubbleOnCount }}/{{ bubbleOptions.length }})</span>
          </button>
        </LayerFilterDropdown>
        <LayerFilterDropdown
          :selection="outlineSelection"
          @toggle="(v) => (outlineSelection = toggleOption(outlineSelection, v))"
          @set-all="(all) => (outlineSelection = all ? selectAllOptions() : clearAllOptions(outlineOptions))"
          v-model:enabled="isChartPatternsEnabled"
          :open="openLayerPanel === 'outline'"
          @update:open="(v) => (openLayerPanel = v ? 'outline' : null)"
          title="形态轮廓"
          :options="outlineOptions"
          empty-text="本图没有识别到形态"
          hint="自动数浪只是一种可能的数法，请当作参考而非结论。"
        >
          <button
            type="button"
            class="toolbar__btn feature-btn"
            :class="{ 'is-active': isChartPatternsEnabled && outlineOnCount > 0 }"
            title="形态轮廓：把几何形态（头肩顶/双底/三角/楔形/旗形）与艾略特波浪画成轮廓——顶点连成折线，颈线与目标位画成虚线。点开可以选要画哪一种"
          >
            形态轮廓<span class="feature-count">({{ outlineOnCount }}/{{ outlineOptions.length }})</span>
          </button>
        </LayerFilterDropdown>
        <button
          type="button"
          class="toolbar__btn feature-btn"
          :class="{ 'is-active': isLevelsEnabled }"
          title="关键位：在现价上下标出最近的两个支撑与两个阻力（枢轴点 / 斐波那契 / 摆动高低点 / 整数关口，重合的合并）。觉得画面太满时可以关掉"
          @click="toggleLevels"
        >
          关键位
        </button>
        <button
          type="button"
          class="toolbar__btn feature-btn"
          :class="{ 'is-active': isAnchorsEnabled }"
          :title="`回答标记：把这条回答里模型标记的时段与价位画在图上，编号与正文里的 ①②③ 一致（最多常驻 ${MAX_PERSISTENT_ANCHORS} 个）`"
          @click="toggleAnchors"
        >
          回答标记<span v-if="workspace.anchors.value.length > 0" class="feature-count">({{ workspace.anchors.value.length }})</span>
        </button>
      </div>

      <div class="toolbar__spacer" />

      <!-- 折叠工作台：收成右侧窄边而不是销毁，当前股票/指标/周期全部保留。
           面板再打开时仍是原来那只股票，不用重新选；这正是它取代原来那个
           「× 直接 close」的原因——两个按钮干同一件事会让人分不清。 -->
      <button
        type="button"
        class="toolbar__icon-btn"
        :title="workspace.isCollapsed.value ? '展开工作台' : '折叠工作台（保留当前股票）'"
        @click="workspace.toggleCollapsed()"
      >
        <t-icon :name="workspace.isCollapsed.value ? 'chevron-left' : 'chevron-right'" size="16px" />
      </button>
    </div>

    <!-- 4b. 多标的对比条。只在意一组多只票时出现，默认收起成一行摘要。
         点击某一行即切换主图到该标的——它同时是「对比」和「切票」两个入口。 -->
    <KLineCompareBar @select="handleCompareSelect" />

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

      <!-- 5c. 取数失败状态
           必须和上面的"本地无此票数据"分开：那是**代码/数据**的问题，
           这里是**链路/服务**的问题（网关 502、服务未就绪、网络不通）。
           曾经两者被合并成同一句提示，代理层一挂就显示成"本地无此票行情"，
           让人以为是数据没同步，排查方向整个跑偏。 -->
      <div v-else-if="loadError" class="kline-workspace__empty is-error">
        <div class="empty__icon">⚠️</div>
        <p class="empty__title">{{ loadError.symbol }} 行情查询失败</p>
        <p class="empty__hint">
          {{ loadError.message }}<br />
          这是<b>取数链路</b>的问题，不是这只票没有行情数据——数据可能完好。<br />
          可稍后重试；若持续失败，请检查 python-service 容器与 nginx 代理。
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
import { ref, shallowRef, computed, watch, onMounted, onUnmounted, nextTick } from 'vue';
import { useI18n } from 'vue-i18n';
import type { Chart } from 'klinecharts';
import { useAgentWorkspace } from '@/composables/useAgentWorkspace';
import { useTheme } from '@/composables/useTheme';
import type { Adjust, ZettarancDatafeed } from './datafeed';
import {
  createCoreChart,
  destroyChart,
  swapSymbol,
  clearAllOverlays,
  drawPriceLevel,
  drawRangeBand,
  scrollToTimestamp as chartScrollToTimestamp,
  getChartData,
  drawAnchorFocus,
  clearAnchorFocus,
  anchorCanvasBoxes,
  swapIndicators,
} from './core-chart';
import { setZettarancPalette } from './palette';
import { getKlineChartTheme } from './theme';
import KLineCompareBar from './KLineCompareBar.vue';
import { registerZettarancIndicators } from './indicators';
import { MAIN_PRESETS, SUB_PRESETS } from './indicator-meta';
import { fetchAnnotations, type Annotation, PATTERN_CONFIG } from './annotate-api';
import { setGlobalOverlayConfig } from './overlay-drawer';
import LayerFilterDropdown from './LayerFilterDropdown.vue';
import { clearAllOptions, collapseByValue, enabledCount, isOptionEnabled, loadSelection, saveSelection, selectAllOptions, toggleOption, type LayerOption, type LayerSelection } from './layer-selection';
import { fetchChartPatterns, resolvePatternGeometry, resolveCandleMarks, patternsAtBar, samePatternSet, type DrawableCandle, type DrawablePattern } from './chart-patterns';
import { computeLevels, pickChartLevels } from './levels';
import { resolveAnchorsForChart, hitTestAnchor, MAX_PERSISTENT_ANCHORS } from './anchor-render';
import { ActionType, type Coordinate } from 'klinecharts';
import { computeAnchorStats, formatAnchorStats } from './anchor-stats';
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

// 取数失败（网络/网关/服务端故障）时的错误态。必须与 noDataSymbol 分开存：
// 两者触发的是完全不同的问题，合并成一个状态就没法给出正确的下一步指引。
// null 表示当前没有错误。
const loadError = ref<{ symbol: string; message: string } | null>(null);
// 底层 klinecharts Chart 实例（非 Pro 包装）。切换标的/周期时就地复用这个实例。
//
// 用 shallowRef 而不是 ref：ref 会对值做深层响应式代理，Chart 与 ZettarancDatafeed
// 都是带原型方法与内部状态的普通对象，被 Proxy 包一层后方法调用时 this 指向代理、
// 私有字段访问与 instanceof 判断都会出问题。
const chartInstance = shallowRef<Chart | null>(null);
// createCoreChart 内部建好的 datafeed，就地换数据时复用它（保住 404/非JSON/code!=0 三态区分）。
const datafeedRef = shallowRef<ZettarancDatafeed | null>(null);
let resizeObserver: ResizeObserver | null = null;

// 跟随平台主题。此前这里写死 true（"专业深色交易终端风"），结果是 K线面板与
// 浅色聊天区并排时主题割裂——这是"左右样式不统一"里最难改的那一半。
//
// 跟随而不是加独立开关：平台本身已有 light/dark/system 三态，K线再自带一套
// 开关只会制造"两边不同步"的第二真相源。
const { effectiveTheme } = useTheme();
const isDark = computed(() => effectiveTheme.value === 'dark');
const adjust = ref<Adjust>('forward');
const periodIdx = ref(0);

// 神奇九转与形态气泡开关
const isTD9Enabled = ref(true);
const isPatternsEnabled = ref(true);
// 关键位（支撑/阻力）开关。默认打开，但必须能一键关掉——图上已经有形态气泡，
// 再加一组横线在某些行情下会糊住 K 线，用户需要能自己让画面回到干净状态。
const isLevelsEnabled = ref(true);
// 正文锚点开关。与关键位同理：锚点是常驻的，用户要能一键清空画面。
const isAnchorsEnabled = ref(true);

// 主图模式 / 副图模式：全部来自 config/indicators.yaml 的 views 段
// （经由生成的 indicator-meta.ts 读入），代码里不再出现任何指标名字符串。
// 改模式就是改 YAML，然后跑 go test ./internal/indicators/ -run TestGeneratedFrontendModule -update。
type MainIndicatorMode = (typeof MAIN_PRESETS)[number]['id'];
type SubIndicatorMode = (typeof SUB_PRESETS)[number]['id'];

// 默认模式取 YAML 里的第一个（主图 = 双线+BBI，副图 = 量+ZX砖型）。
const mainMode = ref<MainIndicatorMode>(MAIN_PRESETS[0].id);
const subMode = ref<SubIndicatorMode>(SUB_PRESETS[0].id);

// 副图选项的 label / hint / 成员同样来自 indicators.yaml —— 之前 7 个副图按钮
// 一个 tooltip 都没有，hint 是 yaml 里的必填字段，加新指标时漏了就过不了 Validate。

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
// 几何形态与波浪（后端识别，前端只负责画）。和标注一样是"增强信息"，
// 拉不到就是空数组，不影响 K 线本身。
const chartPatternGeometry = ref<DrawablePattern[]>([]);
// 蜡烛形态：后端 TA-Lib 识别的信号（下标已换算）+ 它的形态目录（下拉选项来源）。
const candleMarks = ref<DrawableCandle[]>([]);
const candleCatalog = ref<Array<{ key: string; name: string; desc?: string }>>([]);
// 光标所在的这根 K 线落在哪几个形态里。非空时其余形态压暗 —— 这是「气泡」与
// 「轮廓」之间唯一的联动：两者共同的主语是**当前这根 K 线**，而不是互相映射。
const activePatternNames = ref<string[]>([]);
// 形态轮廓图层总开关。默认打开——它比气泡更有信息量（有形状），但画面满时同样要能关。
const isChartPatternsEnabled = ref(true);

// 两个图层各自的「按项勾选」。存的是**关掉的那些**（见 layer-selection.ts）：
// 没被记过的项默认可见，所以以后新增的形态名不会静默消失。
// 勾选跨切票、跨刷新保留——「不想看旗形」不是针对某一只票的偏好。
// 两个下拉共用「谁开着」这一个状态：各管各的 visible 会同时展开、同 z-index 压住彼此。
const openLayerPanel = ref<'bubbles' | 'outline' | null>(null);
const bubbleSelection = ref<LayerSelection>(loadSelection('bubbles'));
const outlineSelection = ref<LayerSelection>(loadSelection('outline'));
watch(bubbleSelection, (v) => { saveSelection('bubbles', v); syncBubbleTypes(v); }, { deep: true });
watch(outlineSelection, (v) => { saveSelection('outline', v); syncOutline(v); }, { deep: true });

/**
 * 气泡可勾选的全部类型 = 战法标注 4 类 + 蜡烛形态若干类，**分两组**。
 *
 * 蜡烛那些类以前根本不可关——它们硬编码在前端的检测器里，工具栏无从得知。
 * 现在既然后端识别（TA-Lib），类型清单也跟着从**后端目录**来（`candle_catalog`）：
 * 前端自己抄一份的话，后端点新增一种、前端不知道，用户就永远勾不到它。
 */
const bubbleOptions = computed<LayerOption[]>(() => [
  ...Object.entries(PATTERN_CONFIG).map(([value, meta]) => ({
    value,
    label: meta.label,
    desc: meta.desc,
    group: '战法标注',
  })),
  ...candleCatalog.value.map((c) => ({
    value: c.key,
    label: c.name,
    desc: c.desc,
    group: '蜡烛形态',
  })),
]);

/**
 * 轮廓可勾选的就是本图实际识别出的形态——不列当前图没有的，免得勾了不生效。
 *
 * 同名的要压成一项：后端一次可以报出两对同名背离（每种指标报最近两对），
 * 而这一层的勾选按名字存、`v-for` 也按 value 做 key。列表里摆两行一模一样的
 * 「RSI顶背离」不只是难看 —— 它们共用一个 value，勾掉任意一行，另一行也跟着
 * 变，用户看到两个开关其实只有一个。压成一项并在右侧标出「本图 N 处」。
 */
const outlineOptions = computed<LayerOption[]>(() =>
  collapseByValue(chartPatternGeometry.value.map((p) => ({ value: p.name, label: p.name, desc: p.desc }))),
);

const bubbleOnCount = computed(() => enabledCount(bubbleOptions.value, bubbleSelection.value));
const outlineOnCount = computed(() => enabledCount(outlineOptions.value, outlineSelection.value));

/** 把勾选结果推给绘制层。两个图层各写各的键，互不覆盖。 */
const syncBubbleTypes = (sel: LayerSelection) => {
  setGlobalOverlayConfig({ hiddenPatternTypes: sel.disabled });
  repaintOverlay();
};

const syncOutline = (sel: LayerSelection) => {
  setGlobalOverlayConfig({
    patternGeometry: isChartPatternsEnabled.value
      ? chartPatternGeometry.value.filter((p) => isOptionEnabled(sel, p.name))
      : [],
  });
  repaintOverlay();
};

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
  return annotations.value.filter((ann) => isOptionEnabled(bubbleSelection.value, ann.type));
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

// 计算主图指标列表。成员取自 indicators.yaml 的 views.main_presets。
const getMainIndicators = () => {
  const preset = MAIN_PRESETS.find((p) => p.id === mainMode.value);
  return preset ? [...preset.indicators] : [];
};

// 计算副图指标列表（严格控制在1~2个，确保蜡烛图主图饱满）。成员取自
// indicators.yaml 的 views.sub_presets。
const getSubIndicators = () => {
  const preset = SUB_PRESETS.find((p) => p.id === subMode.value);
  return preset ? [...preset.indicators] : [];
};

// 换指标：就地增删，不重建图表。此前每次切指标都整图重建，用户刚缩放好的
// 区间和刚画的标注会被清零。
const setMainMode = (mode: MainIndicatorMode) => {
  if (mode === mainMode.value) return;
  mainMode.value = mode;
  swapIndicators(chartInstance.value, getMainIndicators(), getSubIndicators());
};

const switchSubMode = (mode: SubIndicatorMode) => {
  if (mode === subMode.value) return;
  subMode.value = mode;
  swapIndicators(chartInstance.value, getMainIndicators(), getSubIndicators());
};

const toggleLevels = () => {
  isLevelsEnabled.value = !isLevelsEnabled.value;
  // 关键位是 overlay（不是 canvas 自绘），开关后要重画一次：
  // 关掉时把已有的线清掉，打开时按当前数据重新算。
  redrawOverlays(chartInstance.value);
};

// 让主图重画一次。
//
// 这五个开关以前都是 `window.dispatchEvent(new Event('resize'))`。那条路在绕过
// klinecharts Pro 之后已经不存在了（见 onMounted 里 resizeObserver 的注释），
// 派发出去没有任何人监听 —— 于是开关改了 `globalOverlayConfig`、画布却纹丝不动：
// 九转、形态气泡、形态轮廓全都是「点了没反应」，要等一次无关的重绘（比如切副图）
// 才一起生效。核心 Chart 有自己的 resize()，直接调它。
const repaintOverlay = () => {
  chartInstance.value?.resize();
};

const toggleTD9 = () => {
  isTD9Enabled.value = !isTD9Enabled.value;
  setGlobalOverlayConfig({ showTD9: isTD9Enabled.value });
  repaintOverlay();
};

// 图层总开关由下拉面板里的「显示本层」驱动（v-model:enabled），这里只负责
// 把变化推给绘制层。
watch(isPatternsEnabled, (on) => {
  setGlobalOverlayConfig({ showPatterns: on });
  repaintOverlay();
});

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
  // 有数据了，清掉上一只票可能残留的空状态/错误态。
  noDataSymbol.value = '';
  loadError.value = null;
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

  // 数据到位后重画水平位。关键位是从这批 K 线算出来的，必须在数据进来之后
  // 才有意义——换标的时 swapSymbol 会先清空 overlay，若不等数据到达就画，
  // 算出来的会是上一只票的价位。
  redrawOverlays(chartInstance.value);

  // 形态也挂在这里，理由同上：后端给的是日期，换算成图上下标要拿这批 bar。
  // 放在 watch 里会在数据到达前就换算，形态会落到上一只票的坐标上。
  loadChartPatterns();
};

// 加载形态标注
const loadAnnotations = async () => {
  if (!currentTicker.value || !currentExchange.value) return;
  try {
    const symbolStr = `${currentTicker.value}.${currentExchange.value}`;
    const res = await fetchAnnotations(symbolStr, 120);
    annotations.value = res.annotations || [];
    // 标注全量交给绘制层，按类型过滤统一走 hiddenPatternTypes —— 一条过滤路径，
    // 不会出现「计数滤了一种、绘制滤了另一种」的分叉。
    setGlobalOverlayConfig({
      backendAnnotations: annotations.value,
      hiddenPatternTypes: bubbleSelection.value.disabled,
    });
    // 标注也画成水平位，和关键位共用同一批 overlay，必须一起重画。
    redrawOverlays(chartInstance.value);
    repaintOverlay();
  } catch (err) {
    annotations.value = [];
  }
};

// 加载几何形态与波浪（后端 /api/chart-pattern）
//
// 必须等 K 线到位后再拉：后端给的是**日期**，换算成图上下标要拿当前这批 bar，
// 换标的时若不等数据到达就换算，形态会落到上一只票的坐标上。
const loadChartPatterns = async () => {
  if (!currentTicker.value || !currentExchange.value) return;
  const symbolStr = `${currentTicker.value}.${currentExchange.value}`;
  const res = await fetchChartPatterns(symbolStr, 250);
  const bars = getChartData(chartInstance.value);
  chartPatternGeometry.value = resolvePatternGeometry(res, bars);
  candleMarks.value = resolveCandleMarks(res, bars);
  // 目录由后端给，且**只在拿到时覆盖**：拉失败时保留上一次的目录，
  // 否则一次网络抖动就会把下拉清空，用户会以为是自己点坏了。
  if (res?.candle_catalog?.length) {
    candleCatalog.value = res.candle_catalog
      .filter((c): c is { key: string; name: string; desc?: string } => Boolean(c?.key && c?.name))
      .map((c) => ({ key: c.key, name: c.name, desc: c.desc }));
  }
  setGlobalOverlayConfig({
    candleMarks: candleMarks.value,
    hiddenPatternTypes: bubbleSelection.value.disabled,
  });
  // 形态整批换了，光标位置对应的命中集合也要重算 —— 否则会残留上一只票的压暗。
  activePatternNames.value = [];
  syncOutline(outlineSelection.value);
};

watch(isChartPatternsEnabled, () => syncOutline(outlineSelection.value));
// 建图（首次）与整体换主题时才走这里。
//
// 换标的/换周期**不再**走这里 —— 那两条路径改为就地换数据（swapSymbol），
// 以保住用户的缩放级别、十字光标位置和已画标注。此前任何一项变化都整图重建，
// 等于每次切票都把用户的研究状态清零。
const initChart = () => {
  if (!chartContainer.value) return;

  if (chartInstance.value) {
    destroyChart(chartInstance.value, chartContainer.value);
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

  setZettarancPalette(isDark.value);

  const handle = createCoreChart({
    container: chartContainer.value,
    symbol,
    period: PERIODS[periodIdx.value],
    adjust: adjust.value,
    // 画布**固定深色**，不跟随平台主题。这是"浅色外壳 + 深色画布"的关键。
    //
    // 为什么不让画布跟着变浅：Z_MAIN 的第一条线是 `#FFFFFF` 的"白线"(DEMA 10)，
    // 模式名就叫「白黄+BBI」。浅底上白线直接隐形，而改成深色又会让"白黄"这个
    // 叫法名不副实——那套白线/黄线/牵牛绳是策略词汇的一部分，不该因为换了个
    // 配色就改口径。深色画布还有个实际好处：红绿 K 线、形态气泡在深底上的
    // 对比度本来就比浅底高，改浅反而更难读。
    //
    // 外壳（工具栏、候选池条、行情条、底部快捷条）由 .is-dark 这个 CSS class
    // 跟随平台主题。切调色板必须发生在建实例之前：自定义指标是在建实例期间
    // 注册的，它们把 PAL.* 抄进 styles 配置，事后改 PAL 不会回溯已注册的指标。
    styles: getKlineChartTheme(isDark.value),
    mainIndicators: getMainIndicators(),
    subIndicators: getSubIndicators(),
    onDataLoaded: handleDataLoaded,
    onNoData: () => {
      noDataSymbol.value = `${currentTicker.value}.${currentExchange.value}`;
      loadError.value = null;
    },
    onError: (message) => {
      loadError.value = { symbol: `${currentTicker.value}.${currentExchange.value}`, message };
      noDataSymbol.value = '';
    },
  });

  chartInstance.value = handle.chart;
  datafeedRef.value = handle.datafeed;
};

// 对比条点击某一行 -> 切主图过去。
// 复用 setActiveThscode 而不是自己改 activeIndex：那里已经处理了
// 「picks 里没有这个代码就先插进去」的分支，与正文 ticker 点击走的是同一条路径。
const handleCompareSelect = (thscode: string) => {
  // 用户显式选了对比条里的某一行 -> 本轮不再自动切图。
  workspace.markUserPick(thscode);
  workspace.setActiveThscode(thscode);
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

// 换标的 / 换复权 / 换周期：就地换数据，不重建实例。
//
// 三个来源合成一个 watch，因为它们对图表的影响是同一种——换一批 K 线。分开写会
// 在快速连点时互相打架（一次切票 + 一次切周期触发两次换数据）。标的与复权变化
// 还需要重新拉标注（标注跟着标的价格走）。
watch([currentTicker, currentExchange, adjust, periodIdx], async () => {
  if (!chartInstance.value || !datafeedRef.value) {
    // 还没建图（首帧）就整体建一次。
    nextTick(() => {
      initChart();
      loadAnnotations();
    });
    return;
  }
  await nextTick();
  const symbol: SymbolInfo = {
    exchange: currentExchange.value,
    market: 'stocks',
    name: currentStockName.value,
    shortName: currentStockName.value,
    ticker: currentTicker.value,
    priceCurrency: 'cny',
    type: 'stock',
  };
  try {
    await swapSymbol(chartInstance.value, datafeedRef.value, symbol, PERIODS[periodIdx.value]);
    await loadAnnotations();
  } catch (err) {
    loadError.value = {
      symbol: `${currentTicker.value}.${currentExchange.value}`,
      message: err instanceof Error ? err.message : String(err),
    };
  }
});

// 平台主题切换 → 就地换 styles，不重建。
//
// 重建的理由是「自定义指标在建实例时把 PAL.* 抄进了 styles 配置，改调色板不会
// 回溯已注册指标」。但 setZettarancPalette 是可变的全局单例，而指标 draw 时
// 通过 PAL() 实时读取（见 palette.ts 的 `PAL()` 调用模式），所以切主题时先换
// 调色板再 setStyles 即可让已注册指标跟着变色。重建反而会丢用户的缩放与标注。
watch(isDark, () => {
  setZettarancPalette(isDark.value);
  chartInstance.value?.setStyles(getKlineChartTheme(isDark.value));
});

// 正文日期 → 图上区间。这是「聊天 → 图表」第二条通道。
//
// 降级链条是刻意收紧的，原则是**宁可少画，不可画错**：
//   1. 完整日期（from/to）→ 直接滚过去，按该区间的价格上下沿画带；
//   2. 只有 md（缺年份的「5月20日」）→ 在已加载数据里找当年最近的一根；
//   3. 换算不出任何一根 → 什么都不做（不滚、不画），由正文标记自身的 title 兜底。
// 绝不猜年份：「5月20日」在一段 2024 年的分析里可能指完全不同的两段行情。
const resolveTimestamp = (value: number | undefined, md: string | undefined): number | null => {
  const bars = getChartData(chartInstance.value);
  if (bars.length === 0) return null;
  if (value !== undefined) return value;
  if (!md) return null;
  const [mm, dd] = md.split('-');
  if (!mm || !dd) return null;
  // 优先落在与当前数据末根同一年，避免跨年错配。
  const lastYear = new Date(bars[bars.length - 1].timestamp).getUTCFullYear();
  let best: number | null = null;
  for (const bar of bars) {
    const d = new Date(bar.timestamp);
    if (d.getUTCMonth() + 1 !== Number(mm) || d.getUTCDate() !== Number(dd)) continue;
    if (d.getUTCFullYear() !== lastYear) continue;
    if (best === null) best = bar.timestamp;
  }
  return best;
};

/** 区间带上的日期文字。两端同一根时不写成「A ~ A」。 */
const formatFocusLabel = (from: number, to: number): string => {
  const fmt = (ts: number) => {
    const d = new Date(ts);
    const y = d.getUTCFullYear();
    const m = String(d.getUTCMonth() + 1).padStart(2, '0');
    const day = String(d.getUTCDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  };
  const a = fmt(from);
  const b = fmt(to);
  return a === b ? a : `${a} ~ ${b}`;
};

const handleChartFocus = (range: { from?: number; to?: number; md?: string }) => {
  const chart = chartInstance.value;
  if (!chart) return;
  // 先回到"干净的关键位 + 标注"状态，再叠区间带/单点位——否则会把上一次
  // 聚焦留下的带子留在图上，与新的区间叠成两条。
  redrawOverlays(chart);

  const from = resolveTimestamp(range.from, range.md);
  const to = resolveTimestamp(range.to, undefined);
  if (from === null) return;

  if (to !== null && to > from) {
    chartScrollToTimestamp(chart, from);
    // 区间带的上下沿取该段真实价格包络，不是日期对应的单一收盘价——
    // 否则带子可能整个落在 K 线实体之外，看上去像空画一块。
    const bars = getChartData(chart)
    const inRange = bars.filter((b) => b.timestamp >= from && b.timestamp <= to)
    if (inRange.length > 0) {
      drawRangeBand(
        chart,
        'focus_range',
        // 左右边界跟着日期走，上下沿取该段真实价格包络——两个维度都要给，
        // 否则带子会横贯整个时间轴，表达的就成了「这个价位区间」而不是「这段时间」。
        from,
        to,
        Math.min(...inRange.map((b) => b.low)),
        Math.max(...inRange.map((b) => b.high)),
        // 带子上写日期，与正文里那串日期是同一份数据——联动的可读性主要靠
        // 「左边写的是哪段、右边画的就是哪段」这个对应关系能被一眼看出来。
        formatFocusLabel(from, to),
      )
    }
    return
  }

  // 单点：标一根水平位，再把图滚过去。
  const bar = getChartData(chart).find((b) => b.timestamp === from)
  if (bar) {
    drawPriceLevel(chart, 'focus_level', bar.close)
    chartScrollToTimestamp(chart, from)
  }
};

/**
 * 支撑/阻力位。来自本地按后端同一套公式算出的关键位（见 levels.ts）。
 *
 * **每侧只画一条：最近的那个支撑、最近的那个阻力。**
 *
 * 这个数字是压出来的，不是拍的。实测五粮液：现价 70.06 时，关键位是
 * 68.87 / 70.00 / 70.79 / 72.45 —— 四个价位挤在 3.6 个价格单位里。每侧两条
 * 会在十几个像素内叠成一条看不清的色带（第一版就是这样，用户反馈「密密麻麻」）。
 *
 * 而且每侧第二条的价值本来就低：可操作的是**最近**的支撑与阻力，再往外的那条
 * 既不改变决策，也不比第一条更准。支撑与阻力分属两个列表、彼此不参与合并，
 * 所以只能靠减少条数来解决聚集。
 *
 * 重合的位仍然合并（70.00 那条同时是整数关口、枢轴点 PP 和两个摆动低点）。
 */
const LEVELS_PER_SIDE = 1;

/**
 * 画到图上时的价位合并容差（占现价的比例）。
 *
 * 比 `pickChartLevels` 的默认值（0.5%）宽得多，因为这是**画线**不是**列清单**：
 * 现价落在关键位密集区时（实测五粮液：69.33 / 70.00 / 70.79 / 71.53 挤在 2.2 个
 * 价位里），0.5% 的容差会留下 4 条线加 4 个价格标签，在十几个像素里叠成一条
 * 看不清的色带。1.5% 把这一簇收成一条代表线——一簇价位画一条，比画四条重叠线
 * 更能说明「这里是一个支撑/阻力区」。
 */
const LEVEL_MERGE_TOLERANCE = 0.015;

/**
 * 两条价位线在屏幕上挨得比这个还近，就当成同一条合并掉。
 *
 * 判定用像素而不是百分比：用户看到的是像素。实测 7.85 与 7.98 相差 1.6%，
 * 百分比容差放它们过去，而屏幕上只差 5px——看着就是一条渲染故障的双虚线。
 */
const LEVEL_MERGE_PIXELS = 10;

/**
 * 收集所有水平价位 —— **关键位与锚点价位合并后再画**。
 *
 * 之前两者各画各的，于是同一个价位会出现两条线：实测天顺风能 002531.SZ，
 * 关键位算出 8.00（整数关口）、锚点说 7.98（强支撑共振），相差 0.25%，
 * 在图上叠成一条看着像渲染故障的「双虚线」。用户的原话是「支撑线太乱」。
 *
 * 合并规则：
 *  - 价位相差在容差内视为同一条线，只画一次；
 *  - **代表价取锚点的那个数**——用户读的是模型说的 7.98，不是算法算的 8.00；
 *  - 锚点带来的编号徽章与标签优先保留（那是联动的可见部分）。
 */
interface LevelCandidate {
  price: number;
  badge?: number;
  label?: string;
  sources: string[];
}

const collectLevelCandidates = (chart: Chart): LevelCandidate[] => {
  const bars = getChartData(chart);
  const out: LevelCandidate[] = [];

  if (isLevelsEnabled.value) {
    const computed = computeLevels(bars);
    if (computed) {
      for (const lv of pickChartLevels(computed, LEVELS_PER_SIDE, LEVEL_MERGE_TOLERANCE)) {
        out.push({ price: lv.price, sources: lv.sources });
      }
    }
  }

  if (isAnchorsEnabled.value) {
    for (const a of resolveAnchorsForChart(workspace.anchors.value, bars)) {
      if (!a.resolvable || a.kind !== 'level' || a.value === undefined) continue;
      out.push({ price: a.value, badge: a.index, label: a.label, sources: [] });
    }
  }

  // 按**屏幕像素距离**合并，而不是按百分比。
  //
  // 百分比容差与要解决的问题不匹配：实测 7.85（Pivot_PP）与 7.98（锚点）相差
  // 1.6%，刚好越过 1.5% 的容差没被合并，而在屏幕上它们只差约 5px——看着就是
  // 一条渲染出错的"双虚线"。反过来，对一只 100 元的票，2% 就是 2 元，那可能是
  // 两个真的不同的价位。用户看到的是像素，判定就该用像素。
  const yOf = (price: number): number | null => {
    try {
      const pts = chart.convertToPixel([{ dataIndex: 0, value: price }], { paneId: 'candle_pane' }) as Array<Partial<Coordinate>>;
      return pts[0]?.y ?? null;
    } catch {
      return null;
    }
  };

  const sorted = [...out].sort((a, b) => b.price - a.price);
  const merged: LevelCandidate[] = [];
  const mergedY: number[] = [];
  for (const c of sorted) {
    const y = yOf(c.price);
    const hitIdx = y === null ? -1 : mergedY.findIndex((my) => Math.abs(my - y) <= LEVEL_MERGE_PIXELS);
    if (hitIdx < 0) {
      merged.push({ ...c, sources: [...c.sources] });
      mergedY.push(y ?? Number.NaN);
      continue;
    }
    const hit = merged[hitIdx];
    hit.sources.push(...c.sources);
    if (c.badge !== undefined) {
      if (hit.badge === undefined) {
        // 锚点接管这条线：价与标签都以它为准（用户读的是模型说的数）。
        hit.badge = c.badge;
        hit.label = c.label;
        hit.price = c.price;
      } else if (c.label && hit.label !== c.label) {
        hit.label = `${hit.label} · ${c.label}`;
      }
    }
  }
  return merged;
};

/**
 * 正文锚点在图上的常驻呈现。
 *
 * 与「关键位」是两种东西，刻意分开：
 *  - 关键位是**算法从 K 线算出来的**支撑阻力，与对话无关；
 *  - 锚点是**模型在这条回答里主张的**时段与价位，带它的原话标签。
 * 两者都会画水平线，所以视觉上必须能分辨——锚点带编号圆徽章，关键位不带。
 *
 * 上限与截断在 `anchor-render.ts` 里（纯函数，有单测）；这里只管画。
 */
const drawAnchors = (chart: Chart | null) => {
  if (!chart || !isAnchorsEnabled.value) return;
  const renderable = resolveAnchorsForChart(workspace.anchors.value, getChartData(chart));
  for (const anchor of renderable) {
    if (!anchor.resolvable) continue;
    // 价位锚点不在这里画——它要跟关键位合并成一条线（见 collectLevelCandidates），
    // 否则同一个价位会画出两条重叠的线。
    if (anchor.kind === 'level') continue;
    if (
      anchor.kind === 'range' &&
      anchor.startIndex !== undefined &&
      anchor.endIndex !== undefined &&
      anchor.low !== undefined &&
      anchor.high !== undefined
    ) {
      drawRangeBand(
        chart,
        `anchor_rg_${anchor.index}`,
        anchor.startIndex,
        anchor.endIndex,
        anchor.low,
        anchor.high,
        anchor.label,
        anchor.index,
      );
    }
  }
};

/**
 * hover 某个时段锚点：框外压暗 + 框内叠统计。
 *
 * 只在 hover 期间存在（离开即撤）——常驻压暗会把整张图长期灰掉一半，
 * 是最毁观感的做法；而它要起的作用是「瞬间把注意力拉过去」，瞬时态就够了。
 *
 * 价格锚点（水平线）不做压暗：一条横贯全图的线没有"框外"可言，
 * 压暗整张图来表达"看这条线"是反效果。
 */
const applyAnchorHover = (chart: Chart | null) => {
  if (!chart) return;
  clearAnchorFocus(chart);
  const idx = workspace.hoveredAnchorIndex.value;
  if (idx === null || !isAnchorsEnabled.value) return;
  const bars = getChartData(chart);
  const anchor = resolveAnchorsForChart(workspace.anchors.value, bars).find((a) => a.index === idx);
  if (!anchor || !anchor.resolvable) return;
  if (
    anchor.kind !== 'range' ||
    anchor.startIndex === undefined ||
    anchor.endIndex === undefined ||
    anchor.low === undefined ||
    anchor.high === undefined
  ) {
    return;
  }
  const stats = computeAnchorStats(bars, anchor.startIndex, anchor.endIndex);
  drawAnchorFocus(
    chart,
    anchor.startIndex,
    anchor.endIndex,
    anchor.low,
    anchor.high,
    formatAnchorStats(stats),
  );
};

/**
 * 光标在图上移动时，判断它是否落在某个锚点上。
 *
 * 命中几何每帧重算（可见区间、缩放都会变），但只在有锚点时才算——
 * 没有锚点就没有反向联动的对象，省掉 convertToPixel 的开销。
 */
const applyChartHoverHit = (data: { dataIndex?: number; y?: number } | null | undefined) => {
  const chart = chartInstance.value;
  syncPatternHighlight(data?.dataIndex);
  if (!chart || !isAnchorsEnabled.value) return;
  const anchors = resolveAnchorsForChart(workspace.anchors.value, getChartData(chart));
  const boxes = anchorCanvasBoxes(chart, anchors);
  const index = hitTestAnchor(boxes, data?.dataIndex ?? NaN, data?.y ?? NaN);
  if (index !== workspace.hoveredAnchorIndex.value) {
    workspace.setHoveredAnchor(index);
  }
};

/**
 * 光标 -> 点亮包含这根 K 线的形态。
 *
 * **只有集合真的变了才重绘**：crosshair 每秒来几十次，而形态动辄跨 20~40 根，
 * 绝大多数移动落在同一个形态区间内、集合不变。不判这一下就等于每帧重绘整张图。
 */
const syncPatternHighlight = (dataIndex: number | undefined) => {
  const next = Number.isFinite(dataIndex as number)
    ? patternsAtBar(chartPatternGeometry.value, dataIndex as number)
    : [];
  if (samePatternSet(next, activePatternNames.value)) return;
  activePatternNames.value = next;
  setGlobalOverlayConfig({ activePatternNames: next });
  repaintOverlay();
};

const handleChartMouseLeave = () => {
  workspace.setHoveredAnchor(null);
  // 移出图区要清掉压暗。crosshair 不一定补一次事件，不兜底的话压暗会一直挂着，
  // 用户会以为形态坏了（锚点那套同样有这个兜底）。
  syncPatternHighlight(undefined);
};

const toggleAnchors = () => {
  isAnchorsEnabled.value = !isAnchorsEnabled.value;
  redrawOverlays(chartInstance.value);
};

/**
 * 点击正文锚点 -> 图滚过去。
 *
 * 用锚点自己算好的区间，而不是重新按日期找——两边用同一份换算结果，
 * 才不会出现「框画在这儿、滚到那儿」的错位。
 */
const handleAnchorFocus = (index: number) => {
  const chart = chartInstance.value;
  if (!chart) return;
  const anchor = resolveAnchorsForChart(workspace.anchors.value, getChartData(chart)).find(
    (a) => a.index === index,
  );
  if (!anchor || !anchor.resolvable) return;
  if (anchor.kind !== 'range' || anchor.startIndex === undefined) return;
  const data = getChartData(chart);
  const ts = data[anchor.startIndex]?.timestamp;
  if (typeof ts === 'number') chartScrollToTimestamp(chart, ts);
};

/**
 * 重画全部水平位。
 *
 * 这是唯一的入口：`handleChartFocus` 与数据加载都调它，避免"某个路径忘了清"
 * 这类只在特定操作顺序下出现的残留。
 *
 * 注意这里**不画服务端形态标注**。那些标注是按日期锚定的形态（早晨之星、
 * 关键K…），已经由 overlay-drawer 画成 K 线上的胶囊徽章；把它们同时画成
 * 横向价格线是错的——一条横线表达的是「这个价位有意义」，而一个日期上的形态
 * 跟水平价位没有任何关系。之前那版就是这么画的，等于把同一批数据画了两遍，
 * 还是错的那种画法。
 */
const redrawOverlays = (chart: Chart | null) => {
  if (!chart) return;
  clearAllOverlays(chart);
  // 顺序：先画区间框（面积大、在下层），再把合并后的价位线叠上去。
  drawAnchors(chart);
  for (const level of collectLevelCandidates(chart)) {
    drawPriceLevel(chart, `lvl_${level.price.toFixed(2)}`, level.price, level.badge);
  }
  // 聚焦层也归这里管：redraw 会把它一起清掉，所以必须紧接着按当前 hover 复原，
  // 否则「hover 时恰好发生一次数据刷新」会把压暗弄丢。
  applyAnchorHover(chart);
};

// 锚点集合变化（切回答/回答完成）就重画。整组替换而不是累积——见工作台里的说明。
watch(() => workspace.anchors.value, () => {
  nextTick(() => redrawOverlays(chartInstance.value));
});

// hover 态只管聚焦层，不走整张重画——压暗要跟手，重建全部 overlay 会顿。
watch(() => workspace.hoveredAnchorIndex.value, () => {
  applyAnchorHover(chartInstance.value);
});

onMounted(() => {
  workspace.registerChartFocus(handleChartFocus);
  workspace.registerAnchorFocus(handleAnchorFocus);
  // 反向联动：图上 hover 锚点 -> 走同一个 hoveredAnchorIndex，正文据此高亮。
  //
  // 用 crosshair 事件自己算命中，而不是库的 overlay figure 事件——后者在这套
  // 配置下实测不触发（lock 并非原因），排障要钻进库内部的命中判定。
  // crosshair 稳定给出 dataIndex 与画布 y，判定逻辑是纯函数、有单测。
  chartInstance.value?.subscribeAction(ActionType.OnCrosshairChange, (data) => {
    applyChartHoverHit(data);
  });
  // crosshair 在鼠标移出图区时不一定补一次事件，这里兜底清空——
  // 否则压暗会一直挂着，用户以为图坏了。
  chartContainer.value?.addEventListener('mouseleave', handleChartMouseLeave);

  setGlobalOverlayConfig({
    showTD9: isTD9Enabled.value,
    showPatterns: isPatternsEnabled.value,
    patternGeometry: isChartPatternsEnabled.value ? chartPatternGeometry.value : [],
    candleMarks: candleMarks.value,
    activePatternNames: activePatternNames.value,
    // 恢复上次的勾选，否则首帧会把用户关掉的类型又画出来一次。
    hiddenPatternTypes: bubbleSelection.value.disabled,
  });

  nextTick(() => {
    initChart();
    loadAnnotations();
  });

  // 容器尺寸自适应（拖动分栏滑块时）。核心 Chart 有真正的 resize()，
  // 不必再靠派发 window resize 让 Pro 内部的 observer 兜底——那条路径在
  // 绕过 Pro 之后已经不存在了。
  if (chartContainer.value) {
    resizeObserver = new ResizeObserver(() => {
      chartInstance.value?.resize();
    });
    resizeObserver.observe(chartContainer.value);
  }

  window.addEventListener('keydown', handleKeyDown);
});

onUnmounted(() => {
  workspace.registerChartFocus(null);
  workspace.registerAnchorFocus(null);
  chartContainer.value?.removeEventListener('mouseleave', handleChartMouseLeave);
  window.removeEventListener('keydown', handleKeyDown);
  if (resizeObserver) {
    resizeObserver.disconnect();
    resizeObserver = null;
  }
  destroyChart(chartInstance.value, chartContainer.value);
  chartInstance.value = null;
  datafeedRef.value = null;
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

  /* 画布底色：以前来自 @klinecharts/pro 的 CSS 变量（core 的 Styles 里没有
     background 字段，canvas 同样是透明 clearRect 绘制，底色全靠容器透出来）。
     绕过 Pro 后改由这里提供，值与 theme.ts 的 bgColor/surfaceColor 保持一致：
     浅色是暖米 #FAF7F0 而不是纯白（纯白大面积长时间看刺眼）。 */
  .kline-workspace__chart {
    background: #FAF7F0;

    :root[theme-mode="dark"] &,
    .is-dark & {
      background: #11141a;
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
  border-bottom: 1px solid var(--td-component-stroke);
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
    font-size: var(--app-text-xs);
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
    border-radius: var(--app-radius-sm);
    border: 1px solid var(--td-component-stroke);
    background: transparent;
    color: inherit;
    font-size: var(--app-text-sm);
    cursor: pointer;
    white-space: nowrap;
    transition: all var(--app-motion-fast) ease;

    .is-dark & {
      border-color: #333d4d;
    }

    &:hover {
      border-color: var(--td-brand-color);
    }

    &.is-active {
      background: var(--td-brand-color);
      border-color: var(--td-brand-color);
      color: #ffffff;
      font-weight: 500;
    }

    .tab__code {
      font-family: monospace;
    }

    .tab__tag {
      font-size: var(--app-text-2xs);
      padding: 0 4px;
      border-radius: var(--app-radius-xs);
      background: rgba(255, 255, 255, 0.2);
    }
  }

  .picks-bar__hint {
    font-size: var(--app-text-2xs);
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
  border-bottom: 1px solid var(--td-component-stroke);
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
    font-size: var(--app-text-xs);
    color: var(--td-text-color-placeholder);
    margin-right: 2px;
  }

  .strip-ctl__btn {
    padding: 2px 7px;
    font-size: var(--app-text-sm);
    border-radius: var(--app-radius-sm);
    border: 1px solid var(--td-component-stroke);
    background: transparent;
    color: inherit;
    cursor: pointer;
    transition: all var(--app-motion-fast) ease;
    white-space: nowrap;

    .is-dark & {
      border-color: #333d4d;
    }

    &:hover {
      border-color: var(--td-brand-color);
    }

    &.is-active {
      background: var(--td-brand-color);
      border-color: var(--td-brand-color);
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
    border-radius: var(--app-radius-xs);
    transition: background var(--app-motion-fast) ease;

    &:hover {
      background: rgba(128, 128, 128, 0.15);

      .search-hint-icon {
        opacity: 1;
        color: var(--td-brand-color);
      }
    }

    .quote__name {
      font-size: var(--app-text-base);
      font-weight: 700;
    }

    .quote__symbol {
      font-size: var(--app-text-xs);
      color: var(--td-text-color-placeholder);
      font-family: monospace;
    }

    .search-hint-icon {
      opacity: 0.4;
      transition: all var(--app-motion-fast) ease;
    }
  }

  .quote__price {
    font-size: var(--app-text-xl);
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
    font-size: var(--app-text-xs);
    font-weight: 600;
    font-family: monospace;
    padding: 1px 6px;
    border-radius: var(--app-radius-xs);

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
    border-radius: var(--app-radius-lg);
    font-size: var(--app-text-xs);
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
    font-size: var(--app-text-xs);
    color: var(--td-text-color-placeholder);
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
  background: var(--td-bg-color-container);
  border-radius: var(--app-radius-sm);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  border: 1px solid var(--td-component-stroke);
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
    border-bottom: 1px solid var(--td-component-stroke);

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
    font-size: var(--app-text-sm);
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
    font-size: var(--app-text-xs);
    color: var(--td-text-color-placeholder);
  }

  .search-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 7px 12px;
    font-size: var(--app-text-sm);
    cursor: pointer;
    transition: background var(--app-motion-fast) ease;

    &:hover {
      background: rgba(59, 130, 246, 0.15);
    }

    .search-item__name {
      font-weight: 500;
    }

    .search-item__code {
      font-family: monospace;
      color: var(--td-text-color-placeholder);
      font-size: var(--app-text-xs);
    }
  }
}

/* 综合控制工具栏 */
.kline-workspace__toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-container);
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
    font-size: var(--app-text-xs);
    font-weight: 600;
    color: var(--td-text-color-placeholder);
    margin-right: 2px;
  }

  .toolbar__divider {
    width: 1px;
    height: 14px;
    background: var(--td-component-stroke);
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
    font-size: var(--app-text-sm);
    border-radius: var(--app-radius-sm);
    border: 1px solid var(--td-component-stroke);
    background: transparent;
    color: inherit;
    cursor: pointer;
    transition: all var(--app-motion-fast) ease;
    white-space: nowrap;

    .is-dark & {
      border-color: #333d4d;
    }

    &:hover {
      border-color: var(--td-brand-color);
    }

    &.is-active {
      background: var(--td-brand-color);
      border-color: var(--td-brand-color);
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
        font-size: var(--app-text-2xs);
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
    border-radius: var(--app-radius-xs);

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
    font-size: var(--app-text-display);
    opacity: 0.6;
    line-height: 1;
  }

  .empty__title {
    margin: 0;
    font-size: var(--app-text-base);
    font-weight: 600;
    color: #2A2520;
  }

  .empty__hint {
    margin: 0;
    max-width: 420px;
    font-size: var(--app-text-sm);
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

/* 错误态：与"无数据"在视觉上也要能一眼分开。
   只靠文案区分不够——用户扫一眼标题时，"这只票没数据"和"服务挂了"
   需要在第一眼就是两件事。图标不降透明度，标题用告警色。 */
&.kline-workspace__empty.is-error {
  .empty__icon {
    opacity: 1;
  }

  .empty__title {
    color: #B45309;
  }

  .empty__hint {
    max-width: 460px;
  }
}

.is-dark &.kline-workspace__empty.is-error {
  .empty__title {
    color: #F59E0B;
  }
}

/* 底部决策追问快捷卡片 */
.kline-workspace__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 12px;
  border-top: 1px solid var(--td-component-stroke);
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
    font-size: var(--app-text-xs);
    font-weight: 500;
    color: var(--td-text-color-placeholder);
    white-space: nowrap;
  }

  .actions__chips {
    display: flex;
    gap: 8px;
    overflow-x: auto;
  }

  .action-chip {
    padding: 3px 9px;
    font-size: var(--app-text-xs);
    border-radius: var(--app-radius-xl);
    border: 1px solid var(--td-brand-color);
    background: rgba(0, 82, 217, 0.08);
    color: var(--td-brand-color);
    cursor: pointer;
    white-space: nowrap;
    transition: all var(--app-motion-fast) ease;

    .is-dark & {
      border-color: #3b82f6;
      background: rgba(59, 130, 246, 0.15);
      color: #93c5fd;
    }

    &:hover {
      background: var(--td-brand-color);
      color: #ffffff;

      .is-dark & {
        background: #3b82f6;
        color: #ffffff;
      }
    }
  }
}
</style>
