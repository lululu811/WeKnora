<template>
    <div class="dl-c lab-c">
        <LabChrome badge="方向 C · 周末市集" v-model="state" />

        <!-- ============ 空态：便签桌面 ============ -->
        <div v-if="state === 'empty'" class="lab-empty">
            <div class="lab-desk">
                <!-- 今日特惠印章：真实内容 = 当前知识库列表的第一个 -->
                <div class="lab-stamp" aria-hidden="true">
                    <span class="lab-stamp-top"> {{ t('lab.todayHot') }} </span>
                    <span class="lab-stamp-name">{{ stampKbName }}</span>
                    <span v-if="knowledgeBases.length" class="lab-stamp-bottom"> {{ t('lab.knowledgeBase') }} </span>
                </div>

                <div class="lab-desk-center">
                    <h1 class="lab-greeting"> {{ t('lab.weekendMarket') }} </h1>
                    <p class="lab-greeting-sub">
                        {{ selectedAgent ? `摊主「${selectedAgent.name}」等着接单` : '点个招牌选摊主，或者直接下单' }}
                    </p>
                    <div class="lab-empty-composer">
                        <LabComposerC :placeholder="composerPlaceholder" fly-to="down" @send="handleSendFromEmpty" />
                    </div>
                </div>

                <!-- 摊位招牌：散落在桌面四周，点选即选摊主 -->
                <div class="lab-stalls">
                    <template v-if="agents.length === 0 && sessionsLoading">
                        <div v-for="n in 3" :key="n" class="lab-stall is-skeleton" :class="`pos-${n * 2 - 1}`">
                            <t-skeleton animation="gradient"
                                :row-col="[{ width: '70%', height: '16px' }, { width: '40%', height: '11px' }]" />
                        </div>
                    </template>
                    <div v-else-if="agents.length === 0" class="lab-stall is-skeleton pos-1">
                        <span class="lab-stall-name"> {{ t('lab.vendorNotOpen') }} </span>
                        <span class="lab-stall-open"> {{ t('lab.browseAround') }} </span>
                    </div>
                    <button v-for="(agent, i) in displayAgents" v-else :key="agent.id" type="button"
                        class="lab-stall" :class="[`pos-${(i % 6) + 1}`, {
                            'is-selected': selectedAgentId === agent.id,
                            'is-flipping': flippingId === agent.id,
                        }]"
                        :style="stallStyle(agent, i)" @click="selectAgent(agent)">
                        <span class="lab-stall-sticker" :style="{ '--sticker-rot': `${(i % 2 ? 1 : -1) * 2}deg` }">
                            {{ stallChar(agent) }}
                        </span>
                        <span class="lab-stall-name">{{ agent.name }}</span>
                        <span class="lab-stall-open">{{ selectedAgentId === agent.id ? '接单中' : '营业中' }}</span>
                    </button>
                </div>

                <p class="lab-datascope">{{ agents.length }} 个摊位出摊中 · {{ knowledgeBases.length }} 个知识库</p>
            </div>
        </div>

        <!-- ============ 对话中：单列消息流（基线不动） ============ -->
        <div v-else class="lab-conv">
            <div ref="scrollEl" class="lab-conv-scroll">
                <div class="lab-conv-column" :style="{ '--stall-color': currentStall.bg, '--stall-on': currentStall.fg }">
                    <!-- 当前摊主的招牌横幅 -->
                    <div class="lab-conv-sign">
                        <span class="lab-conv-sign-name">{{ selectedAgent ? selectedAgent.name : '市集摊主' }}</span>
                        <span class="lab-conv-sign-sub">{{ sessionTitle || '随便聊，都在行' }}</span>
                    </div>

                    <div v-if="messagesLoading" class="lab-making">
                        <span class="lab-making-icon"><t-icon name="tools" /></span>
                        <span> {{ t('lab.vendorPreparing') }} </span>
                    </div>
                    <div v-else-if="messagesError" class="lab-conv-status">
                        <span>会话消息加载失败（{{ messagesError }}）</span>
                        <button type="button" class="lab-retry" @click="reloadMessages"> {{ t('common.retry') }} </button>
                    </div>
                    <div v-else-if="displayMessages.length === 0" class="lab-conv-status">{{ t('lab.noOrders') }}
                    </div>
                    <template v-else>
                        <template v-for="msg in displayMessages" :key="msg.id">
                            <!-- 用户消息：圆胖彩色气泡 -->
                            <div v-if="msg.role === 'user'" class="lab-msg-user-row">
                                <div class="lab-msg-user">
                                    <span class="lab-msg-user-text">{{ msg.content }}</span>
                                </div>
                                <span class="lab-msg-time">{{ labRelativeTime(msg.created_at) }}</span>
                            </div>
                            <!-- AI 答复：牛皮纸质感卡片 + 摊主贴纸 -->
                            <div v-else-if="msg.content && msg.content.trim()" class="lab-msg-ai-row">
                                <span class="lab-msg-ai-sticker" aria-hidden="true">{{ stallChar(selectedAgent) }}</span>
                                <div class="lab-msg-ai-card">
                                    <LabAnswerBody :content="msg.content"
                                        :knowledge-references="msg.knowledge_references" />
                                    <span class="lab-msg-time lab-msg-time-ai">{{ labRelativeTime(msg.created_at)
                                        }}</span>
                                </div>
                            </div>
                        </template>
                    </template>
                </div>
            </div>
            <div class="lab-conv-composer">
                <div class="lab-conv-composer-inner">
                    <LabComposerC fly-to="up" :placeholder="composerPlaceholder" @send="handleSend" />
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
    type LabNamedItem,
} from '../shared/useLabData'
import LabComposerC from './LabComposerC.vue'
import LabAnswerBody from '../a/LabAnswerBody.vue'

