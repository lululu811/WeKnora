<template>
    <div class="submenu">
        <!-- 稳定挂载的来源筛选器与分组折叠控制：预留在固定行，避免切换会话类型时控件跳动 -->
        <div class="session-list-scope-header">
            <button
                v-if="groups.length > 0"
                type="button"
                class="session-list-collapse-toggle"
                :aria-label="allGroupsCollapsed ? t('menu.expandAllGroups') : t('menu.collapseAllGroups')"
                :title="allGroupsCollapsed ? t('menu.expandAllGroups') : t('menu.collapseAllGroups')"
                @click="toggleAllGroups"
            >
                <t-icon :name="allGroupsCollapsed ? 'chevron-down' : 'chevron-up'" size="12px" />
            </button>
            <SessionSourceFilter
                v-if="showSourceFilter && !batchMode"
                inline
                :emphasized="sourceFilterPinned"
                :sources="sourceOptions"
                :current="activeBucketKey"
                @select="$emit('select-source', $event)"
            />
        </div>

        <!-- 首屏骨架 -->
        <template v-if="booting && !hasAnySession">
            <div v-for="n in 4" :key="'skel-' + n" class="submenu_item_p session-chat-row">
                <div class="session-list-row session-list-row--flat">
                    <t-skeleton animation="gradient" class="session-list-row__body" :row-col="[{ width: '100%', height: '14px' }]" />
                </div>
            </div>
        </template>

        <div v-else class="session-filtered-list">
            <!-- 当前来源首屏加载中 -->
            <template v-if="activeBucket?.loading && !activeBucket.loaded && groups.length === 0">
                <div v-for="n in 4" :key="'bucket-skel-' + n" class="submenu_item_p session-chat-row">
                    <div class="session-list-row session-list-row--flat">
                        <t-skeleton animation="gradient" class="session-list-row__body" :row-col="[{ width: '100%', height: '14px' }]" />
                    </div>
                </div>
            </template>

            <!-- 空态 -->
            <template v-else-if="activeBucket?.loaded && groups.length === 0">
                <div class="submenu_empty">{{ t('menu.noSessions') }}</div>
            </template>

            <template v-else>
                <template v-for="group in groups" :key="group.key">
                    <div
                        v-if="group.label"
                        class="timeline_header timeline_header--collapsible session-list-row session-list-row--flat"
                        role="button"
                        tabindex="0"
                        :aria-expanded="!isGroupCollapsed(group.key)"
                        :aria-label="`${group.label} (${group.items.length})`"
                        @click="toggleGroup(group.key)"
                        @keydown.enter.prevent="toggleGroup(group.key)"
                        @keydown.space.prevent="toggleGroup(group.key)"
                    >
                        <span class="session-list-row__body timeline_header__body">
                            <t-icon
                                name="chevron-down"
                                size="12px"
                                class="timeline_header-arrow"
                                :class="{ 'timeline_header-arrow--collapsed': isGroupCollapsed(group.key) }"
                            />
                            <span class="timeline_header-label">{{ group.label }}</span>
                            <span class="timeline_header-count">{{ group.items.length }}</span>
                        </span>
                    </div>
                    <div
                        v-if="!isGroupCollapsed(group.key)"
                        class="timeline_group-items"
                    >
                        <div
                            v-for="row in group.items"
                            :key="row.id"
                            class="submenu_item_p session-chat-row"
                            :data-session-id="row.id"
                            :class="{
                                'session-chat-row--active': !batchMode && row.path === activeSessionPath,
                                'session-chat-row--selected': batchMode && selectedIds.includes(row.id),
                                'session-chat-row--revealed': revealedSessionId === row.id,
                            }"
                        >
                            <div class="session-list-row session-list-row--flat">
                                <div class="session-list-row__body">
                                    <SessionSidebarRow
                                        :item="row"
                                        :batch-mode="batchMode"
                                        :running="Boolean(activityById[row.id])"
                                        :active-path="activeSessionPath"
                                        :selected-ids="selectedIds"
                                        :menu-options="buildMenuOptions(row)"
                                        @navigate="$emit('navigate', $event)"
                                        @toggle-select="$emit('toggle-select', $event)"
                                        @menu-click="$emit('menu-click', $event, row)"
                                        @rename-submit="$emit('rename-submit', row, $event.title)"
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                </template>

                <div
                    v-if="activeBucket?.loading && groups.length > 0"
                    class="session-list-loading session-list-row session-list-row--flat"
                >
                    <span class="session-list-row__body">
                        <t-loading size="small" />
                    </span>
                </div>
            </template>
        </div>
    </div>
</template>

