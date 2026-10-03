<template>
    <div class="dl-b lab-b">
        <LabChrome badge="方向 B · 主编的长卷" v-model="state" />

        <!-- ============ 空态：晨报首屏 ============ -->
        <div v-if="state === 'empty'" class="lab-empty">
            <header class="lab-masthead">
                <div class="lab-masthead-top">
                    <span class="lab-masthead-brand">{{ t('lab.morningBrief') }}</span>
                    <span class="lab-masthead-date">{{ todayLine }}</span>
                </div>
                <div class="lab-rule-double" aria-hidden="true"></div>
                <h1 class="lab-headline">{{ t('lab.whatToResearch') }}</h1>
            </header>

            <div class="lab-empty-composer">
                <LabComposerB fly-to="down" @send="handleSendFromEmpty" />
            </div>

            <section class="lab-issues">
                <div class="lab-section-head">
                    <span class="lab-section-label"> {{ t('lab.pastIssues') }} </span>
                    <span class="lab-section-rule" aria-hidden="true"></span>
                </div>
                <div v-if="sessionsLoading" class="lab-issue-list">
                    <div v-for="n in 3" :key="n" class="lab-issue is-skeleton">
                        <t-skeleton animation="gradient"
                            :row-col="[{ width: '34%', height: '15px' }, { width: '82%', height: '12px' }]" />
                    </div>
                </div>
                <div v-else-if="sessionsError" class="lab-issues-fallback">
                    <span> {{ t('lab.pastIssuesFailed') }} </span>
                    <button type="button" class="lab-retry" @click="loadAll">{{ t('common.retry') }}</button>
                </div>
                <div v-else-if="recentSessions.length === 0" class="lab-issues-fallback">
                    还没有往期研究，从上方写下第一问吧。
                </div>
                <div v-else class="lab-issue-list">
                    <button v-for="s in recentSessions" :key="s.id" type="button" class="lab-issue"
                        @click="state = 'conversation'">
                        <span class="lab-issue-date">{{ labRelativeTime(s.updated_at) }}</span>
                        <span class="lab-issue-main">
                            <span class="lab-issue-title">{{ s.title }}</span>
                            <span class="lab-issue-preview">{{ s.preview || '（暂无消息预览）' }}</span>
                        </span>
                    </button>
                </div>
                <p class="lab-colophon">本刊资料室 {{ knowledgeBases.length }} 个知识库 · 编辑部 {{ agents.length }} 位智能体</p>
            </section>
        </div>

        <!-- ============ 对话中：文档长卷 ============ -->
        <div v-else class="lab-conv">
            <div ref="scrollEl" class="lab-conv-scroll">
                <article class="lab-doc">
                    <header class="lab-doc-head">
                        <p class="lab-doc-kicker">{{ t('lab.researchDispatch') }}</p>
                        <h1 class="lab-doc-title">{{ sessionTitle || '未命名研究' }}</h1>
                        <p class="lab-doc-meta">共 {{ chapterCount }} 章 · 参考来源 {{ allReferences.length }} 条</p>
                        <div class="lab-rule-double" aria-hidden="true"></div>
                    </header>

                    <div v-if="messagesLoading" class="lab-doc-status">
                        <t-skeleton animation="gradient"
                            :row-col="[{ width: '40%', height: '16px' }, { width: '92%', height: '14px' }, { width: '76%', height: '14px' }]" />
                    </div>
                    <div v-else-if="messagesError" class="lab-doc-status">
                        <span>会话消息加载失败（{{ messagesError }}）</span>
                        <button type="button" class="lab-retry" @click="reloadMessages">{{ t('common.retry') }}</button>
                    </div>
                    <div v-else-if="displayMessages.length === 0" class="lab-doc-status"> {{ t('lab.noMessages') }} </div>

                    <template v-else>
                        <template v-for="msg in displayMessages" :key="msg.id">
                            <!-- 用户提问 = 章节标题（衬线大字左对齐，带编号） -->
                            <section v-if="msg.role === 'user'" class="lab-chapter"
                                :class="{ 'is-current': isCurrentChapter(msg.id) }">
                                <span class="lab-chapter-no">{{ chapterNoOf(msg.id) }}</span>
                                <div class="lab-chapter-head">
                                    <h2 class="lab-chapter-title">
                                        <template v-if="msg.id === freshChapterId">
                                            <span v-for="(ch, ci) in charsOf(msg.content)" :key="ci"
                                                class="lab-chapter-char"
                                                :style="{ animationDelay: charDelay(ci, msg.content.length) }">{{ ch
                                                }}</span>
                                        </template>
                                        <template v-else>{{ msg.content }}</template>
                                    </h2>
                                    <span class="lab-chapter-time">{{ labRelativeTime(msg.created_at) }}</span>
                                </div>
                            </section>
                            <!-- AI 答复 = 正文长文（无气泡、无框） -->
                            <div v-else-if="msg.content && msg.content.trim()" class="lab-chapter-body">
                                <LabAnswerBody :content="msg.content"
                                    :knowledge-references="msg.knowledge_references" />
                                <details v-if="refsOf(msg).length" class="lab-notes">
                                    <summary>※ 采访手记 · 本篇检索 {{ refsOf(msg).length }} 处资料</summary>
                                    <ul class="lab-notes-list">
                                        <li v-for="(r, ri) in refsOf(msg)" :key="ri">
                                            <span class="lab-notes-title">{{ refTitle(r) }}</span>
                                            <span v-if="refSnippet(r)" class="lab-notes-snippet">{{ refSnippet(r)
                                                }}</span>
                                        </li>
                                    </ul>
                                </details>
                                <p class="lab-chapter-byline">¶ 本篇完 · {{ labRelativeTime(msg.created_at) }}</p>
                            </div>
                        </template>

                        <!-- 文末「参考来源」章：脚注编号 [n]，悬停原位展开来源卡 -->
                        <footer v-if="allReferences.length" class="lab-references">
                            <div class="lab-rule-double" aria-hidden="true"></div>
                            <p class="lab-section-label"> {{ t('lab.references') }} </p>
                            <ol class="lab-ref-list">
                                <li v-for="(r, i) in allReferences" :key="i" class="lab-ref">
                                    <span class="lab-ref-no">[{{ i + 1 }}]</span>
                                    <span class="lab-ref-title">{{ refTitle(r) }}</span>
                                    <div class="lab-ref-card" role="tooltip">
                                        <p class="lab-ref-card-title">{{ refTitle(r) }}</p>
                                        <p v-if="refSnippet(r)" class="lab-ref-card-snippet">{{ refSnippet(r) }}</p>
                                        <p v-else class="lab-ref-card-snippet"> {{ t('lab.noExcerpts') }} </p>
                                    </div>
                                </li>
                            </ol>
                        </footer>
                    </template>
                </article>
            </div>
            <div class="lab-conv-composer">
                <div class="lab-conv-composer-inner">
                    <LabComposerB fly-to="up" placeholder="继续提问，新问将排为下一章…" @send="handleSend" />
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
import LabComposerB from './LabComposerB.vue'
import LabAnswerBody from '../a/LabAnswerBody.vue'

