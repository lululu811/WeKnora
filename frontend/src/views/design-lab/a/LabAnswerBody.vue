<template>
    <div ref="rootEl" class="lab-answer markdown-content" v-html="html"></div>
</template>

<script setup lang="ts">
// REUSED, not replicated: this is the exact render pipeline used by botmsg.vue
// and the dev MarkdownTestPage — createChatMarkdownRenderer + renderChatMarkdown
// (katex / mermaid / citation pills / table wrapping included), plus the shared
// chat-markdown / chat-citations Less mixins below. The citation *popover*
// (useChatCitationPopover) is intentionally not wired: pills render visually
// but hover-preview is out of scope for the小样.
import { computed, nextTick, ref, useId, watch } from 'vue'
import 'katex/dist/katex.min.css'
import { sanitizeMarkdownHTML, safeMarkdownToHTML } from '@/utils/security'
import { createChatMarkdownRenderer, renderChatMarkdown } from '@/utils/chatMarkdownRenderer'
import {
    createMermaidCodeRenderer,
    ensureMermaidInitialized,
    enhanceMarkdownContainer,
} from '@/utils/mermaidShared'
import { replaceIncompleteMermaidWithPlaceholder } from '@/utils/chatMessageShared'

const props = defineProps<{
    content: string
    knowledgeReferences?: any[]
}>()

ensureMermaidInitialized()

// Mermaid svg ids are prefixed per instance so multiple answers on one page
// cannot collide.
const renderer = createChatMarkdownRenderer({
    codeRenderer: createMermaidCodeRenderer(`dl-answer-${useId()}`),
})

const rootEl = ref<HTMLElement | null>(null)

const html = computed(() =>
    renderChatMarkdown(props.content || '', {
        renderer,
        escapeMarkdown: safeMarkdownToHTML,
        sanitizeHtml: sanitizeMarkdownHTML,
        streaming: false,
        knowledgeReferences: props.knowledgeReferences,
        prepareMarkdown: (markdown: string) => replaceIncompleteMermaidWithPlaceholder(markdown),
    }),
)

watch(html, () => {
    nextTick(() => {
        void enhanceMarkdownContainer(rootEl.value)
    })
}, { flush: 'post' })
</script>

<style lang="less" scoped>
@import '../../../components/css/chat-markdown.less';
@import '../../../components/css/chat-citations.less';

.lab-answer {
    .chat-markdown-typography();
    .chat-citation-pills();

    // Direction A：AI 答复是无框长文，正文色跟随方向墨色的同时保持排版规范。
    color: var(--dl-ink);
    font-size: 15px;
}
</style>
