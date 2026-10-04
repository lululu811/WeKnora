<template>
    <div class="submenu">
        <!-- 稳定挂载的来源筛选器：预留在固定行，避免切换会话类型时右上角控件跳动 -->
        <div v-if="showSourceFilter && !batchMode" class="session-list-scope-header">
            <SessionSourceFilter
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
                    <div v-if="group.label" class="timeline_header session-list-row session-list-row--flat">
                        <span class="session-list-row__body">
                            <span class="timeline_header-label">{{ group.label }}</span>
                        </span>
                    </div>
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
 * 分组、筛选、分页、批量操作全部由 menu.vue 计算好后传入；本组件只做渲染
 * 与事件回传。折叠态下整块不渲染（与拆分前 `v-if` 位置一致）。
 */
import { useI18n } from 'vue-i18n';
import SessionSidebarRow from '../SessionSidebarRow.vue';
import SessionSourceFilter from '../SessionSourceFilter.vue';
import type { SidebarSessionBucket } from '../sessionSidebarBuckets';
import type { SessionSourceOption } from '../sessionSidebarSourceFilter';
import type { SessionMenuOption, SessionRowView, SidebarSessionGroup } from './menuTypes';

defineProps<{
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
// 而 SessionSidebarRow 早先发的是**空载荷**，于是批量勾选一直拿到 undefined。
defineEmits<{
  (e: 'select-source', key: string): void;
  (e: 'navigate', path: string): void;
  (e: 'toggle-select', id: string): void;
  (e: 'menu-click', payload: { value: string }, row: SessionRowView): void;
  (e: 'rename-submit', row: SessionRowView, title: string): void;
}>();

const { t } = useI18n();
</script>
