<template>
  <div class="kb-list-header">
    <div class="header" style="--wails-draggable: drag">
      <div class="header-title" style="--wails-draggable: drag">
        <div class="title-row" style="--wails-draggable: drag">
          <h2 style="--wails-draggable: drag">
            <ResourceIcon type="knowledge" :size="24" />
            {{ $t('knowledgeBase.title') }}
          </h2>
          <div class="header-actions" style="--wails-draggable: no-drag">
            <ResourceSortControl v-model="sortModel" />
            <t-tooltip
              v-if="canCreate"
              :content="$t('knowledgeList.create')"
              placement="bottom"
            >
              <t-button
                theme="primary"
                variant="base"
                size="small"
                class="header-action-btn kb-create-btn"
                data-guide="kb-list-create"
                style="--wails-draggable: no-drag"
                @click="emit('create')"
              >
                <template #icon><t-icon name="folder-add" size="16px" /></template>
                {{ $t('knowledgeList.create') }}
              </t-button>
            </t-tooltip>
          </div>
        </div>
        <p class="header-subtitle" style="--wails-draggable: drag">{{ $t('knowledgeList.subtitle') }}</p>
      </div>
    </div>

    <ResourceListToolbar
      :hide-scopes="isLiteMode"
      :model-value="scope"
      :query="query"
      :count-all="countAll"
      :count-mine="countMine"
      :count-by-org="countByOrg"
      :count-favorites="countFavorites"
      :count-recents="countRecents"
      @update:model-value="emit('update:scope', $event)"
      @update:query="emit('update:query', $event)"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ResourceIcon from '@/components/icons/ResourceIcon.vue'
import ResourceListToolbar from '@/components/ResourceListToolbar.vue'
import ResourceSortControl from '@/components/ResourceSortControl.vue'
import { useAuthStore } from '@/stores/auth'
import { DEFAULT_RESOURCE_SORT, type ResourceSortValue } from '@/utils/resourceSorting'

/**
 * 列表页头部：标题 + 排序 + 新建按钮 + 筛选栏（scope / 搜索）。
 *
 * 拆分前这块与卡片网格同处一个 SFC，但它只依赖「角色 + 筛选状态 + 计数」，
 * 不碰任何数据加载逻辑，因此可以整块搬走。列表页仍持有全部状态与副作用，
 * 这里仅做受控渲染。
 */
const props = withDefaults(
  defineProps<{
    sortValue?: ResourceSortValue
    scope: string
    query: string
    countAll: number
    countMine: number
    countByOrg: Record<string, number>
    countFavorites: number
    countRecents: number
  }>(),
  { sortValue: DEFAULT_RESOURCE_SORT },
)

const emit = defineEmits<{
  'update:sortValue': [value: ResourceSortValue]
  'update:scope': [value: string]
  'update:query': [value: string]
  create: []
}>()

const authStore = useAuthStore()

const canCreate = computed(() => authStore.hasRole('contributor'))
const isLiteMode = computed(() => authStore.isLiteMode)

/** v-model 代理：子组件不直接改 props，统一经 emit 回吐给列表页。 */
const sortModel = computed({
  get: () => props.sortValue,
  set: v => emit('update:sortValue', v),
})
</script>

<style scoped lang="less">
.header {
  padding: 20px 24px 12px;
  background: transparent;
  user-select: none;
}

.header-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;

  h2 {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    font-size: var(--app-text-3xl);
    font-weight: 600;
    color: var(--td-text-color-primary);
  }
}

.header-subtitle {
  margin: 0;
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

/* 方向 A「午后的工作室」：新建按钮是桌面上的实色便签贴。
   珊瑚实底 + 白字，按下时轻轻陷回桌面。 */
.kb-create-btn {
  border-radius: var(--app-radius-lg);
  border: 1px solid var(--td-brand-color);
  background: var(--td-brand-color);
  color: var(--td-text-color-anti);
  font-weight: 500;
  transition:
    background var(--app-motion-base) ease,
    transform var(--app-motion-base) cubic-bezier(0.16, 1, 0.3, 1),
    box-shadow var(--app-motion-base) ease;

  &:hover {
    background: var(--td-brand-color-hover);
    border-color: var(--td-brand-color-hover);
    box-shadow: var(--td-shadow-2);
  }

  &:active {
    transform: scale(0.98);
    box-shadow: var(--td-shadow-1);
  }
}
</style>