const { t } = useI18n()

const state = ref<LabState>('empty')

const {
    messages,
    messagesLoading,
    messagesError,
    sessionTitle,
    sessionsLoading,
    knowledgeBases,
    agents,
    loadAll,
    reloadMessages,
} = useLabData()

onMounted(loadAll)

const reducedMotion = typeof window !== 'undefined'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches

// ---------- 摊位（智能体） ----------

interface StallColor {
    bg: string
    fg: string
}

// 市集色语义固定：Z哥=砖红、快速问答=杏黄、智能推理=青绿、维基问答=陶紫。
const STALL_SEMANTICS: Array<{ match: RegExp; color: StallColor }> = [
    { match: /z哥/i, color: { bg: 'var(--dl-accent)', fg: '#FFFFFF' } },
    { match: /快速/, color: { bg: 'var(--dl-accent-2)', fg: '#3B2A1E' } },
    { match: /推理/, color: { bg: 'var(--dl-aux)', fg: '#FFFFFF' } },
    { match: /维基|wiki/i, color: { bg: 'var(--dl-accent-3)', fg: '#FFFFFF' } },
]
const STALL_CYCLE: StallColor[] = STALL_SEMANTICS.map((s) => s.color)

const stallColorOf = (agent: LabNamedItem, index: number): StallColor => {
    const named = STALL_SEMANTICS.find((s) => s.match.test(agent.name))
    return named ? named.color : STALL_CYCLE[index % STALL_CYCLE.length]
}

const stallStyle = (agent: LabNamedItem, index: number) => {
    const c = stallColorOf(agent, index)
    return { '--i': index, '--stall-color': c.bg, '--stall-on': c.fg }
}

const stallChar = (agent?: LabNamedItem | null) => (agent?.name || '市').trim().charAt(0)

// 桌面最多摆 6 块招牌，摆不下的不硬塞。
const displayAgents = computed(() => agents.value.slice(0, 6))

const selectedAgentId = ref('')
const selectedAgent = computed(() => agents.value.find((a) => a.id === selectedAgentId.value) || null)

const flippingId = ref('')
let flipTimer: ReturnType<typeof setTimeout> | null = null

const selectAgent = (agent: LabNamedItem) => {
    selectedAgentId.value = agent.id
    if (reducedMotion) return
    // 招牌翻转（rotateY 90° 换面）：重选同一块也翻。
    if (flipTimer) clearTimeout(flipTimer)
    flippingId.value = ''
    nextTick(() => {
        flippingId.value = agent.id
        flipTimer = setTimeout(() => {
            flippingId.value = ''
            flipTimer = null
        }, 350)
    })
}

const composerPlaceholder = computed(() =>
    selectedAgent.value ? `向「${selectedAgent.value.name}」下单，问点什么…` : '先逛逛，或者直接问点什么…',
)

