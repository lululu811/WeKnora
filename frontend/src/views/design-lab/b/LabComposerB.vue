<template>
    <div class="lab-composer-b" :class="{ 'is-focused': focused }">
        <textarea ref="taRef" v-model="text" class="lab-composer-b-input" rows="1"
            :placeholder="placeholder" @focus="focused = true" @blur="focused = false"
            @input="autoGrow" @keydown.enter.exact.prevent="send"></textarea>
        <div class="lab-composer-b-bar">
            <div class="lab-composer-b-tools">
                <button type="button" class="lab-composer-b-tool" title="@ 知识库（小样占位）">¶</button>
                <button type="button" class="lab-composer-b-tool" title="附件（小样占位）">※</button>
            </div>
            <button type="button" class="lab-composer-b-send" :disabled="!text.trim()" title="发送（Enter）"
                @click="send">付印</button>
        </div>
        <!-- 落版动效：发送那一刻文字像铅字被压印进长卷 -->
        <div v-if="flightText" class="lab-composer-b-flight" :class="`fly-${flyTo}`" aria-hidden="true">{{ flightText }}
        </div>
    </div>
</template>

<script setup lang="ts">
// LAB-NOTE: VISUAL REPLICA of src/components/Input-field.vue (4.2k lines).
// The real composer is deeply coupled to menuStore / chatResources /
// editorResources / route state (kb selection, agent mode, mentions, skills,
// attachments, steer queue) — none of which exists on the design-lab canvas,
// and wiring it up would drag half the chat shell into the sample. This
// replica keeps Direction B's composer contract (radius 4 / 1px 墨线描边 /
// 墨底白字「付印」按钮 hover 变朱砂 / 字符图标 ¶ ※ / 落版 imprint flight)
// and emits plain text via `send`; the parent appends it to the local
// message list as a new 章节 (no backend call).
import { nextTick, ref } from 'vue'

const props = withDefaults(defineProps<{
    placeholder?: string
    /** 落版压印方向：空态长卷在 composer 下方（down），对话中在上方（up）。 */
    flyTo?: 'up' | 'down'
}>(), {
    placeholder: '写下今天的第一问…',
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
    // 与 CSS 240ms ease-in 对齐；结束后移除压印副本。
    flightTimer = setTimeout(() => {
        flightText.value = ''
        flightTimer = null
    }, 250)
}
</script>

<style lang="less" scoped>
.lab-composer-b {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 14px 14px 10px;
    border-radius: var(--dl-radius);
    border: 1px solid var(--dl-border);
    background: var(--dl-surface-2);
    transition: border-color 160ms ease-out;

    &.is-focused {
        // 聚焦：墨线描边加实，像主编用钢笔圈定了这一栏。
        border-color: var(--dl-border-strong);
    }
}

.lab-composer-b-input {
    width: 100%;
    border: none;
    outline: none;
    resize: none;
    background: transparent;
    font-family: var(--dl-font-body);
    font-size: 15px;
    line-height: 24px;
    color: var(--dl-ink);
    box-sizing: border-box;

    &::placeholder {
        color: var(--dl-ink-3);
    }
}

.lab-composer-b-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.lab-composer-b-tools {
    display: flex;
    gap: 2px;
}

// 功能图标用字符与编号（¶、※），1px 细描边。
.lab-composer-b-tool {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border: none;
    border-radius: var(--dl-radius);
    background: transparent;
    color: var(--dl-ink-2);
    font-family: var(--dl-font-body);
    font-size: 15px;
    cursor: pointer;
    transition: background 150ms ease-out, color 150ms ease-out;

    &:hover {
        background: var(--dl-surface);
        color: var(--dl-ink);
    }
}

// 主按钮：墨底白字，hover 变朱砂。
.lab-composer-b-send {
    border: none;
    border-radius: var(--dl-radius);
    padding: 5px 16px;
    background: var(--dl-ink);
    color: var(--dl-bg);
    font-family: var(--dl-font-ui);
    font-size: 12px;
    letter-spacing: 0.12em;
    cursor: pointer;
    transition: background 150ms ease-out, transform 100ms ease-out, opacity 150ms ease-out;

    &:hover:not(:disabled) {
        background: var(--dl-accent);
        color: var(--dl-on-accent);
    }

    &:active:not(:disabled) {
        transform: translateY(1px);
    }

    &:disabled {
        opacity: 0.35;
        cursor: default;
    }
}

.lab-composer-b-flight {
    position: absolute;
    left: 16px;
    right: 76px;
    top: 14px;
    padding: 2px 0;
    color: var(--dl-ink-2);
    font-family: var(--dl-font-body);
    font-size: 14px;
    line-height: 22px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    pointer-events: none;
    z-index: 2;

    &.fly-down {
        animation: lab-imprint-down 240ms ease-in forwards;
    }

    &.fly-up {
        animation: lab-imprint-up 240ms ease-in forwards;
    }
}

// 落版：文字加速压进纸面（ease-in），字距微收、尺寸微缩，像铅字入版。
@keyframes lab-imprint-down {
    from {
        opacity: 1;
        letter-spacing: 0.02em;
        transform: translateY(0) scale(1);
    }

    to {
        opacity: 0;
        letter-spacing: -0.04em;
        transform: translateY(44px) scale(0.94);
    }
}

@keyframes lab-imprint-up {
    from {
        opacity: 1;
        letter-spacing: 0.02em;
        transform: translateY(0) scale(1);
    }

    to {
        opacity: 0;
        letter-spacing: -0.04em;
        transform: translateY(-44px) scale(0.94);
    }
}
</style>
