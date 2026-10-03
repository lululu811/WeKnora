<template>
    <!--
        SidebarShell —— 侧栏外壳。
        只负责几何：logo 行、折叠/展开、resize 手柄、租户选择器、汉堡按钮。
        列表内容由 SidebarNavList / SidebarSessionList / SidebarUserArea 承担，
        本组件通过 slot 透传，不感知任何会话数据。
    -->
    <div
        class="aside_box"
        :class="{
            'aside_box--collapsed': collapsed,
            'aside_box--resizing': resizing,
            'aside_box--drawer-open': drawerOpen,
        }"
    >
        <!-- 展开态：Logo + 搜索/折叠按钮同行 -->
        <div class="logo_row" v-if="!collapsed">
            <div class="logo_box" @click="$emit('go-home')">
                <span class="logo logo-text">{{ appName }}</span>
                <sup v-if="isLiteEdition" class="lite-badge">Lite</sup>
            </div>
            <div class="logo_actions">
                <t-tooltip placement="bottom">
                    <template #content>
                        <span class="cmdk-tip">
                            <span class="cmdk-tip-label">{{ t('menu.search') }}</span>
                            <span class="cmdk-tip-keys">{{ cmdModKeyLabel }}K</span>
                        </span>
                    </template>
                    <div class="header-icon-btn" @click="$emit('open-search')" :aria-label="t('menu.search')">
                        <img class="header-icon-img" :src="getImgSrc('search.svg')" alt="">
                    </div>
                </t-tooltip>
                <div class="sidebar-toggle" @click="$emit('toggle')" :title="t('menu.collapseSidebar')">
                    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <rect x="1.5" y="1.5" width="17" height="17" rx="3" stroke="currentColor" stroke-width="1.2" />
                        <line x1="7.5" y1="1.5" x2="7.5" y2="18.5" stroke="currentColor" stroke-width="1.2" />
                        <line x1="4" y1="7.5" x2="4" y2="12.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
                    </svg>
                </div>
            </div>
        </div>

        <!-- 折叠态：展开按钮 -->
        <t-tooltip v-else :content="t('menu.expandSidebar')" placement="right">
            <div class="menu_item sidebar-toggle-item" @click="$emit('toggle')">
                <div class="menu_item-box">
                    <div class="menu_icon">
                        <svg class="icon" viewBox="0 0 20 20" width="20" height="20" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <rect x="1.5" y="1.5" width="17" height="17" rx="3" stroke="currentColor" stroke-width="1.2" />
                            <line x1="7.5" y1="1.5" x2="7.5" y2="18.5" stroke="currentColor" stroke-width="1.2" />
                            <line x1="5" y1="10" x2="3" y2="8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
                            <line x1="5" y1="10" x2="3" y2="12" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
                        </svg>
                    </div>
                </div>
            </div>
        </t-tooltip>

        <!-- 空间选择器：仅在用户可切换空间时显示 -->
        <TenantSelector v-if="canAccessAllTenants && !collapsed" />

        <!-- 侧栏边缘拖拽调宽，拖窄时自动收缩 -->
        <PanelResizeHandle
            edge="right"
            :label="resizeLabel"
            :value="displayWidth"
            :min="collapsedWidth"
            :max="maxWidth"
            @start="$emit('resize-start')"
            @resize="(delta: number, keyboard: boolean) => $emit('resize', delta, keyboard)"
            @end="$emit('resize-end')"
        />

        <slot />
    </div>
</template>

<script setup lang="ts">
/**
 * SidebarShell —— 侧栏外壳（阶段 3.1 拆分产物）。
 *
 * 纯几何组件：不知道会话、不发请求、不写 store。menu.vue 仍然持有全部状态
 * 与副作用，只把结果作为 props 传进来、把交互作为 emit 接回去。
 */
import { useI18n } from 'vue-i18n';
import PanelResizeHandle from '../PanelResizeHandle.vue';
import TenantSelector from '../TenantSelector.vue';

defineProps<{
  /** 侧栏折叠态。 */
  collapsed: boolean;
  /** 拖拽中（关闭宽度过渡）。 */
  resizing: boolean;
  /** <480px 抽屉是否展开。 */
  drawerOpen: boolean;
  /** 顶部展示的应用名。 */
  appName: string;
  /** lite 版本角标。 */
  isLiteEdition: boolean;
  /** 是否可切换空间（决定租户选择器是否渲染）。 */
  canAccessAllTenants: boolean;
  /** ⌘K 提示前缀。 */
  cmdModKeyLabel: string;
  /** resize 手柄的 aria label。 */
  resizeLabel: string;
  /** 当前展示宽度（折叠态为 60）。 */
  displayWidth: number;
  /** 折叠宽度下限。 */
  collapsedWidth: number;
  /** 宽度上限。 */
  maxWidth: number;
}>();

defineEmits<{
  (e: 'toggle'): void;
  (e: 'open-search'): void;
  (e: 'go-home'): void;
  (e: 'resize-start'): void;
  (e: 'resize', delta: number, keyboard: boolean): void;
  (e: 'resize-end'): void;
}>();

const { t } = useI18n();

const getImgSrc = (url: string) => new URL(`/src/assets/img/${url}`, import.meta.url).href;
</script>
