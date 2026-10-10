<template>
    <div class="dialogue-wrap">
        <div class="workbench" ref="workbenchRef">
            <!-- 欢迎语：缩成 composer 上方一行衬线小字 -->
            <header class="workbench-greeting" data-rise>
                <h1 class="workbench-greeting__line">{{ greetingLine }}</h1>
                <p class="workbench-greeting__sub">{{ $t('createChat.workbench.greetingSub') }}</p>
            </header>

            <!-- 主角：composer 居中偏上 1/3 处，聚焦时珊瑚暖光从下缘晕开 -->
            <div class="create-chat-composer" :class="{ 'is-focused': composerFocused }" data-rise>
                <div v-if="hostSandboxEnabled" class="project-dir-bar">
                    <button type="button" class="project-dir-bar__btn"
                        :class="{ 'is-bound': !!selectedProjectDir, 'is-picking': pickingProjectDir }"
                        :disabled="pickingProjectDir" :title="selectedProjectDir || $t('createChat.openProject')"
                        @click="openProjectDir">
                        <t-icon :name="pickingProjectDir ? 'loading' : 'folder'" />
                        <span class="project-dir-bar__name">{{
                            selectedProjectDir ? projectDirBasename(selectedProjectDir) : $t('createChat.openProject')
                        }}</span>
                    </button>
                    <button v-if="selectedProjectDir" type="button" class="project-dir-bar__clear"
                        :aria-label="$t('createChat.clearProject')" @click="clearProjectDir">×</button>
                </div>
                <div class="create-chat-composer__stage">
                    <InputField ref="inputFieldRef" @send-msg="sendMsg" />
                </div>
            </div>

            <!-- 大盘预览入口：横幅 + 三组微型指标（真实数据），点击进 /dashboard 全屏大屏 -->
            <section v-if="marketPreview.ok" class="workbench-market" data-rise>
                <h2 class="workbench-section-title">{{ $t('createChat.marketEntry.sectionTitle') }}</h2>
                <router-link class="market-entry" to="/dashboard"
                    :aria-label="$t('createChat.marketEntry.open')">
                    <span class="market-entry__text">
                        <span class="market-entry__title">{{ $t('createChat.marketEntry.title') }}</span>
                        <span class="market-entry__sub">{{ $t('createChat.marketEntry.subtitle') }}</span>
                    </span>
                    <span class="market-entry__metrics">
                        <span class="market-entry__metric">
                            <span class="k">{{ $t('createChat.marketEntry.metrics.index') }}</span>
                            <span class="v md-num" :class="marketPreview.indexTone">
                                {{ marketPreview.indexText }}
                            </span>
                        </span>
                        <span class="market-entry__metric">
                            <span class="k">{{ $t('createChat.marketEntry.metrics.limits') }}</span>
                            <span class="v md-num">
                                <span :class="marketPreview.limitUpTone">{{ marketPreview.limitUpText }}</span>
                                <span class="md-sep">/</span>
                                <span :class="marketPreview.limitDownTone">{{ marketPreview.limitDownText }}</span>
                            </span>
                        </span>
                        <span class="market-entry__metric">
                            <span class="k">{{ $t('createChat.marketEntry.metrics.watchlist') }}</span>
                            <span class="v md-num">{{ marketPreview.watchlistText }}</span>
                        </span>
                    </span>
                    <span class="market-entry__go" aria-hidden="true">
                        <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                            <path d="M4.5 2.5 9 6.5l-4.5 4" stroke="currentColor" stroke-width="1.6"
                                stroke-linecap="round" stroke-linejoin="round" />
                        </svg>
                    </span>
                </router-link>
            </section>

            <!-- 桌上摊着的便签：继续昨天的工作 -->
            <section v-if="recentSessions.length > 0" class="workbench-recents" data-rise>                <h2 class="workbench-section-title">{{ $t('createChat.workbench.continueTitle') }}</h2>
                <div class="workbench-recents__grid">
                    <button v-for="(s, i) in recentSessions" :key="s.id" type="button" class="recent-card"
                        :style="{ transitionDelay: `${i * 60}ms` }" @click="resumeSession(s.id)">
                        <span class="recent-card__title">{{ s.title }}</span>
                        <span v-if="s.preview" class="recent-card__preview">{{ s.preview }}</span>
                        <span class="recent-card__time">{{ relativeTime(s.updated_at) }}</span>
                    </button>
                </div>
            </section>

            <!-- 推荐问题（保留原有能力，降到次要位置） -->
            <div v-if="suggestedQuestions.length > 0 || sqLoading" ref="sqContainerRef"
                class="suggested-questions-container" data-rise>
                <div class="suggested-questions-title-row">
                    <p class="suggested-questions-caption">
                        <span class="suggested-questions-title">{{ $t('chat.suggestedQuestions') }}</span>
                        <button type="button" class="suggested-questions-refresh" :disabled="sqLoading"
                            :title="$t('chat.refreshSuggestedQuestions')"
                            :aria-label="$t('chat.refreshSuggestedQuestions')" @click="fetchSuggestedQuestions">
                            <t-icon :name="sqLoading ? 'loading' : 'refresh'"
                                :class="{ 'sq-refresh-spin': sqLoading }" />
                        </button>
                    </p>
                </div>
                <div class="suggested-questions-grid">
                    <div v-for="item in suggestedQuestions" :key="item.question" class="suggested-question-card"
                        @click="handleSuggestedQuestionClick(item)">
                        <span class="suggested-question-text">{{ item.question }}</span>
                        <span v-if="item.source === 'faq'" class="suggested-question-badge faq">FAQ</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <ContextualGuide tour="chat" :when="showChatContextualGuide" />

    <!-- 知识库编辑器（创建/编辑统一组件） -->
    <KnowledgeBaseEditorModal :visible="uiStore.showKBEditorModal" :mode="uiStore.kbEditorMode"
        :kb-id="uiStore.currentKBId || undefined" :initial-type="uiStore.kbEditorType"
        @update:visible="(val) => val ? null : uiStore.closeKBEditor()" @success="handleKBEditorSuccess" />