const { t } = useI18n()

const state = ref<LabState>('empty')

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

// 报头日期行：真实日期 + 以期号（年内第几天）冒充刊期。
const now = new Date()
const dayOfYear = Math.floor((now.getTime() - new Date(now.getFullYear(), 0, 0).getTime()) / 86_400_000)
const todayLine = `${now.getFullYear()} 年 ${now.getMonth() + 1} 月 ${now.getDate()} 日 · 第 ${dayOfYear} 期`

// 小样内本地追加的用户消息（模拟发送，不调用后端）。
const localMessages = ref<LabMessage[]>([])
let localSeq = 0

const displayMessages = computed(() => {
    const merged = [...messages.value, ...localMessages.value]
    return merged.sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')))
})

// ---- 章节编号：一次提问 = 一章，编号用大字中文数字（壹/贰/叁…） ----

const CN_DIGITS = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖']

function labChapterNumeral(n: number): string {
    if (n <= 0) return ''
    if (n < 10) return CN_DIGITS[n]
    if (n === 10) return '拾'
    if (n < 20) return `拾${CN_DIGITS[n % 10]}`
    if (n < 100) {
        const tens = Math.floor(n / 10)
        const ones = n % 10
        return `${CN_DIGITS[tens]}拾${ones ? CN_DIGITS[ones] : ''}`
    }
    return String(n).padStart(2, '0')
}

