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

            <!-- 桌上摊着的便签：继续昨天的工作 -->
            <section v-if="recentSessions.length > 0" class="workbench-recents" data-rise>
                <h2 class="workbench-section-title">{{ $t('createChat.workbench.continueTitle') }}</h2>
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

onMounted(() => {
    fetchSuggestedQuestions();
    loadRecentSessions();
    // 首次进入：工作台卡片从下方 12px 处依次浮起（stagger 60ms）
    requestAnimationFrame(() => {
        if (workbenchRef.value) rise(workbenchRef.value.querySelectorAll('[data-rise]'));
    });
    const queryQ = route.query.q;
    if (typeof queryQ === 'string' && queryQ.trim()) {
        inputFieldRef.value?.triggerSend(queryQ.trim());
    }
});

watch(
    () => route.query.q,
    (newQ) => {
        if (typeof newQ === 'string' && newQ.trim()) {
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
    align-items: center;
    overflow-y: auto;
}

/* 骨架：composer 居中偏上 1/3 处是主角，卡片沉在它下面 */
.workbench {
    display: flex;
    flex-flow: column;
    align-items: center;
    width: 100%;
    max-width: 960px;
    gap: var(--app-space-6);
    /* 1/3 处：整体重心偏上，桌面留白在下方 */
    padding: var(--app-space-10) var(--app-space-6) var(--app-space-10);
    box-sizing: border-box;

    :deep(.answers-input) {
        position: static;
        transform: translateX(0);
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
}

.create-chat-composer__stage {
    position: relative;
    width: 100%;
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
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: var(--app-space-4);
}

.recent-card {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--app-space-1);
    min-width: 0;
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
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--app-text-xs);
    color: var(--td-text-color-secondary);
}

.recent-card__time {
    font-family: var(--app-font-family-mono);
    font-size: var(--app-text-2xs);
    color: var(--td-text-color-placeholder);
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
    max-width: 960px;
    margin: 0;
    padding: 0 16px;
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