</template>
<script setup lang="ts">
import { ref, watch, onMounted, computed } from 'vue';
import ContextualGuide from '@/components/ContextualGuide.vue';
import InputField from '@/components/Input-field.vue';
import { createSessions, getSessionsList, getMessageList } from "@/api/chat/index";
import { pickHostProjectDir } from '@/utils/desktopProjectDir';
import { projectDirBasename, shouldRenderHostProjectSettings, withOptionalProjectDir } from '@/utils/hostWorkspace';
import { getSuggestedQuestions } from "@/api/agent/index";
import type { SuggestedQuestion } from "@/api/agent/index";
import { questionOriginFromSuggestion, type SendMessageOptions } from '@/utils/questionOrigin';
import { parseLaunchAgentId } from '@/utils/launchAgent';
import { useMenuStore } from '@/stores/menu';
import { useSettingsStore } from '@/stores/settings';
import { useUIStore } from '@/stores/ui';
import { useDeploymentCapabilitiesStore } from '@/stores/deploymentCapabilities';
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router';
import { MessagePlugin } from 'tdesign-vue-next';
import { useI18n } from 'vue-i18n';
import KnowledgeBaseEditorModal from '@/views/knowledge/KnowledgeBaseEditorModal.vue';
import { useKnowledgeBaseCreationNavigation } from '@/hooks/useKnowledgeBaseCreationNavigation';
import { useStaggerRise } from '@/composables/useMotion';
import { getMarketSnapshot } from '@/finance/api/market';
import { listWatchlist } from '@/finance/api/watchlist';
import { stripMarkdownToPreview, toRelativeTime } from '@/utils/workbenchFormat';

