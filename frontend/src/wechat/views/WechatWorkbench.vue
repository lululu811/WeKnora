<template>
  <div class="wechat-page-container">
    <!-- 服务不可达时的兜底：iframe 在连接失败时不会报错，只会在框里显示
         浏览器自己的错误页 —— 从外面看就是一片空白， 用户不知道发生了什么。
         这里自己探一次活，探不通就换成一句能看懂的说明 + 重试。 -->
    <div v-if="!reachable" class="wechat-offline">
      <p class="wechat-offline__hint">{{ t('wechat.radar.offlineHint', { url: bizUrl }) }}</p>
      <div class="wechat-offline__actions">
        <t-button size="small" theme="primary" :loading="probing" @click="probe">
          {{ t('wechat.radar.retry') }}
        </t-button>
        <t-button size="small" variant="outline" @click="openExternal">
          {{ t('wechat.radar.openExternal') }}
        </t-button>
      </div>
    </div>

    <iframe
      v-else
      ref="radarIframeRef"
      :src="bizUrl"
      class="wechat-radar-frame"
      frameborder="0"
      @load="onIframeLoad"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useTheme } from '@/composables/useTheme';
import { CHATLOG_BASE_URL, CHATLOG_ORIGIN, chatlogBizUrl } from '@/wechat/config';

const { t } = useI18n();
const { effectiveTheme } = useTheme();
const radarIframeRef = ref<HTMLIFrameElement | null>(null);
const reachable = ref(true);
const probing = ref(false);

const theme = computed<'light' | 'dark'>(() =>
  effectiveTheme.value === 'dark' ? 'dark' : 'light',
);
const bizUrl = computed(() => chatlogBizUrl(theme.value));

/**
 * 往 iframe 投递主题。
 *
 * targetOrigin 钉死成 chatlog 的 origin：iframe 的 src 是可导航的，一旦它
 * 跳到别处，用 '*' 就等于把主题信息发给了第三方页面。
 */
function postTheme(): void {
  const win = radarIframeRef.value?.contentWindow;
  if (!win || !CHATLOG_ORIGIN) return;
  win.postMessage({ type: 'WEKNORA_THEME_CHANGE', theme: theme.value }, CHATLOG_ORIGIN);
}

/**
 * 接收 chatlog 侧的消息。
 *
 * 契约（chatlog 侧尚未实现，先把接收端留好）：
 *   WEKNORA_BIZ_READY    iframe 加载完成，宿主回一次当前主题
 *   WEKNORA_THEME_REQUEST iframe 想要当前主题，宿主回一次
 * 只接受来自 CHATLOG_ORIGIN 的消息 —— 不校验 origin 的 message 监听器
 * 等于给同页任何脚本开了个后门。
 */
function onMessage(event: MessageEvent): void {
  if (!CHATLOG_ORIGIN || event.origin !== CHATLOG_ORIGIN) return;
  const data = event.data as { type?: string } | null;
  if (!data || typeof data !== 'object') return;
  if (data.type === 'WEKNORA_BIZ_READY' || data.type === 'WEKNORA_THEME_REQUEST') {
    postTheme();
  }
}

/** 探一次活：跨域只关心「通不通」，不读响应体。 */
async function probe(): Promise<void> {
  probing.value = true;
  try {
    await fetch(`${CHATLOG_BASE_URL}/biz`, { mode: 'no-cors' });
    reachable.value = true;
  } catch {
    reachable.value = false;
  } finally {
    probing.value = false;
  }
}

function onIframeLoad(): void {
  postTheme();
}

function openExternal(): void {
  window.open(bizUrl.value, '_blank', 'noopener');
}

watch(theme, () => {
  postTheme();
});

onMounted(() => {
  window.addEventListener('message', onMessage);
  void probe();
});

onBeforeUnmount(() => {
  window.removeEventListener('message', onMessage);
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

.wechat-offline {
  margin: auto;
  padding: var(--app-space-6, 24px);
  max-width: 520px;
  text-align: center;
  color: var(--td-text-color-secondary);

  &__hint {
    margin: 0 0 var(--app-space-4, 16px);
    line-height: 1.6;
    word-break: break-all;
  }

  &__actions {
    display: flex;
    gap: var(--app-space-2, 8px);
    justify-content: center;
  }
}
</style>
