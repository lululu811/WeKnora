<template>
    <div class="lab-composer" :class="{ 'is-focused': focused }">
        <textarea ref="taRef" v-model="text" class="lab-composer-input" rows="1"
            :placeholder="placeholder" @focus="focused = true" @blur="focused = false"
            @input="autoGrow" @keydown.enter.exact.prevent="send"></textarea>
        <div class="lab-composer-bar">
            <div class="lab-composer-tools">
                <button type="button" class="lab-composer-tool" title="@ 知识库（小样占位）">@</button>
                <button type="button" class="lab-composer-tool" title="附件（小样占位）">
                    <t-icon name="attach" />
                </button>
            </div>
            <button type="button" class="lab-composer-send" :disabled="!text.trim()" title="发送（Enter）"
                @click="send">
                <t-icon name="send" />
            </button>
        </div>
    </div>
</template>

<script setup lang="ts">
// LAB-NOTE: VISUAL REPLICA of src/components/Input-field.vue (4.2k lines).
// The real composer is deeply coupled to menuStore / chatResources /
// editorResources / route state (kb selection, agent mode, mentions, skills,
// attachments, steer queue) — none of which exists on the design-lab canvas,
// and wiring it up would drag half the chat shell into the sample. This
// replica keeps Direction A's composer contract (radius 10 / warm 1px border /
// coral send / focus 暖光 / 投递 flight) and emits plain text via `send`;
// the parent appends it to the local message list (no backend call).
import { nextTick, ref } from 'vue'
import { useSendFlight } from '@/composables/useMotion'

const props = withDefaults(defineProps<{
    placeholder?: string
    /** 投递飞行方向：空态消息区在 composer 下方（down），对话中在上方（up）。 */
    flyTo?: 'up' | 'down'
}>(), {
    placeholder: '问点什么，或继续昨天的工作…',
    flyTo: 'down',
})

void props // props are consumed in the template (placeholder / flyTo)
const emit = defineEmits<{
    (e: 'send', text: string): void
}>()

const text = ref('')
const focused = ref(false)
const taRef = ref<HTMLTextAreaElement | null>(null)

// 使用统一的投递动效 composable（motion-v 驱动，自动处理 reduced-motion）
const { trigger: triggerFlight } = useSendFlight({
    sourceRef: taRef,
    targetRef: taRef, // 小样无真实消息区，飞向自身即可
    direction: props.flyTo,
    duration: 200,
})

const autoGrow = () => {
    const ta = taRef.value
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`
}

const send = () => {
    const value = text.value.trim()
    if (!value) return
    text.value = ''
    nextTick(autoGrow)
    emit('send', value)
    // 投递动效由 composable 处理（内部已检查 reduced-motion）
    triggerFlight(value)
}
</script>

<style lang="less" scoped>
.lab-composer {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 14px 14px 10px;
    border-radius: var(--dl-radius);
    border: 1px solid var(--dl-border);
    background: var(--dl-surface-2);
    box-shadow: var(--dl-shadow-card);
    transition: border-color 180ms ease-out, box-shadow 180ms ease-out;

    // 主视觉「光影本身」：聚焦时一圈极淡的珊瑚暖光从输入框下缘晕开。
    &::after {
        content: '';
        position: absolute;
        left: 8%;
        right: 8%;
        bottom: -18px;
        height: 44px;
        border-radius: 50%;
        background: radial-gradient(50% 100% at 50% 0%, var(--dl-glow), transparent 72%);
        filter: blur(6px);
        opacity: 0;
        transition: opacity 220ms ease-out;
        pointer-events: none;
    }

    &.is-focused {
        border-color: var(--dl-accent);

        &::after {
            opacity: 1;
        }
    }
}

.lab-composer-input {
    width: 100%;
    border: none;
    outline: none;
    resize: none;
    background: transparent;
    font-family: var(--dl-font-body);
    font-size: 14px;
    line-height: 22px;
    color: var(--dl-ink);
    box-sizing: border-box;

    &::placeholder {
        color: var(--dl-ink-3);
    }
}

.lab-composer-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.lab-composer-tools {
    display: flex;
    gap: 4px;
}

.lab-composer-tool {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: var(--dl-ink-2);
    font-size: 14px;
    cursor: pointer;
    transition: background 150ms ease-out, color 150ms ease-out;

    &:hover {
        background: var(--dl-surface);
        color: var(--dl-ink);
    }

    &:active {
        transform: scale(0.98);
    }
}

.lab-composer-send {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    border: none;
    border-radius: 9px;
    background: var(--dl-accent);
    color: var(--dl-on-accent);
    font-size: 15px;
    cursor: pointer;
    transition: background 150ms ease-out, transform 100ms ease-out, opacity 150ms ease-out;

    &:hover:not(:disabled) {
        background: var(--dl-accent-hover);
    }

    &:active:not(:disabled) {
        transform: scale(0.98);
    }

    &:disabled {
        opacity: 0.4;
        cursor: default;
    }
}
</style>