// 对话中的摊位色跟随选中的摊主，默认砖红。
const currentStall = computed<StallColor>(() => {
    if (!selectedAgent.value) return { bg: 'var(--dl-accent)', fg: '#FFFFFF' }
    const i = agents.value.findIndex((a) => a.id === selectedAgent.value!.id)
    return stallColorOf(selectedAgent.value, Math.max(i, 0))
})

// 印章内容：卡片写"今日最活跃的知识库"，接口无活跃度口径，取列表第一个。
const stampKbName = computed(() => knowledgeBases.value[0]?.name || '市集上新')

// ---------- 消息 ----------

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
@import '../tokens/c.less';

.lab-c {
    min-height: 100vh;
    background: var(--dl-bg);
    color: var(--dl-ink);
    font-family: var(--dl-font-body);
    -webkit-font-smoothing: antialiased;
}

/* ================= 空态 · 便签桌面 ================= */

.lab-empty {
    min-height: 100vh;
    box-sizing: border-box;
    padding: 0 24px;
}

.lab-desk {
    position: relative;
    width: min(1120px, 100%);
    min-height: 100vh;
    margin: 0 auto;
}

.lab-desk-center {
    position: absolute;
    left: 50%;
    top: 46%;
    transform: translate(-50%, -50%);
    width: min(540px, 46%);
    text-align: center;
}

.lab-greeting {
    margin: 0 0 8px;
    font-family: var(--dl-font-display);
    font-weight: 800;
    font-size: clamp(26px, 3.4vw, 34px);
    letter-spacing: 0.03em;
    line-height: 1.3;
    color: var(--dl-ink);
}

.lab-greeting-sub {
    margin: 0 0 22px;
    font-size: 13px;
    color: var(--dl-ink-2);
}

// 「今日特惠」印章：双圈红印 + 微旋转，盖在桌面角落
.lab-stamp {
    position: absolute;
    top: 76px;
    right: 56px;
    width: 128px;
    height: 128px;
    border-radius: 50%;
    border: 2px solid var(--dl-accent);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    padding: 14px;
    box-sizing: border-box;
    text-align: center;
    color: var(--dl-accent);
    transform: rotate(-8deg);
    user-select: none;

    &::before {
        content: '';
        position: absolute;
        inset: 5px;
        border-radius: 50%;
        border: 2px dashed var(--dl-accent);
        opacity: 0.55;
    }
}

.lab-stamp-top,
.lab-stamp-bottom {
    font-size: 11px;
    letter-spacing: 0.2em;
}

.lab-stamp-name {
    font-family: var(--dl-font-hand);
    font-size: 17px;
    line-height: 1.25;
    max-width: 100%;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
}

/* ---------- 摊位招牌 ---------- */

.lab-stalls {
    position: absolute;
    inset: 0;
    pointer-events: none;
}

