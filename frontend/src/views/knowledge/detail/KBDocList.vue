<script setup lang="ts">
/**
 * 文档列表工具条：目录面包屑 + 搜索 + 筛选面板 + 排序 + 视图切换 + 上传入口。
 *
 * 纯展示 + 受控组件：筛选 / 搜索 / 排序状态全部由父组件 KnowledgeBase 持有，
 * 这里用 defineModel 做双向同步，任何取值和变更都直接落到父组件，
 * 父组件的加载/清空逻辑（loadKnowledgeFiles / clearDocumentFilters 等）通过
 * emit 调用。这里不持有业务状态，也不发请求。
 */
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import KbUploadSourceDropdown from '../components/KbUploadSourceDropdown.vue'
import type { KbDetailFilterOption, KbDetailFolderCrumb, KbDetailTagOption } from './kbDetail.types'
import type { DocumentSortOption } from '../documentSorting'

const props = defineProps<{
  kbId: string
  kbInfo: any
  showFolderTree: boolean
  folderTreeCollapsed: boolean
  folderBreadcrumbs: KbDetailFolderCrumb[]
  total: number
  isFiltering: boolean
  loading: boolean
  // 筛选面板
  activeFilterCount: number
  fileTypeOptions: KbDetailFilterOption[]
  parseStatusOptions: KbDetailFilterOption[]
  sourceOptions: KbDetailFilterOption[]
  disableFutureDate: { after: Date }
  // 标签筛选
  tagLoading: boolean
  tagLoadingMore: boolean
  tagHasMore: boolean
  filterTagOptions: KbDetailTagOption[]
  // 排序
  documentSortGroups: Array<{ key: string; label: string; description: string; options: DocumentSortOption[] }>
  activeDocumentSortLabel: string
  documentSortOptionLabel: (option: DocumentSortOption) => string
  // 批量
  hasItems: boolean
  batchDeleting: boolean
  batchReparsing: boolean
  batchTagging: boolean
  batchDownloading: boolean
  canDownloadKnowledge: boolean
  canMutateKnowledge: boolean
  // 上传
  canEdit: boolean
  acceptFileTypes: string
  supportedFileTypes: string[]
}>()

// —— 受控状态：v-model 直连父组件 ——
const docSearchKeyword = defineModel<string>('docSearchKeyword', { required: true })
const filtersExpanded = defineModel<boolean>('filtersExpanded', { required: true })
const selectedFileType = defineModel<string | undefined>('selectedFileType', { required: true })
const selectedParseStatus = defineModel<string | undefined>('selectedParseStatus', { required: true })
const selectedSource = defineModel<string | undefined>('selectedSource', { required: true })
const updatedTimeRange = defineModel<string[]>('updatedTimeRange', { required: true })
const selectedTagIds = defineModel<string[]>('selectedTagIds', { required: true })
const tagSearchQuery = defineModel<string>('tagSearchQuery', { required: true })
const viewMode = defineModel<'grid' | 'list'>('viewMode', { required: true })
const batchMode = defineModel<boolean>('batchMode', { required: true })
const documentSortPanelVisible = defineModel<boolean>('documentSortPanelVisible', { required: true })
const selectedDocumentSort = defineModel<DocumentSortOption['value']>('selectedDocumentSort', { required: true })

const emit = defineEmits<{
  (e: 'navigate-folder', path: string): void
  (e: 'toggle-folder-tree', collapsed: boolean): void
  (e: 'reload'): void
  (e: 'clear-filters'): void
  (e: 'load-tags'): void
  (e: 'tag-filter-change', ids: string[]): void
  (e: 'sort-select', value: any): void
  (e: 'toggle-batch'): void
  (e: 'upload-files', files: any): void
  (e: 'upload-url', url: string): void
  (e: 'manual-create'): void
}>()

const { t } = useI18n()

// 父组件持有 KbUploadSourceDropdown 的实例 ref（用于 programmatic open），
// 这里用 expose 转发上去，父组件改成用子组件 ref 拿到。
const uploadSourceRef = ref<InstanceType<typeof KbUploadSourceDropdown> | null>(null)
defineExpose({ uploadSourceRef })
</script>

