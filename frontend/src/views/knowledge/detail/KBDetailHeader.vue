<script setup lang="ts">
/**
 * KB 详情页头部：面包屑（知识库列表 / KB 切换器）+ 视图标签页 + 信息/设置入口。
 *
 * 纯展示组件：所有数据由父组件 KnowledgeBase 通过 props 传入，所有交互通过
 * emit 抛回父组件。这里不持有任何业务状态，也不发请求。
 */
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import KBInfoPopover from '@/components/KBInfoPopover.vue'
import KBSwitcherDropdown from '@/components/KBSwitcherDropdown.vue'
import { useStaggerRise } from '@/composables/useMotion'
import type { KbDetailOption, KbDetailTab, KbDetailTabItem } from './kbDetail.types'

const props = withDefaults(defineProps<{
  kbId: string
  kbInfo: any
  knowledgeList: KbDetailOption[]
  /** 父组件组装好的标签页列表（wiki/graph 只在 wiki KB 上出现）。 */
  tabs: KbDetailTabItem[]
  /** 实际高亮的标签页：activeTab 在当前 KB 上不存在时回落到 documents。 */
  shownTab: KbDetailTab
  canManage: boolean
  isLiteMode: boolean
  supportedFileTypes: string[]
  unsupportedFileTypes: string[]
  /** 没有任何 storage backend 时给出提示，点击跳到设置。 */
  missingStorageEngine: boolean
}>(), {
  kbInfo: null,
  knowledgeList: () => [],
  tabs: () => [],
  unsupportedFileTypes: () => [],
  supportedFileTypes: () => [],
  missingStorageEngine: false,
  canManage: false,
  isLiteMode: false,
})

const emit = defineEmits<{
  (e: 'navigate-to-list'): void
  (e: 'navigate-current'): void
  (e: 'select-kb', id: string): void
  (e: 'open-settings'): void
  (e: 'go-parser-settings'): void
  (e: 'update:tab', tab: KbDetailTab): void
}>()

const { t } = useI18n()

// Direction A：首次进入标签页做一次 stagger rise（12px / 60ms）。
const tabListEl = ref<HTMLElement | null>(null)
const { rise } = useStaggerRise({ stagger: 60, displacement: 12 })
onMounted(() => {
  if (tabListEl.value) rise(tabListEl.value.querySelectorAll('.kb-view-tab'))
})
</script>

<template>
  <div class="document-header">
    <div class="document-header-title">
      <div class="document-title-row">
        <h2 class="document-breadcrumb">
          <button type="button" class="breadcrumb-link" @click="emit('navigate-to-list')">
            {{ t('menu.knowledgeBase') }}
          </button>
          <t-icon name="chevron-right" class="breadcrumb-separator" />
          <KBSwitcherDropdown v-if="knowledgeList.length" :kb-list="knowledgeList" :current-kb-id="kbId"
            @select="(id: string) => emit('select-kb', id)">
            <button type="button" class="breadcrumb-link dropdown" :disabled="!kbId">
              <template v-if="!kbInfo">
                <t-skeleton animation="gradient" :row-col="[{ width: '120px', height: '20px' }]" />
              </template>
              <template v-else>
                <span>{{ kbInfo.name }}</span>
                <t-icon name="chevron-down" />
              </template>
            </button>
          </KBSwitcherDropdown>
          <button v-else type="button" class="breadcrumb-link" :disabled="!kbId" @click="emit('navigate-current')">
            <template v-if="!kbInfo">
              <t-skeleton animation="gradient" :row-col="[{ width: '120px', height: '20px' }]" />
            </template>
            <template v-else>
              {{ kbInfo.name }}
            </template>
          </button>
          <t-icon name="chevron-right" class="breadcrumb-separator" />
          <div ref="tabListEl" class="kb-view-tabs" role="tablist" :aria-label="t('knowledgeEditor.wikiBrowser.viewTabs')">
            <t-tooltip v-for="tab in tabs" :key="tab.key" :content="tab.tip" placement="bottom">
              <button type="button" role="tab" class="kb-view-tab"
                :class="{ active: shownTab === tab.key, indexing: tab.indexing }"
                :aria-selected="shownTab === tab.key" @click="emit('update:tab', tab.key)">
                <t-loading v-if="tab.indexing" size="small" class="kb-view-tab__indicator" />
                <t-icon v-else :name="tab.icon" size="16px" />
                <span>{{ tab.label }}</span>
              </button>
            </t-tooltip>
          </div>
        </h2>
        <!-- 标题行右侧的动作锚点：聚拢"信息"和"设置"两个圆形按钮。 -->
        <div class="kb-title-actions">
          <KBInfoPopover v-if="kbInfo && !isLiteMode" :kb-info="kbInfo" :supported-file-types="supportedFileTypes" />
          <t-tooltip v-if="canManage" :content="t('knowledgeBase.settings')" placement="top">
            <button type="button" class="kb-settings-button" :aria-label="t('knowledgeBase.settings')" :disabled="!kbId"
              @click="emit('open-settings')">
              <t-icon name="setting" size="16px" />
            </button>
          </t-tooltip>
        </div>
      </div>
      <p v-if="kbInfo?.description" class="document-subtitle">{{ kbInfo.description }}</p>
      <p v-if="unsupportedFileTypes.length" class="parser-hint" @click="emit('go-parser-settings')">
        <t-icon name="info-circle" class="parser-hint-icon" />
        <span>{{ t('knowledgeBase.unsupportedTypesHint', { types: unsupportedFileTypes.map((t) => '.' + t).join('、') }) }}</span>
        <span class="parser-hint-link">{{ t('knowledgeBase.goToParserSettings') }} →</span>
      </p>
      <p v-if="missingStorageEngine" class="storage-engine-warning" @click="emit('open-settings')">
        <t-icon name="info-circle" class="warning-icon" />
        <span>{{ t('knowledgeBase.missingStorageEngine') }}</span>
        <span class="warning-link">{{ t('knowledgeBase.goToStorageSettings') }} →</span>
      </p>
    </div>
  </div>
</template>

<style scoped lang="less">
@import './kbDetailHeader.less';
</style>