const userOrder = computed(() =>
    displayMessages.value.filter((m) => m.role === 'user').map((m) => m.id),
)
const chapterCount = computed(() => userOrder.value.length)
const chapterNoOf = (id: string) => labChapterNumeral(userOrder.value.indexOf(id) + 1)
// 朱砂纪律：只有「当前章节」（最后一章）的编号用朱砂，其余退为三级墨色。
const isCurrentChapter = (id: string) => userOrder.value[userOrder.value.length - 1] === id

// ---- 落版动效：新章节的标题按字母级 fade-in 压进长卷 ----

const reducedMotion = typeof window !== 'undefined'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
const freshChapterId = ref('')
let freshTimer: ReturnType<typeof setTimeout> | null = null

const charsOf = (text: string) => Array.from(text || '')
// 总 stagger 约 120ms：按字数均分，长问句也不会拖慢落版。
const charDelay = (i: number, len: number) =>
    `${Math.round(i * Math.min(120 / Math.max(len - 1, 1), 30))}ms`

const markFresh = (id: string) => {
    if (reducedMotion) return
    freshChapterId.value = id
    if (freshTimer) clearTimeout(freshTimer)
    // 播完即归还为纯文本，避免后续重渲染时重播。
    freshTimer = setTimeout(() => {
        freshChapterId.value = ''
        freshTimer = null
    }, 900)
}

// ---- 脚注系统：参考来源取自真实 knowledge_references ----

const refsOf = (msg: LabMessage): any[] =>
    Array.isArray(msg.knowledge_references) ? msg.knowledge_references : []

const refTitle = (r: any): string =>
    r?.knowledge_title || r?.knowledge_filename || r?.metadata?.url || '未命名资料'

const refSnippet = (r: any): string => {
    const flat = String(r?.content || '').replace(/\s+/g, ' ').trim()
    return flat.length > 140 ? `${flat.slice(0, 140)}…` : flat
}

// 文末「参考来源」章：汇总全部 AI 章节的引用，按文档去重、统一落号。
const allReferences = computed(() => {
    const seen = new Set<string>()
    const out: any[] = []
    for (const m of displayMessages.value) {
        if (m.role === 'user') continue
        for (const r of refsOf(m)) {
            const key = String(r?.knowledge_id || r?.id || refTitle(r))
            if (seen.has(key)) continue
            seen.add(key)
            out.push(r)
            if (out.length >= 20) return out
        }
    }
    return out
})

// ---- 滚动与发送 ----

const scrollEl = ref<HTMLElement | null>(null)
const scrollToBottom = () => {
    nextTick(() => {
        const el = scrollEl.value
        if (el) el.scrollTop = el.scrollHeight
    })
}