<template>
  <div class="doc-filter-bar">
    <nav class="doc-folder-path" :aria-label="t('knowledgeBase.folderTree.title')">
      <button v-if="showFolderTree && folderTreeCollapsed" type="button" class="doc-folder-path__tree-toggle"
        :aria-expanded="false" :title="t('knowledgeBase.folderTree.expand')"
        :aria-label="t('knowledgeBase.folderTree.expand')" @click="emit('toggle-folder-tree', false)">
        <t-icon name="view-list" size="16px" />
      </button>
      <button v-if="folderBreadcrumbs.length" type="button" class="doc-folder-path__crumb" :title="kbInfo?.name"
        @click="emit('navigate-folder', '')">
        {{ kbInfo?.name }}
      </button>
      <span v-else class="doc-folder-path__crumb is-current" :title="kbInfo?.name" aria-current="page">{{ kbInfo?.name }}</span>
      <template v-for="(crumb, index) in folderBreadcrumbs" :key="crumb.path">
        <t-icon name="chevron-right" class="doc-folder-path__sep" />
        <span v-if="index === folderBreadcrumbs.length - 1" class="doc-folder-path__crumb is-current" :title="crumb.name"
          aria-current="page">{{ crumb.name }}</span>
        <button v-else type="button" class="doc-folder-path__crumb" :title="crumb.name" @click="emit('navigate-folder', crumb.path)">{{ crumb.name }}</button>
      </template>
      <span v-if="!loading" class="doc-folder-path__count">{{ t(isFiltering ? 'knowledgeBase.folderTree.filteredCount' : 'knowledgeBase.documentCount', { count: total }) }}</span>
      <t-tooltip v-if="showFolderTree && isFiltering" :content="t('knowledgeBase.folderTree.searchingSubtree')">
        <t-icon name="info-circle" class="doc-folder-path__sep" />
      </t-tooltip>
    </nav>
    <div class="doc-filter-bar__trailing">
      <t-input v-model.trim="docSearchKeyword" :placeholder="t('knowledgeBase.docSearchPlaceholder')"
        :aria-label="t('knowledgeBase.docSearchPlaceholder')" clearable class="doc-search-input" @clear="emit('reload')"
        @enter="emit('reload')">
        <template #prefix-icon>
          <t-icon name="search" size="16px" />
        </template>
      </t-input>
      <t-popup v-model:visible="filtersExpanded" trigger="click" placement="bottom-right"
        overlay-class-name="document-filter-popup" :overlay-inner-style="{ padding: 0 }">
        <button type="button" class="doc-filter-toggle" :class="{ active: filtersExpanded || activeFilterCount > 0 }"
          :aria-expanded="filtersExpanded" aria-controls="document-filters">
          <t-icon name="filter" size="16px" />
          {{ t('knowledgeBase.filters') }}
          <span v-if="activeFilterCount" class="doc-filter-count">{{ activeFilterCount }}</span>
        </button>
        <template #content>
          <section id="document-filters" class="doc-filter-panel" :aria-label="t('knowledgeBase.filters')">
            <header class="doc-filter-panel__header">
              <strong>{{ t('knowledgeBase.filters') }}</strong>
              <button type="button" :disabled="!activeFilterCount" @click="emit('clear-filters')">{{ t('knowledgeBase.clearFilters') }}</button>
            </header>
            <div class="doc-filter-panel__fields">
              <div class="doc-filter-field">
                <span>{{ t('knowledgeBase.fileTypeFilter') }}</span>
                <t-select v-model="selectedFileType" :options="fileTypeOptions" :placeholder="t('knowledgeBase.fileTypeFilter')" clearable />
              </div>
              <div class="doc-filter-field">
                <span>{{ t('knowledgeBase.parseStatusFilter') }}</span>
                <t-select v-model="selectedParseStatus" :options="parseStatusOptions" :placeholder="t('knowledgeBase.parseStatusFilter')" clearable />
              </div>
              <div class="doc-filter-field">
                <span>{{ t('knowledgeBase.sourceFilter') }}</span>
                <t-select v-model="selectedSource" :options="sourceOptions" :placeholder="t('knowledgeBase.sourceFilter')" clearable />
              </div>
              <div class="doc-filter-field">
                <span>{{ t('knowledgeBase.columnUpdatedAt') }}</span>
                <t-date-range-picker v-model="updatedTimeRange"
                  :placeholder="[t('knowledgeBase.updatedTimeFrom'), t('knowledgeBase.updatedTimeTo')]"
                  :disable-date="disableFutureDate" clearable allow-input />
              </div>
            </div>
            <div class="doc-filter-tags">
              <div class="doc-filter-tags__heading">
                <span>{{ t('knowledgeBase.columnTag') }}<span v-if="selectedTagIds.length" class="doc-filter-tags__count">{{ selectedTagIds.length }}</span></span>
              </div>
              <t-input v-model.trim="tagSearchQuery" :placeholder="t('knowledgeBase.tagSearchPlaceholder')" clearable>
                <template #prefix-icon><t-icon name="search" size="14px" /></template>
              </t-input>
              <div class="doc-filter-tags__list">
                <t-loading v-if="tagLoading && !filterTagOptions.length" size="small" />
                <t-checkbox v-for="tag in filterTagOptions" :key="tag.id" class="doc-filter-tag" :title="tag.name"
                  :checked="selectedTagIds.includes(tag.id)"
                  @change="(checked: boolean) => emit('tag-filter-change', checked ? [...selectedTagIds, tag.id] : selectedTagIds.filter(id => id !== tag.id))">
                  <span>{{ tag.name }}</span>
                </t-checkbox>
                <span v-if="!tagLoading && !filterTagOptions.length" class="doc-filter-tags__empty">{{ t(tagSearchQuery ? 'knowledgeBase.tagEmptyResult' : 'knowledgeBase.noTags') }}</span>
              </div>
              <t-button v-if="tagHasMore" variant="text" size="small" :loading="tagLoadingMore" @click="emit('load-tags')">{{ t('tenant.loadMore') }}</t-button>
            </div>
          </section>
        </template>
      </t-popup>
      <button v-if="viewMode === 'grid' && (canDownloadKnowledge || canMutateKnowledge) && hasItems"
        type="button" class="doc-filter-toggle doc-batch-toggle" :class="{ active: batchMode }" :aria-pressed="batchMode"
        :disabled="batchDeleting || batchReparsing || batchTagging || batchDownloading" @click="emit('toggle-batch')">
        <t-icon :name="batchMode ? 'close' : 'check-rectangle'" size="16px" />
        {{ t(batchMode ? 'common.cancel' : 'menu.batchManage') }}
      </button>
      <t-popup v-model:visible="documentSortPanelVisible" trigger="click" placement="bottom-right"
        overlay-class-name="document-sort-popup" :overlay-inner-style="{ padding: 0 }">
        <template #content>
          <div class="document-sort-panel" role="menu" :aria-label="t('knowledgeBase.sort.title')">
            <section v-for="group in documentSortGroups" :key="group.key" class="document-sort-group">
              <div class="document-sort-group__heading">
                <div class="document-sort-group__label">{{ group.label }}</div>
                <div class="document-sort-group__description">{{ group.description }}</div>
              </div>
              <div class="document-sort-group__options">
                <button v-for="option in group.options" :key="option.value" type="button" class="document-sort-option"
                  :class="{ active: selectedDocumentSort === option.value }" role="menuitemradio"
                  :aria-checked="selectedDocumentSort === option.value" @click.stop="emit('sort-select', option.value)">
                  <span>{{ documentSortOptionLabel(option) }}</span>
                  <t-icon v-if="selectedDocumentSort === option.value" name="check" size="14px" />
                </button>
              </div>
            </section>
          </div>
        </template>
        <button type="button" class="doc-sort-trigger" :class="{ active: documentSortPanelVisible }"
          :title="`${t('knowledgeBase.sort.title')}: ${activeDocumentSortLabel}`"
          :aria-label="`${t('knowledgeBase.sort.title')}: ${activeDocumentSortLabel}`">
          <t-icon name="filter-sort" size="16px" />
          <span class="doc-sort-trigger__label">
            {{ t('knowledgeBase.sort.title') }} · {{ activeDocumentSortLabel }}
          </span>
          <t-icon name="chevron-down" size="14px" class="doc-sort-trigger__caret" :class="{ open: documentSortPanelVisible }" />
        </button>
      </t-popup>
      <div class="doc-view-toggle" role="group" :aria-label="t('knowledgeBase.viewModeToggle')">
        <t-tooltip :content="t('knowledgeBase.viewModeGrid')" placement="top">
          <button type="button" class="doc-view-toggle-btn" :class="{ active: viewMode === 'grid' }"
            :aria-label="t('knowledgeBase.viewModeGrid')" @click="viewMode = 'grid'" :aria-pressed="viewMode === 'grid'">
            <t-icon name="view-module" size="16px" />
          </button>
        </t-tooltip>
        <t-tooltip :content="t('knowledgeBase.viewModeList')" placement="top">
          <button type="button" class="doc-view-toggle-btn" :class="{ active: viewMode === 'list' }"
            :aria-label="t('knowledgeBase.viewModeList')" @click="viewMode = 'list'" :aria-pressed="viewMode === 'list'">
            <t-icon name="view-list" size="16px" />
          </button>
        </t-tooltip>
      </div>
      <div v-if="canEdit" class="doc-filter-actions">
        <KbUploadSourceDropdown ref="uploadSourceRef" :accept-file-types="acceptFileTypes"
          :supported-file-types="supportedFileTypes" include-manual trigger-icon="add" :trigger-label="t('knowledgeBase.addDocument')"
          trigger-class="content-bar-icon-btn" data-guide="kb-detail-add-doc"
          :tooltip="t('knowledgeBase.addDocument')" placement="bottom-right" @files="emit('upload-files', $event)"
          @url="emit('upload-url', $event)" @manual="emit('manual-create')" />
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
@import './kbDocList.less';
</style>