const router = useRouter();
const route = useRoute();
const usemenuStore = useMenuStore();
const settingsStore = useSettingsStore();
onBeforeRouteLeave((to) => {
    // The first send carries the draft into its new session; abandoning the
    // composer must not make this a default for the next conversation.
    if (!to.path.startsWith('/platform/chat/') || !usemenuStore.isFirstSession) {
        settingsStore.reasoningEffortOverride = '';
    }
});
const uiStore = useUIStore();
const deploymentCapabilities = useDeploymentCapabilitiesStore();
const { t } = useI18n();
const { navigateToKnowledgeBaseList } = useKnowledgeBaseCreationNavigation();

const hostSandboxEnabled = computed(() =>
    shouldRenderHostProjectSettings(deploymentCapabilities.isSupported('settings.sandbox.host')),
);
const selectedProjectDir = ref('');
const pickingProjectDir = ref(false);

const showChatContextualGuide = computed(() => {
    return route.name === 'globalCreatChat' || route.name === 'kbCreatChat';
});

// ===== 午后的工作室 · 工作台首屏 =====
const workbenchRef = ref<HTMLElement | null>(null);
const composerFocused = ref(false);
const { rise } = useStaggerRise({ stagger: 60, duration: 260, displacement: 12 });

/** 按时段给一句衬线问候（方向 A：欢迎语缩成 composer 上方一行小字）。 */
const greetingLine = computed(() => {
    const h = new Date().getHours();
    const key = h < 6 ? 'night' : h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening';
    return t(`createChat.workbench.greeting.${key}`);
});

interface RecentSessionCard { id: string; title: string; preview: string; updated_at: string }
const recentSessions = ref<RecentSessionCard[]>([]);
const relativeTime = (iso: string) => toRelativeTime(iso, t);

const loadRecentSessions = async () => {
    try {
        const res: any = await getSessionsList(1, 3);
        const rows: any[] = Array.isArray(res?.data) ? res.data : [];
        const previews = await Promise.all(
            rows.map((row) =>
                getMessageList({ session_id: row.id, limit: 1, created_at: '' })
                    .then((r: any) => stripMarkdownToPreview(r?.data?.[0]?.content || ''))
                    .catch(() => ''),
            ),
        );
        recentSessions.value = rows.map((row, i) => ({
            id: row.id,
            title: row.title || t('createChat.workbench.untitledSession'),
            preview: previews[i],
            updated_at: row.updated_at || row.created_at,
        }));
    } catch {
        recentSessions.value = [];
    }
};

const resumeSession = (sessionId: string) => {
    router.push(`/platform/chat/${sessionId}`);
};

// ===== 大盘预览入口横幅 =====
// 只取三个数（上证涨跌 / 涨停跌停 / 自选触发数），但它们与 /dashboard 大屏读的是
// 同一个 `/api/market/snapshot` 聚合接口 —— 一次请求两处复用，不额外打一轮。
//
// `ok=false` 时整条横幅不渲染：行情服务不可用时，工作台不该多出一块永远空着的
// 卡片让人以为"今天没行情"。不显示比显示占位更诚实。
const marketPreview = ref({
    ok: false,
    indexText: '—',
    indexTone: '',
    limitUpText: '—',
    limitUpTone: '',
    limitDownText: '—',
    limitDownTone: '',
    watchlistText: '—',
});