const appendLocalUserMessage = (text: string) => {
    const id = `lab-local-${++localSeq}`
    localMessages.value.push({
        id,
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
    })
    markFresh(id)
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
@import '../tokens/b.less';

.lab-b {
    min-height: 100vh;
    background: var(--dl-bg);
    color: var(--dl-ink);
    font-family: var(--dl-font-body);
    -webkit-font-smoothing: antialiased;
}

// 报头双线：上粗下细，报刊的第一身份。
.lab-rule-double {
    height: 6px;
    box-sizing: border-box;
    border-top: 2px solid var(--dl-rule);
    border-bottom: 1px solid var(--dl-rule);
}

.lab-section-head {
    display: flex;
    align-items: baseline;
    gap: 14px;
    margin-bottom: 4px;
}

.lab-section-label {
    font-family: var(--dl-font-ui);
    font-size: 12px;
    letter-spacing: 0.14em;
    color: var(--dl-ink-2);
    white-space: nowrap;
}

.lab-section-rule {
    flex: 1;
    border-top: 1px solid var(--dl-hairline);
    transform: translateY(-3px);
}

.lab-retry {
    border: 1px solid var(--dl-border);
    border-radius: var(--dl-radius);
    background: var(--dl-surface-2);
    color: var(--dl-accent);
    font-family: var(--dl-font-ui);
    font-size: 12px;
    padding: 4px 12px;
    cursor: pointer;

    &:hover {
        border-color: var(--dl-accent);
    }
}

/* ================= 空态 · 晨报首屏 ================= */

.lab-empty {
    min-height: 100vh;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 15vh 24px 64px;
}

.lab-masthead {
    width: min(680px, 100%);
}

.lab-masthead-top {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 10px;
    font-family: var(--dl-font-ui);
    font-size: 11px;
    letter-spacing: 0.16em;
    color: var(--dl-ink-2);
}

.lab-masthead-date {
    letter-spacing: 0.08em;
    color: var(--dl-ink-3);
}

// 报头式主标题：展示衬线 700，尺度比对正文 ~2.5:1。
.lab-headline {
    margin: 26px 0 30px;
    font-family: var(--dl-font-display);
    font-weight: 700;
    font-size: clamp(30px, 4.6vw, 40px);
    line-height: 1.25;
    letter-spacing: 0.02em;
    color: var(--dl-ink);
}

.lab-empty-composer {
    width: min(680px, 100%);
}

.lab-issues {
    width: min(680px, 100%);
    margin-top: 56px;
}

// 期刊目录：纯文字列表，无卡片，行间通栏细线。
.lab-issue-list {
    display: flex;
    flex-direction: column;
}

.lab-issue {
    display: flex;
    align-items: baseline;
    gap: 18px;
    padding: 13px 6px;
    border: none;
    border-bottom: 1px solid var(--dl-hairline);
    background: transparent;
    text-align: left;
    cursor: pointer;
    font-family: var(--dl-font-body);
    transition: background 150ms ease-out, transform 150ms ease-out;

    &:hover {
        background: var(--dl-accent-soft);
        transform: translateX(4px);

        .lab-issue-title {
            color: var(--dl-accent);
        }
    }

    &.is-skeleton {
        cursor: default;

        &:hover {
            background: transparent;
            transform: none;
        }
    }
}

.lab-issue-date {
    flex-shrink: 0;
    width: 72px;
    font-family: var(--dl-font-ui);
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--dl-ink-3);
}

.lab-issue-main {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
}

.lab-issue-title {
    font-family: var(--dl-font-display);
    font-weight: 600;
    font-size: 15.5px;
    line-height: 1.5;
    color: var(--dl-ink);
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    transition: color 150ms ease-out;
}

.lab-issue-preview {
    font-family: var(--dl-font-ui);
    font-size: 12px;
    line-height: 1.5;
    color: var(--dl-ink-2);
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
}

.lab-issues-fallback {
    padding: 18px 4px;
    font-family: var(--dl-font-ui);
    font-size: 13px;
    color: var(--dl-ink-2);
    display: flex;
    align-items: center;
    gap: 10px;
}

.lab-colophon {
    margin: 20px 0 0;
    font-family: var(--dl-font-ui);
    font-size: 11px;
    letter-spacing: 0.06em;
    color: var(--dl-ink-3);
}

/* ================= 对话中 · 文档长卷 ================= */

.lab-conv {
    height: 100vh;
    display: flex;
    flex-direction: column;
}

.lab-conv-scroll {
    flex: 1;
    overflow-y: auto;
}

// 长文阅读栏宽：680px 的编辑尺度（比聊天基线 720 窄一档）。
.lab-doc {
    max-width: 680px;
    margin: 0 auto;
    padding: 88px 24px 40px;
    box-sizing: border-box;
}

.lab-doc-head {
    margin-bottom: 8px;
}

.lab-doc-kicker {
    margin: 0 0 10px;
    font-family: var(--dl-font-ui);
    font-size: 11px;
    letter-spacing: 0.18em;
    color: var(--dl-ink-2);
}

