<template>
    <div class="dl-a lab-a" :class="{ 'dl-a--neutral-dark': darkBase === 'neutral' }">
        <LabChrome badge="方向 A · 午后的工作室" v-model="state" />

        <!-- Q12 实物对比：暗色基底切换（仅暗色模式生效，dev 工具常显） -->
        <div class="lab-darkbase" role="radiogroup" aria-:label="t('lab.darkBase')">
            <span class="lab-darkbase-label"> {{ t('lab.darkBase') }} </span>
            <div class="lab-darkbase-switch">
                <button type="button" class="lab-darkbase-btn" :class="{ 'is-active': darkBase === 'warm' }"
                    role="radio" :aria-checked="darkBase === 'warm'"
                    @click="darkBase = 'warm'">暖棕</button>
                <button type="button" class="lab-darkbase-btn" :class="{ 'is-active': darkBase === 'neutral' }"
                    role="radio" :aria-checked="darkBase === 'neutral'"
                    @click="darkBase = 'neutral'">中性灰</button>
            </div>
        </div>

        <!-- ============ 空态：工作台首屏 ============ -->
        <div v-if="state === 'empty'" class="lab-empty">
            <h1 class="lab-greeting"> {{ t('lab.afternoonGreeting') }} </h1>
            <div class="lab-empty-composer">
                <LabComposerA fly-to="down" @send="handleSendFromEmpty" />
            </div>

            <section class="lab-recent">
                <p class="lab-recent-label"> {{ t('lab.continueYesterday') }} </p>
                <div v-if="sessionsLoading" class="lab-recent-grid">
                    <div v-for="n in 3" :key="n" class="lab-recent-card is-skeleton">
                        <t-skeleton animation="gradient"
                            :row-col="[{ width: '60%', height: '15px' }, { width: '100%', height: '12px' }, { width: '30%', height: '11px' }]" />
                    </div>
                </div>
                <div v-else-if="sessionsError" class="lab-recent-fallback">
                    <span> {{ t('lab.recentLoadFailed') }} </span>
                    <button type="button" class="lab-retry" @click="loadAll"> {{ t('common.retry') }} </button>
                </div>
                <div v-else-if="recentSessions.length === 0" class="lab-recent-fallback">
                    还没有历史会话，从上方开始第一段研究吧。
                </div>
                <div v-else class="lab-recent-grid">
                    <button v-for="(s, i) in recentSessions" :key="s.id" type="button" class="lab-recent-card"
                        :style="{ '--i': i }" @click="state = 'conversation'">
                        <span class="lab-recent-title">{{ s.title }}</span>
                        <span class="lab-recent-preview">{{ s.preview || '（' + t('lab.noPreview') + '）' }}</span>
                        <span class="lab-recent-time">{{ labRelativeTime(s.updated_at) }}</span>
                    </button>
                </div>
                <p class="lab-datascope">{{ knowledgeBases.length }} 个知识库 · {{ agents.length }} 个智能体</p>
            </section>
        </div>

        <!-- ============ 对话中 ============ -->
        <div v-else class="lab-conv">
            <div ref="scrollEl" class="lab-conv-scroll">
                <div class="lab-conv-column">
                    <p v-if="sessionTitle" class="lab-conv-title">{{ sessionTitle }}</p>
                    <div v-if="messagesLoading" class="lab-conv-status">
                        <t-skeleton animation="gradient"
                            :row-col="[{ width: '40%', height: '14px' }, { width: '90%', height: '14px' }, { width: '75%', height: '14px' }]" />
                    </div>
                    <div v-else-if="messagesError" class="lab-conv-status">
                        <span>{{ t('lab.messagesLoadFailed') }}</span>
                        <button type="button" class="lab-retry" @click="reloadMessages">{{ t('common.retry') }}</button>
                    </div>
                    <div v-else-if="displayMessages.length === 0" class="lab-conv-status"> {{ t('lab.noMessages') }} </div>
                    <template v-else>
                        <template v-for="msg in displayMessages" :key="msg.id">
                            <!-- 用户消息：右对齐窄条 -->
                            <div v-if="msg.role === 'user'" class="lab-msg-user-row">
                                <div class="lab-msg-user">
                                    <span class="lab-msg-user-text">{{ msg.content }}</span>
                                    <span class="lab-msg-time">{{ labRelativeTime(msg.created_at) }}</span>
                                </div>
                            </div>
                            <!-- AI 答复：无框长文 -->
                            <div v-else-if="msg.content && msg.content.trim()" class="lab-msg-ai">
                                <LabAnswerBody :content="msg.content"
                                    :knowledge-references="msg.knowledge_references" />
                                <span class="lab-msg-time lab-msg-time-ai">{{ labRelativeTime(msg.created_at) }}</span>
                            </div>
                        </template>
                    </template>
                </div>
            </div>
            <div class="lab-conv-composer">
                <div class="lab-conv-composer-inner">
                    <LabComposerA fly-to="up" :placeholder="t('lab.continueAsk')" @send="handleSend" />
                </div>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import LabChrome from '../shared/LabChrome.vue'