const loadMarketPreview = async () => {
    try {
        const [snapRes, watchRes] = await Promise.allSettled([getMarketSnapshot(60), listWatchlist()]);
        if (snapRes.status !== 'fulfilled') return;
        const snap = snapRes.value;
        // 四大指数一个都没拿到 → 不渲染横幅
        const sse = snap.indices.find((i) => i.thscode === '000001.SH') ?? snap.indices[0];
        if (!sse) return;

        const tone = (v: number | null) => (v == null ? 'md-flat' : v >= 0 ? 'md-up' : 'md-down');
        const pct = (v: number | null) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);

        let watchlistText = t('createChat.marketEntry.metrics.noWatchlist');
        if (watchRes.status === 'fulfilled') {
            const items = (watchRes.value.data ?? []).filter((i) => i.state !== 'dropped');
            const triggered = items.filter((i) => i.state === 'triggered').length;
            watchlistText = triggered > 0
                ? t('createChat.marketEntry.metrics.triggered', { n: triggered })
                : t('createChat.marketEntry.metrics.noWatchlist');
        }

        const s = snap.sentiment;
        marketPreview.value = {
            ok: true,
            indexText: `${fmtCompact(sse.last)} ${pct(sse.change_pct)}`.trim(),
            indexTone: tone(sse.change_pct),
            // null 显示破折号，不显示 0：0 在金融语义里是"真的是零只涨停"
            limitUpText: s.limit_up == null ? '—' : String(s.limit_up),
            limitUpTone: 'md-up',
            limitDownText: s.limit_down == null ? '—' : String(s.limit_down),
            limitDownTone: 'md-down',
            watchlistText,
        };
    } catch {
        marketPreview.value.ok = false;
    }
};

/** 指数点位：万位以上不硬塞小数（3,892.45 这种在窄横幅里太长）。 */
const fmtCompact = (v: number | null) => {
    if (v == null || !Number.isFinite(v)) return '—';
    return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

// ===== 推荐问题 =====
const suggestedQuestions = ref<SuggestedQuestion[]>([]);
const sqLoading = ref(true);
const sqContainerRef = ref<HTMLElement | null>(null);
let suggestedQuestionsFetchId = 0;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;

const fetchSuggestedQuestions = async () => {
    const fetchId = ++suggestedQuestionsFetchId;
    sqLoading.value = true;
    try {
        const agentId = settingsStore.selectedAgentId;
        if (!agentId) return;
        const res = await getSuggestedQuestions(agentId, settingsStore.getSuggestedQuestionsParams());
        if (fetchId === suggestedQuestionsFetchId) {
            suggestedQuestions.value = res?.data?.questions || [];
        }
    } catch (err) {
        console.warn('[SuggestedQuestions] Failed to fetch:', err);
        if (fetchId === suggestedQuestionsFetchId) {
            suggestedQuestions.value = [];
        }
    } finally {
        if (fetchId === suggestedQuestionsFetchId) {
            sqLoading.value = false;
        }
    }
};

// 防抖包装，切换知识库/文件时300ms内不重复请求
const debouncedFetch = () => {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { fetchSuggestedQuestions(); }, 300);
};

// 监听 Agent / 知识库 / 文件 / 标签 / MCP / Skill @mention
watch(
    () => ({
        agentId: settingsStore.selectedAgentId,
        kbs: settingsStore.settings.selectedKnowledgeBases,
        files: settingsStore.settings.selectedFiles,
        tags: settingsStore.settings.selectedTags,
        mcps: settingsStore.settings.selectedMCPServices,
        skills: settingsStore.settings.selectedSkills,
    }),
    debouncedFetch,
    { deep: true },
);

/**
 * 把落点指定的 agent 应用到输入态。必须在**路由守卫跑完之后**执行：会话页的
 * onBeforeRouteLeave 会 restoreDefaultsIfSnapshotted()，把整个 settings 换成进入
 * 会话前的快照 —— 在跳转前 selectAgent() 会连同 agent 一起被丢掉，然后新会话就按
 * 「全局默认」（默认 builtin-quick-answer）发出去。
 *
 * 这是「带 prompt 开新对话」这条通道的接盘处（约定的键名与校验见
 * utils/launchAgent.ts）：K 线面板的 HALO 报告按钮、自选页工作台底部的问法都从
 * 这里进来。少了这一步，用户看到的是「检索材料里没有年报数据」—— 快速问答没有
 * 工具，halo.analyze 这类工具名连 schema 都进不去。
 */
