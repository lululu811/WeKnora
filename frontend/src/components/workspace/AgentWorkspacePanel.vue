<template>
  <Transition name="workspace-slide" :duration="{ enter: 200, leave: 240 }">
    <div v-if="workspace.isOpen.value && activeComponent" class="agent-workspace-container">
      <aside
        class="agent-workspace-panel"
        :class="{ 'is-resizing': resizing }"
        :style="{ '--agent-workspace-w': `${workspace.width.value}px` }"
        role="complementary"
        aria-label="Agent Workspace"
      >
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
      </aside>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { useAgentWorkspace, WORKSPACE_MIN_WIDTH, WORKSPACE_MAX_WIDTH } from '@/composables/useAgentWorkspace';
import { WORKSPACE_COMPONENTS } from './registry';
import PanelResizeHandle from '@/components/PanelResizeHandle.vue';

const workspace = useAgentWorkspace();
const resizing = ref(false);
let startWidth = 0;

const activeComponent = computed(() => {
  return WORKSPACE_COMPONENTS[workspace.activeType.value] || null;
});

const startResize = () => {
  resizing.value = true;
  startWidth = workspace.width.value;
};

const handleResize = (delta: number) => {
  // 左侧把手：鼠标向左拖动（delta < 0），工作台宽度变大
  workspace.setWidth(startWidth - delta);
};
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
  background: var(--td-bg-color-container, #ffffff);
  border-left: 1px solid var(--td-component-stroke, #e7e7e7);
  box-shadow: -4px 0 16px rgba(0, 0, 0, 0.06);

  // 宽度走 CSS 变量而非行内 width，这样下面的断点能覆盖它
  width: var(--agent-workspace-w, 560px);

  &.is-resizing {
    transition: none;
    user-select: none;
  }

  // 窄屏：不再与聊天区并排（.chat 在此断点不做 padding 让位），
  // 改为整屏覆盖，否则 560px 面板会直接盖住聊天内容。
  @media (max-width: 959.98px) {
    width: 100vw;
  }
}

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
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.workspace-slide-enter-from,
.workspace-slide-leave-to {
  transform: translateX(100%);
}
</style>
