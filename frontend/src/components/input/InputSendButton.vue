<script setup lang="ts">
/**
 * 发送 / 停止按钮。
 *
 * 生成中且输入为空时显示「停止」；一旦有新内容，同一位置变成「投递」——
 * 位置不变、语义切换是这个输入区的一条既有约定，所以两个状态共用同一个容器。
 *
 * 点击发送时先触发 useSendFlight 的投递动效（文字从输入框飞向消息区），再把事件
 * 交给父组件真正发起会话；动效失败或用户开了「减少动态效果」时自动跳过，不影响
 * 发送本身。
 */
import { computed, type PropType } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSendFlight } from '@/composables/useMotion'
import type { SendButtonState } from './input.types'

const props = defineProps({
  isReplying: { type: Boolean, default: false },
  canSteer: { type: Boolean, default: false },
  /** 输入框是否有内容 */
  hasText: { type: Boolean, default: false },
  /** 会话进行中禁止改动的锁 */
  locked: { type: Boolean, default: false },
  /** 投递动效要飞的那段文字 */
  flightText: { type: String, default: '' },
  /** 动效起点：输入框元素 */
  sourceEl: { type: Object as PropType<HTMLElement | null>, default: null },
  /** 动效终点：消息列表容器 */
  targetEl: { type: Object as PropType<HTMLElement | null>, default: null },
  /** 消息区在输入框上方时改为向上飞 */
  direction: { type: String as PropType<'up' | 'down'>, default: 'down' },
})

const emit = defineEmits<{
  (e: 'send'): void
  (e: 'stop'): void
}>()

const { t: $t } = useI18n()

const sourceRef = computed(() => props.sourceEl)
const targetRef = computed(() => props.targetEl)
const { trigger } = useSendFlight({
  sourceRef: sourceRef as any,
  targetRef: targetRef as any,
  direction: computed(() => props.direction) as any,
})

const disabled = computed(() => !props.hasText || props.locked)
/** 生成中且没有新内容时才是「停止」；生成中输入了内容则变成 steer 投递。 */
const showStop = computed(() => props.isReplying && (!props.canSteer || !props.hasText))
const label = computed(() =>
  props.isReplying && props.canSteer ? $t('input.steerAfter') : $t('input.send'),
)

const onClick = () => {
  if (showStop.value) {
    emit('stop')
    return
  }
  if (props.flightText) trigger(props.flightText)
  emit('send')
}
</script>

<template>
  <div class="control-right">
    <t-tooltip v-if="showStop" :content="$t('input.stopGeneration')" placement="top">
      <button type="button" class="control-btn stop-btn" :aria-label="$t('input.stopGeneration')" @click="onClick">
        <t-icon name="stop" />
      </button>
    </t-tooltip>
    <t-tooltip v-else :content="`${label} · Enter`">
      <button
        type="button"
        class="control-btn send-btn"
        data-guide="chat-send"
        :disabled="disabled"
        :class="{ disabled }"
        :aria-label="label"
        @click="onClick"
      >
        <t-icon name="arrow-up" />
      </button>
    </t-tooltip>
  </div>
</template>

<style scoped lang="less">
/* Direction A：发送按钮是暖光区里唯一的实色块，作为投递动作的视觉锚点。 */
.control-right {
  display: flex;
  align-items: center;
  gap: var(--app-space-2);
}

.stop-btn,
.send-btn {
  width: 28px;
  height: 28px;
  padding: 0;
  box-sizing: border-box;
  font-size: var(--app-text-xl);
  line-height: 1;
  border-radius: var(--app-radius-lg);
  border: 1px solid transparent;
  cursor: pointer;
  /* 控件语法：按下 scale(0.98)，只动 transform 不动 layout 属性 */
  transition: background-color var(--app-motion-fast), transform var(--app-motion-instant),
    box-shadow var(--app-motion-fast);

  &:focus-visible {
    outline: 2px solid var(--td-brand-color);
    outline-offset: 2px;
  }

  &:active:not(.disabled) {
    transform: scale(0.98);
  }

  img {
    width: 16px;
    height: 16px;
  }
}

.stop-btn,
.send-btn {
  background-color: var(--td-brand-color);
  color: var(--td-text-color-anti);

  /* hover 加深 6%：用 color-mix 而非硬编码色值，深色模式自动跟随品牌色 */
  &:hover:not(.disabled) {
    background-color: color-mix(in srgb, var(--td-brand-color) 94%, #000);
    box-shadow: 0 2px 8px color-mix(in srgb, var(--td-brand-color) 24%, transparent);
  }

  &.disabled {
    /* 不可用时仍站在珊瑚家族里（淡化珊瑚），不让青绿冒充主行动色 */
    background-color: var(--td-brand-color-disabled);
    cursor: not-allowed;
  }
}
</style>