const applyLaunchAgent = () => {
    const agentId = parseLaunchAgentId(route.query as Record<string, unknown>);
    if (agentId) settingsStore.selectAgentForLaunch(agentId);
};

onMounted(() => {
    fetchSuggestedQuestions();
    loadRecentSessions();
    loadMarketPreview();
    // 首次进入：工作台卡片从下方 12px 处依次浮起（stagger 60ms）
    requestAnimationFrame(() => {
        if (workbenchRef.value) rise(workbenchRef.value.querySelectorAll('[data-rise]'));
    });
    applyLaunchAgent();
    const queryQ = route.query.q;
    if (typeof queryQ === 'string' && queryQ.trim()) {
        inputFieldRef.value?.triggerSend(queryQ.trim());
    }
});

watch(
    () => route.query.q,
    (newQ) => {
        if (typeof newQ === 'string' && newQ.trim()) {
            applyLaunchAgent();
            inputFieldRef.value?.triggerSend(newQ.trim());
        }
    },
);

const inputFieldRef = ref();

// The suggestion's source rides with this send to the new session's first
// request, so the agent searches it before answering.
const handleSuggestedQuestionClick = (item: SuggestedQuestion) => {
    inputFieldRef.value?.triggerSend(item.question, { questionOrigin: questionOriginFromSuggestion(item) });
};

const sendMsg = (value: string, modelId: string, mentionedItems: any[], imageFiles: any[] = [], attachmentFiles: any[] = [], options: SendMessageOptions = {}) => {
    createNewSession(value, modelId, mentionedItems, imageFiles, attachmentFiles, options);
}

async function createNewSession(value: string, modelId: string, mentionedItems: any[] = [], imageFiles: any[] = [], attachmentFiles: any[] = [], options: SendMessageOptions = {}) {
    const selectedKbs = settingsStore.settings.selectedKnowledgeBases || [];
    const selectedFiles = settingsStore.settings.selectedFiles || [];

    // 构建 session 数据，包含 Agent 配置
    const sessionData: any = {};

    // 添加 Agent 配置（知识库信息在 agent_config 中）
    sessionData.agent_config = {
        enabled: true,
        max_iterations: settingsStore.agentConfig.maxIterations,
        temperature: settingsStore.agentConfig.temperature,
        knowledge_bases: selectedKbs,  // 所有选中的知识库
        knowledge_ids: selectedFiles,  // 所有选中的普通知识/文件
        allowed_tools: settingsStore.agentConfig.allowedTools
    };

    try {
        const res = await createSessions(withOptionalProjectDir(sessionData, selectedProjectDir.value));
        if (res.data && res.data.id) {
            await navigateToSession(res.data.id, value, modelId, mentionedItems, imageFiles, attachmentFiles, options);
        } else {
            console.error('[createChat] Failed to create session');
            MessagePlugin.error(t('createChat.messages.createFailed'));
        }
    } catch (error) {
        console.error('[createChat] Create session error:', error);
        MessagePlugin.error(t('createChat.messages.createError'));
    }
}

const navigateToSession = async (sessionId: string, value: string, modelId: string, mentionedItems: any[], imageFiles: any[] = [], attachmentFiles: any[] = [], options: SendMessageOptions = {}) => {
    const now = new Date().toISOString();
    let obj = {
        title: t('createChat.newSessionTitle'),
        path: `chat/${sessionId}`,
        id: sessionId,
        isMore: false,
        isNoTitle: true,
        created_at: now,
        updated_at: now
    };
    usemenuStore.updataMenuChildren(obj);
    usemenuStore.changeIsFirstSession(true);
    usemenuStore.changeFirstQuery(value, mentionedItems, modelId, imageFiles, attachmentFiles, options.questionOrigin ?? null);
    router.push(`/platform/chat/${sessionId}`);
}

const handleKBEditorSuccess = (kbId: string) => {
    navigateToKnowledgeBaseList(kbId)
}