.lab-stall {
    --rot: 0deg;
    position: absolute;
    pointer-events: auto;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    width: 172px;
    padding: 15px 16px 12px;
    border: 2px solid var(--dl-border);
    border-radius: var(--dl-radius-lg);
    background: var(--stall-color, var(--dl-accent));
    color: var(--stall-on, #FFFFFF);
    box-shadow: var(--dl-shadow-card);
    transform: rotate(var(--rot));
    cursor: pointer;
    text-align: left;
    font-family: var(--dl-font-body);
    transition: transform 160ms ease-out, box-shadow 160ms ease-out;

    &.pos-1 { left: 1%; top: 8%; --rot: -2.5deg; }
    &.pos-2 { right: 3%; top: 30%; --rot: 2deg; }
    &.pos-3 { left: 0; top: 52%; --rot: 1.5deg; }
    &.pos-4 { right: 0.5%; top: 58%; --rot: -2deg; }
    &.pos-5 { left: 15%; bottom: 6%; --rot: -1.5deg; }
    &.pos-6 { right: 14%; bottom: 4%; --rot: 2.5deg; }

    &:hover {
        transform: rotate(var(--rot)) translateY(-2px);
        box-shadow: var(--dl-shadow-card-hover);
    }

    // 按下凹陷：位移 + 厚度阴影消失
    &:active {
        transform: rotate(var(--rot)) translateY(2px);
        box-shadow: var(--dl-shadow-pressed);
    }

    &.is-selected {
        transform: rotate(var(--rot)) translateY(-3px);
        box-shadow: var(--dl-shadow-card-hover);
        outline: 3px dashed var(--dl-ink);
        outline-offset: 4px;
    }

    &.is-skeleton {
        background: var(--dl-surface);
        cursor: default;

        &:hover {
            transform: rotate(var(--rot));
            box-shadow: var(--dl-shadow-card);
        }
    }
}

// 贴纸徽章：白边 + 微旋转，斜贴在招牌右上角
.lab-stall-sticker {
    position: absolute;
    top: -13px;
    right: -11px;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    border: 3px solid var(--dl-sticker-edge);
    background: var(--dl-surface-2);
    color: var(--stall-color, var(--dl-accent));
    box-shadow: 0 2px 0 rgba(59, 42, 30, 0.35);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-family: var(--dl-font-hand);
    font-size: 16px;
    transform: rotate(var(--sticker-rot, 2deg));
}

.lab-stall-name {
    font-family: var(--dl-font-hand);
    font-size: 19px;
    line-height: 1.3;
    max-width: 100%;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
}

.lab-stall-open {
    font-size: 11px;
    letter-spacing: 0.12em;
    opacity: 0.85;
}

.lab-datascope {
    position: absolute;
    bottom: 20px;
    left: 50%;
    transform: translateX(-50%);
    margin: 0;
    font-size: 11px;
    letter-spacing: 0.06em;
    color: var(--dl-ink-3);
    white-space: nowrap;
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
    max-width: 760px;
    margin: 0 auto;
    padding: 88px 24px 40px;
    box-sizing: border-box;
}

.lab-conv-sign {
    display: inline-flex;
    align-items: baseline;
    gap: 10px;
    max-width: 100%;
    padding: 10px 18px;
    border: 2px solid var(--dl-border);
    border-radius: var(--dl-radius);
    background: var(--stall-color, var(--dl-accent));
    color: var(--stall-on, #FFFFFF);
    box-shadow: var(--dl-shadow-card);
    transform: rotate(-1deg);
    margin-bottom: 34px;
}

.lab-conv-sign-name {
    font-family: var(--dl-font-hand);
    font-size: 19px;
    white-space: nowrap;
}

.lab-conv-sign-sub {
    font-size: 12px;
    opacity: 0.85;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
}

// AI 工作时：摊主「正在制作」的小动画
.lab-making {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    border: 2px dashed var(--dl-border);
    border-radius: 999px;
    background: var(--dl-surface);
    color: var(--dl-ink-2);
    font-size: 13px;
    margin: 6px 0 4px;
}

.lab-making-icon {
    display: inline-flex;
    color: var(--dl-accent);
}

.lab-conv-status {
    padding: 24px 0;
    font-size: 13px;
    color: var(--dl-ink-2);
    display: flex;
    align-items: center;
    gap: 10px;
}

.lab-retry {
    border: 2px solid var(--dl-border);
    border-radius: 10px;
    background: var(--dl-surface-2);
    color: var(--dl-accent);
    font-size: 12px;
    padding: 4px 14px;
    cursor: pointer;
    box-shadow: var(--dl-shadow-pressed);

    &:active {
        transform: translateY(1px);
        box-shadow: none;
    }
}

// 用户消息：圆胖彩色气泡，颜色跟随当前摊位
.lab-msg-user-row {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 5px;
    margin: 28px 0 16px;
}

.lab-msg-user {
    max-width: 74%;
    padding: 11px 16px;
    border-radius: 20px;
    border: 2px solid var(--dl-border);
    background: var(--stall-color, var(--dl-accent));
    color: var(--stall-on, #FFFFFF);
    box-shadow: 0 2px 0 rgba(59, 42, 30, 0.35);
}

.lab-msg-user-text {
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
}

// AI 答复：牛皮纸质感卡片 + 摊主贴纸斜贴左上角
.lab-msg-ai-row {
    position: relative;
    margin: 0 0 12px;
}

.lab-msg-ai-sticker {
    position: absolute;
    top: -12px;
    left: -10px;
    z-index: 1;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    border: 3px solid var(--dl-sticker-edge);
    background: var(--dl-surface-2);
    color: var(--stall-color, var(--dl-accent));
    box-shadow: 0 2px 0 rgba(59, 42, 30, 0.35);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-family: var(--dl-font-hand);
    font-size: 15px;
    transform: rotate(-6deg);
}

.lab-msg-ai-card {
    border: 2px solid var(--dl-border);
    border-radius: var(--dl-radius-lg);
    background: var(--dl-kraft);
    // 牛皮纸纤维：极淡的斜向纹路
    background-image: repeating-linear-gradient(105deg,
            rgba(59, 42, 30, 0.025) 0 2px,
            transparent 2px 5px);
    box-shadow: var(--dl-shadow-card);
    padding: 22px 22px 12px;
}

.lab-msg-time {
    font-family: var(--dl-font-mono);
    font-size: 10.5px;
    color: var(--dl-ink-3);
}

.lab-msg-time-ai {
    display: block;
    margin-top: 8px;
}

.lab-conv-composer {
    flex-shrink: 0;
    padding: 10px 24px 22px;
    background: linear-gradient(to top, var(--dl-bg) 72%, transparent);
}

.lab-conv-composer-inner {
    max-width: 760px;
    margin: 0 auto;
}

/* ================= 窄屏：散落桌面收成单列 ================= */

@media (max-width: 980px) {
    .lab-desk {
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 14vh 4px 48px;
    }

    .lab-desk-center {
        position: static;
        transform: none;
        width: min(560px, 100%);
    }

    .lab-stalls {
        position: static;
        pointer-events: auto;
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 20px 18px;
        margin-top: 40px;
    }

    .lab-stall {
        position: static;
    }

    .lab-stamp {
        position: static;
        order: 3;
        margin-top: 44px;
    }

    .lab-datascope {
        position: static;
        transform: none;
        margin-top: 26px;
    }
}

/* ================= 动效（橡皮筋与木牌；签名弹性时刻允许 >300ms） ================= */

@media (prefers-reduced-motion: no-preference) {
    .lab-greeting {
        animation: lab-pop 300ms ease-out backwards;
    }

    .lab-greeting-sub {
        animation: lab-pop 300ms ease-out 60ms backwards;
    }

    .lab-empty-composer {
        animation: lab-pop 300ms ease-out 110ms backwards;
    }

    // 首次进入：招牌从上方掉落挂起（spring, bounce 0.4）
    .lab-stall:not(.is-skeleton) {
        animation: lab-stall-drop 460ms cubic-bezier(0.3, 1.4, 0.5, 1) calc(140ms + var(--i, 0) * 70ms) backwards;
    }

    // 切换摊主：招牌翻转（rotateY 90° 换面）
    .lab-stall.is-flipping {
        animation: lab-stall-flip 340ms ease-in-out;
    }

    // 印章盖下
    .lab-stamp {
        animation: lab-stamp-in 480ms cubic-bezier(0.3, 1.5, 0.5, 1) 320ms backwards;
    }

    // 摊主「正在制作」的捶打小动画
    .lab-making-icon {
        animation: lab-hammer 560ms ease-in-out infinite;
    }
}

@keyframes lab-pop {
    from {
        opacity: 0;
        transform: translateY(10px) scale(0.96);
    }

    to {
        opacity: 1;
        transform: translateY(0) scale(1);
    }
}

@keyframes lab-stall-drop {
    0% {
        opacity: 0;
        transform: translateY(-30px) rotate(var(--rot)) scale(1.05);
    }

    55% {
        opacity: 1;
        transform: translateY(5px) rotate(var(--rot)) scale(1);
    }

    78% {
        transform: translateY(-2px) rotate(var(--rot));
    }

    100% {
        transform: translateY(0) rotate(var(--rot));
    }
}

@keyframes lab-stall-flip {
    0% {
        transform: rotate(var(--rot)) rotateY(0deg);
    }

    50% {
        transform: rotate(var(--rot)) rotateY(90deg);
    }

    100% {
        transform: rotate(var(--rot)) rotateY(0deg);
    }
}

@keyframes lab-stamp-in {
    0% {
        opacity: 0;
        transform: rotate(-8deg) scale(1.4);
    }

    60% {
        opacity: 1;
        transform: rotate(-8deg) scale(0.92);
    }

    100% {
        transform: rotate(-8deg) scale(1);
    }
}

@keyframes lab-hammer {
    0%,
    100% {
        transform: rotate(0deg);
    }

    25% {
        transform: rotate(-18deg);
    }

    60% {
        transform: rotate(4deg);
    }
}
</style>
