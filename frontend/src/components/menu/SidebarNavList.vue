<template>
    <div class="sidebar-nav-list">
        <!-- 折叠态保留 ⌘K 搜索入口（展开态在 shell 的 logo 行） -->
        <div v-if="collapsed" class="menu_box menu_box--cmdk">
            <t-tooltip placement="right">
                <template #content>
                    <span class="cmdk-tip">
                        <span class="cmdk-tip-label">{{ t('menu.search') }}</span>
                        <span class="cmdk-tip-keys">{{ cmdModKeyLabel }}K</span>
                    </span>
                </template>
                <div class="menu_item menu_item--cmdk" @click="$emit('open-search')">
                    <div class="menu_item-box">
                        <div class="menu_icon">
                            <img class="icon" :src="getImgSrc('search.svg')" alt="">
                        </div>
                    </div>
                </div>
            </t-tooltip>
        </div>

        <!-- 导航项：首次进入用 useStaggerRise 依次浮起 -->
        <div
            v-for="item in items"
            :key="item.path"
            class="menu_box nav-rise"
            :class="{ 'menu_box--sticky': item.children && !collapsed }"
        >
            <t-tooltip :content="item.title" placement="right" :disabled="!collapsed">
                <div
                    @click="$emit('select', item.path)"
                    @mouseenter="$emit('hover', item.path)"
                    @mouseleave="$emit('leave', item.path)"
                    :data-guide="`nav-${item.path}`"
                    :class="[
                        'menu_item',
                        item.childrenPath && item.childrenPath === currentPath
                            ? 'menu_item_c_active'
                            : isActive(item.path)
                                ? 'menu_item_active'
                                : '',
                    ]"
                >
                    <div class="menu_item-box">
                        <div class="menu_icon">
                            <img class="icon" :src="resolveIcon(item)" alt="">
                        </div>
                        <template v-if="!collapsed">
                            <span class="menu_title" :title="item.title">{{ item.title }}</span>

                            <span
                                v-if="item.path === 'organizations' && orgPendingCount > 0"
                                class="menu-pending-badge"
                                :title="t('organization.settings.pendingJoinRequestsBadge')"
                            >{{ orgPendingCount }}</span>

                            <span
                                v-if="item.path === 'toolbox' && toolboxPreview.length"
                                class="menu-toolbox-stack"
                                :title="toolboxPreview
                                    .map((tool) =>
                                        tool.key === 'browserconnection' && browserStackStatus
                                            ? `${t(tool.title)} (${t(`localBrowser.${browserStackStatus}`)})`
                                            : t(tool.title),
                                    )
                                    .join(' · ')"
                            >
                                <span v-for="tool in toolboxPreview" :key="tool.key" class="menu-toolbox-stack__item">
                                    <template v-if="tool.key === 'browserconnection'">
                                        <BrowserIcon width="12" height="12" />
                                        <i
                                            v-if="browserStackStatus"
                                            class="menu-toolbox-stack__status"
                                            :class="`is-${browserStackStatus}`"
                                            aria-hidden="true"
                                        />
                                    </template>
                                    <t-icon v-else :name="tool.icon" size="12px" />
                                </span>
                            </span>
                        </template>
                    </div>
                </div>
            </t-tooltip>
        </div>
    </div>
</template>

<script setup lang="ts">
/**
 * SidebarNavList —— 主导航项列表（新对话 / 知识库 / 智能体 / 共享空间 …）。
 *
 * 纯展示 + 事件回传：激活判定、图标切换、路由跳转都留在 menu.vue，
 * 这里只接收结果并把交互以 emit 抛回去，保证拆分不改变数据流。
 */
import { nextTick, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { Icon as TIcon } from 'tdesign-vue-next';
import BrowserIcon from '@/components/icons/BrowserIcon.vue';
import { useStaggerRise } from '@/composables/useMotion';
import type { BrowserStackStatus, MenuItem, ToolboxPreviewItem } from './menuTypes';

const props = defineProps<{
  /** 顶部导航项（menu.vue 已按 BOTTOM_MENU_PATHS 排除法过滤）。 */
  items: MenuItem[];
  /** 侧栏折叠态：折叠时只显示图标 + tooltip。 */
  collapsed: boolean;
  /** 当前路由名，用于 childrenPath 命中判断。 */
  currentPath: string;
  /** 由 menu.vue 提供的统一激活判定（保持单一事实来源）。 */
  isActive: (path: string) => boolean;
  /** 图标名 → 图片 URL 的解析函数。 */
  resolveIcon: (item: MenuItem) => string;
  /** 工具箱图标叠加预览项。 */
  toolboxPreview: ToolboxPreviewItem[];
  /** 浏览器连接状态（空串表示不展示）。 */
  browserStackStatus: BrowserStackStatus;
  /** 组织待审批数，>0 时显示角标。 */
  orgPendingCount: number;
  /** ⌘K 提示：Mac 显示 ⌘，其它平台显示 Ctrl。 */
  cmdModKeyLabel: string;
}>();

defineEmits<{
  (e: 'select', path: string): void;
  (e: 'hover', path: string): void;
  (e: 'leave', path: string): void;
  (e: 'open-search'): void;
}>();

const { t } = useI18n();

const getImgSrc = (url: string) => new URL(`/src/assets/img/${url}`, import.meta.url).href;

// 首次进入：导航项 12px 位移、60ms stagger 依次浮起（Direction A 动效预算内）
const root = ref<HTMLElement | null>(null);
const { rise } = useStaggerRise({ stagger: 60, displacement: 12 });

onMounted(async () => {
    await nextTick();
    if (!root.value) return;
    rise(root.value.querySelectorAll<HTMLElement>('.nav-rise'));
});

// 导航项集合随能力开关/权限变化时，补一次浮起，让新增项也有入场反馈
watch(
    () => props.items.map((item) => item.path).join(','),
    async () => {
        await nextTick();
        if (!root.value) return;
        rise(root.value.querySelectorAll<HTMLElement>('.nav-rise'));
    },
);
</script>
