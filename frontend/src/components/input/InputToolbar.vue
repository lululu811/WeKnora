<script setup lang="ts">
/**
 * 底部工具栏：Agent 模式切换、本地浏览器 / WebSearch / 图片 / 附件 / @ 知识库按钮，
 * 以及模型显示与推理档位选择。
 *
 * 纯展示层。所有开关状态、回调与弹层定位都来自父组件（Input-field.vue），
 * 本组件不持有业务状态。四个元素 ref（agent 模式按钮、附件上传、@ 按钮、模型触发器）
 * 由父组件传入并在本组件回写实例，保证父脚本里既有的定位逻辑继续可用。
 */
import { useI18n } from 'vue-i18n'
import { watch, ref, type PropType } from 'vue'
import BrowserIcon from '@/components/icons/BrowserIcon.vue'
import AgentSelector from '@/components/AgentSelector.vue'
import type { CustomAgent } from '@/api/agent'
import type { ModelConfig } from '@/api/model'

const props = defineProps({
  embeddedMode: { type: Boolean, default: false },

  // ---- Agent 模式切换 ----
  isCustomAgent: Boolean,
  isAgentEnabled: Boolean,
  selectedAgent: { type: Object as PropType<any>, default: () => ({}) },
  showAgentModeSelector: Boolean,
  currentAgentId: { type: String, default: '' },
  enabledAgents: { type: Array as PropType<CustomAgent[]>, default: () => [] },
  allModels: { type: Array as PropType<ModelConfig[]>, default: () => [] },
  toggleAgentModeSelector: { type: Function as PropType<() => void>, required: true },
  closeAgentModeSelector: { type: Function as PropType<() => void>, required: true },
  handleSelectAgent: { type: Function as PropType<any>, required: true },
  handleAgentNotReady: { type: Function as PropType<any>, required: true },

  // ---- 本地浏览器来源 ----
  isAgentStreamMode: Boolean,
  isLocalBrowserEnabled: Boolean,
  browserConnection: { type: Object as PropType<any>, default: () => ({}) },
  browserSourceUnavailableHint: { type: String, default: '' },
  openBrowserConnectionSettings: { type: Function as PropType<() => void>, required: true },
  toggleBrowserSource: { type: Function as PropType<() => void>, required: true },

  // ---- WebSearch ----
  showWebSearchButton: Boolean,
  isWebSearchConfigured: Boolean,
  isWebSearchEnabled: Boolean,
  toggleWebSearch: { type: Function as PropType<() => void>, required: true },
  handleGoToWebSearchConfig: { type: Function as PropType<() => void>, required: true },

  // ---- 图片 / 附件 ----
  showImageUploadButton: Boolean,
  imageCount: { type: Number, default: 0 },
  triggerImageUpload: { type: Function as PropType<() => void>, required: true },
  attachmentCount: { type: Number, default: 0 },
  triggerAttachment: { type: Function as PropType<() => void>, required: true },

  // ---- @ 知识库 / 文件 ----
  isMentionDisabled: Boolean,
  isKnowledgeBaseDisabledByAgent: Boolean,
  selectedCount: { type: Number, default: 0 },
  triggerMention: { type: Function as PropType<() => void>, required: true },
  handleGoToAgentSettings: { type: Function as PropType<any>, required: true },

  // ---- 模型触发器（模型下拉与推理档位弹层留在父组件）----
  isModelLockedByAgent: Boolean,
  toggleModelSelector: { type: Function as PropType<() => void>, required: true },
  selectedModelDisplayName: { type: String, default: '' },
  selectedModelContextLabel: { type: String, default: '' },
  selectedModelContextIsDefault: Boolean,
  selectedModelContextTitle: { type: String, default: '' },
  showModelSelector: Boolean,

  // ---- 由本组件回写实例的引用 ----
  // 这三个 ref 只是「把元素交还父组件」的单向管道，父组件里它们的声明类型各不相同
  // （有的 inferred 成 Ref<HTMLElement | undefined>），因此这里按 any 收口，
  // 避免管道本身成为类型噪音。形状见 input.types.ts 的 ElRef。
  agentModeButtonElRef: { type: Object as PropType<any>, default: null },
  atButtonElRef: { type: Object as PropType<any>, default: null },
  modelButtonElRef: { type: Object as PropType<any>, default: null },
})

