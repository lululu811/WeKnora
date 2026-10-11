<template>
  <div v-if="mentionedStocks.length > 0" class="mentioned-stocks-bar">
    <div class="stocks-bar__inner">
      <div class="stocks-bar__label">
        <span class="stocks-bar__icon">📈</span>
        <span class="stocks-bar__title">{{ t('mentionedStocks.title') }}</span>
        <span class="stocks-bar__count">({{ mentionedStocks.length }})</span>
      </div>

      <div class="stocks-bar__list">
        <!-- 每个标的是一组：左边原有的「看 K 线」chip，右边「进池」。
             不能把「进池」嵌进 chip 里 —— chip 本身已经是 <button>，按钮里套按钮
             是无效 HTML，浏览器会按自己的心情拆分它。 -->
        <div v-for="st in mentionedStocks" :key="st.thscode" class="stocks-bar__item">
          <button
            type="button"
            class="stock-chip"
            :class="{ 'stock-chip--active': st.thscode === activeThscode }"
            :aria-pressed="st.thscode === activeThscode"
            @click="handleClickStock(st)"
            :title="st.thscode === activeThscode
              ? t('mentionedStocks.viewingTitle', { name: st.name, thscode: st.thscode })
              : t('mentionedStocks.viewTitle', { name: st.name, thscode: st.thscode })"
          >
            <span class="stock-chip__name">{{ st.name }}</span>
            <span class="stock-chip__code">{{ st.thscode }}</span>
            <span class="stock-chip__action">{{ st.thscode === activeThscode ? t('mentionedStocks.viewing') : t('mentionedStocks.viewKline') }}</span>
          </button>

          <!-- 进池 = 加入个股追踪。chip 上已经有 thscode/name/exchange，直接调用
               已存在的添加接口，不再造一个选择器。 -->
          <button
            type="button"
            class="stock-chip__pool"
            :class="{ 'is-added': pooledCodes.has(st.thscode) }"
            :disabled="pooledCodes.has(st.thscode)"
            :title="pooledCodes.has(st.thscode) ? t('watchlist.inPool') : t('watchlist.addToPool')"
            @click="handleAddToPool(st)"
          >
            {{ pooledCodes.has(st.thscode) ? t('watchlist.inPool') : t('watchlist.addToPool') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { MessagePlugin } from 'tdesign-vue-next';
import { useAgentWorkspace } from '@/finance/composables/useAgentWorkspace';
import { extractMentionedStocks, extractTrackingReason, registerStockName, type MentionedStock } from '@/finance/utils/stockMentions';
import { addWatchItem, distillWatchReason, fetchQuotes } from '@/finance/api/watchlist';

const props = defineProps<{
  session: any;
}>();

const { t } = useI18n();

// 右侧工作台当前显示的标的。取不到 provider（如本组件被单独复用/测试挂载）时
// 退化成空串，所有 chip 都不高亮 —— 缺状态不该被渲染成「都在看」。
const activeThscode = (() => {
  try {
    return useAgentWorkspace().activeThscode
  } catch {
    return ref('')
  }
})();

const emit = defineEmits<{
  (e: 'select-stock', stock: MentionedStock, allStocks: MentionedStock[]): void;
}>();

const rawContent = computed(() => {
  const s = props.session;
  if (!s) return '';
  return s.answer || s.message || s.content || '';
});

/**
 * 本轮提及的票。
 *
 * `extractMentionedStocks` 是**同步纯函数**，名称只能从它的本地表里查
 * （15 条手写 + 运行期 `registerStockName` 攒下来的）。而 `registerStockName`
 * 只有自选股页面在调 —— 聊天里模型提及的票大多没进过池子，于是查不到名称，
 * 列表里就成了「002261 002261.SZ」这种代码顶替名称的样子。
 *
 * 所以这里在渲染前把缺名称的批量补齐：
 *   1. 一次性 `fetchQuotes`（接受数组，不是一只一只查）
 *   2. 查到后 `registerStockName` 写回模块级缓存 —— 同一页再算一次就是同步命中
 *   3. 失败就算了，保持代码顶替，**不能因为取不到名称就不显示这只票**
 */
const resolvedNames = ref<Record<string, string>>({});

/**
 * 补齐名称。
 *
 * 注意这个组件在聊天记录里是**每条消息挂一个**（v-for），所以：
 *   - 「已补过」必须是**模块级**的，不能是组件内 ref —— 否则同一只票在两条
 *     消息里出现会被查两次，而 `registerStockName` 写的却是共享缓存，两边分裂
 *   - 在途请求也要共享，否则并发挂载时会打出好几个相同请求
 */
const nameBackfilled = new Set<string>();
const nameInFlight = new Set<string>();

async function backfillMissingNames(stocks: MentionedStock[]): Promise<void> {
  // name 就是 ticker 顶替的（stockMentions 里 `name || getStockName() || ticker`）
  const todo = stocks
    .filter((s) => s.name === s.ticker.split('.')[0])
    .map((s) => s.thscode)
    .filter((c) => !nameBackfilled.has(c) && !nameInFlight.has(c));
  if (todo.length === 0) return;
  todo.forEach((c) => nameInFlight.add(c));

  try {
    const res = await fetchQuotes(todo);
    // res.data 是 **Record<thscode, Quote>**（按代码索引的字典），不是数组 ——
    // 写成 for...of 会拿到 key 字符串，补全静默不生效。
    const quotes = res.data ?? {};
    const next = { ...resolvedNames.value };
    for (const q of Object.values(quotes)) {
      if (q?.thscode && q?.name) {
        next[q.thscode] = q.name;
        // 写回模块级缓存，同页内其他调用方也一起受益
        registerStockName(q.thscode, q.name);
        nameBackfilled.add(q.thscode);
      }
    }
    resolvedNames.value = next;
  } catch {
    // 取不到名称不是错误：代码顶替是可读的降级，不是空状态。
    // 不写 nameBackfilled，允许下次正文变化时重试。
  } finally {
    todo.forEach((c) => nameInFlight.delete(c));
  }
}

const mentionedStocks = computed<MentionedStock[]>(() => {
  const list = extractMentionedStocks(rawContent.value);
  const fix = resolvedNames.value;
  if (Object.keys(fix).length === 0) return list;
  return list.map((s) => {
    const name = fix[s.thscode];
    return name ? { ...s, name } : s;
  });
});

/**
 * 补齐触发。
 *
 * **必须 immediate** —— 这个组件挂在聊天记录里，页面打开时 session 往往已经
 * 渲染完，正文不会��再变化，普通 watch 一次都不会触发（首版就是这么写的，
 * 结果 23 只票全是代码）。immediate 让挂载时立刻补一次。
 *
 * immediate 会让首屏多一个请求，但：
 *   - 它是异步的，不阻塞渲染（列表先用代码顶替渲染出来，拿到名字后自动替换）
 *   - 同一批代码只发一次（backfillMissingNames 内部按 resolvedNames 去重）
 */
watch(rawContent, () => {
  const list = extractMentionedStocks(rawContent.value);
  if (list.length) void backfillMissingNames(list);
}, { immediate: true });

const handleClickStock = (stock: MentionedStock) => {
  emit('select-stock', stock, mentionedStocks.value);
};

/**
 * 本次会话里已经进过池的标的。
 *
 * 只是**本地的即时反馈**，不是权威名单 —— 真正的判定在服务端（重复添加是
 * upsert，返回 created=false）。所以这里不预取整个池子：为一次「进池」把
 * 追踪列表整份拉下来，比多按一次按钮贵得多。
 */
const pooledCodes = ref<Set<string>>(new Set());

/**
 * 先问模型要一句理由，拿不到再退回机械抽取。
 *
 * 顺序是刻意的：LLM 蒸馏出来的是「为什么值得跟」，机械抽取取的是「最后一次
 * 提及所在的那一段」——而模型写股票分析收尾常常是一张汇总清单，抽出来就是
 * 「⭐ 万科A —— 地产板块龙头，放量突破」，复述信号、不给理由；引出句更糟，
 * 存进去的是「好，数据回来了，给你掰开了揉碎了聊」。
 *
 * 但模型调用会失败：没配模型、配额耗尽、超时。所以蒸馏是**增强**不是依赖 ——
 * 它自己吞掉所有错误并回落到机械抽取，入池这个动作永远不会因为它而失败。
 */
const resolveNote = async (stock: MentionedStock): Promise<string> => {
  const fallback = () => extractTrackingReason(rawContent.value, stock.thscode)
  try {
    const res = await distillWatchReason({
      thscode: stock.thscode,
      name: stock.name,
      conversation: rawContent.value,
    });
    return res.reason?.trim() || fallback();
  } catch {
    return fallback();
  }
};

const handleAddToPool = async (stock: MentionedStock) => {
  if (pooledCodes.value.has(stock.thscode)) return;
  try {
    const res = await addWatchItem({
      thscode: stock.thscode,
      name: stock.name,
      exchange: stock.exchange,
      // 理由随入池一起提交，服务端与 `added` 事件在同一事务里落库。
      // 分两次请求的话，中间失败会留下一行没有理由的记录，而事件快照也是空的
      // ——「它当初为什么进池」就再也答不出来了。
      //
      // 抽不到就传空串：note 允许为空，用户可以之后手写。宁可空着，
      // 也不要存一段不相干的话——那比空更难被发现。
      note: await resolveNote(stock),
    });
    // created=false 说明它本来就在池子里（服务端顺手刷新了名称）——照实说，
    // 而不是让用户以为自己刚做了一件没发生过的事。
    MessagePlugin.success(res.created ? t('watchlist.added') : t('watchlist.alreadyWatched'));
    pooledCodes.value = new Set(pooledCodes.value).add(stock.thscode);
  } catch (error: any) {
    MessagePlugin.error(error?.message || t('watchlist.loadFailed'));
  }
};
</script>

<style lang="less" scoped>
.mentioned-stocks-bar {
  width: 100%;
  // 与 FollowUpSuggestions 的 .follow-ups 保持同宽，两者同属一个 .message-row，
  // 宽度不一致会让"本轮提及个股"横跨整行而下面的追问卡片缩在左边一截。
  max-width: 720px;
  margin: 6px 0 10px 0;
  box-sizing: border-box;
  animation: fadeIn 0.2s ease-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.stocks-bar__inner {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  padding: 8px 14px;
  background: var(--td-bg-color-secondarycontainer);
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-md);
  box-shadow: var(--td-shadow-1);
}

.stocks-bar__label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--app-text-sm);
  font-weight: 600;
  color: var(--td-text-color-primary);
  white-space: nowrap;
  flex-shrink: 0;

  .stocks-bar__icon {
    font-size: var(--app-text-base);
  }

  .stocks-bar__count {
    font-size: var(--app-text-xs);
    color: var(--td-text-color-placeholder);
  }
}

