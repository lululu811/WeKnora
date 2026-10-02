<template>
  <Transition name="workspace-slide" :duration="{ enter: 200, leave: 240 }">
    <div v-if="workspace.isOpen.value && activeComponent" class="agent-workspace-container">
      <aside
        class="agent-workspace-panel"
        :class="{ 'is-resizing': resizing, 'is-collapsed': workspace.isCollapsed.value }"
        :style="{ '--agent-workspace-w': `${workspace.effectiveWidth.value}px` }"
        role="complementary"
        aria-label="Agent Workspace"
      >
        <!-- 折叠态：整条窄边就是展开把手，组件不卸载，K 线状态原样留着 -->
        <button
          v-if="workspace.isCollapsed.value"
          type="button"
          class="agent-workspace-rail"
          title="展开工作台"
          @click="workspace.toggleCollapsed()"
        >
          <t-icon name="chevron-left" size="16px" />
          <span class="agent-workspace-rail__text">{{ railLabel }}</span>
        </button>

        <template v-else>
          <!-- 左侧拖拽拉手 -->
          <PanelResizeHandle
            edge="left"
            label="调整工作台宽度"
            :value="workspace.width.value"
            :min="WORKSPACE_MIN_WIDTH"
            :max="WORKSPACE_MAX_WIDTH"
            @start="startResize"
            @resize="handleResize"
            @end="resizing = false"
          />

          <!-- 动态工作台挂载插槽 -->
          <div class="agent-workspace-body">
            <component :is="activeComponent" />
          </div>
        </template>
      </aside>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { useAgentWorkspace, WORKSPACE_MIN_WIDTH, WORKSPACE_MAX_WIDTH } from '@/composables/useAgentWorkspace';
import { useCurrentWorkbenchComponents } from '@/composables/useWorkbench';
import { WORKSPACE_COMPONENTS, WORKSPACE_LABELS } from './registry';
import PanelResizeHandle from '@/components/PanelResizeHandle.vue';

const workspace = useAgentWorkspace();
const resizing = ref(false);
let startWidth = 0;

const { allowedComponents } = useCurrentWorkbenchComponents();

// 面板只渲染「当前 agent 所属工作台允许」的组件。
// 工作台由 agent 推导（session → agent → workbench），因此换 agent 即换工作台；
// 允许哪些组件由后端词表决定——前端不硬编码 workbench→component 映射，
// 避免与 Go 侧词表漂移。未注册 / 拼错 / 无工作台一律不渲染。
const activeComponent = computed(() => {
  const type = workspace.activeType.value;
  if (type === 'none') return null;
  if (!allowedComponents.value.includes(type)) return null;
  return WORKSPACE_COMPONENTS[type] || null;
});

const startResize = () => {
  resizing.value = true;
  startWidth = workspace.width.value;
};

const handleResize = (delta: number) => {
  // 左侧把手：鼠标向左拖动（delta < 0），工作台宽度变大
  workspace.setWidth(startWidth - delta);
};

// 折叠窄边上竖排显示的名字；没登记就退回类型名，不留空白条。
const railLabel = computed(() => WORKSPACE_LABELS[workspace.activeType.value] || workspace.activeType.value);
</script>

<style lang="less" scoped>
.agent-workspace-container {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 20;
}

.agent-workspace-panel {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  pointer-events: auto;
  display: flex;
  flex-direction: column;
  background: var(--td-bg-color-container);
  border-left: 1px solid var(--td-component-stroke);
  box-shadow: -4px 0 16px rgba(0, 0, 0, 0.06);

  // 宽度走 CSS 变量而非行内 width，这样下面的断点能覆盖它
  width: var(--agent-workspace-w, 560px);

  // 折叠/展开走宽度过渡。注意不能放在 .is-resizing 里——拖拽时要即时跟手。
  transition: width var(--app-motion-slow) cubic-bezier(0.16, 1, 0.3, 1);

  &.is-resizing {
    transition: none;
    user-select: none;
  }

  // 窄屏：不再与聊天区并排（.chat 在此断点不做 padding 让位），
  // 改为整屏覆盖，否则 560px 面板会直接盖住聊天内容。
  // 折叠态要排除在这条之外——整屏宽的竖排把手没法看，留 36px 窄边才合理。
  @media (max-width: 959.98px) {
    &:not(.is-collapsed) {
      width: 100vw;
    }
  }
}

// 折叠态：竖排把手就是整个面板，hover 时提示展开
.agent-workspace-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 100%;
  padding: 12px 0;
  border: 0;
  border-left: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-secondarycontainer);
  color: var(--td-text-color-secondary);
  cursor: pointer;
  transition: background-color var(--app-motion-base), color var(--app-motion-base);

  &:hover {
    background: var(--td-bg-color-container-hover);
    color: var(--td-brand-color);
  }
}

.agent-workspace-rail__text {
  writing-mode: vertical-rl;
  letter-spacing: 2px;
  font-size: var(--app-text-sm);
  font-weight: 600;
}

// 展开态的折叠把手：贴左边框中线，和上方拖拽拉手不重叠

.agent-workspace-body {
  flex: 1;
  min-height: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.workspace-slide-enter-active,
.workspace-slide-leave-active {
  transition: transform var(--app-motion-base) cubic-bezier(0.16, 1, 0.3, 1);
}

.workspace-slide-enter-from,
.workspace-slide-leave-to {
  transform: translateX(100%);
}
</style>