function clearProjectDir() {
    selectedProjectDir.value = '';
}

async function openProjectDir() {
    if (pickingProjectDir.value) return;
    pickingProjectDir.value = true;
    try {
        const picked = await pickHostProjectDir();
        if (picked) selectedProjectDir.value = picked;
    } catch (e: any) {
        MessagePlugin.error(e?.message || t('createChat.pickFailed'));
    } finally {
        pickingProjectDir.value = false;
    }
}
</script>
<style lang="less" scoped>
.dialogue-wrap {
    flex: 1;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    overflow-y: auto;
    width: 100%;
    box-sizing: border-box;
}

/* 骨架：composer 居中偏上 1/3 处是主角，卡片沉在它下面 */
.workbench {
    display: flex;
    flex-flow: column;
    align-items: stretch;
    width: 100%;
    max-width: 960px;
    margin: 0 auto;
    gap: var(--app-space-6);
    /* 整体重心偏上，桌面留白在下方 */
    padding: var(--app-space-10) var(--app-space-6) var(--app-space-10);
    box-sizing: border-box;

    :deep(.answers-input) {
        position: static !important;
        transform: none !important;
        width: 100% !important;
        max-width: 100% !important;
        align-items: stretch !important;
    }
}

/* 欢迎语：衬线一行小字，不再是页面主标题 */
.workbench-greeting {
    text-align: center;
    margin: 0;
}

.workbench-greeting__line {
    margin: 0;
    font-family: var(--app-font-display);
    /* 展示:正文尺度比 ~2.2:1 —— 正文 14px，欢迎语 28px */
    font-size: var(--app-text-4xl);
    font-weight: 600;
    line-height: 1.3;
    letter-spacing: 0.01em;
    color: var(--td-text-color-primary);
}

.workbench-greeting__sub {
    margin: var(--app-space-2) 0 0;
    font-size: var(--app-text-sm);
    color: var(--td-text-color-secondary);
}

.create-chat-composer {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: var(--app-space-2);
    width: 100%;
    box-sizing: border-box;
}

.create-chat-composer__stage {
    position: relative;
    width: 100%;
    box-sizing: border-box;

    :deep(.rich-input-container) {
        box-sizing: border-box !important;
        width: 100% !important;
        max-width: 100% !important;
    }
}

/* 主视觉：聚焦时一圈极淡的珊瑚暖光从输入框下缘晕开（像台灯亮了） */
.create-chat-composer__stage::after {
    content: '';
    position: absolute;
    left: 12%;
    right: 12%;
    bottom: -18px;
    height: 56px;
    pointer-events: none;
    opacity: 0;
    transition: opacity var(--app-motion-slow) ease-out;
    background: radial-gradient(
        ellipse at 50% 0%,
        color-mix(in srgb, var(--td-brand-color) 26%, transparent) 0%,
        color-mix(in srgb, var(--td-brand-color) 8%, transparent) 42%,
        transparent 72%
    );
    filter: blur(10px);
}

.create-chat-composer:focus-within .create-chat-composer__stage::after {
    opacity: 1;
}

/* 继续昨天的工作：桌上摊着的便签 */
.workbench-recents {
    width: 100%;
    box-sizing: border-box;
}

.workbench-section-title {
    margin: 0 0 var(--app-space-3);
    font-size: var(--app-text-2xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--td-text-color-placeholder);
}

.workbench-recents__grid {
    display: grid;
    /* 260px 下限 → 在 912px 内容宽里正好落 3 列（3×260 + 2×16 = 812 ≤ 912，
       第 4 列要到 1088 才排得下）。*/
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: var(--app-space-4);
    width: 100%;
    box-sizing: border-box;
}

