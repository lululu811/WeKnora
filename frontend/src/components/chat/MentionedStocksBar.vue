<template>
  <div v-if="mentionedStocks.length > 0" class="mentioned-stocks-bar">
    <div class="stocks-bar__inner">
      <div class="stocks-bar__label">
        <span class="stocks-bar__icon">📈</span>
        <span class="stocks-bar__title">本轮提及个股</span>
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
              ? `右侧工作台正在显示 ${st.name} (${st.thscode})`
              : `点击在右侧工作台查看 ${st.name} (${st.thscode}) 的知行战法K线与砖型图`"
          >
            <span class="stock-chip__name">{{ st.name }}</span>
            <span class="stock-chip__code">{{ st.thscode }}</span>
            <span class="stock-chip__action">{{ st.thscode === activeThscode ? '正在查看' : 'K线诊断 →' }}</span>
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
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { MessagePlugin } from 'tdesign-vue-next';
import { useAgentWorkspace } from '@/composables/useAgentWorkspace';
import { extractMentionedStocks, type MentionedStock } from '@/utils/stockMentions';
import { addWatchItem } from '@/api/watchlist';

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

const mentionedStocks = computed<MentionedStock[]>(() => {
  return extractMentionedStocks(rawContent.value);
});

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

const handleAddToPool = async (stock: MentionedStock) => {
  if (pooledCodes.value.has(stock.thscode)) return;
  try {
    const res = await addWatchItem({
      thscode: stock.thscode,
      name: stock.name,
      exchange: stock.exchange,
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
  background: var(--td-bg-color-secondarycontainer, rgba(0, 82, 217, 0.04));
  border: 1px solid var(--td-component-stroke, rgba(0, 82, 217, 0.12));
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);

  :root[theme-mode="dark"] & {
    background: rgba(30, 41, 59, 0.5);
    border-color: rgba(51, 65, 85, 0.6);
  }
}

.stocks-bar__label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--td-text-color-primary, #1e293b);
  white-space: nowrap;
  flex-shrink: 0;

  .stocks-bar__icon {
    font-size: 14px;
  }

  .stocks-bar__count {
    font-size: 11px;
    color: var(--td-text-color-placeholder, #64748b);
  }
}

.stocks-bar__list {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  flex: 1;
}

/* chip 与「进池」紧挨着成组：它们说的是同一只票，拆成两个独立间距会让
   「进池」看起来像在说别的标的。 */
.stocks-bar__item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.stock-chip__pool {
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
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  border: 1px solid var(--td-component-stroke, #cbd5e1);
  background: var(--td-bg-color-container, #ffffff);
  color: var(--td-text-color-primary, #0f172a);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;

  :root[theme-mode="dark"] & {
    background: #1e293b;
    border-color: #334155;
    color: #f8fafc;
  }

  &:hover {
    border-color: var(--td-brand-color, #0052d9);
    background: var(--td-brand-color-light, rgba(0, 82, 217, 0.08));
    transform: translateY(-1px);
    box-shadow: 0 2px 6px rgba(0, 82, 217, 0.15);

    .stock-chip__action {
      color: var(--td-brand-color, #0052d9);
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

  .stock-chip__name {
    font-weight: 600;
  }

  .stock-chip__code {
    font-family: monospace;
    font-size: 11px;
    color: var(--td-text-color-secondary, #64748b);
  }

  .stock-chip__action {
    font-size: 11px;
    color: var(--td-brand-color, #0052d9);
    opacity: 0.85;
    margin-left: 2px;
    font-weight: 500;
  }
}
</style>