.lab-doc-title {
    margin: 0 0 8px;
    font-family: var(--dl-font-display);
    font-weight: 700;
    font-size: clamp(24px, 3vw, 30px);
    line-height: 1.3;
    color: var(--dl-ink);
}

.lab-doc-meta {
    margin: 0 0 14px;
    font-family: var(--dl-font-ui);
    font-size: 11.5px;
    letter-spacing: 0.05em;
    color: var(--dl-ink-3);
}

.lab-doc-status {
    padding: 24px 0;
    font-family: var(--dl-font-ui);
    font-size: 13px;
    color: var(--dl-ink-2);
    display: flex;
    align-items: center;
    gap: 10px;
}

// 章节：通栏细分隔线 + 大字编号。
.lab-chapter {
    display: flex;
    align-items: flex-start;
    gap: 18px;
    margin-top: 40px;
    padding-top: 22px;
    border-top: 1px solid var(--dl-hairline);

    &:first-of-type {
        margin-top: 26px;
    }
}

.lab-chapter-no {
    flex-shrink: 0;
    width: 58px;
    padding-top: 2px;
    font-family: var(--dl-font-display);
    font-weight: 700;
    font-size: 40px;
    line-height: 1;
    color: var(--dl-ink-3);
    text-align: left;

    // 朱砂只落在「当前章节」编号上。
    .lab-chapter.is-current & {
        color: var(--dl-accent);
    }
}

.lab-chapter-head {
    min-width: 0;
    flex: 1;
}

.lab-chapter-title {
    margin: 0;
    font-family: var(--dl-font-display);
    font-weight: 700;
    font-size: clamp(22px, 2.6vw, 28px);
    line-height: 1.45;
    color: var(--dl-ink);
    white-space: pre-wrap;
    word-break: break-word;
}

.lab-chapter-char {
    display: inline-block;
}

.lab-chapter-time {
    display: block;
    margin-top: 6px;
    font-family: var(--dl-font-ui);
    font-size: 11px;
    letter-spacing: 0.05em;
    color: var(--dl-ink-3);
}

// AI 答复：正文长文，衬线 16px / 1.9 行高的阅读规格。
.lab-chapter-body {
    margin: 18px 0 8px;
    padding-left: 76px; // 与章节标题对齐（编号栏 58px + 间距 18px），主编不允许两处对齐误差

    :deep(.lab-answer) {
        font-family: var(--dl-font-body);
        font-size: 16px;
        line-height: 1.9;
        color: var(--dl-ink);

        p,
        li {
            font-size: 16px;
            line-height: 1.9;
        }

        h1,
        h2,
        h3,
        h4 {
            font-family: var(--dl-font-display);
            color: var(--dl-ink);
        }

        blockquote {
            border-left-color: var(--dl-ink-3);
            color: var(--dl-ink-2);
        }

        hr {
            border-color: var(--dl-hairline);
        }
    }
}

// 采访手记：工具/检索过程收进折叠栏，字符图标 ※。
.lab-notes {
    margin: 20px 0 4px;
    border-top: 1px solid var(--dl-hairline);
    border-bottom: 1px solid var(--dl-hairline);
    padding: 8px 2px;
    font-family: var(--dl-font-ui);

    summary {
        font-size: 12px;
        letter-spacing: 0.08em;
        color: var(--dl-ink-2);
        cursor: pointer;
        list-style: none;

        &:hover {
            color: var(--dl-ink);
        }
    }
}

.lab-notes-list {
    margin: 10px 0 4px;
    padding: 0 0 0 2px;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 8px;

    li {
        display: flex;
        flex-direction: column;
        gap: 2px;
    }
}

.lab-notes-title {
    font-size: 12.5px;
    font-weight: 600;
    color: var(--dl-ink);
}

.lab-notes-snippet {
    font-size: 12px;
    line-height: 1.6;
    color: var(--dl-ink-2);
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
}

.lab-chapter-byline {
    margin: 14px 0 0;
    font-family: var(--dl-font-ui);
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--dl-ink-3);
}