const { t: $t } = useI18n()

const agentModeButtonRef = ref<HTMLElement | null>(null)
const atButtonRef = ref<HTMLElement | null>(null)
const modelButtonRef = ref<HTMLElement | null>(null)

// 把内部元素实例交还父组件：父组件的 KnowledgeBaseSelector / AgentSelector 依赖
// 这些 ref 做弹层定位，拆分前它们指向的就是同一批元素。
const relay = (el: unknown, target: any) => {
  if (target) target.value = el
}
watch(agentModeButtonRef, (el) => relay(el, props.agentModeButtonElRef), { immediate: true, flush: 'post' })
watch(atButtonRef, (el) => relay(el, props.atButtonElRef), { immediate: true, flush: 'post' })
watch(modelButtonRef, (el) => relay(el, props.modelButtonElRef), { immediate: true, flush: 'post' })
</script>

<template>
  <div class="control-bar" :class="{ 'is-embedded': embeddedMode }">
    <!-- 左侧控制按钮 -->
    <div class="control-left" v-if="!embeddedMode">
      <!-- Agent 模式切换按钮 -->
      <div
        ref="agentModeButtonRef"
        class="control-btn agent-mode-btn"
        :class="{
          'is-normal': !isCustomAgent && !isAgentEnabled,
          'is-agent': !isCustomAgent && isAgentEnabled,
          'is-custom': isCustomAgent,
        }"
        @click.stop="toggleAgentModeSelector"
      >
        <span class="agent-mode-text">
          {{ selectedAgent.name || (isAgentEnabled ? $t('input.agentMode') : $t('input.normalMode')) }}
        </span>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" class="dropdown-arrow" :class="{ 'rotate': showAgentModeSelector }">
          <path d="M2.5 4.5L6 8L9.5 4.5H2.5Z" />
        </svg>
      </div>

      <!-- Agent 选择器下拉菜单 -->
      <AgentSelector
        :visible="showAgentModeSelector"
        :anchorEl="agentModeButtonRef ?? undefined"
        :currentAgentId="currentAgentId"
        :agents="enabledAgents"
        :all-models="allModels"
        @close="closeAgentModeSelector"
        @select="handleSelectAgent"
        @not-ready="handleAgentNotReady"
      />

      <t-tooltip v-if="isAgentStreamMode" placement="top" theme="light" :popupProps="{ overlayClassName: 'input-field-tooltip' }">
        <template #content>
          <div v-if="!browserConnection.knownOffline" class="browser-source-tooltip">
            <strong>{{ $t('localBrowser.local') }}</strong>
            <span>{{ $t('localBrowser.sourceHint') }}</span>
          </div>
          <div v-else class="tooltip-with-link">
            <span>{{ $t(browserSourceUnavailableHint) }}</span>
            <a href="#" @click.prevent="openBrowserConnectionSettings">{{ $t('localBrowser.openSettings') }}</a>
          </div>
        </template>
        <button
          type="button"
          class="control-btn browser-source-btn"
          :class="{
            active: isLocalBrowserEnabled && browserConnection.online,
            disabled: browserConnection.knownOffline,
          }"
          :aria-pressed="isLocalBrowserEnabled && browserConnection.online"
          :aria-disabled="browserConnection.knownOffline"
          :aria-label="$t('localBrowser.local')"
          @click.stop="toggleBrowserSource"
        >
          <BrowserIcon class="control-icon" />
        </button>
      </t-tooltip>

      <!-- WebSearch 开关按钮（智能体未启用时不显示） -->
      <t-tooltip v-if="showWebSearchButton" placement="top" theme="light" :popupProps="{ overlayClassName: 'input-field-tooltip' }">
        <template #content>
          <span v-if="isWebSearchConfigured">
            {{ isWebSearchEnabled ? $t('input.webSearch.toggleOff') : $t('input.webSearch.toggleOn') }}
          </span>
          <div v-else class="tooltip-with-link">
            <span>{{ $t('input.webSearch.notConfigured') }}</span>
            <a href="#" @click.prevent="handleGoToWebSearchConfig">{{ $t('input.goToAgentSettings') }}</a>
          </div>
        </template>
        <div
          class="control-btn websearch-btn"
          :class="{ 'active': isWebSearchEnabled && isWebSearchConfigured, 'disabled': !isWebSearchConfigured }"
          @click.stop="toggleWebSearch"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 18 18"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            class="control-icon websearch-icon"
            :class="{ 'active': isWebSearchEnabled && isWebSearchConfigured }"
          >
            <circle cx="9" cy="9" r="7" stroke="currentColor" stroke-width="1.2" fill="none" />
            <path d="M 9 2 A 3.5 7 0 0 0 9 16" stroke="currentColor" stroke-width="1.2" fill="none" />
            <path d="M 9 2 A 3.5 7 0 0 1 9 16" stroke="currentColor" stroke-width="1.2" fill="none" />
            <line x1="2.94" y1="5.5" x2="15.06" y2="5.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
            <line x1="2.94" y1="12.5" x2="15.06" y2="12.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
          </svg>
        </div>
      </t-tooltip>

      <!-- 图片上传按钮（智能体未启用时不显示） -->
      <t-tooltip v-if="showImageUploadButton" placement="top" theme="light" :popupProps="{ overlayClassName: 'input-field-tooltip' }">
        <template #content>
          <span>{{ $t('chat.imageUploadTooltip') }}</span>
        </template>
        <div class="control-btn image-upload-btn" :class="{ 'active': imageCount > 0 }" @click.stop="triggerImageUpload()">
          <svg width="18" height="18" viewBox="0 0 1024 1024" fill="currentColor" class="control-icon">
            <path
              d="M896 128H128c-35.3 0-64 28.7-64 64v640c0 35.3 28.7 64 64 64h768c35.3 0 64-28.7 64-64V192c0-35.3-28.7-64-64-64zM128 832V192h768l0.1 640H128z"
            />
            <path d="M352 448a96 96 0 1 0 0-192 96 96 0 0 0 0 192z" />
            <path d="M128 768l224-288 160 160 192-256L896 640v128H128z" />
          </svg>
          <span v-if="imageCount > 0" class="image-count">{{ imageCount }}</span>
        </div>
      </t-tooltip>

      <!-- 附件上传按钮 -->
      <t-tooltip placement="top" theme="light" :popupProps="{ overlayClassName: 'input-field-tooltip' }">
        <template #content>
          <span>
            {{
              attachmentCount > 0
                ? $t('chat.attachmentWithCount', { count: attachmentCount })
                : $t('chat.attachmentUploadTooltip')
            }}
          </span>
        </template>
        <div class="control-btn attachment-upload-btn" :class="{ 'active': attachmentCount > 0 }" @click.stop="triggerAttachment()">
          <!-- 回形针图标 -->
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
            class="control-icon"
          >
            <path
              d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"
            />
          </svg>
          <span v-if="attachmentCount > 0" class="attachment-count">{{ attachmentCount }}</span>
        </div>
      </t-tooltip>

      <!-- @ 知识库/文件选择按钮 -->
      <t-tooltip placement="top" theme="light" :popupProps="{ overlayClassName: 'input-field-tooltip' }">
        <template #content>
          <div v-if="isMentionDisabled && isKnowledgeBaseDisabledByAgent" class="tooltip-with-link">
            <span>{{ $t('input.kbDisabledByAgent') }}</span>
            <a href="#" @click.prevent="handleGoToAgentSettings('knowledge')">{{ $t('input.goToAgentSettings') }}</a>
          </div>
          <span v-else>
            {{ selectedCount > 0 ? $t('input.knowledgeBaseWithCount', { count: selectedCount }) : $t('input.knowledgeBase') }}
          </span>
        </template>
        <div
          ref="atButtonRef"
          class="control-btn kb-btn"
          data-guide="chat-kb-mention"
          :class="{ 'active': selectedCount > 0, 'disabled': isMentionDisabled }"
          @click.stop
          @mousedown.prevent="triggerMention"
        >
          <svg width="18" height="18" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" class="control-icon at-icon">
            <circle cx="10" cy="10" r="3.5" stroke="currentColor" stroke-width="1.8" />
            <path
              d="M13.5 10V11.5C13.5 12.163 13.7634 12.7989 14.2322 13.2678C14.7011 13.7366 15.337 14 16 14C16.663 14 17.2989 13.7366 17.7678 13.2678C18.2366 12.7989 18.5 12.163 18.5 11.5V10C18.5 7.74566 17.6045 5.58365 16.0104 3.98959C14.4163 2.39553 12.2543 1.5 10 1.5C7.74566 1.5 5.58365 2.39553 3.98959 3.98959C2.39553 5.58365 1.5 7.74566 1.5 10C1.5 12.2543 2.39553 14.4163 3.98959 16.0104C5.58365 17.6045 7.74566 18.5 10 18.5H12"
              stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
            />
          </svg>
          <span v-if="selectedCount > 0" class="kb-count">{{ selectedCount }}</span>
        </div>
      </t-tooltip>

      <!-- 模型显示 -->
      <t-tooltip :content="isModelLockedByAgent ? $t('input.modelLockedByAgent') : ''" :disabled="!isModelLockedByAgent">
        <div class="model-display" :class="{ 'agent-controlled': isModelLockedByAgent }">
          <div ref="modelButtonRef" class="model-selector-trigger" @click.stop="toggleModelSelector">
            <span class="model-selector-name">{{ selectedModelDisplayName }}</span>
            <span
              v-if="selectedModelContextLabel"
              class="model-selector-ctx"
              :class="{ 'is-default': selectedModelContextIsDefault }"
              :title="selectedModelContextTitle"
            >{{ selectedModelContextLabel }}</span>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" class="model-dropdown-arrow" :class="{ 'rotate': showModelSelector }">
              <path d="M2.5 4.5L6 8L9.5 4.5H2.5Z" />
            </svg>
          </div>
        </div>
      </t-tooltip>
      <!-- 推理档位的 t-popup 留在父组件：它挂在 body 上，用的是父组件 scoped 的样式，
           搬过来反而会丢样式；这里只保留模型触发器。 -->
    </div>

    <!-- 模型下拉走 Teleport：挂在 body 上以脱离输入区定位上下文 -->
    <Teleport to="body">
      <div v-if="showModelSelector" class="model-selector-overlay" @click="closeModelSelector">
        <div class="model-selector-dropdown" :style="modelDropdownStyle" @click.stop>
          <div class="model-selector-header">
            <span>{{ $t('conversationSettings.models.chatGroupLabel') }}</span>
            <button class="model-selector-add" type="button" @click="handleModelChange('__add_model__')">
              <span class="add-icon">+</span>
              <span class="add-text">{{ $t('input.addModel') }}</span>
            </button>
          </div>
          <div class="model-selector-content">
            <div
              v-for="model in availableModels"
              :key="model.id"
              class="model-option"
              :class="{ selected: model.id === selectedModelId }"
              @click="handleModelChange(model.id || '')"
            >
              <div class="model-option-left">
                <div class="model-option-icon">
                  <t-icon name="chat" size="14px" />
                </div>
                <div class="model-option-name-wrap">
                  <span class="model-option-name">{{ modelDisplayName(model) }}</span>
                  <span v-if="model.display_name" class="model-option-raw-name">{{ model.name }}</span>
                </div>
              </div>
              <span
                class="model-option-ctx"
                :class="{ 'is-default': isDefaultContextWindow(model.parameters?.context_window) }"
                :title="contextWindowTitle(model.parameters?.context_window)"
              >{{ formatContextWindow(model.parameters?.context_window) }}</span>
            </div>
            <div v-if="availableModels.length === 0" class="model-option empty">{{ $t('input.noModel') }}</div>
          </div>
        </div>
      </div>
    </Teleport>

    <slot name="trailing" />
  </div>
</template>

<style scoped lang="less">
/* 控制栏的样式随工具栏一起搬过来：按钮、计数徽标、模型触发器等 */
@import './css/input-toolbar.less';
</style>