.recent-card {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--app-space-1);
    min-width: 0;
    width: 100%;
    box-sizing: border-box;
    padding: var(--app-space-4) var(--app-space-5);
    border: 1px solid var(--td-component-border);
    border-radius: var(--app-radius-md);
    background: var(--td-bg-color-container);
    text-align: left;
    cursor: pointer;
    /* hover 抬起 -2px + 暖光阴影 */
    transition:
        transform var(--app-motion-base) cubic-bezier(0.16, 1, 0.3, 1),
        box-shadow var(--app-motion-base) ease-out,
        border-color var(--app-motion-base) ease-out;

    &:hover {
        transform: translateY(-2px);
        box-shadow: var(--td-shadow-2);
        border-color: color-mix(in srgb, var(--td-brand-color) 28%, var(--td-component-border));
    }

    /* 按下 scale(0.98) */
    &:active {
        transform: scale(0.98);
        box-shadow: var(--td-shadow-1);
    }
}

.recent-card__title {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--app-font-display);
    font-size: var(--app-text-base);
    font-weight: 600;
    color: var(--td-text-color-primary);
}

.recent-card__preview {
    max-width: 100%;
    overflow: hidden;
    /* 卡片降到 ~293px 宽后单行截断只剩半句话，改双行 clamp。
       原来这里是 white-space:nowrap，正是它把 grid 的 max-content 顶高、
       挤出 2 列窄轨的元凶之一。 */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow-wrap: anywhere;
    font-size: var(--app-text-xs);
    line-height: 1.5;
    color: var(--td-text-color-secondary);
}

.recent-card__time {
    font-family: var(--app-font-family-mono);
    font-size: var(--app-text-2xs);
    color: var(--td-text-color-placeholder);
}

/* ===== 大盘预览入口横幅 =====
   形态是通栏横幅（不是 recent-card 的三列网格），但交互语言完全沿用 recent-card：
   hover 抬起 -2px + 暖光阴影、按下 scale(0.98)。分隔线用 --td-component-border
   （而不是更浅的 --td-component-stroke）—— 样稿评审时把这里从 stroke 加深到
   border 过一次，三组指标挨得太近时 stroke 分隔读不出来。 */
.workbench-market {
    width: 100%;
}

.md-num {
    font-family: var(--app-font-family-mono);
    font-variant-numeric: tabular-nums;
}

