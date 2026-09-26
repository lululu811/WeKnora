<template>
  <Transition name="kline-panel" :duration="{ enter: 240, leave: 300 }">
    <div v-if="panel?.visible.value && panel.activeThscode.value" class="chat-kline-panel-clip">
      <aside
        class="chat-kline-panel"
        :class="{ 'is-resizing': resizing }"
        :style="{ width: `${panel.width.value}px` }"
        role="complementary"
        :aria-label="t('chat.klinePanel.title')"
      >
        <!-- 左缘拖拽把手 -->
        <PanelResizeHandle edge="left" :label="t('knowledgeStages.resizeDrawer')"
          :value="panel.width.value" :min="KLINE_PANEL_MIN_WIDTH" :max="KLINE_PANEL_MAX_WIDTH"
          @start="startResize" @resize="resizePanel" @end="resizing = false" />

        <div class="chat-kline-panel__header">
          <div class="chat-kline-panel__title">
            <t-icon name="chart" size="16px" />
            <span>{{ t('chat.klinePanel.title') }}</span>
          </div>
          <button type="button" class="chat-kline-panel__close"
            :aria-label="t('common.close')" @click="panel.close()">
            <t-icon name="close" size="16px" />
          </button>
        </div>

        <div v-if="panel.picks.value.length > 1" class="chat-kline-panel__chips" role="tablist">
          <button
            v-for="(pick, idx) in panel.picks.value"
            :key="`${pick.ticker}-${pick.exchange}-${idx}`"
            type="button"
            class="chat-kline-panel__chip"
            :class="{ 'is-active': panel.activeIndex.value === idx }"
            role="tab"
            :aria-selected="panel.activeIndex.value === idx"
            :title="`${pick.ticker}.${pick.exchange}`"
            @click="panel.setActive(idx)"
          >
            <span class="chat-kline-panel__chip-code">{{ pick.ticker }}</span>
            <span class="chat-kline-panel__chip-exchange">{{ pick.exchange }}</span>
          </button>
        </div>

        <div class="chat-kline-panel__body">
          <iframe
            v-if="iframeSrc"
            :key="iframeSrc"
            :src="iframeSrc"
            class="chat-kline-panel__iframe"
            :title="panel.activeThscode.value || ''"
            referrerpolicy="no-referrer"
            allow="clipboard-read; clipboard-write"
          />
        </div>
      </aside>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useChatKLinePanel, KLINE_PANEL_MIN_WIDTH, KLINE_PANEL_MAX_WIDTH } from '@/composables/useChatKLinePanel'
import PanelResizeHandle from '@/components/PanelResizeHandle.vue'

const panel = useChatKLinePanel()
const { t } = useI18n()
const resizing = ref(false)

// 默认指向本地 dev 的 kline-studio frontend（Vite :5173）。生产/Docker
// 编排下通过 VITE_KLINE_STUDIO_BASE_URL 注入为 :4000（backend serve dist）。
const klineStudioBaseUrl =
  (typeof import.meta !== 'undefined' &&
    (import.meta as any).env?.VITE_KLINE_STUDIO_BASE_URL) ||
  'http://localhost:4000'

const iframeSrc = computed(() => {
  if (!panel?.activeThscode.value) return ''
  const base = String(klineStudioBaseUrl).replace(/\/$/, '')
  // embedded=1 让 KLinePage 隐藏 PicksBanner / StockHeader，只渲染 K 线图。
  return `${base}/k/${panel.activeThscode.value}?embedded=1`
})

const startResize = () => {
  resizing.value = true
}

const resizePanel = (next: number) => {
  panel?.setWidth(next)
}
</script>

<style lang="less" scoped>
.chat-kline-panel-clip {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 5;
}

.chat-kline-panel {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  pointer-events: auto;
  display: flex;
  flex-direction: column;
  background: var(--td-bg-color-container, #fff);
  border-left: 1px solid var(--td-component-stroke, #e7e7e7);
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.04);

  &.is-resizing {
    transition: none;
  }
}

.chat-kline-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--td-component-stroke, #e7e7e7);
  flex-shrink: 0;
}

.chat-kline-panel__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
}

.chat-kline-panel__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--td-text-color-secondary, #666);
  cursor: pointer;
  transition: background 0.12s ease;

  &:hover {
    background: var(--td-bg-color-secondarycontainer, #f3f3f3);
  }
}

.chat-kline-panel__chips {
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--td-component-stroke, #e7e7e7);
  overflow-x: auto;
  flex-shrink: 0;
  scrollbar-width: thin;
}

.chat-kline-panel__chip {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 14px;
  border: 1px solid var(--td-component-stroke, #e7e7e7);
  background: transparent;
  color: inherit;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.12s ease, border-color 0.12s ease;

  &:hover {
    border-color: var(--td-brand-color, #0052d9);
  }

  &.is-active {
    background: var(--td-brand-color, #0052d9);
    border-color: var(--td-brand-color, #0052d9);
    color: #fff;
  }
}

.chat-kline-panel__chip-code {
  font-weight: 600;
  font-family: var(--td-font-family-mono, monospace);
}

.chat-kline-panel__chip-exchange {
  font-size: 11px;
  opacity: 0.75;
}

.chat-kline-panel__body {
  flex: 1;
  min-height: 0;
  position: relative;
  background: var(--td-bg-color-secondarycontainer, #f5f5f5);
}

.chat-kline-panel__iframe {
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
}

.kline-panel-enter-active,
.kline-panel-leave-active {
  transition: transform 0.24s ease;
}

.kline-panel-enter-from,
.kline-panel-leave-to {
  transform: translateX(100%);
}
</style>