import type { LabState } from '../shared/types'
import {
    useLabData,
    labRelativeTime,
    type LabMessage,
} from '../shared/useLabData'
import LabComposerA from './LabComposerA.vue'
import LabAnswerBody from './LabAnswerBody.vue'

const { t } = useI18n()

const state = ref<LabState>('empty')

// Q12 暗色基底对比：'warm'（暖深棕，默认）| 'neutral'（中性深灰）。
// 选择持久化到 localStorage；仅暗色模式下有视觉效果。
type LabDarkBase = 'warm' | 'neutral'
const DARK_BASE_KEY = 'weknora_lab_a_darkbase'
const storedDarkBase = typeof window !== 'undefined'
    ? window.localStorage.getItem(DARK_BASE_KEY)
    : null
const darkBase = ref<LabDarkBase>(storedDarkBase === 'neutral' ? 'neutral' : 'warm')
watch(darkBase, (v) => {
    window.localStorage.setItem(DARK_BASE_KEY, v)
})

const {
    messages,
    messagesLoading,
    messagesError,
    sessionTitle,
    recentSessions,
    sessionsLoading,
    sessionsError,
    knowledgeBases,
    agents,
    loadAll,
    reloadMessages,
} = useLabData()

onMounted(loadAll)

// 小样内本地追加的用户消息（模拟发送，不调用后端）。
const localMessages = ref<LabMessage[]>([])
let localSeq = 0

const displayMessages = computed(() => {
    const merged = [...messages.value, ...localMessages.value]
    return merged.sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')))
})

const scrollEl = ref<HTMLElement | null>(null)
const scrollToBottom = () => {
    nextTick(() => {
        const el = scrollEl.value
        if (el) el.scrollTop = el.scrollHeight
    })
}

const appendLocalUserMessage = (text: string) => {
    localMessages.value.push({
        id: `lab-local-${++localSeq}`,
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
    })
    scrollToBottom()
}

const handleSend = (text: string) => {
    appendLocalUserMessage(text)
}

const handleSendFromEmpty = (text: string) => {
    state.value = 'conversation'
    appendLocalUserMessage(text)
}

watch(state, (s) => {
    if (s === 'conversation') scrollToBottom()
})
</script>

<style lang="less" scoped>
@import '../tokens/a.less';

.lab-a {
    min-height: 100vh;
    background: var(--dl-bg);
    color: var(--dl-ink);
    font-family: var(--dl-font-body);
    -webkit-font-smoothing: antialiased;
}

/* ============ Q12 暗色基底切换（复用 LabChrome 的胶囊语言） ============ */

.lab-darkbase {
    position: fixed;
    top: 48px;
    left: 16px;
    z-index: 60;
    display: flex;
    align-items: center;
    gap: 8px;
}

.lab-darkbase-label {
    font-family: var(--dl-font-mono);
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--dl-ink-3);
}

.lab-darkbase-switch {
    display: flex;
    gap: 2px;
    padding: 2px;
    border-radius: 999px;
    border: 1px solid var(--dl-border);
    background: var(--dl-surface);
}

.lab-darkbase-btn {
    border: none;
    background: transparent;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 12px;
    line-height: 18px;
    color: var(--dl-ink-2);
    cursor: pointer;
    transition: color 150ms ease-out, background 150ms ease-out;

    &.is-active {
        background: var(--dl-accent);
        color: var(--dl-on-accent);
    }

    &:not(.is-active):hover {
        color: var(--dl-ink);
    }
}

/* ================= 空态 · 工作台首屏 ================= */

.lab-empty {
    min-height: 100vh;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 17vh 24px 64px;
}

.lab-greeting {
    margin: 0 0 26px;
    font-family: var(--dl-font-display);
    font-weight: 600;
    font-size: clamp(24px, 3vw, 31px);
    line-height: 1.3;
    color: var(--dl-ink);
    text-align: center;
}

.lab-empty-composer {
    width: min(680px, 100%);
}

.lab-recent {
    width: min(680px, 100%);
    margin-top: 58px;
}

.lab-recent-label {
    margin: 0 0 12px;
    font-size: 12px;
    letter-spacing: 0.08em;
    color: var(--dl-ink-2);
}

.lab-recent-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 12px;
}