.md-up {
    color: var(--md-up, #dc2626);
}

.md-down {
    color: var(--md-down, #047857);
}

.md-flat {
    color: var(--td-text-color-placeholder);
}

.md-sep {
    color: var(--td-text-color-placeholder);
    margin: 0 3px;
}

.market-entry {
    display: flex;
    align-items: center;
    gap: var(--app-space-5);
    width: 100%;
    /* 必须显式声明：项目没有全局 * { box-sizing: border-box }，缺这一行时
       width:100% + padding 0 18px 会让这张卡比 .workbench 内容宽出 38px
       （912 → 950），右边缘对不齐下方输入框。 */
    box-sizing: border-box;
    padding: 14px 18px;
    border: 1px solid var(--td-component-border);
    border-radius: var(--app-radius-xl);
    background: var(--td-bg-color-container);
    box-shadow: var(--td-shadow-1);
    text-align: left;
    text-decoration: none;
    color: inherit;
    transition:
        transform var(--app-motion-base) cubic-bezier(0.16, 1, 0.3, 1),
        box-shadow var(--app-motion-base) ease-out,
        border-color var(--app-motion-base) ease-out;

    &:hover {
        transform: translateY(-2px);
        box-shadow: var(--td-shadow-2);
        border-color: color-mix(in srgb, var(--td-brand-color) 28%, var(--td-component-border));
    }

    &:active {
        transform: scale(0.98);
        box-shadow: var(--td-shadow-1);
    }
}

.market-entry__text {
    display: block;
    min-width: 0;
}

.market-entry__title {
    display: block;
    font-family: var(--app-font-display);
    font-size: var(--app-text-xl);
    font-weight: 600;
    white-space: nowrap;
}

.market-entry__sub {
    display: block;
    margin-top: 2px;
    font-size: var(--app-text-2xs);
    color: var(--td-text-color-placeholder);
    white-space: nowrap;
}

.market-entry__metrics {
    display: flex;
    align-items: center;
    gap: 0;
    margin-left: auto;
}

.market-entry__metric {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 0;
    padding: 0 16px;
    border-left: 1px solid var(--td-component-border);

    .k {
        font-size: var(--app-text-2xs);
        color: var(--td-text-color-placeholder);
        white-space: nowrap;
    }

    .v {
        font-size: var(--app-text-md);
        font-weight: 600;
        white-space: nowrap;
    }
}

.market-entry__go {
    flex: 0 0 auto;
    width: 30px;
    height: 30px;
    border-radius: 50%;
    border: 1px solid color-mix(in srgb, var(--td-brand-color) 34%, var(--td-component-border));
    color: var(--td-brand-color-active);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: background var(--app-motion-base) ease-out;
}

.market-entry:hover .market-entry__go {
    background: color-mix(in srgb, var(--td-brand-color) 8%, transparent);
}

.project-dir-bar {
    display: flex;
    align-items: center;
    gap: var(--app-space-1);
    width: 100%;
    max-width: 960px;
    padding: 0;
    box-sizing: border-box;
}

.project-dir-bar__btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 100%;
    height: 28px;
    padding: 0 10px;
    border: 0.5px solid var(--td-component-border);
    border-radius: var(--app-radius-md);
    background: var(--td-bg-color-container);
    color: var(--td-text-color-secondary);
    font-size: var(--app-text-sm);
    cursor: pointer;
}

.project-dir-bar__btn.is-bound {
    color: var(--td-text-color-primary);
}

.project-dir-bar__btn.is-picking,
.project-dir-bar__btn:disabled {
    cursor: default;
    opacity: 0.75;
}

.project-dir-bar__name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.project-dir-bar__clear {
    flex: 0 0 auto;
    border: none;
    background: transparent;
    padding: 0 4px;
    color: var(--td-text-color-placeholder);
    font-size: var(--app-text-base);
    line-height: 1;
    cursor: pointer;
}

@import '../../components/css/suggested-questions.less';

.suggested-questions-container {
    width: 100%;
    max-width: 100%;
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

.suggested-questions-grid {
    display: flex;
    flex-wrap: wrap;
    gap: var(--app-space-2);
}

.suggested-question-card {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    border: 1px solid var(--td-component-border);
    border-radius: var(--app-radius-md);
    background: var(--td-bg-color-container);
    cursor: pointer;
    transition:
        background var(--app-motion-fast) ease-out,
        border-color var(--app-motion-fast) ease-out,
        transform var(--app-motion-fast) ease-out;

    &:hover {
        border-color: color-mix(in srgb, var(--td-brand-color) 30%, var(--td-component-border));
        background: var(--td-bg-color-container-hover);
    }

    &:active {
        transform: scale(0.98);
    }
}

.suggested-question-text {
    font-size: var(--app-text-xs);
    color: var(--td-text-color-secondary);
}

.suggested-questions-title-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: var(--app-space-2);
}

.suggested-questions-caption {
    display: flex;
    align-items: center;
    gap: 4px;
    margin: 0;
}

.suggested-questions-title {
    font-size: var(--app-text-2xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--td-text-color-placeholder);
}

.suggested-questions-refresh {
    border: none;
    background: transparent;
    padding: 2px;
    color: var(--td-text-color-placeholder);
    cursor: pointer;
}

.sq-refresh-spin {
    animation: sqSpin 1s linear infinite;
}

@keyframes sqSpin {
    to {
        transform: rotate(360deg);
    }
}
</style>
<style lang="less">
.del-menu-popup {
    z-index: 99 !important;

    .t-popup__content {
        width: 100px;
        height: 40px;
        line-height: 30px;
        padding-left: 14px;
        cursor: pointer;
        margin-top: 4px !important;

    }
}
</style>
