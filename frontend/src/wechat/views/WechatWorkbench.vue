<template>
  <div class="wechat-page-container">
    <iframe
      ref="radarIframeRef"
      :src="radarIframeUrl"
      class="wechat-radar-frame"
      frameborder="0"
      @load="onIframeLoad"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useTheme } from '@/composables/useTheme';

const { effectiveTheme } = useTheme();
const radarIframeRef = ref<HTMLIFrameElement | null>(null);

const radarIframeUrl = computed(() => {
  const theme = effectiveTheme.value === 'dark' ? 'dark' : 'light';
  return `http://127.0.0.1:5030/biz?embed=1&theme=${theme}`;
});

function onIframeLoad() {
  radarIframeRef.value?.contentWindow?.postMessage(
    { type: 'WEKNORA_THEME_CHANGE', theme: effectiveTheme.value },
    '*',
  );
}

watch(effectiveTheme, (theme) => {
  radarIframeRef.value?.contentWindow?.postMessage(
    { type: 'WEKNORA_THEME_CHANGE', theme },
    '*',
  );
});
</script>

<style lang="less" scoped>
.wechat-page-container {
  display: flex;
  height: 100%;
  width: 100%;
  overflow: hidden;
  background: var(--td-bg-color-page);
}

.wechat-radar-frame {
  width: 100%;
  height: 100%;
  border: none;
  display: block;
}
</style>