// 会话卡片 = 桌上的一张便签
.lab-recent-card {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    padding: 14px 16px;
    border: 1px solid var(--dl-border);
    border-radius: var(--dl-radius);
    background: var(--dl-surface-2);
    box-shadow: var(--dl-shadow-card);
    text-align: left;
    cursor: pointer;
    font-family: var(--dl-font-body);
    transition: transform 180ms ease-out, box-shadow 180ms ease-out, border-color 180ms ease-out;

    &:hover {
        transform: translateY(-2px);
        box-shadow: var(--dl-shadow-card-hover);
        border-color: var(--dl-border-strong);
    }

    &:active {
        transform: scale(0.98);
    }

    &.is-skeleton {
        cursor: default;

        &:hover {
            transform: none;
            box-shadow: var(--dl-shadow-card);
        }
    }
}

.lab-recent-title {
    font-family: var(--dl-font-display);
    font-weight: 600;
    font-size: 15px;
    line-height: 1.4;
    color: var(--dl-ink);
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
}

.lab-recent-preview {
    font-size: 12.5px;
    line-height: 1.5;
    color: var(--dl-ink-2);
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    max-width: 100%;
}

.lab-recent-time {
    font-family: var(--dl-font-mono);
    font-size: 11px;
    color: var(--dl-ink-3);
}

.lab-recent-fallback {
    padding: 18px 4px;
    font-size: 13px;
    color: var(--dl-ink-2);
    display: flex;
    align-items: center;
    gap: 10px;
}

.lab-retry {
    border: 1px solid var(--dl-border);
    border-radius: 8px;
    background: var(--dl-surface-2);
    color: var(--dl-accent);
    font-size: 12px;
    padding: 4px 12px;
    cursor: pointer;

    &:hover {
        border-color: var(--dl-accent);
    }
}

.lab-datascope {
    margin: 18px 0 0;
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--dl-ink-3);
}

/* ================= 对话中 ================= */

.lab-conv {
    height: 100vh;
    display: flex;
    flex-direction: column;
}

.lab-conv-scroll {
    flex: 1;
    overflow-y: auto;
}

.lab-conv-column {
    max-width: 720px;
    margin: 0 auto;
    padding: 84px 24px 32px;
    box-sizing: border-box;
}

.lab-conv-title {
    margin: 0 0 28px;
    font-family: var(--dl-font-display);
    font-weight: 600;
    font-size: 20px;
    color: var(--dl-ink);
}

.lab-conv-status {
    padding: 24px 0;
    font-size: 13px;
    color: var(--dl-ink-2);
    display: flex;
    align-items: center;
    gap: 10px;
}

.lab-msg-user-row {
    display: flex;
    justify-content: flex-end;
    margin: 26px 0 14px;
}

.lab-msg-user {
    max-width: 72%;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
    padding: 9px 14px;
    border-radius: var(--dl-radius);
    border: 1px solid var(--dl-border);
    background: var(--dl-surface);
}

.lab-msg-user-text {
    font-size: 14px;
    line-height: 1.6;
    color: var(--dl-ink);
    white-space: pre-wrap;
    word-break: break-word;
}

.lab-msg-ai {
    margin: 0 0 8px;
    padding: 2px 0 4px;
}

.lab-msg-time {
    font-family: var(--dl-font-mono);
    font-size: 10.5px;
    color: var(--dl-ink-3);
}

.lab-msg-time-ai {
    display: block;
    margin-top: 6px;
}

.lab-conv-composer {
    flex-shrink: 0;
    padding: 10px 24px 22px;
    background: linear-gradient(to top, var(--dl-bg) 72%, transparent);
}

.lab-conv-composer-inner {
    max-width: 720px;
    margin: 0 auto;
}

/* ================= 动效（≤300ms，位移 ≤12px，投递除外） ================= */

@media (prefers-reduced-motion: no-preference) {
    // fill-mode 用 backwards：动画结束后交还样式控制权，hover 抬升才不被
    // 完成态 keyframe 压住。
    .lab-greeting {
        animation: lab-rise 260ms ease-out backwards;
    }

    .lab-empty-composer {
        animation: lab-rise 260ms ease-out 60ms backwards;
    }

    // 首次进入：工作台卡片从下方 12px 处依次浮起（stagger 60ms）
    .lab-recent-card:not(.is-skeleton) {
        animation: lab-rise 260ms ease-out calc(120ms + var(--i, 0) * 60ms) backwards;
    }

    .lab-recent-label {
        animation: lab-rise 260ms ease-out 100ms backwards;
    }
}

@keyframes lab-rise {
    from {
        opacity: 0;
        transform: translateY(12px);
    }

    to {
        opacity: 1;
        transform: translateY(0);
    }
}
</style>