/*
 * 两列等宽网格，不用 flex-wrap。
 *
 * 之前是 `flex-wrap`，它按内容宽度自然换行：每行能塞几个塞几个，chip 宽度又随
 * 名称字数变化，于是右边一列参差不齐（截图里「聚飞光电」和「盈趣科技」错位）。
 * 固定两列 + `1fr` 后每行的基线对齐，两列宽度一致，奇数个时最后一只自然落在左列。
 */
.stocks-bar__list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: center;
  gap: 6px 10px;
  flex: 1;
}

/* chip 与「进池」紧挨着成组：它们说的是同一只票，拆成两个独立间距会让
   「进池」看起来像在说别的标的。 */
.stocks-bar__item {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.stock-chip__pool {
  flex: 0 0 auto;
  padding: 4px 8px;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-sm);
  background: transparent;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-xs);
  line-height: 1.4;
  cursor: pointer;
  white-space: nowrap;
  transition: border-color var(--app-motion-fast) ease, color var(--app-motion-fast) ease;

  &:hover:not(:disabled) {
    border-color: var(--td-brand-color);
    color: var(--td-brand-color);
  }

  &:disabled {
    cursor: default;
    opacity: 0.6;
  }
}

.stock-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  /*
   * 在两列 grid 里吃掉剩余宽度，让「名称+代码+看K线」这一组和右侧的「进池」
   * 各自靠边 —— 对齐的落点是「进池」的左边缘，两列等宽才看得出齐。
   * min-width:0 必需：否则长名称会把 grid 列撑破，白白加的列宽就废了。
   */
  flex: 1;
  min-width: 0;
  padding: 4px 10px;
  border-radius: var(--app-radius-sm);
  border: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-container);
  color: var(--td-text-color-primary);
  font-size: var(--app-text-sm);
  cursor: pointer;
  white-space: nowrap;
  transition: all var(--app-motion-fast) ease;

  :root[theme-mode="dark"] & {
    /*
     * 之前这里是 Tailwind slate 色（#1e293b / #334155 / #f8fafc）——
     * 冷蓝灰，而本项目的深色主题是暖棕（--td-gray-color-11: #2B2320），
     * 深色模式下两者并置会明显跳色。同一块的上方浅色态用的就是
     * TDesign 变量，深色态却另写了一套色，改成同一套。
     */
    background: var(--td-bg-color-secondarycontainer);
    border-color: var(--td-component-border);
    color: var(--td-text-color-primary);
  }

  &:hover {
    border-color: var(--td-brand-color);
    background: var(--td-brand-color-light);
    transform: translateY(-1px);
    box-shadow: 0 2px 6px color-mix(in srgb, var(--td-brand-color) 18%, transparent);

    .stock-chip__action {
      color: var(--td-brand-color);
    }
  }

  &:active {
    transform: translateY(0);
  }

  // 右侧图位正在显示这只票时的态。让「正文里提到哪只」和「图上画着哪只」可对照，
  // 这是聊天与 K 线之间最便宜的一层双向绑定。
  &--active {
    // 全部走令牌，不写死颜色：品牌色透明叠加用 color-mix（深色模式会跟着变），
    // 这也是样式守卫（styleGuard.test.mjs）要求的形式——它只允许绕过令牌的
    // 写法计数下降，新代码不该抬高基线。
    border-color: var(--td-brand-color);
    background: color-mix(in srgb, var(--td-brand-color) 12%, transparent);
    box-shadow: 0 0 0 1px var(--td-brand-color) inset;

    .stock-chip__code {
      color: var(--td-brand-color);
    }

    &:hover {
      transform: none;
    }
  }

  /*
   * 名称吃掉剩余宽度，代码与「看K线」固定。窄列里名称过长时省略而不是撑破
   * 容器 —— 完整名称在 title 里，鼠标悬停能看。
   */
  .stock-chip__name {
    font-weight: 600;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* 代码固定宽度：两列对齐时它是主要的参照物，被名称挤压会整行错位 */
  .stock-chip__code {
    flex: 0 0 auto;
    font-family: monospace;
    font-size: var(--app-text-xs);
    color: var(--td-text-color-secondary);
  }

  .stock-chip__action {
    flex: 0 0 auto;
    font-size: var(--app-text-xs);
    color: var(--td-brand-color);
    opacity: 0.85;
    margin-left: 2px;
    font-weight: 500;
  }
}

/*
 * 容器够宽时两列，窄了退成一列。
 *
 * 用 `@container` 而不是媒体查询：这个条挂在聊天主区里，宽度由聊天区决定，
 * 不是由视口决定 —— 侧栏展开与否都会改变它，用视口宽度判断会判错。
 *
 * container 挂在**父级** .stocks-bar__inner 上：元素不能查询自己的尺寸
 * （container-type 加在 .stocks-bar__list 自己身上会让它的 @container 永远
 * 匹配不到）。
 */
.stocks-bar__inner {
  container-type: inline-size;
  container-name: stockbar;
}

@container stockbar (max-width: 460px) {
  .stocks-bar__list {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
