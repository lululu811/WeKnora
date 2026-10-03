<template>
    <div class="lab-chrome">
        <div class="lab-chrome-badge">design-lab · {{ badge }}</div>
        <div v-if="modelValue !== undefined" class="lab-chrome-switch" role="tablist">
            <button type="button" class="lab-chrome-switch-btn" :class="{ 'is-active': modelValue === 'empty' }"
                role="tab" :aria-selected="modelValue === 'empty'"
                @click="emit('update:modelValue', 'empty')">空态</button>
            <button type="button" class="lab-chrome-switch-btn" :class="{ 'is-active': modelValue === 'conversation' }"
                role="tab" :aria-selected="modelValue === 'conversation'"
                @click="emit('update:modelValue', 'conversation')">对话中</button>
        </div>
    </div>
</template>

<script setup lang="ts">
import type { LabState } from './types'

// Floating lab chrome: direction badge + 空态/对话中 state switch.
// The switch only renders when the parent binds v-model; stub views (b/c)
// show the badge alone. Colors inherit the direction tokens (var(--dl-*))
// from the wrapping .dl-a/.dl-b/.dl-c root, with direction-A fallbacks.
defineProps<{
    badge: string
    modelValue?: LabState
}>()

const emit = defineEmits<{
    (e: 'update:modelValue', value: LabState): void
}>()
</script>

<style lang="less" scoped>
.lab-chrome {
    position: fixed;
    top: 14px;
    left: 16px;
    right: 16px;
    z-index: 60;
    display: flex;
    align-items: center;
    justify-content: space-between;
    pointer-events: none;
    font-family: var(--dl-font-body, var(--app-font-family));
}

.lab-chrome-badge {
    pointer-events: auto;
    padding: 4px 10px;
    border-radius: 999px;
    border: 1px solid var(--dl-border, #E6DBC9);
    background: var(--dl-surface, #F3EAD9);
    color: var(--dl-ink-2, #8A7B6D);
    font-family: var(--dl-font-mono, var(--app-font-family-mono));
    font-size: 11px;
    letter-spacing: 0.04em;
}

.lab-chrome-switch {
    pointer-events: auto;
    display: flex;
    gap: 2px;
    padding: 2px;
    border-radius: 999px;
    border: 1px solid var(--dl-border, #E6DBC9);
    background: var(--dl-surface, #F3EAD9);
}

.lab-chrome-switch-btn {
    border: none;
    background: transparent;
    padding: 4px 14px;
    border-radius: 999px;
    font-size: 12px;
    line-height: 18px;
    color: var(--dl-ink-2, #8A7B6D);
    cursor: pointer;
    transition: color 150ms ease-out, background 150ms ease-out;

    &.is-active {
        background: var(--dl-accent, #E85D3D);
        color: var(--dl-on-accent, #FFFFFF);
    }

    &:not(.is-active):hover {
        color: var(--dl-ink, #2B2320);
    }
}
</style>
