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
        <!-- 弹弓投递：发送那一刻消息像弹弓射出（蓄力→射出→回弹），落向消息区 -->
        <div v-if="flightText" class="lab-composer-flight" :class="`fly-${flyTo}`" aria-hidden="true">{{ flightText }}
        </div>
    </div>
</template>

<script setup lang="ts">
// LAB-NOTE: VISUAL REPLICA of src/components/Input-field.vue (4.2k lines).
// The real composer is deeply coupled to menuStore / chatResources /
// editorResources / route state (kb selection, agent mode, mentions, skills,
// attachments, steer queue) — none of which exists on the design-lab canvas,
// and wiring it up would drag half the chat shell into the sample. This
// replica keeps Direction C's composer contract (订单夹：2px 墨描边 / 圆角 16 /
// 木牌厚度阴影 / 砖红发送键 / 按下凹陷 / 弹弓 flight) and emits plain text via
// `send`; the parent appends it to the local message list (no backend call).
import { nextTick, ref } from 'vue'

const props = withDefaults(defineProps<{
    placeholder?: string
    /** 弹弓飞行方向：空态消息区在 composer 下方（down），对话中在上方（up）。 */
    flyTo?: 'up' | 'down'
}>(), {
    placeholder: '先逛逛，或者直接问点什么…',
    flyTo: 'down',
})

void props // props are consumed in the template (placeholder / flyTo)
const emit = defineEmits<{
    (e: 'send', text: string): void
}>()

const reducedMotion = typeof window !== 'undefined'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const text = ref('')
const focused = ref(false)
const flightText = ref('')
const taRef = ref<HTMLTextAreaElement | null>(null)
let flightTimer: ReturnType<typeof setTimeout> | null = null

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
    if (reducedMotion) return
    if (flightTimer) clearTimeout(flightTimer)
    flightText.value = value.length > 80 ? `${value.slice(0, 80)}…` : value
    // 与 CSS 440ms 弹弓动画对齐；结束后移除飞行副本。
    flightTimer = setTimeout(() => {
        flightText.value = ''
        flightTimer = null
    }, 450)
}
</script>

<style lang="less" scoped>
.lab-composer {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 14px 16px 10px;
    border-radius: var(--dl-radius-lg);
    border: 2px solid var(--dl-border);
    background: var(--dl-surface-2);
    box-shadow: var(--dl-shadow-card);
    transition: box-shadow 160ms ease-out;

    &.is-focused {
        // 订单夹被拿起：木牌厚度 + 一圈杏黄小光晕
        box-shadow: var(--dl-shadow-card), 0 0 0 4px var(--dl-glow);
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
    width: 30px;
    height: 30px;
    border: 2px solid transparent;
    border-radius: 10px;
    background: transparent;
    color: var(--dl-ink-2);
    font-size: 14px;
    cursor: pointer;
    transition: background 150ms ease-out, color 150ms ease-out, border-color 150ms ease-out;

    &:hover {
        background: var(--dl-surface);
        border-color: var(--dl-border);
        color: var(--dl-ink);
    }

    &:active {
        transform: translateY(1px);
    }
}

// 主按钮 = 砖红木牌：2px 墨边 + 厚度阴影，按下凹陷（位移 + 阴影消失）
.lab-composer-send {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    border: 2px solid var(--dl-border);
    border-radius: 12px;
    background: var(--dl-accent);
    color: #FFFFFF;
    font-size: 15px;
    cursor: pointer;
    box-shadow: 0 2px 0 var(--dl-border);
    transition: background 150ms ease-out, transform 100ms ease-out, box-shadow 100ms ease-out,
        opacity 150ms ease-out;

    &:hover:not(:disabled) {
        background: var(--dl-accent-hover);
    }

    &:active:not(:disabled) {
        transform: translateY(2px);
        box-shadow: 0 0 0 var(--dl-border);
    }

    &:disabled {
        opacity: 0.45;
        cursor: default;
        box-shadow: none;
    }
}

// 飞行副本 = 一颗 mini 彩色气泡
.lab-composer-flight {
    position: absolute;
    left: 16px;
    right: 64px;
    top: 14px;
    padding: 7px 12px;
    border-radius: var(--dl-radius);
    border: 2px solid var(--dl-border);
    background: var(--dl-accent);
    color: #FFFFFF;
    font-size: 13px;
    line-height: 18px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    pointer-events: none;
    z-index: 2;
    box-shadow: 0 2px 0 rgba(59, 42, 30, 0.35);

    &.fly-down {
        animation: lab-sling-down 440ms cubic-bezier(0.3, 1.35, 0.5, 1) forwards;
    }

    &.fly-up {
        animation: lab-sling-up 440ms cubic-bezier(0.3, 1.35, 0.5, 1) forwards;
    }
}

// 弹弓：先反向蓄力，再 overshoot 射出，回弹后消散（本方向签名动效，允许 >300ms）
@keyframes lab-sling-down {
    0% {
        opacity: 1;
        transform: translateY(0) scale(1) rotate(0deg);
    }

    18% {
        opacity: 1;
        transform: translateY(-10px) scale(0.9) rotate(-2deg);
    }

    62% {
        opacity: 1;
        transform: translateY(76px) scale(0.84) rotate(1.5deg);
    }

    82% {
        opacity: 0.6;
        transform: translateY(66px) scale(0.8) rotate(0deg);
    }

    100% {
        opacity: 0;
        transform: translateY(70px) scale(0.78);
    }
}

@keyframes lab-sling-up {
    0% {
        opacity: 1;
        transform: translateY(0) scale(1) rotate(0deg);
    }

    18% {
        opacity: 1;
        transform: translateY(10px) scale(0.9) rotate(2deg);
    }

    62% {
        opacity: 1;
        transform: translateY(-76px) scale(0.84) rotate(-1.5deg);
    }

    82% {
        opacity: 0.6;
        transform: translateY(-66px) scale(0.8) rotate(0deg);
    }

    100% {
        opacity: 0;
        transform: translateY(-70px) scale(0.78);
    }
}
</style>