<script setup lang="ts">
/**
 * SidebarSessionList —— 会话历史列表。
 *
 * 分组、筛选、分页、批量操作全部由 menu.vue 计算好后传入；本组件负责渲染
 * 与事件回传。支持按智能体（及置顶）分组进行折叠与展开，并持久化到本地存储。
 */
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import SessionSidebarRow from '../SessionSidebarRow.vue';
import SessionSourceFilter from '../SessionSourceFilter.vue';
import type { SidebarSessionBucket } from '../sessionSidebarBuckets';
import type { SessionSourceOption } from '../sessionSidebarSourceFilter';
import type { SessionMenuOption, SessionRowView, SidebarSessionGroup } from './menuTypes';

const props = defineProps<{
  /** 分组后的会话（按日期或按智能体）。 */
  groups: SidebarSessionGroup<SessionRowView>[];
  /** 首屏加载中。 */
  booting: boolean;
  /** 是否已有任意会话（区分骨架与空态）。 */
  hasAnySession: boolean;
  /** 当前来源的 bucket，用于 loading / loaded 判定。 */
  activeBucket?: SidebarSessionBucket;
  /** 是否展示来源筛选器。 */
  showSourceFilter: boolean;
  /** 筛选器是否被锁定（非默认来源）。 */
  sourceFilterPinned: boolean;
  /** 来源筛选项。 */
  sourceOptions: SessionSourceOption[];
  /** 当前来源 key。 */
  activeBucketKey: string;
  /** 批量管理模式。 */
  batchMode: boolean;
  /** 已选会话 id。 */
  selectedIds: string[];
  /** 当前会话路径。 */
  activeSessionPath: string;
  /** 本地新建 fork 的高亮 id。 */
  revealedSessionId: string;
  /** 运行中会话表（来自 sessionActivity store）。 */
  activityById: Record<string, unknown>;
  /** 行菜单项构造（留在 menu.vue，避免 t() 与图标状态分叉）。 */
  buildMenuOptions: (row: SessionRowView) => SessionMenuOption[];
}>();

// 载荷必须与 menu.vue 的处理器对齐：那边的 toggleBatchSelect(id) 要 id，
// 而 SessionSidebarRow 早先发的是空载荷，于是批量勾选一直拿到 undefined。
defineEmits<{
  (e: 'select-source', key: string): void;
  (e: 'navigate', path: string): void;
  (e: 'toggle-select', id: string): void;
  (e: 'menu-click', payload: { value: string }, row: SessionRowView): void;
  (e: 'rename-submit', row: SessionRowView, title: string): void;
}>();

const { t } = useI18n();

import {
    areAllGroupsCollapsed,
    ensureActiveGroupExpanded,
    readCollapsedGroupsFromStorage,
    toggleAllGroupKeys,
    toggleGroupKey,
    writeCollapsedGroupsToStorage,
} from '../sessionGroupCollapse';

const collapsedGroups = ref<Set<string>>(readCollapsedGroupsFromStorage());

function isGroupCollapsed(key: string): boolean {
    return collapsedGroups.value.has(key);
}

function toggleGroup(key: string) {
    const next = toggleGroupKey(collapsedGroups.value, key);
    collapsedGroups.value = next;
    writeCollapsedGroupsToStorage(next);
}

const allGroupKeys = computed(() => props.groups.map((g) => g.key));

const allGroupsCollapsed = computed(() =>
    areAllGroupsCollapsed(collapsedGroups.value, allGroupKeys.value),
);

function toggleAllGroups() {
    const next = toggleAllGroupKeys(collapsedGroups.value, allGroupKeys.value);
    collapsedGroups.value = next;
    writeCollapsedGroupsToStorage(next);
}

// 仅在主动切换会话（activeSessionPath 改变）或新建会话（revealedSessionId 改变）时自动展开目标分组
// 避免在用户手动折叠分组后，因列表数据刷新而反复被强制重新展开
let lastHandledPath = '';
let lastHandledRevealed = '';

watch(
    () => [props.activeSessionPath, props.revealedSessionId] as const,
    ([activePath, revealedId]) => {
        if (!props.groups.length) return;
        if (activePath === lastHandledPath && revealedId === lastHandledRevealed) return;
        lastHandledPath = activePath;
        lastHandledRevealed = revealedId;

        const { collapsed: next, changed } = ensureActiveGroupExpanded(
            collapsedGroups.value,
            props.groups,
            activePath,
            revealedId,
        );
        if (changed) {
            collapsedGroups.value = next;
            writeCollapsedGroupsToStorage(next);
        }
    },
    { immediate: true },
);

</script>