// 文末「参考来源」章：脚注编号朱砂，悬停原位展开来源卡。
.lab-references {
    margin-top: 44px;

    .lab-rule-double {
        margin-bottom: 16px;
    }

    .lab-section-label {
        display: block;
        margin-bottom: 10px;
    }
}

.lab-ref-list {
    margin: 0;
    padding: 0;
    list-style: none;
}

.lab-ref {
    position: relative;
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 7px 2px;
    border-bottom: 1px solid var(--dl-hairline);
    cursor: default;
}

.lab-ref-no {
    flex-shrink: 0;
    font-family: var(--dl-font-body);
    font-size: 13px;
    color: var(--dl-accent);
}

.lab-ref-title {
    min-width: 0;
    font-family: var(--dl-font-body);
    font-size: 14px;
    line-height: 1.6;
    color: var(--dl-ink);
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
}

.lab-ref-card {
    position: absolute;
    left: 24px;
    bottom: calc(100% - 2px);
    width: min(340px, 80vw);
    padding: 12px 14px;
    border: 1px solid var(--dl-border);
    border-radius: var(--dl-radius);
    background: var(--dl-surface-2);
    box-shadow: var(--dl-shadow-pop);
    opacity: 0;
    visibility: hidden;
    transform: translateY(4px);
    transition: opacity 160ms ease-out, transform 160ms ease-out, visibility 160ms;
    z-index: 5;

    .lab-ref:hover & {
        opacity: 1;
        visibility: visible;
        transform: translateY(0);
    }
}

.lab-ref-card-title {
    margin: 0 0 6px;
    font-family: var(--dl-font-display);
    font-weight: 600;
    font-size: 13.5px;
    line-height: 1.5;
    color: var(--dl-ink);
}

.lab-ref-card-snippet {
    margin: 0;
    font-family: var(--dl-font-ui);
    font-size: 12px;
    line-height: 1.7;
    color: var(--dl-ink-2);
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    -webkit-box-orient: vertical;
}

.lab-conv-composer {
    flex-shrink: 0;
    padding: 10px 24px 22px;
    background: linear-gradient(to top, var(--dl-bg) 72%, transparent);
}

.lab-conv-composer-inner {
    max-width: 680px;
    margin: 0 auto;
}

/* ================= 动效（≤300ms；报头 400ms 为方向签名时刻） ================= */

@media (prefers-reduced-motion: no-preference) {
    // 首次进入：报头标题从字距拉开状态收拢（0.2em → 0.02em，400ms）。
    .lab-headline {
        animation: lab-masthead 400ms ease-out backwards;
    }

    .lab-masthead-top,
    .lab-empty .lab-rule-double {
        animation: lab-fade-in 240ms ease-out backwards;
    }

    .lab-empty-composer {
        animation: lab-fade-in 240ms ease-out 80ms backwards;
    }

    .lab-issues {
        animation: lab-fade-in 240ms ease-out 160ms backwards;
    }

    // 章节切换：整页向上「翻」一档，像翻过一张校样。
    .lab-doc {
        animation: lab-page-turn 260ms ease-out backwards;
    }

    // 落版：新章节标题字母级 fade-in，总 stagger ~120ms。
    .lab-chapter-char {
        animation: lab-type-in 240ms ease-out backwards;
    }
}

@keyframes lab-masthead {
    from {
        opacity: 0.3;
        letter-spacing: 0.2em;
    }

    to {
        opacity: 1;
        letter-spacing: 0.02em;
    }
}

@keyframes lab-fade-in {
    from {
        opacity: 0;
        transform: translateY(8px);
    }

    to {
        opacity: 1;
        transform: translateY(0);
    }
}

@keyframes lab-page-turn {
    from {
        opacity: 0;
        transform: translateY(12px);
    }

    to {
        opacity: 1;
        transform: translateY(0);
    }
}

@keyframes lab-type-in {
    from {
        opacity: 0;
        transform: translateY(6px);
    }

    to {
        opacity: 1;
        transform: translateY(0);
    }
}
</style>
