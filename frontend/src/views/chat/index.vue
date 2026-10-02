<template>
    <div class="chat" :class="{
        'is-embedded': embeddedMode,
        'has-right-panels': rightPanelsWidth > 0,
        'has-references-panel': referencesDrawerVisible,
    }" :style="{
        '--right-panels-width': `${rightPanelsWidth}px`,
    }">
        <div v-if="!embeddedMode" class="chat-topbar">
            <ChatHeader :session="currentSession" />
            <div v-if="!sandboxPanel.visible.value" class="sandbox-header-toggle">
                <t-tooltip placement="bottom">
                    <template #content>{{ t('chatHeader.toggleSandboxPanel') }}</template>
                    <button type="button" class="sandbox-header-toggle__btn"
                        :aria-label="t('chatHeader.toggleSandboxPanel')" @click="sandboxPanel.open()">
                        <svg viewBox="0 0 20 20" width="16" height="16" fill="none" xmlns="http://www.w3.org/2000/svg"
                            aria-hidden="true">
                            <rect x="1.5" y="1.5" width="17" height="17" rx="3" stroke="currentColor" stroke-width="1.2" />
                            <line x1="12.5" y1="1.5" x2="12.5" y2="18.5" stroke="currentColor" stroke-width="1.2" />
                            <line x1="16" y1="7.5" x2="16" y2="12.5" stroke="currentColor" stroke-width="1.2"
                                stroke-linecap="round" />
                        </svg>
                    </button>
                </t-tooltip>
            </div>
        </div>
        <div class="chat_thread" :style="{ '--chat-composer-height': `${composerHeight}px`, '--chat-scrollbar-gutter': `${scrollbarGutter}px` }">
            <div ref="scrollContainer" class="chat_scroll_box" @scroll="handleScroll">
                <div class="chat_scroll_content">
                    <div class="msg_list" :class="{ 'is-embedded': embeddedMode }">
                        <!-- 消息列表骨架屏 -->
                        <div v-if="historyLoading && messagesList.length === 0" class="msg-skeleton-list">
                            <div class="msg-skeleton msg-skeleton-user">
                                <t-skeleton animation="gradient"
                                    :row-col="[{ width: '45%', height: '36px', type: 'rect' }]" />
                            </div>
                            <div class="msg-skeleton msg-skeleton-bot">
                                <t-skeleton animation="gradient"
                                    :row-col="[{ width: '80%', height: '16px' }, { width: '100%', height: '16px' }, { width: '60%', height: '16px' }]" />
                            </div>
                            <div class="msg-skeleton msg-skeleton-user">
                                <t-skeleton animation="gradient"
                                    :row-col="[{ width: '35%', height: '36px', type: 'rect' }]" />
                            </div>
                            <div class="msg-skeleton msg-skeleton-bot">
                                <t-skeleton animation="gradient"
                                    :row-col="[{ width: '70%', height: '16px' }, { width: '90%', height: '16px' }]" />
                            </div>
                        </div>
                        <!-- 推荐问题卡片 - 仅在新会话（无消息）时展示 -->
                        <div v-if="!embeddedMode && messagesList.length === 0 && !loading"
                            class="suggested-questions-container"
                            :class="{ 'has-questions': suggestedQuestions.length > 0 || suggestedQuestionsLoading }">
                            <!-- 骨架屏占位 -->
                            <div v-if="suggestedQuestionsLoading && suggestedQuestions.length === 0"
                                class="suggested-questions-inner">
                                <div class="suggested-questions-title"><t-skeleton animation="gradient"
                                        :row-col="[{ width: '120px', height: '14px' }]" /></div>
                                <div class="suggested-questions-grid">
                                    <div v-for="n in 6" :key="'sq-skel-' + n"
                                        class="suggested-question-card sq-card-skeleton">
                                        <t-skeleton animation="gradient"
                                            :row-col="[{ width: '100%', height: '14px', type: 'rect' }]" />
                                    </div>
                                </div>
                            </div>
                            <transition v-else appear name="sq-fade">
                                <div v-if="suggestedQuestions.length > 0" class="suggested-questions-inner">
                                    <div class="suggested-questions-title-row">
                                        <p class="suggested-questions-caption">
                                            <span class="suggested-questions-title">{{ t('chat.suggestedQuestions')
                                                }}</span>
                                            <button type="button" class="suggested-questions-refresh"
                                                :disabled="suggestedQuestionsLoading"
                                                :title="t('chat.refreshSuggestedQuestions')"
                                                :aria-label="t('chat.refreshSuggestedQuestions')"
                                                @click="fetchSuggestedQuestions">
                                                <t-icon :name="suggestedQuestionsLoading ? 'loading' : 'refresh'"
                                                    :class="{ 'sq-refresh-spin': suggestedQuestionsLoading }" />
                                            </button>
                                        </p>
                                    </div>
                                    <div class="suggested-questions-grid">
                                        <div v-for="(item, index) in suggestedQuestions" :key="item.question"
                                            class="suggested-question-card"
                                            @click="handleSuggestedQuestionClick(item)">
                                            <span class="suggested-question-text">{{ item.question }}</span>
                                            <span v-if="item.source === 'faq'"
                                                class="suggested-question-badge faq">FAQ</span>
                                        </div>
                                    </div>
                                </div>
                            </transition>
                        </div>
                        <!--
                      关键：必须用 session.id 作为 key，不能用 v-for 的索引。
                      向上滚动加载历史时会插入一批消息（push/unshift）到列表，
                      若用索引作 key 会让所有已渲染消息的 key 漂移，触发整个列表的销毁重建
                      （botmsg / AgentStreamDisplay 全部重新挂载、markdown 重新渲染），
                      这是历史加载时白屏 + layout shift 蔓延到 session 列表的根因。
                      仅对极少数尚未拿到 id 的本地占位消息 fallback 到 role+created_at+index。
                    -->
                        <div v-for="(session, index) in messagesList"
                            :key="session.id || `${session.role}-${session.created_at}-${index}`" class="msg-item-wrapper"
                            :class="{ 'is-steer-prefix': session.steerForked, 'is-empty-segment': session.role === 'assistant' && !shouldRenderAssistantMessage(session) }">
                            <MessageTimestamp v-if="shouldShowConversationTimestamp(messagesList, index)"
                                :value="session.created_at" />

                            <div v-if="session.role == 'user'" class="message-row"
                                :data-message-id="session.id || undefined">
                                <usermsg :content="session.content" :mentioned_items="session.mentioned_items"
                                    :images="session.images" :attachments="session.attachments" :embeddedMode="embeddedMode"
                                    :session-id="session_id"
                                    :message-id="session.id"
                                    :created-at="session.created_at"
                                    :can-fork="!embeddedMode && forkAffordanceOf(session.id).canFork"
                                    :can-rewind="canRewindMessage(session.id)"
                                    :steer-failed="Boolean(session._steerFailed)"
                                    @retry-steer="handleRetrySteer(session.steer_id)"
                                    @remove-steer="handleRemoveSteer(session.steer_id)"
                                    @fork="handleFork"
                                    @rewind="handleRewind">
                                </usermsg>
                            </div>
                            <div v-if="session.role == 'assistant' && shouldRenderAssistantMessage(session)"
                                class="message-row"
                                :data-message-id="session.id || undefined">
                                <botmsg :content="session.content" :session="session" :session-id="session_id"
                                    :user-query="getUserQuery(index)" @scroll-bottom="scrollToBottom"
                                    :isFirstEnter="isFirstEnter" :embeddedMode="embeddedMode"
                                    :follow-up-loading="Boolean(session.suggestionLoading && !session.suggestionSet?.questions?.length)"
                                    :can-fork="!embeddedMode && forkAffordanceOf(session.id).canFork"
                                    :can-rewind="canRewindMessage(session.id)"
                                    @fork="handleFork"
                                    @rewind="handleRewind"
                                    @render-complete-change="(ready) => handleAnswerRenderComplete(session, ready)">
                                </botmsg>
                                <MentionedStocksBar
                                    :session="session"
                                    @select-stock="handleOpenStockWorkspace"
                                />
                                <FollowUpSuggestions v-if="session.answerFullyRendered && !session.steerForked && !session.suggestionsDismissed"
                                    :suggestion-set="session.suggestionSet"
                                    :loading="session.suggestionLoading"
                                    :allow-regenerate="session.suggestionSet?.allow_regenerate"
                                    @select="(item) => handleFollowUpSelect(session, item)"
                                    @regenerate="loadFollowUpSuggestions(session, true, true)"
                                    @impression="(set) => recordSuggestionEvent(session, set, 'impression')"
                                    @dismiss="(set) => dismissSuggestions(session, set)" />
                            </div>
                        </div>
                        <div v-if="showGlobalTypingIndicator" class="chat-global-wait" role="status"
                            :aria-label="t('chat.thinkingAlt')">
                            <span class="chat-global-wait__spinner" aria-hidden="true"></span>
                        </div>
                    </div>
                </div>
            </div>
            <div ref="composerElement" class="chat_composer">
                <div class="input-container" :class="{ 'is-embedded': embeddedMode }">
                    <transition name="scroll-btn-fade">
                        <div v-show="userHasScrolledUp" class="scroll-to-bottom-btn" @click="onClickScrollToBottom">
                            <t-icon name="chevron-down" size="18px" />
                        </div>
                    </transition>
                    <InputField ref="inputFieldRef" :auto-focus="focusComposerOnMount" :compact="!embeddedMode"
                        @send-msg="(query, modelId, mentionedItems, imageFiles, attachmentFiles, options) => sendMsg(query, modelId, mentionedItems, imageFiles, attachmentFiles, options)"
                        @steer-msg="(query, mentionedItems, delivery) => handleSteerMsg(query, mentionedItems, delivery)"
                        @promote-steer="handlePromoteSteer"
                        @remove-steer="handleRemoveSteer"
                        @retry-steer="handleRetrySteer"
                        @stop-generation="handleStopGeneration"
                        @stop-confirmed="handleStopConfirmed"
                        @stop-failed="handleStopFailed" :isReplying="isReplying" :composer-locked="composerLocked" :sessionId="session_id"
                        :assistantMessageId="currentAssistantMessageId" :embeddedMode="embeddedMode"
                        :queuedSteers="steerQueue.filter(item => item.delivery === 'after')" :canSteer="isAgentStreamSession()"></InputField>
                </div>
            </div>
            <div v-if="!embeddedMode" class="chat_overlays">
                <BrowserTaskPreview v-if="session_id" :key="session_id" :session-id="session_id" />
                <ChatQuestionMinimap :scroll-container="scrollContainer" :messages="messagesList"
                    @jump="jumpToQuestion" />
            </div>
        </div>
    </div>
    <KnowledgeBaseEditorModal :visible="uiStore.showKBEditorModal" :mode="uiStore.kbEditorMode"
        :kb-id="uiStore.currentKBId || undefined" :initial-type="uiStore.kbEditorType"
        @update:visible="(val) => val ? null : uiStore.closeKBEditor()" @success="handleKBEditorSuccess" />
    <ChatReferencesDrawer />
    <ChatAttachmentPreviewDrawer />
    <SandboxSidePanel v-if="!embeddedMode" :session-id="session_id"
        :agent-id="useSettingsStoreInstance.selectedAgentId"
        :agent-source-tenant-id="useSettingsStoreInstance.selectedAgentSourceTenantId"
        :shifted="referencesDrawerVisible"
        :shift-width="referencesPanelWidth"
        :artifacts="sessionArtifacts" :artifacts-collecting="sessionArtifactsCollecting"
        @artifact-deleted="handleArtifactDeleted" />
    <AgentWorkspacePanel v-if="!embeddedMode" />
    <StockCitationFloat
        :visible="stockFloat.visible"
        :top="stockFloat.top"
        :left="stockFloat.left"
        :thscode="stockFloat.thscode"
        :name="stockFloat.name"
        @enter="cancelStockFloatClose"
        @leave="scheduleStockFloatClose(150)"
        @open-workspace="handleOpenStockWorkspace"
    />
    <!-- 日期标记的悬浮提示。用 Teleport 到 body：正文容器有 overflow 裁剪，
         放在里面会被裁掉一半。 -->
    <Teleport to="body">
        <div
            v-if="rangeTip && rangeTip.visible"
            class="kline-range-tip"
            :style="{ top: `${rangeTip.top}px`, left: `${rangeTip.left}px` }"
        >
            {{ rangeTip.text }}
        </div>
    </Teleport>
</template>
<script setup>
import { makeSteerClientId } from '@/utils/steerId';
import { storeToRefs } from 'pinia';
import { ref, onMounted, onBeforeMount, onUnmounted, nextTick, watch, reactive, computed } from 'vue';
import { useRoute, useRouter, onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router';
import InputField from '../../components/Input-field.vue';
import botmsg from './components/botmsg.vue';
import usermsg from './components/usermsg.vue';
import { getMessageList, getSession, forkSession, rewindSession } from "@/api/chat/index";
import { resolveForkAffordance } from './forkPoint';
import { rewindSkipMessage } from './rewindNotice';
import { rewindPrefillText, rewindBlockedByOutgoingWork, canReplaceRewindTranscript, shouldApplyRewindLocally, rewindHistoryHasMore, keepMessagesThroughRewindPoint, rewindableMessageIds, rewindHttpConflictCode, rewindConflictI18nKey } from './rewindView';
import { getSuggestedQuestions } from "@/api/agent/index";
import { questionOriginFromSuggestion } from '@/utils/questionOrigin';
import { deleteTemporaryAttachment, uploadTemporaryAttachment } from '@/api/chat/temporary-attachments';
import { useStream } from '../../api/chat/streame'
import { listSteerSession, promoteSteerSession, removeSteerSession, steerSession } from '@/api/chat/steer';
import { persistedAssistantId, previewSteerMessage, discardSteerPreview, reconcileSteerMessageId } from '@/utils/steerStreamFork';
import { useMenuStore } from '@/stores/menu';
import { useSettingsStore } from '@/stores/settings';
import { useBrowserConnectionStore } from '@/stores/browserConnection';
import { MessagePlugin } from 'tdesign-vue-next';
import { useI18n } from 'vue-i18n';
import { useUIStore } from '@/stores/ui';
import KnowledgeBaseEditorModal from '@/views/knowledge/KnowledgeBaseEditorModal.vue';
import { useKnowledgeBaseCreationNavigation } from '@/hooks/useKnowledgeBaseCreationNavigation';
import { useChatStreamHandler } from '@/composables/useChatStreamHandler';
import { useStickyBottomOnResize } from '@/composables/useStickyBottomOnResize';
import { clearCitationChunkCache } from '@/utils/citationChunkCache';
import ChatReferencesDrawer from '@/components/ChatReferencesDrawer.vue';
import ChatAttachmentPreviewDrawer from '@/components/ChatAttachmentPreviewDrawer.vue';
import FollowUpSuggestions from '@/components/chat/FollowUpSuggestions.vue';
import MessageTimestamp from '@/components/chat/MessageTimestamp.vue';
import ChatQuestionMinimap from '@/components/chat/ChatQuestionMinimap.vue';
import { shouldShowConversationTimestamp } from '@/utils/messageTimestamp';
import ChatHeader from '@/components/ChatHeader.vue';
import {
    notifySessionMutation,
    SESSION_MUTATION_EVENT,
} from '@/components/sessionMutations';
import {
    ensureMessageSuggestions,
    getMessageSuggestions,
    recordMessageSuggestionEvent,
} from '@/api/message-suggestion';
import { provideChatReferencesDrawer } from '@/composables/useChatReferencesDrawer';
import { provideChatAttachmentPreviewDrawer } from '@/composables/useChatAttachmentPreviewDrawer';
import { useSessionActivityStore } from '@/stores/sessionActivity';
import { provideChatSandboxPanel } from '@/composables/useChatSandboxPanel';
import SandboxSidePanel from '@/components/chat/SandboxSidePanel.vue';
import AgentWorkspacePanel from '@/components/workspace/AgentWorkspacePanel.vue';
import MentionedStocksBar from '@/components/chat/MentionedStocksBar.vue';
import StockCitationFloat from '@/components/workspace/kline/StockCitationFloat.vue';
import { KNOWN_STOCK_NAMES, pickPrimaryMention } from '@/utils/stockMentions';
import { shouldAutoSwitchChart, isStreamedAnswer } from '@/utils/chartAutoSwitch';
import { provideChatKLinePanel } from '@/composables/useChatKLinePanel';
import { provideAgentWorkspace } from '@/composables/useAgentWorkspace';
import { useKLineTickerObserver } from '@/composables/useKLineTickerObserver';
import { useKLineMarkerObserver } from '@/composables/useKLineMarkerObserver';
import { createHoverDebounce } from '@/utils/hoverDebounce';
import { findAnchors, KLINE_ANCHOR_INDEX_ATTR } from '@/utils/klineAnchors';
import BrowserTaskPreview from './components/BrowserTaskPreview.vue';
import { collectSessionArtifacts, markSessionArtifactDeleted } from '@/utils/sessionArtifacts';
import { isCollectingSkillArtifacts } from '@/utils/skillArtifacts';
const referencesDrawer = provideChatReferencesDrawer();
provideChatAttachmentPreviewDrawer();
const sandboxPanel = provideChatSandboxPanel();
const agentWorkspace = provideAgentWorkspace();
const klinePanel = provideChatKLinePanel(agentWorkspace);
const { visible: referencesDrawerVisible, panelWidth: referencesPanelWidth } = referencesDrawer;

/**
 * 所有右侧面板占位宽度之和，**由 JS 单点计算**。
 *
 * 历史问题：这些宽度原先散落在 4 条 `.chat.has-*-panel` 的 `padding-right` 规则里
 * 互相竞争，只有 sandbox+references 一条组合规则存在，导致
 * 「引用面板 + 工作台」同时打开时后声明的 650px 静默胜出、420px 被吞掉，
 * 抽屉直接压在聊天内容上。这里改为求和后由**唯一一条** padding 规则消费，
 * 互撞在结构上不再可能发生。
 */
const rightPanelsWidth = computed(() => {
  if (props.embeddedMode) return 0;
  let total = 0;
  if (referencesDrawerVisible.value) total += referencesPanelWidth.value;
  if (sandboxPanel.visible.value) total += sandboxPanel.width.value;
  // klinePanel 是 agentWorkspace 的兼容 shim，共用同一份 isOpen/width，不能重复累加。
  // 用 effectiveWidth 而非 width：工作台折叠成窄边时让位也应该缩到窄边宽度，
  // 否则聊天区会一直空着原来 560px+ 的位置。
  if (agentWorkspace.isOpen.value) total += agentWorkspace.effectiveWidth.value;
  return total;
});

const props = defineProps({
    session_id: { type: String, default: '' },
    agentId: { type: String, default: '' },
    kbIds: { type: Array, default: () => [] },
    embeddedMode: { type: Boolean, default: false },
});

const usemenuStore = useMenuStore();
const useSettingsStoreInstance = useSettingsStore();

// Whether the active chat session is using the Agent pipeline (not quick-answer).
const isAgentStreamSession = () => {
    if (props.embeddedMode) {
        return !!(props.agentId && props.agentId !== 'builtin-quick-answer');
    }
    return useSettingsStoreInstance.isAgentStreamMode;
};

const uiStore = useUIStore();
const { navigateToKnowledgeBaseList } = useKnowledgeBaseCreationNavigation();
const { t } = useI18n();
const { firstQuery, firstMentionedItems, firstModelId, firstImageFiles, firstAttachmentFiles, firstQuestionOrigin } = storeToRefs(usemenuStore);
// Capture before the initial send consumes firstQuery; the child focuses after mounting.
const focusComposerOnMount = Boolean(firstQuery.value);
const { onChunk, error, isStreaming, startStream, stopStream, lastStreamRequest } = useStream();
/** Snapshot of the in-flight HTTP request for attaching to the next assistant message. */
const pendingStreamDebug = ref(null);

const buildStreamDebugPayload = () => {
    const meta = lastStreamRequest.value;
    if (!meta) return null;
    return {
        requestId: meta.requestId,
        url: meta.url,
        method: meta.method,
        body: meta.body,
        sentAt: meta.sentAt,
        sessionId: session_id.value,
    };
};

const attachStreamDebugToMessage = (message) => {
    if (!message) return;
    const payload = pendingStreamDebug.value || buildStreamDebugPayload();
    if (!payload) return;
    if (payload.requestId && !message.request_id) {
        message.request_id = payload.requestId;
    }
    message.debugRequest = payload;
};
const route = useRoute();
const router = useRouter();
const session_id = ref(props.session_id || route.params.chatid);
const currentSession = ref(null);

// 拉 session 详情，并按其 last_request_state 把输入栏状态恢复到当时的发起态。
// 嵌入式（embeddedMode）由宿主页面注入 agent/KB，所以跳过整套恢复逻辑，
// 避免污染宿主的 settings store。
const loadSessionAndHydrate = async (sid) => {
    if (!sid || props.embeddedMode) return;
    // Capture before awaiting: onMounted sends and clears firstQuery while this
    // request is in flight. A new session must retain the createChat draft.
    const preserveDraft = Boolean(firstQuery.value);
    try {
        const sessionRes = await getSession(sid);
        if (sessionRes?.data && sid === session_id.value) {
            currentSession.value = sessionRes.data;
            const lastState = sessionRes.data.last_request_state;
            useSettingsStoreInstance.hydrateSessionInputState(lastState, preserveDraft);
        }
    } catch (error) {
        console.error('Failed to load session data:', error);
    }
};
const inputFieldRef = ref();
const created_at = ref('');
const limit = ref(20);
const messagesList = reactive([]);

function forkAffordanceOf(messageId) {
    if (!messageId) return { canFork: false }
    return resolveForkAffordance(messagesList, messageId)
}

// One pass over the transcript per render instead of two per rendered row:
// the template asks this for every message and re-asks on every streamed token.
const rewindableIds = computed(() => rewindableMessageIds(messagesList, {
    embeddedMode: props.embeddedMode,
    outgoingWork: outgoingWorkBlocksRewind.value,
}))

function canRewindMessage(messageId) {
    return Boolean(messageId) && rewindableIds.value.has(String(messageId))
}

const FORK_PREFILL_KEY = 'weknora:fork-prefill'
let forkInFlight = false
const rewindInFlight = ref(false)
const rewindLockSessionId = ref('')
const composerLocked = computed(() =>
    rewindInFlight.value && String(session_id.value || '') === rewindLockSessionId.value
)

function stashForkLanding(sessionId, text) {
    const payload = JSON.stringify({ sessionId, text })
    try {
        sessionStorage.setItem(FORK_PREFILL_KEY, payload)
    } catch {
        // sessionStorage can throw in private mode; landing still navigates.
    }
}

function readForkLanding() {
    try {
        const raw = sessionStorage.getItem(FORK_PREFILL_KEY)
        if (!raw) return null
        const parsed = JSON.parse(raw)
        if (!parsed || typeof parsed !== 'object') return null
        return {
            sessionId: String(parsed.sessionId || ''),
            text: String(parsed.text || ''),
        }
    } catch {
        return null
    }
}

function clearForkLanding() {
    try {
        sessionStorage.removeItem(FORK_PREFILL_KEY)
    } catch {
        // ignore
    }
}

function applyForkLanding() {
    const landed = readForkLanding()
    if (!landed || landed.sessionId !== String(session_id.value || '')) {
        return false
    }
    clearForkLanding()
    inputFieldRef.value?.prefill(landed.text)
    return true
}

async function handleFork(messageId) {
    if (props.embeddedMode) return
    if (forkInFlight || composerLocked.value) return
    if (!messageId || !session_id.value) return
    const source = messagesList.find((m) => m.id === messageId)
    if (!source) return
    const sourceSessionId = session_id.value

    forkInFlight = true
    try {
        const res = await forkSession(sourceSessionId, { message_id: messageId })
        const data = res?.data
        if (!data?.session_id) return

        // Carry the question across navigation in sessionStorage: the chat view
        // is reused across chat/:chatid, and history reload / composer reset
        // would clobber an in-memory prefill if we applied it too early.
        const prefill = source.role === 'user' ? String(source.content ?? '') : ''
        stashForkLanding(data.session_id, prefill)

        const now = new Date().toISOString()
        const sourceTitle = currentSession.value?.title || t('menu.newSession')
        usemenuStore.updataMenuChildren({
            id: data.session_id,
            path: `chat/${data.session_id}`,
            title: `${sourceTitle}（分支）`,
            parent_session_id: sourceSessionId,
            isMore: false,
            isNoTitle: false,
            created_at: now,
            updated_at: now,
        })

        await router.push(`/platform/chat/${data.session_id}`)
    } catch (err) {
        if (err?.status === 409 || err?.$httpStatus === 409) {
            MessagePlugin.warning('请等本轮回答结束后再分叉')
            return
        }
        MessagePlugin.error('分叉失败，请重试')
    } finally {
        forkInFlight = false
    }
}

async function handleRewind(messageId) {
    if (props.embeddedMode) return
    if (forkInFlight || composerLocked.value) return
    if (rewindBlockedByOutgoingWork({
        isReplying: isReplying.value,
        isStreaming: isStreaming.value,
        isRecovering: isImRecovering.value,
    })) return
    if (!messageId || !session_id.value) return
    const source = messagesList.find((m) => m.id === messageId || persistedAssistantId(m) === messageId)
    if (!source) return
    const sourceSessionId = session_id.value
    const sourceRole = source.role
    const sourceContent = source.content

    rewindInFlight.value = true
    rewindLockSessionId.value = sourceSessionId
    try {
        const res = await rewindSession(sourceSessionId, { message_id: messageId })
        const data = res?.data
        if (!data) return
        if (!shouldApplyRewindLocally(String(session_id.value || ''), sourceSessionId)) return

        let batch
        let reloadFailed = false
        try {
            const history = await fetchMessageList({
                session_id: sourceSessionId,
                created_at: '',
                limit: limit.value,
            })
            batch = history?.data
            if (!Array.isArray(batch)) {
                throw new Error('rewind history reload returned no list')
            }
        } catch {
            reloadFailed = true
        }
        if (!shouldApplyRewindLocally(String(session_id.value || ''), sourceSessionId)) return

        steerQueue.value = []
        historyLoading.value = false
        if (reloadFailed) {
            const kept = keepMessagesThroughRewindPoint(
                [...messagesList],
                messageId,
                sourceRole,
                (m) => m.id === messageId || persistedAssistantId(m) === messageId,
            )
            messagesList.splice(0, messagesList.length, ...kept)
            // created_at still points at the oldest message we actually hold.
            // Clearing it here would send the next scroll-up back to the newest
            // page, which this prefix already contains, instead of older ones.
            MessagePlugin.warning(t('chat.rewind.reloadFailed'))
        } else {
            if (!canReplaceRewindTranscript(String(session_id.value || ''), sourceSessionId, undefined)) return
            messagesList.splice(0)
            created_at.value = ''
            if (batch.length) {
                created_at.value = batch[0].created_at
                hasMoreHistory.value = rewindHistoryHasMore(batch.length, limit.value)
                await handleMsgList(batch, false)
            } else {
                hasMoreHistory.value = false
            }
        }

        const prefill = rewindPrefillText(sourceRole, sourceContent)
        if (prefill) {
            inputFieldRef.value?.prefill(prefill)
        }

        if (reloadFailed) {
            return
        }
        if (data.workspace_reset) {
            MessagePlugin.success(t('chat.rewind.success'))
            return
        }
        const skip = rewindSkipMessage(String(data.reason || ''), t)
        if (skip) {
            MessagePlugin.info(skip)
        }
    } catch (err) {
        const conflictCode = rewindHttpConflictCode(err)
        if (conflictCode || err?.status === 409 || err?.$httpStatus === 409) {
            MessagePlugin.warning(t(rewindConflictI18nKey(conflictCode)))
            return
        }
        MessagePlugin.error(t('chat.rewind.failed'))
    } finally {
        rewindInFlight.value = false
        rewindLockSessionId.value = ''
    }
}

const sessionArtifacts = computed(() => collectSessionArtifacts(messagesList));
// The panel already deleted the file server side; flag it in the loaded
// history so the computed drops it without reloading the conversation.
function handleArtifactDeleted({ messageId, index }) {
    markSessionArtifactDeleted(messagesList, messageId, index);
}
const sessionArtifactsCollecting = computed(() =>
    messagesList.some((message) => isCollectingSkillArtifacts(message)),
);
const steerQueue = ref([]);
const isReplying = ref(false);
const currentAssistantMessageId = ref(''); // 当前正在生成的 assistant message ID
// True only while attaching to an in-flight *IM-originated* reply via continue-stream.
// Such replies are generated on the IM side and never stream through this server, so
// continue-stream always fails even though the answer is coming — recover by polling
// instead of erroring. Web/api replies are left on the original error path.
const isAttachingImStream = ref(false);
let recoverPollTimer = null;
// True while polling to recover an in-flight IM reply we couldn't stream. Drives
// the same "generating" typing indicator the normal reply path shows, so the wait
// isn't a silent gap. IM-only: false everywhere else, so other flows are unchanged.
const isImRecovering = ref(false);
const outgoingWorkBlocksRewind = computed(() => rewindBlockedByOutgoingWork({
    isReplying: isReplying.value,
    isStreaming: isStreaming.value,
    isRecovering: isImRecovering.value,
}))
const scrollLock = ref(false);
const isFirstEnter = ref(true);
const loading = ref(false);
const sessionActivity = useSessionActivityStore();
const activitySessionId = ref('');
watch([activitySessionId, isReplying, isImRecovering, currentAssistantMessageId], () => {
    if (props.embeddedMode || !activitySessionId.value) return;
    // SSE may stay connected after a stop/complete event. The sidebar tracks
    // generation, not the transport, just like the composer's Stop button.
    sessionActivity.update(activitySessionId.value, isReplying.value || isImRecovering.value, currentAssistantMessageId.value);
}, { flush: 'sync' });
const historyLoading = ref(true);
const historyLoadingMore = ref(false);
const hasMoreHistory = ref(true);

// Prefill after THIS session's history load settles. A messagesList watch
// would fire on the splice-to-empty that starts a session switch and then
// get clobbered by composer reset / history mount.
watch(historyLoading, (loading) => {
    if (loading) return
    applyForkLanding()
}, { flush: 'post' })
let fullContent = ref('')
const scrollContainer = ref(null)
const composerElement = ref(null)

const stockFloat = ref({
  visible: false,
  top: 0,
  left: 0,
  thscode: '',
  name: '',
});
let stockFloatCloseTimer = null;

const cancelStockFloatClose = () => {
  if (stockFloatCloseTimer) {
    clearTimeout(stockFloatCloseTimer);
    stockFloatCloseTimer = null;
  }
};

const scheduleStockFloatClose = (delay = 200) => {
  cancelStockFloatClose();
  stockFloatCloseTimer = setTimeout(() => {
    stockFloat.value.visible = false;
  }, delay);
};

const handleStockHover = (thscode, el) => {
  cancelStockFloatClose();
  const rect = el.getBoundingClientRect();
  const ticker = thscode.split('.')[0];
  const matchedName = KNOWN_STOCK_NAMES[ticker] || '';
  stockFloat.value = {
    visible: true,
    top: rect.top,
    left: rect.left + rect.width / 2,
    thscode,
    name: matchedName,
  };
};

/**
 * 打开 K 线工作台前，先让服务端把候选池过一遍。
 *
 * 候选池来自模型回答里的自由文本抽取，未经任何校验，两类坏东西会混进来：
 *   - 幻觉代码：模型把 600487（亨通光电）写成 688487。后者本地从未发行，
 *     进了池子点下去就是一片黑——图表取不到任何行情。
 *   - name 是代码：抽取正则会把 `600105（600101.SH）` 里的纯数字当股票名，
 *     于是池子出现 "600105 600101.SH" 这种 name 与 code 相同的条目。
 *
 * /api/symbols/resolve 一次查询同时解决两件事：剔掉本地不存在的代码，并用
 * v_symbol 的权威名称覆盖抽取阶段猜出来的名字。
 *
 * 这一步是**增强**不是依赖：接口挂了或超时都退回原始候选池，不阻断用户点开图表。
 */
const resolveStocks = async (stocks) => {
  const symbols = stocks.map((s) => `${s.ticker}.${s.exchange}`);
  try {
    const res = await fetch('/api/symbols/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols }),
    });
    if (!res.ok) return null;
    const body = await res.json();
    const rows = body?.data;
    if (!Array.isArray(rows) || rows.length !== stocks.length) return null;

    const kept = [];
    stocks.forEach((s, i) => {
      const hit = rows[i];
      if (!hit?.valid) return;
      kept.push({ ticker: hit.ticker || s.ticker, exchange: hit.exchange || s.exchange, name: hit.name || s.ticker });
    });
    // 全部无效时保留原列表：与其什么都不显示，不如让用户点开看到空状态提示。
    return kept.length > 0 ? kept : null;
  } catch {
    return null;
  }
};

const handleOpenStockWorkspace = async (stock, allStocks) => {
  stockFloat.value.visible = false;
  // 票签点击与正文 ticker 点击共用这个入口，都是用户的显式选择：
  // 标记之后本轮不再自动切图。
  agentWorkspace.markUserPick(`${stock.ticker}.${stock.exchange}`);
  const picks = allStocks && allStocks.length > 0
    ? allStocks.map((s) => ({ ticker: s.ticker, exchange: s.exchange, name: s.name }))
    : [{ ticker: stock.ticker, exchange: stock.exchange, name: stock.name }];
  const activeIdx = Math.max(0, picks.findIndex((p) => p.ticker === stock.ticker));

  // 先按过滤后的列表开面板（不阻塞交互），解析回来后再用权威名称刷新一次。
  agentWorkspace.open('kline', picks, activeIdx);

  const resolved = await resolveStocks(picks);
  if (!resolved) return;
  const stillThere = resolved.findIndex((p) => p.ticker === stock.ticker);
  agentWorkspace.open(
    'kline',
    resolved,
    stillThere >= 0 ? stillThere : Math.min(activeIdx, resolved.length - 1),
  );
};

// 监听 chat 文本中的股票代码：hover 唤出轻量 5 星持股评分卡片，点击打开完整右侧工作台
useKLineTickerObserver(scrollContainer, {
  onHover: (thscode, el) => {
    handleStockHover(thscode, el);
  },
  onLeave: () => {
    scheduleStockFloatClose(200);
  },
  onClick: (thscode) => {
    cancelStockFloatClose();
    const [ticker, exchange] = thscode.split('.');
    if (!ticker) return;
    // 用户显式点了正文里的标的 -> 本轮不再自动切图
    agentWorkspace.markUserPick(thscode);
    const matchedName = KNOWN_STOCK_NAMES[ticker] || '';
    handleOpenStockWorkspace({ ticker, exchange: exchange || 'SH', name: matchedName });
  },
});

onMounted(() => {
  window.__openKLineWorkspace = (ticker = '600519', exchange = 'SH', name = '贵州茅台') => {
    agentWorkspace.open('kline', [{ ticker, exchange, name }], 0);
  };
  agentWorkspace.sendToChatCallback.value = (text) => {
    if (inputFieldRef.value?.triggerSend) {
      inputFieldRef.value.triggerSend(text);
    }
  };
});

// 正文里的绝对日期 → 右侧 K 线滚动到该区间并画出价格带。
//
// **hover 也触发**，不只是 click。第一版只绑了 click，理由是「hover 会让图跟着
// 鼠标来回跳」；但代价是把可发现性一起砍掉了——用户看不出这个日期是可交互的，
// 交互感因此很模糊。抖动问题用防抖解决（见下），而不是靠砍掉交互。
//
// 移开时**不撤销**：用户此刻正在看那张图，把标记撤掉等于把上下文抽走。
// 防抖 200ms：鼠标只是**路过**这段文字不该动图，只有停上去才算「我要看这一段」。
// 没有这层，把鼠标移向滚动条的路上就会触发好几次跳转。实现在 utils/hoverDebounce.ts，
// 那里有单测覆盖「重复 schedule 只触发一次」「cancel 后不补触发」。
const RANGE_HOVER_DELAY = 200;
const rangeTip = ref(null);

const rangeHover = createHoverDebounce(RANGE_HOVER_DELAY, (range, el) => {
    agentWorkspace.focusRange(range);
    const rect = el.getBoundingClientRect();
    rangeTip.value = {
        visible: true,
        text: `在 K 线图上定位 ${el.textContent || ''}`,
        top: rect.top,
        left: rect.left + rect.width / 2,
    };
});

/**
 * 把某条回答的锚点整组推给工作台。
 *
 * 「一条回答 = 一组锚点」：切到另一条回答就整组替换，不累积。用 messageId 去重，
 * 避免每次列表刷新都重推一遍——重推会连带清掉 hover 态。
 */
const anchorsMessageId = ref(null);
const answerText = (m) => m?.answer || m?.content || m?.message || '';

const syncAnchorsForMessage = (message) => {
    if (!message || !message.id) return;
    if (anchorsMessageId.value === message.id) return;
    anchorsMessageId.value = message.id;
    agentWorkspace.setAnchors(findAnchors(answerText(message)));
};

/** 从被 hover/点击的锚点元素回溯到它所属的回答。 */
const syncAnchorsFromElement = (el) => {
    const row = el && el.closest ? el.closest('[data-message-id]') : null;
    const id = row && row.getAttribute('data-message-id');
    if (!id) return;
    syncAnchorsForMessage(messagesList.find((m) => m.id === id));
};

/**
 * 反向联动：图上 hover 锚点 -> 正文里对应的那句话高亮。
 *
 * 两个方向共用 `hoveredAnchorIndex` 一份状态，所以「正文 hover」和「图上 hover」
 * 得到完全一致的压暗与高亮，不必各写一套、也不会互相打架。
 *
 * 滚动只在句子**完全不在视野内**时才做：反向联动不该有副作用，用户明明看着
 * 正文时把正文拽走是最讨厌的一种。
 */
let highlightedAnchorEl = null;

const clearAnchorHighlight = () => {
    if (highlightedAnchorEl) {
        highlightedAnchorEl.classList.remove('is-anchor-highlighted');
        highlightedAnchorEl = null;
    }
};

const highlightAnchorSentence = (index) => {
    clearAnchorHighlight();
    if (index === null || index === undefined) return;
    const root = scrollContainer.value;
    if (!root) return;
    const el = root.querySelector(`.kline-anchor[${KLINE_ANCHOR_INDEX_ATTR}="${index}"]`);
    if (!el) return;
    el.classList.add('is-anchor-highlighted');
    highlightedAnchorEl = el;

    const rect = el.getBoundingClientRect();
    const box = root.getBoundingClientRect();
    const fullyVisible = rect.top >= box.top && rect.bottom <= box.bottom;
    if (!fullyVisible) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
};

watch(() => agentWorkspace.hoveredAnchorIndex.value, (index) => {
    highlightAnchorSentence(index);
});

useKLineMarkerObserver(scrollContainer, {
    range: {
        onHover: (range, el) => rangeHover.schedule(range, el),
        onLeave: () => {
            rangeHover.cancel();
            if (rangeTip.value) rangeTip.value.visible = false;
        },
        onActivate: (range) => {
            // 点击/回车立即生效，不走防抖——用户已经明确表达了意图。
            rangeHover.cancel();
            agentWorkspace.focusRange(range);
        },
    },
    anchor: {
        // 锚点的 hover 是「看」（压暗 + 统计），不改变视野，所以**不需要防抖**——
        // 防抖是为了躲开"滚动视野"这种有副作用的动作，而压暗是可逆的。
        onHover: (index, el) => {
            // 先同步锚点集合（可能 hover 的是另一条回答里的锚点），再置 hover 编号：
            // 顺序不能反——setAnchors 会清掉 hover 态。
            syncAnchorsFromElement(el);
            agentWorkspace.setHoveredAnchor(index);
        },
        onLeave: () => {
            agentWorkspace.setHoveredAnchor(null);
        },
        onActivate: (index, el) => {
            // 点击才是「去」：把图滚到这一段。
            syncAnchorsFromElement(el);
            agentWorkspace.focusAnchor(index);
        },
    },
});
const composerHeight = ref(0)
const scrollbarGutter = ref(0)
// Reserve space for the independent composer and keep it aligned with the
// message column when drawers, multiline input or attachments change its size.
watch([composerElement, scrollContainer], ([element, scroller], _, onCleanup) => {
    if (!element || !scroller) return
    const measure = () => {
        composerHeight.value = element.offsetHeight
        scrollbarGutter.value = scroller.offsetWidth - scroller.clientWidth
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    observer.observe(scroller)
    onCleanup(() => observer.disconnect())
}, { flush: 'post' })
const userHasScrolledUp = ref(false)
const SCROLL_BOTTOM_THRESHOLD = 80

const isNearBottom = () => {
    if (!scrollContainer.value) return true;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer.value;
    return scrollHeight - scrollTop - clientHeight < SCROLL_BOTTOM_THRESHOLD;
}

const jumpToQuestion = (id) => {
    const root = scrollContainer.value
    if (!root || !id) return
    const el = root.querySelector(`[data-message-id="${CSS.escape(id)}"]`)
    if (!el) return

    const offset = el.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop
    const nearEnd = root.scrollHeight - offset < root.clientHeight + SCROLL_BOTTOM_THRESHOLD
    userHasScrolledUp.value = !nearEnd

    el.scrollIntoView({ block: 'start', behavior: 'smooth' })

}

const handleKBEditorSuccess = (kbId) => {
    navigateToKnowledgeBaseList(kbId)
}

// ===== 推荐问题 =====
const suggestedQuestions = ref([]);
const suggestedQuestionsLoading = ref(false);
let suggestedQuestionsFetchId = 0; // 用于取消过时的请求
let suggestedDebounceTimer = null;
let pendingSuggestionAttribution = null;
let pendingSuggestionKnowledgeBaseIds = [];

const cancelSuggestedQuestionsFetch = () => {
    suggestedQuestionsFetchId++;
    suggestedQuestionsLoading.value = false;
    suggestedQuestions.value = [];
    if (suggestedDebounceTimer) {
        clearTimeout(suggestedDebounceTimer);
        suggestedDebounceTimer = null;
    }
};

const fetchSuggestedQuestionsIfNeeded = async () => {
    if (props.embeddedMode) return;
    // 初始历史尚未拉完时不能判断是否有消息，避免有历史的会话误请求推荐问法
    if (historyLoading.value || messagesList.length > 0) {
        if (messagesList.length > 0) {
            cancelSuggestedQuestionsFetch();
        }
        return;
    }
    await fetchSuggestedQuestions();
};

const fetchSuggestedQuestions = async () => {
    if (historyLoading.value || messagesList.length > 0) {
        return;
    }
    const fetchId = ++suggestedQuestionsFetchId;
    suggestedQuestionsLoading.value = true;
    // 加载期间保留旧数据，不清空，避免布局抖动
    try {
        const agentId = useSettingsStoreInstance.selectedAgentId;
        if (!agentId) return;
        const res = await getSuggestedQuestions(agentId, useSettingsStoreInstance.getSuggestedQuestionsParams());
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
            suggestedQuestionsLoading.value = false;
        }
    }
};

// The suggestion's source rides with this send only, as a retrieval hint.
const handleSuggestedQuestionClick = (item) => {
    const options = { questionOrigin: questionOriginFromSuggestion(item) };
    if (inputFieldRef.value?.triggerSend) {
        inputFieldRef.value.triggerSend(item.question, options);
    } else {
        sendMsg(item.question, '', [], [], [], options);
    }
};

const resolveAssistantMessageId = (message) => message?.assistant_message_id || message?.id;

const handleAnswerRenderComplete = (message, ready) => {
    message.answerFullyRendered = Boolean(ready);
};

const loadFollowUpSuggestions = async (message, ensure = false, regenerate = false) => {
    const messageId = resolveAssistantMessageId(message);
    const targetSessionId = session_id.value;
    if (!messageId || !targetSessionId || message.suggestionsDismissed) return;
    message.suggestionLoading = true;
    try {
        let response = ensure
            ? await ensureMessageSuggestions(targetSessionId, messageId, regenerate)
            : await getMessageSuggestions(targetSessionId, messageId);
        let set = response?.data;
        for (let attempt = 0; set?.status === 'generating' && attempt < 120; attempt++) {
            await new Promise((resolve) => setTimeout(resolve, 1000));
            if (session_id.value !== targetSessionId || message.suggestionsDismissed) return;
            response = await getMessageSuggestions(targetSessionId, messageId);
            set = response?.data;
        }
        message.suggestionSet = set?.status === 'ready' ? set : null;
    } catch (error) {
        if (ensure) console.warn('[FollowUpSuggestions] Failed to generate:', error);
        message.suggestionSet = null;
    } finally {
        message.suggestionLoading = false;
    }
};

const recordSuggestionEvent = (message, set, eventType, questionId = '') => {
    if (!set?.id) return;
    void recordMessageSuggestionEvent(session_id.value, set.id, eventType, questionId).catch(() => undefined);
};

const handleFollowUpSelect = (message, item) => {
    recordSuggestionEvent(message, message.suggestionSet, 'click', item.id);
    pendingSuggestionAttribution = {
        suggestion_set_id: message.suggestionSet.id,
        question_id: item.id,
    };
    // Knowledge-backed follow-ups are generated from a specific KB. Keep that
    // authorized retrieval anchor for the immediate next request; model-backed
    // suggestions intentionally do not inherit transient @file/@tag/MCP/Skill scope.
    pendingSuggestionKnowledgeBaseIds = [...new Set(item.knowledge_base_ids || [])];
    if (inputFieldRef.value?.triggerSend) inputFieldRef.value.triggerSend(item.text);
    else sendMsg(item.text);
};

const dismissSuggestions = (message, set) => {
    message.suggestionsDismissed = true;
    recordSuggestionEvent(message, set, 'dismiss');
};

// 防抖包装，切换知识库/文件时300ms内不重复请求
const debouncedFetchSuggestions = () => {
    if (historyLoading.value || messagesList.length > 0) return;
    if (suggestedDebounceTimer) clearTimeout(suggestedDebounceTimer);
    suggestedDebounceTimer = setTimeout(() => { fetchSuggestedQuestionsIfNeeded(); }, 300);
};

// 监听 Agent / 知识库 / 文件 / 标签 / MCP / Skill @mention，重新获取推荐问题
watch(
    () => ({
        agentId: useSettingsStoreInstance.selectedAgentId,
        kbs: useSettingsStoreInstance.settings.selectedKnowledgeBases,
        files: useSettingsStoreInstance.settings.selectedFiles,
        tags: useSettingsStoreInstance.settings.selectedTags,
        mcps: useSettingsStoreInstance.settings.selectedMCPServices,
        skills: useSettingsStoreInstance.settings.selectedSkills,
    }),
    debouncedFetchSuggestions,
    { deep: true },
);

function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

const getUserQuery = (index) => {
    if (index <= 0) {
        return '';
    }
    const previous = messagesList[index - 1];
    if (previous && previous.role === 'user') {
        return previous.content || '';
    }
    return '';
};

watch([() => route.params], async (newvalue) => {
    isFirstEnter.value = true;
    if (newvalue[0].chatid) {
        if (!firstQuery.value) {
            scrollLock.value = false;
        }
        messagesList.splice(0);
        steerQueue.value = [];
        session_id.value = newvalue[0].chatid;
        currentSession.value = null;
        clearCitationChunkCache();

        // 切换会话时，重置状态
        historyLoading.value = true;
        historyLoadingMore.value = false;
        hasMoreHistory.value = true;
        created_at.value = '';
        loading.value = false;
        isReplying.value = false;
        currentAssistantMessageId.value = '';
        userHasScrolledUp.value = false;

        // 跨会话切换：先把旧会话覆盖前的全局默认还原，再让新会话重新拍快照
        // 并应用自己的 last_request_state（在 loadSessionAndHydrate 内部完成）。
        useSettingsStoreInstance.restoreDefaultsIfSnapshotted();

        await loadSessionAndHydrate(session_id.value);
        let data = {
            session_id: session_id.value,
            created_at: '',
            limit: limit.value
        }
        getmsgList(data);
    }
});
const scrollToBottom = (force = false) => {
    if (!force && userHasScrolledUp.value) return;
    nextTick(() => {
        if (scrollContainer.value) {
            scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight;
        }
    })
}
const onClickScrollToBottom = () => {
    userHasScrolledUp.value = false;
    scrollToBottom(true);
}

// Images and other rich Markdown content can grow after the SSE chunk that
// introduced them. Follow those delayed height changes while the user remains
// at the live edge; preserve position when they intentionally scroll upward.
useStickyBottomOnResize(scrollContainer, userHasScrolledUp);

const debounce = (fn, delay) => {
    let timer
    return (...args) => {
        clearTimeout(timer)
        timer = setTimeout(() => fn(...args), delay)
    }
}
const onChatScrollTop = () => {
    if (scrollLock.value || historyLoadingMore.value || !hasMoreHistory.value) return;
    if (!scrollContainer.value) return;
    const { scrollTop, scrollHeight } = scrollContainer.value;
    isFirstEnter.value = false
    if (scrollTop <= 0) {
        let data = {
            session_id: session_id.value,
            created_at: created_at.value,
            limit: limit.value
        }
        getmsgList(data, true, scrollHeight);
    }
}
const debouncedScrollTop = debounce(onChatScrollTop, 500);
let lastScrollTop = 0;
const handleScroll = () => {
    const el = scrollContainer.value;
    if (el) {
        const currentTop = el.scrollTop;
        // Only an actual upward scroll detaches from the live edge. Content that
        // grows after a chunk (images, diagrams) keeps scrollTop fixed and would
        // otherwise fire a stale scroll event that falsely marks the user as
        // scrolled up, killing the auto-follow during streaming.
        if (currentTop < lastScrollTop - 1) {
            userHasScrolledUp.value = !isNearBottom();
        } else if (isNearBottom()) {
            userHasScrolledUp.value = false;
        }
        lastScrollTop = currentTop;
    }
    debouncedScrollTop();
};

const fetchMessageList = (data) => getMessageList(data);

// The server is the source of truth for what is still queued. `onlyWhenLive`
// guards the hand-off window: a follow-up run publishes itself a moment before
// its carried-over queue is readable, and treating that gap as "queue is
// empty" would wipe messages the user can still see.
const hydrateSteerQueue = async ({ onlyWhenLive = false } = {}) => {
    if (!session_id.value) return;
    try {
        const res = await listSteerSession(session_id.value);
        if (onlyWhenLive && !res?.assistant_message_id) return;
        const items = Array.isArray(res?.items) ? res.items : [];
        steerQueue.value = items.map((item) => ({
            steer_id: item.steer_id,
            content: item.content || '',
            delivery: item.delivery === 'inject' ? 'inject' : 'after',
            mentioned_items: item.mentioned_items || [],
            expected_assistant_message_id: res.assistant_message_id,
        })).concat(steerQueue.value.filter(item => item.failed && !items.some(remote => remote.steer_id === item.steer_id)));
        for (const item of steerQueue.value) {
            if (item.delivery === 'inject') previewSteerMessage(messagesList, item);
        }
    } catch (e) {
        console.warn('[Steer] Failed to restore queue:', e);
    }
};

const {
    findLastMessage,
    shouldRenderAssistantMessage,
    shouldShowGlobalTypingIndicator,
    handleMsgList,
    processStreamChunk,
    prepareForNewOutgoingMessage,
    markInFlightAssistantStopped,
} = useChatStreamHandler({
    messagesList,
    loading,
    isReplying,
    currentAssistantMessageId,
    fullContent,
    isAgentStreamSession,
    scrollToBottom,
    onError: (msg) => MessagePlugin.error(msg),
    preserveIncompleteStreamReactive: true,
    isFirstEnter,
    scrollContainer,
    debug: import.meta.env.DEV,
    onAfterMsgList: async () => {
        activitySessionId.value = String(session_id.value);
        for (const message of messagesList) {
            if (message.role === 'assistant' && message.is_completed && message.suggestionSet === undefined) {
                void loadFollowUpSuggestions(message, false);
            }
        }
        // 回答落地后考虑把图切到它的主标的。放在这里而不是流式回调里，
        // 是因为这里拿到的才是「已经完整」的回答文本——流式过程中判定主语
        // 会随着后续文字反复变化，图位就会来回跳。
        //
        // 只针对**正在生成的这一条**（currentAssistantMessageId），不能遍历整个
        // messagesList：那样每次列表刷新都会把历史里每一条完成的回答都过一遍，
        // 一路切到最后一条为止。加载历史会话时也会因此把图切走。
        //
        // 判据在 utils/chartAutoSwitch.ts 里（两个 id 字段都要比，理由见那里），
        // 是纯函数、有单测覆盖。
        const streamedAnswer = findLastMessage(
            (message) => isStreamedAnswer(message, currentAssistantMessageId.value)
        );
        maybeAutoSwitchChart(streamedAnswer);
        // 回答落地后把它的锚点整组推给图。放在这里而不是流式回调里，理由同
        // 自动切图：流式过程中解析会读到半截标签，锚点集合会反复变。
        if (streamedAnswer && streamedAnswer.is_completed) {
            syncAnchorsForMessage(streamedAnswer);
        }
        if (!steerQueue.value.length) {
            await hydrateSteerQueue();
        }
        // Resume the trailing *assistant*, not simply the last row: a turn
        // that absorbed a mid-run message ends with the injected user bubble
        // in some orderings, and keying off that row would skip the resume
        // entirely, leaving a running agent with no visible output.
        const lastMessage = findLastMessage(
            (message) => message.role === 'assistant' && !message.is_completed
        );
        const locallyRunning = isReplying.value || isImRecovering.value;
        // History reload can finish after sendMsg already marked this session
        // running. Do not clear that marker just because the snapshot's last
        // message still looks completed. A scanned incomplete assistant counts: a
        // turn that absorbed a mid-run message leaves such a row in history even
        // when the tail row is a user bubble.
        if (!props.embeddedMode && !locallyRunning && !lastMessage) {
            sessionActivity.update(activitySessionId.value, false);
        }
        if (lastMessage) {
            isReplying.value = true;
            // Such a turn renders as several assistant segments; only the
            // persisted id addresses the row continue-stream and stop
            // actually operate on.
            const resumeId = persistedAssistantId(lastMessage);
            currentAssistantMessageId.value = resumeId;
            console.log('[Continue Stream] Set assistant message ID:', resumeId);
            // Only IM-originated replies (channel === 'im') get the quiet poll-to-recover
            // path: their answer is generated on the IM side and never streams through
            // this server, so continue-stream always 404s even though the reply *is*
            // coming. Web/api replies keep the original behaviour (a real failure to
            // resume the stream still surfaces as an error) — we don't touch them.
            isAttachingImStream.value = lastMessage.channel === 'im';
            await startStream({
                session_id: session_id.value,
                query: resumeId,
                method: 'GET',
                url: '/api/v1/sessions/continue-stream',
            });
            // On success the stream resumed normally; on failure the error watcher
            // already took over (quiet recovery for IM), so only clear the flag here.
            if (!error.value) isAttachingImStream.value = false;
        }
    },
    onAgentQuery: (data, existingMessage) => {
        pendingStreamDebug.value = buildStreamDebugPayload();
        if (existingMessage) attachStreamDebugToMessage(existingMessage);
    },
    onMessageCreated: (message) => attachStreamDebugToMessage(message),
    onMessageUpdated: (message, payload) => {
        attachStreamDebugToMessage(message);
        if (payload?.is_completed) pendingStreamDebug.value = null;
    },
    onAgentAnswerDone: (message) => {
        attachStreamDebugToMessage(message);
        pendingStreamDebug.value = null;
    },
    onAgentChunkBound: (message) => {
        attachStreamDebugToMessage(message);
        pendingStreamDebug.value = null;
    },
    onUserMessageInjected: (steerId) => {
        dropSteerQueueItem(steerId);
    },
    onGenerationStopped: () => {
        for (const item of steerQueue.value) discardSteerPreview(messagesList, item.steer_id);
        steerQueue.value = [];
    },
    onTurnComplete: (message) => {
        void loadFollowUpSuggestions(message, true);
        void flushSteerAfterTurn(persistedAssistantId(message));
    },
});

const showGlobalTypingIndicator = computed(() =>
    shouldShowGlobalTypingIndicator(messagesList, loading.value, isImRecovering.value),
);

const getmsgList = (data, isScrollType = false, scrollHeight) => {
    if (isScrollType) {
        if (historyLoadingMore.value || !hasMoreHistory.value) return;
        historyLoadingMore.value = true;
    }
    return fetchMessageList(data).then(async (res) => {
        if (data?.session_id && String(data.session_id) !== String(session_id.value || '')) {
            return
        }
        const batch = res?.data;
        if (!batch?.length) {
            if (isScrollType) {
                hasMoreHistory.value = false;
            }
            return;
        }
        if (!isScrollType) {
            cancelSuggestedQuestionsFetch();
        }
        const nextCursor = batch[0].created_at;
        if (isScrollType && created_at.value && nextCursor === created_at.value) {
            hasMoreHistory.value = false;
            return;
        }
        if (batch.length < limit.value) {
            hasMoreHistory.value = false;
        }
        created_at.value = nextCursor;
        await handleMsgList(batch, isScrollType, scrollHeight);
    }).catch((err) => {
        console.error('Failed to load messages:', err);
        if (isScrollType) {
            hasMoreHistory.value = false;
        }
    }).finally(() => {
        historyLoading.value = false;
        historyLoadingMore.value = false;
        if (!isScrollType && messagesList.length === 0) {
            fetchSuggestedQuestionsIfNeeded();
        }
    })
}

// 发送消息
// 处理停止生成事件 - 立即清除 loading 状态
const handleStopGeneration = () => {
    console.log('[Stop Generation] Immediately clearing loading state');
    stopStream();
    loading.value = false;
    isReplying.value = false;
    if (recoverPollTimer) { clearTimeout(recoverPollTimer); recoverPollTimer = null; }
    isImRecovering.value = false;
    markInFlightAssistantStopped(currentAssistantMessageId.value);
};

const handleStopConfirmed = () => {
    for (const item of steerQueue.value) discardSteerPreview(messagesList, item.steer_id);
    steerQueue.value = [];
};

const handleStopFailed = () => {
    isReplying.value = true;
    loading.value = true;
};

const dropSteerQueueItem = (steerId) => {
    if (!steerId) return;
    const idx = steerQueue.value.findIndex((item) => item.steer_id === steerId);
    if (idx >= 0) steerQueue.value.splice(idx, 1);
};

const findSteerQueueItem = (steerId) =>
    steerQueue.value.find((item) => item.steer_id === steerId);

// Enter queues a follow-up; an explicit inject appears in the transcript immediately.
const handleSteerMsg = async (value, mentionedItems = [], delivery = 'after', retryId = '') => {
    if (composerLocked.value) return
    if (!session_id.value || !value?.trim()) return;
    if (!isReplying.value && !retryId) {
        // 空闲时没有运行中的 turn 可排队：直接走正常发送，而不是把
        // steering（服务端为 handleSteer/指定事务）当隐形 sendMsg 用。
        await sendMsg(value, '', mentionedItems);
        return;
    }
    const requestSessionId = session_id.value;
    const clientId = retryId || makeSteerClientId();
    const retryItem = retryId ? findSteerQueueItem(retryId) : null;
    const expectedId = retryItem?.expected_assistant_message_id || currentAssistantMessageId.value;
    if (retryItem) { retryItem.pending = true; retryItem.failed = false; }
    else steerQueue.value.push({
        steer_id: clientId,
        client_id: clientId,
        expected_assistant_message_id: expectedId,
        content: value,
        delivery,
        mentioned_items: mentionedItems,
        pending: true,
    });
    if (delivery === 'inject') {
        const preview = previewSteerMessage(messagesList, findSteerQueueItem(clientId));
        delete preview._steerFailed;
        scrollToBottom(true);
    }
    try {
        const res = await steerSession(requestSessionId, value, mentionedItems, delivery, expectedId, clientId);
        if (session_id.value !== requestSessionId) return;
        const serverId = res?.steer_id || clientId;
        const received = reconcileSteerMessageId(messagesList, clientId, serverId);
        const queued = findSteerQueueItem(clientId);
        if (received && !received._steerPending) {
            // The SSE receipt can arrive before the HTTP response, including
            // when an older backend generated a different steer ID.
            dropSteerQueueItem(clientId);
        } else if (queued) {
            queued.steer_id = serverId;
        }
        if (res?.status === 'already_injected') {
            dropSteerQueueItem(serverId);
            const preview = messagesList.find(m => m.steer_id === serverId);
            if (preview) delete preview._steerPending;
            MessagePlugin.info(t('input.messages.steerAlreadyInjected'));
            return;
        }
        if (res?.status === 'new_run') {
            const item = findSteerQueueItem(serverId);
            // Still attached to a stream: aborting it to POST AgentQA races the
            // finishing turn and can start a second engine. Keep the message and
            // send once the current SSE completes.
            if (isReplying.value || isStreaming.value) {
                if (item) {
                    item.pending = false;
                    item.awaitingIdleSend = true;
                }
                return;
            }
            dropSteerQueueItem(serverId);
            discardSteerPreview(messagesList, serverId);
            await sendMsg(value, '', mentionedItems);
            return;
        }
        const item = findSteerQueueItem(serverId);
        if (item) {
            item.pending = false;
        }
    } catch (e) {
        console.error('[Steer] Failed to queue message:', e);
        if (session_id.value !== requestSessionId) return;
        const item = findSteerQueueItem(clientId);
        if (!item) return; // The delivery receipt may have already consumed it.
        item.pending = false;
        item.failed = true;
        const preview = messagesList.find(m => m.steer_id === clientId && m._steerPending);
        if (preview) preview._steerFailed = true;
        if (e?.status === 409) item.expected_assistant_message_id = currentAssistantMessageId.value;
        MessagePlugin.error(e?.message || t('input.messages.steerFailed'));
    }
};

const handleRetrySteer = async (steerId) => {
    const item = findSteerQueueItem(steerId);
    if (!item || item.pending) return;
    await handleSteerMsg(item.content, item.mentioned_items || [], item.delivery, steerId);
};

const handlePromoteSteer = async (steerId) => {
    if (!session_id.value || !steerId) return;
    const item = findSteerQueueItem(steerId) || steerQueue.value.find((entry) => entry.client_id === steerId);
    if (!item || item.delivery === 'inject') return;
    if (item.pending || item.promoting || item.failed) return;
    const requestSessionId = session_id.value;
    item.promoting = true;
    item.delivery = 'inject';
    previewSteerMessage(messagesList, item);
    scrollToBottom(true);
    try {
        const res = await promoteSteerSession(requestSessionId, item.steer_id);
        if (session_id.value !== requestSessionId) return;
        if (res?.status === 'already_injected') {
            dropSteerQueueItem(item.steer_id);
            const preview = messagesList.find(m => m.steer_id === item.steer_id);
            if (preview) delete preview._steerPending;
            MessagePlugin.info(t('input.messages.steerAlreadyInjected'));
            return;
        }
        if (res?.status === 'new_run') {
            if (isReplying.value || isStreaming.value) {
                item.awaitingIdleSend = true;
                return;
            }
            const content = item.content;
            const mentions = item.mentioned_items || [];
            dropSteerQueueItem(steerId);
            discardSteerPreview(messagesList, steerId);
            await sendMsg(content, '', mentions);
            return;
        }
        item.delivery = 'inject';
    } catch (e) {
        console.error('[Steer] Failed to promote queued message:', e);
        if (session_id.value !== requestSessionId) return;
        if (!findSteerQueueItem(steerId)) return;
        item.delivery = 'after';
        discardSteerPreview(messagesList, steerId);
        MessagePlugin.error(e?.message || t('input.messages.steerPromoteFailed'));
    } finally {
        item.promoting = false;
    }
};

const handleRemoveSteer = async (steerId) => {
    if (!steerId) return;
    const item = findSteerQueueItem(steerId);
    if (!item) return;
    if (item.pending) return;
    item.promoting = true;
    try {
        if (session_id.value) {
            const res = await removeSteerSession(session_id.value, item.steer_id);
            if (res?.status === 'already_injected') {
                MessagePlugin.info(t('input.messages.steerAlreadyInjected'));
                dropSteerQueueItem(steerId);
                return;
            }
            if (res?.status === 'gone') {
                discardSteerPreview(messagesList, steerId);
                dropSteerQueueItem(steerId);
                return;
            }
            if (res && res.removed === false && !item.failed) {
                MessagePlugin.error(t('input.messages.steerRemoveFailed'));
                return;
            }
        }
        discardSteerPreview(messagesList, steerId);
        dropSteerQueueItem(steerId);
    } catch (e) {
        console.error('[Steer] Failed to remove queued message:', e);
        MessagePlugin.error(e?.message || t('input.messages.steerRemoveFailed'));
    } finally {
        if (findSteerQueueItem(steerId)) item.promoting = false;
    }
};

let attachingSteerFollowUp = false;

const flushSteerAfterTurn = async (completedAssistantId) => {
    const awaiting = steerQueue.value.filter((item) => item.awaitingIdleSend);
    if (awaiting.length) {
        const batch = awaiting.slice();
        for (const item of batch) discardSteerPreview(messagesList, item.steer_id);
        steerQueue.value = steerQueue.value.filter((item) => !item.awaitingIdleSend);
        const first = batch[0];
        await sendMsg(first.content, '', first.mentioned_items || []);
        for (const rest of batch.slice(1)) {
            await handleSteerMsg(rest.content, rest.mentioned_items || [], rest.delivery || 'after');
        }
        return;
    }
    void attachSteerFollowUp(completedAssistantId);
};

const attachSteerFollowUp = async (completedAssistantId) => {
    const queued = steerQueue.value.filter(item => !item.failed);
    if (!queued.length || attachingSteerFollowUp || !session_id.value) return;
    const sessionId = session_id.value;
    attachingSteerFollowUp = true;
    isReplying.value = true;
    loading.value = true;
    let attached = false;
    let attachedAssistantId = '';
    const sessionChanged = () => session_id.value !== sessionId;
    try {
        for (let attempt = 0; attempt < 40; attempt++) {
            if (sessionChanged()) return;
            const res = await getMessageList({ session_id: sessionId, limit: 30, created_at: '' });
            if (sessionChanged()) return;
            const batch = res?.data || [];
            const newAssistant = [...batch].reverse().find((m) =>
                m.role === 'assistant' && !m.is_completed && m.id && m.id !== completedAssistantId
            );
            if (newAssistant) {
                // The follow-up run persists its query under its own
                // request_id, so the new user rows are identified exactly.
                // Matching on message text instead would attach the wrong
                // bubble whenever the user sends the same thing twice.
                const claimed = new Set();
                for (const persisted of batch) {
                    if (persisted.role !== 'user' || !persisted.id) continue;
                    if (persisted.request_id !== newAssistant.request_id) continue;
                    if (messagesList.some((existing) => existing.id === persisted.id)) continue;
                    const queuedMatch = queued.find(
                        (q) => q.content === persisted.content && !claimed.has(q.steer_id)
                    );
                    if (queuedMatch) claimed.add(queuedMatch.steer_id);
                    const userRow = {
                        ...persisted,
                        mentioned_items: queuedMatch?.mentioned_items?.length
                            ? queuedMatch.mentioned_items
                            : persisted.mentioned_items,
                    };
                    const preview = queuedMatch && messagesList.find(m => m.steer_id === queuedMatch.steer_id && m._steerPending);
                    if (preview) {
                        delete preview._steerPending;
                        delete preview._steerFailed;
                        delete preview.isSteer;
                        Object.assign(preview, userRow);
                    } else messagesList.push(userRow);
                }
                // Rows that made it into the transcript are no longer queued.
                for (const steerId of claimed) dropSteerQueueItem(steerId);
                if (sessionChanged()) return;
                // Then reconcile with the server, which owns the backlog that
                // moved to the new run — but only once that run is visible.
                await hydrateSteerQueue({ onlyWhenLive: true });
                if (sessionChanged()) return;

                currentAssistantMessageId.value = newAssistant.id;
                attachedAssistantId = newAssistant.id;
                await startStream({
                    session_id: sessionId,
                    query: newAssistant.id,
                    method: 'GET',
                    url: '/api/v1/sessions/continue-stream',
                });
                attached = true;
                return;
            }
            await new Promise((r) => setTimeout(r, 200));
        }
    } catch (e) {
        console.error('[Steer] Failed to attach follow-up run:', e);
    } finally {
        attachingSteerFollowUp = false;
        if (sessionChanged()) {
            // The session we started on is gone; do not touch the new chat's
            // loading / isReplying, and do not chain another attach there.
        } else if (!attached) {
            loading.value = false;
            isReplying.value = false;
            MessagePlugin.error(t('input.messages.steerFollowUpTimeout'));
        } else if (steerQueue.value.some(item => !item.failed)) {
            // startStream awaits the whole SSE. The follow-up's onTurnComplete
            // therefore runs while attachingSteerFollowUp is still true and
            // no-ops. Chain remaining after-items once that guard drops.
            void attachSteerFollowUp(attachedAssistantId);
        }
    }
};

/** 已经为哪条回答自动切过图，避免同一轮重复触发。 */
const autoSwitchedForMessageId = ref(null)

/**
 * 回答完成后，把右侧 K 线切到这条回答的「主标的」。
 *
 * 三重克制，缺一不可：
 *  1. **只在面板已经打开时切**。面板关着还去开，就是把「看K线」这个决定
 *     替用户做了——那正是刚修掉的 KLineStudioResult 自动开图缺陷。
 *  2. **用户手动选过就不切**。用户的显式选择永远优先于模型的暗示。
 *  3. **每条回答只切一次**。判不出主标的（`pickPrimaryMention` 返回 null）时
 *     什么都不做，宁可空着也不猜。
 */
const maybeAutoSwitchChart = (message) => {
    if (!message || message.role !== 'assistant') return
    // 判定逻辑在 utils/chartAutoSwitch.ts 里，是纯函数、有单测覆盖——
    // 这几条守卫写错的表现是静默的（要么抢图位，要么永远不联动）。
    if (!shouldAutoSwitchChart({
        messageCompleted: Boolean(message.is_completed),
        panelOpen: agentWorkspace.isOpen.value,
        userPickedThscode: agentWorkspace.userPickedThscode.value,
        alreadySwitchedForId: autoSwitchedForMessageId.value,
        messageId: message.id,
    })) return

    const text = message.answer || message.content || message.message || ''
    const primary = pickPrimaryMention(text)
    // 先记账再判定：即使这条回答判不出主标的，也不该在后续刷新里反复尝试。
    autoSwitchedForMessageId.value = message.id
    if (!primary) return
    if (primary.thscode === agentWorkspace.activeThscode.value) return

    agentWorkspace.setActiveThscode(primary.thscode)
}

const sendMsg = async (value, modelId = '', mentionedItems = [], imageFiles = [], attachmentFiles = [], options = {}) => {
    if (composerLocked.value) return
    // 新一轮开始：清掉上一轮的「用户已表态」与「已自动切过」，让本轮的自动
    // 联动重新有机会发生。
    agentWorkspace.resetUserPick()
    autoSwitchedForMessageId.value = null
    const reasoningEffort = props.embeddedMode ? undefined : (useSettingsStoreInstance.reasoningEffortOverride || undefined);
    stopStream();
    prepareForNewOutgoingMessage();
    activitySessionId.value = String(session_id.value);
    isReplying.value = true;
    loading.value = true;
    const selectedAgentId = props.embeddedMode ? props.agentId : (useSettingsStoreInstance.selectedAgentId || '');
    const selectedAgentSourceTenantId = props.embeddedMode
        ? undefined
        : (useSettingsStoreInstance.selectedAgentSourceTenantId || undefined);

    // Images are unified with the attachment pipeline: on the authenticated web
    // client they upload as temporary documents (understood in the background by
    // the VLM) and are sent as attachment_ids. The inline base64 `images`
    // payload is kept only for the embedded/public API path. A base64 fallback
    // is used per-image if the async upload fails.
    let imageAttachments = [];
    let userImages = [];
    const imageAttachmentIds = [];
    if (imageFiles && imageFiles.length > 0) {
        for (const file of imageFiles) {
            let dataURI;
            try {
                dataURI = await fileToBase64(file);
            } catch (e) {
                console.error('[Image] Failed to read images:', e);
                loading.value = false;
                isReplying.value = false;
                return;
            }
            userImages.push({ url: dataURI });
            if (props.embeddedMode) {
                imageAttachments.push({ data: dataURI });
                continue;
            }
            try {
                const upload = await uploadTemporaryAttachment(
                    session_id.value, file, selectedAgentId, selectedAgentSourceTenantId, 'auto'
                );
                imageAttachmentIds.push(upload.data.id);
            } catch (e) {
                console.error('[Image] Temporary image upload failed, falling back to inline:', e);
                imageAttachments.push({ data: dataURI });
            }
        }
    }

    // The create-chat page cannot upload before its session exists. Once it
    // navigates here, move those local files through the same asynchronous
    // upload/parse flow before starting the first stream.
    const localAttachments = (attachmentFiles || []).filter(attachment => !attachment.documentId);
    if (!props.embeddedMode && localAttachments.length > 0) {
        try {
            // Only upload to obtain a document ID; parsing continues in the
            // background and is awaited by the backend (shown on the timeline).
            await Promise.all(localAttachments.map(async (attachment) => {
                attachment.status = 'uploading';
                const upload = await uploadTemporaryAttachment(
                    session_id.value, attachment.file, selectedAgentId, selectedAgentSourceTenantId, 'auto'
                );
                attachment.documentId = upload.data.id;
                attachment.status = upload.data.status;
            }));
        } catch (error) {
            console.error('[Attachment] Temporary document upload failed:', error);
            await Promise.all(localAttachments
                .filter(attachment => attachment.documentId)
                .map(attachment => deleteTemporaryAttachment(session_id.value, attachment.documentId).catch(() => undefined)));
            MessagePlugin.error(error?.message || t('chat.attachmentParseFailed'));
            loading.value = false;
            isReplying.value = false;
            return;
        }
    }

    // Send any successfully uploaded attachment (parsing may still be running);
    // the backend waits for readiness and reports progress on the timeline.
    const attachmentIds = (attachmentFiles || [])
        .filter(attachment => attachment.documentId && attachment.status !== 'failed')
        .map(attachment => attachment.documentId);
    attachmentIds.push(...imageAttachmentIds);
    // Embedded public routes do not expose the authenticated session upload API;
    // keep their existing inline payload for compatibility.
    const legacyAttachmentFiles = props.embeddedMode
        ? (attachmentFiles || []).filter(attachment => !attachment.documentId)
        : [];
    let attachmentUploads = [];
    if (legacyAttachmentFiles.length > 0) {
        try {
            for (const attachment of legacyAttachmentFiles) {
                const reader = new FileReader();
                const base64Promise = new Promise((resolve, reject) => {
                    reader.onload = () => {
                        const result = reader.result;
                        // Extract base64 content (remove data:...;base64, prefix)
                        const base64 = result.split(',')[1];
                        resolve(base64);
                    };
                    reader.onerror = reject;
                    reader.readAsDataURL(attachment.file);
                });
                const base64Data = await base64Promise;
                attachmentUploads.push({
                    data: base64Data,
                    file_name: attachment.name,
                    file_size: attachment.size
                });
            }
        } catch (e) {
            console.error('[Attachment] Failed to read attachments:', e);
            loading.value = false;
            isReplying.value = false;
            return;
        }
    }

    // 将@提及的知识库和文件信息存入用户消息
    messagesList.push({ content: value, role: 'user', mentioned_items: mentionedItems, images: userImages, attachments: attachmentFiles.map(a => ({ id: a.documentId, file_name: a.name, file_size: a.size, file_type: '.' + a.name.split('.').pop()?.toLowerCase() })), channel: 'web', created_at: new Date().toISOString() });
    userHasScrolledUp.value = false;
    scrollToBottom(true);

    // Get agent mode status from settings store (prefer selectedAgentId for builtins)
    const agentEnabled = props.embeddedMode
        ? (props.agentId && props.agentId !== 'builtin-quick-answer')
        : useSettingsStoreInstance.isAgentStreamMode;

    // Get web search status from settings store
    const webSearchEnabled = props.embeddedMode ? false : useSettingsStoreInstance.isWebSearchEnabled;

    // Get knowledge_base_ids from settings store (selected by user via KnowledgeBaseSelector)
    // Merge @mentioned KB/file IDs so retrieval uses the same targets user @mentioned (including shared KBs)
    const sidebarKbIds = props.embeddedMode ? props.kbIds : (useSettingsStoreInstance.settings.selectedKnowledgeBases || []);
    const sidebarFileIds = props.embeddedMode ? [] : (useSettingsStoreInstance.settings.selectedFiles || []);
    const kbIdSet = new Set(sidebarKbIds);
    const fileIdSet = new Set(sidebarFileIds);
    for (const kbId of pendingSuggestionKnowledgeBaseIds) {
        if (kbId) kbIdSet.add(kbId);
    }
    for (const item of mentionedItems || []) {
        if (!item?.id) continue;
        if (item.type === 'kb' && !kbIdSet.has(item.id)) {
            kbIdSet.add(item.id);
        } else if (item.type === 'file' && !fileIdSet.has(item.id)) {
            fileIdSet.add(item.id);
        }
    }
    const kbIds = [...kbIdSet];
    const knowledgeIds = [...fileIdSet];
    const tagIds = [...new Set((mentionedItems || []).filter(item => item.type === 'tag' && item.id).map(item => item.id))];
    const mcpServiceIds = [...new Set((mentionedItems || []).filter(item => item.type === 'mcp' && item.id).map(item => item.id))];
    const skillNames = [...new Set((mentionedItems || []).filter(item => item.type === 'skill' && item.id).map(item => item.skill_name || item.id))];

    const endpoint = agentEnabled ? '/api/v1/agent-chat' : '/api/v1/knowledge-chat';

    const requestMcpServiceIds = agentEnabled ? mcpServiceIds : [];
    const requestSkillNames = agentEnabled ? skillNames : [];

    const suggestionAttribution = pendingSuggestionAttribution;
    pendingSuggestionAttribution = null;
    pendingSuggestionKnowledgeBaseIds = [];
    await startStream({
        session_id: session_id.value,
        knowledge_base_ids: kbIds,
        knowledge_ids: knowledgeIds,
        agent_enabled: agentEnabled,
        agent_id: selectedAgentId,
        agent_source_tenant_id: selectedAgentSourceTenantId,
        web_search_enabled: webSearchEnabled,
        local_browser_enabled: !props.embeddedMode && agentEnabled && useSettingsStoreInstance.isLocalBrowserEnabled && !useBrowserConnectionStore().knownOffline,
        summary_model_id: modelId,
        reasoning_effort: reasoningEffort,
        mcp_service_ids: requestMcpServiceIds,
        skill_names: requestSkillNames,
        tag_ids: tagIds,
        mentioned_items: mentionedItems,
        images: imageAttachments.length > 0 ? imageAttachments : undefined,
        attachment_uploads: attachmentUploads.length > 0 ? attachmentUploads : undefined,
        attachment_ids: attachmentIds.length > 0 ? attachmentIds : undefined,
        query: value,
        suggestion_attribution: suggestionAttribution || undefined,
        question_origin: options?.questionOrigin,
        method: 'POST',
        url: endpoint,
    });
}

// Quietly recover an in-flight IM reply we couldn't attach to (it's generated on
// the IM side, so it never streamed through this server). Poll until it completes,
// then reload the thread so it renders via the normal path. Bounded so an IM reply
// that genuinely died (e.g. the bot crashed) doesn't spin forever — on timeout we
// surface the original error so the failure isn't hidden.
const RECOVER_POLL_INTERVAL = 2500;
const RECOVER_POLL_MAX_ATTEMPTS = 48; // ~2 min
const recoverIncompleteMessage = () => {
    const targetSession = session_id.value;
    const targetMessageId = currentAssistantMessageId.value;
    if (recoverPollTimer) { clearTimeout(recoverPollTimer); recoverPollTimer = null; }
    if (!targetMessageId) { isReplying.value = false; isImRecovering.value = false; return; }
    isImRecovering.value = true; // show the "generating" indicator while we poll
    let attempts = 0;
    const poll = async () => {
        recoverPollTimer = null;
        if (session_id.value !== targetSession) { isReplying.value = false; isImRecovering.value = false; return; } // navigated away
        attempts++;
        try {
            const res = await getMessageList({ session_id: targetSession, limit: limit.value, created_at: '' });
            const target = (res?.data || []).find((m) => m.id === targetMessageId);
            if (target && target.is_completed) {
                created_at.value = '';
                messagesList.splice(0);
                getmsgList({ session_id: targetSession, limit: limit.value, created_at: '' });
                isReplying.value = false;
                isImRecovering.value = false;
                currentAssistantMessageId.value = '';
                return;
            }
        } catch (e) {
            console.warn('[Continue Stream] recovery poll failed:', e);
        }
        if (attempts >= RECOVER_POLL_MAX_ATTEMPTS) {
            // The IM reply never completed — don't hide it; surface the standard
            // stream-failure message (reuses the existing i18n key, no raw HTTP code).
            MessagePlugin.error(t('error.streamFailed'));
            isReplying.value = false;
            isImRecovering.value = false;
            currentAssistantMessageId.value = '';
            return;
        }
        recoverPollTimer = setTimeout(poll, RECOVER_POLL_INTERVAL);
    };
    recoverPollTimer = setTimeout(poll, RECOVER_POLL_INTERVAL);
};

// Watch for stream errors and show message
watch(error, (newError) => {
    if (!newError) return;
    // A failed attach to an in-flight IM reply isn't a real error — the answer is
    // produced on the IM side and never streams here. Recover quietly by polling to
    // completion instead of flashing a "stream failed" toast. Web/api replies fall
    // through to the normal error toast below, unchanged.
    if (isAttachingImStream.value) {
        isAttachingImStream.value = false;
        recoverIncompleteMessage();
        return;
    }
    MessagePlugin.error(newError);
    isReplying.value = false;
    loading.value = false;
    // 清空当前 assistant message ID
    currentAssistantMessageId.value = '';
});

onChunk((data) => {
    if (data.response_type === 'session_title') {
        const title = data.content || data.data?.title;
        if (title && data.data?.session_id) {
            console.log('[Session Title Update]', {
                session_id: data.data.session_id,
                title: title,
            });
            usemenuStore.updatasessionTitle(data.data.session_id, title);
            usemenuStore.changeIsFirstSession(false);
            notifySessionMutation({
                sessionId: data.data.session_id,
                patch: { title },
            });
        }
        return;
    }
    processStreamChunk(data);
});

const handleSessionMutation = (event) => {
    const detail = event.detail;
    if (detail?.sessionId !== session_id.value) return;

    if (detail.patch) {
        currentSession.value = {
            ...(currentSession.value || { id: session_id.value }),
            ...detail.patch,
        };
    }
    if (detail.messagesCleared) {
        messagesList.splice(0);
        steerQueue.value = [];
        created_at.value = '';
        hasMoreHistory.value = true;
        historyLoadingMore.value = false;
        fetchSuggestedQuestionsIfNeeded();
    }
};

onBeforeMount(async () => {
    // 若从智能体列表点击共享智能体进入，URL 带 agent_id 与 source_tenant_id，同步到 store
    const agentIdFromQuery = props.agentId || (route.query.agent_id && String(route.query.agent_id));
    const sourceTenantIdFromQuery = route.query.source_tenant_id && String(route.query.source_tenant_id);
    if (agentIdFromQuery && sourceTenantIdFromQuery) {
        useSettingsStoreInstance.selectAgent(agentIdFromQuery, sourceTenantIdFromQuery);
    } else if (agentIdFromQuery) {
        useSettingsStoreInstance.selectAgent(agentIdFromQuery, null);
    }

    if (props.kbIds && props.kbIds.length > 0) {
        useSettingsStoreInstance.selectKnowledgeBases(props.kbIds);
    }

    // 必须在 Input-field onMounted 之前完成：按 session.last_request_state 恢复输入栏
    await loadSessionAndHydrate(session_id.value);
});

onMounted(async () => {
    window.addEventListener(SESSION_MUTATION_EVENT, handleSessionMutation);
    messagesList.splice(0);
    steerQueue.value = [];

    // 初始化状态：加载历史消息时不应显示loading
    loading.value = false;
    isReplying.value = false;

    if (firstQuery.value) {
        scrollLock.value = true;
        historyLoading.value = false;
        if (firstModelId.value) {
            useSettingsStoreInstance.updateConversationModels({
                summaryModelId: firstModelId.value,
                selectedChatModelId: firstModelId.value,
                rerankModelId: '',
            });
        }
        sendMsg(firstQuery.value, firstModelId.value || '', firstMentionedItems.value || [], firstImageFiles.value || [], firstAttachmentFiles.value || [], { questionOrigin: firstQuestionOrigin.value || undefined });
        usemenuStore.changeFirstQuery('', [], '', [], []);
    } else {
        scrollLock.value = false;
        hasMoreHistory.value = true;
        historyLoadingMore.value = false;
        let data = {
            session_id: session_id.value,
            created_at: '',
            limit: limit.value
        }
        getmsgList(data)
    }
})
const clearData = () => {
    if (!props.embeddedMode) sessionActivity.detach(activitySessionId.value);
    activitySessionId.value = '';
    stopStream();
    referencesDrawer.close();
    isReplying.value = false;
    fullContent.value = '';
    // Stop any IM-reply recovery poll for the session we're leaving/switching.
    if (recoverPollTimer) { clearTimeout(recoverPollTimer); recoverPollTimer = null; }
    isImRecovering.value = false;
}
onUnmounted(() => {
    if (!props.embeddedMode) sessionActivity.detach(activitySessionId.value);
    activitySessionId.value = '';
    window.removeEventListener(SESSION_MUTATION_EVENT, handleSessionMutation);
    if (recoverPollTimer) { clearTimeout(recoverPollTimer); recoverPollTimer = null; }
});
onBeforeRouteLeave((to, from, next) => {
    clearData()
    // 离开聊天会话 → 还原"用户全局默认"，避免旧会话的请求态泄漏到新建对话。
    useSettingsStoreInstance.restoreDefaultsIfSnapshotted();
    next()
})
onBeforeRouteUpdate((to, from, next) => {
    clearData()
    // 仅"会话 → 会话"会落到这里；跨会话覆盖的还原放到 route.params 的 watch 里，
    // 因为新会话的 getSession 也在那边触发，便于保证 restore→snapshot→apply 顺序。
    next()
})
</script>
<style lang="less" scoped>
.chat {
    // 水平方向不留 padding，让滚动条贴到内容区最右缘；
    // 消息列与输入列各自用 --chat-content-inset 做左右对称的留白（窄屏时才可见）。
    padding: 0;
    --chat-content-inset: 20px;
    box-sizing: border-box;
    flex: 1;
    // The parent .platform-route-outlet is a flex column with min-height:0
    // and overflow:hidden — we also need min-height:0 here so that our
    // own flex:1 child (.chat_thread) can shrink below its content height.
    min-height: 0;
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    max-width: 100%;
    min-width: 400px;

    &.is-embedded {
        max-width: 100%;
        min-width: 100%;
        padding: 0;
        overflow-x: hidden;
    }

    &:not(.is-embedded) {
        @media (min-width: 960px) {
            transition: padding-right var(--app-motion-slow) cubic-bezier(0.22, 0.61, 0.36, 1);
        }
    }

    // 右侧面板让位：宽度由 JS 求和后单点下发（--right-panels-width），
    // 这里只有唯一一条 padding 规则，不再依赖多条 padding-right 互相竞争。
    // <960px 时不做任何让位——工作台面板在该断点改为整屏覆盖（见
    // AgentWorkspacePanel.vue），让位反而会造成聊天区被压成一条缝。
    &.has-right-panels:not(.is-embedded) {
        @media (min-width: 960px) {
            box-sizing: border-box;
            // 上限 60vw：多个面板叠加时不至于把聊天区挤到不可用
            padding-right: min(var(--right-panels-width, 0px), 60vw);
        }
    }

    // 引用抽屉打开时收起滚动区顶部留白（该留白是为抽屉头部预留的，
    // 与工作台/沙箱无关，故单独一条，不参与上面的宽度计算）。
    &.has-references-panel:not(.is-embedded) {
        @media (min-width: 960px) {
            .chat_scroll_box {
                padding-top: 0;
            }
        }
    }

    &.is-embedded :deep(.answers-input) {
        position: relative;
        transform: translateX(0);
        width: 100%;
        left: 0;
        bottom: auto;
        display: flex;
        justify-content: center;
    }

    &.is-embedded :deep(.control-bar) {
        justify-content: flex-end;
    }

    &:not(.is-embedded) :deep(.answers-input) {
        position: static;
        transform: translateX(0);

        .t-textarea__inner {
            width: 100% !important;
        }
    }

    &.is-embedded :deep(.answers-input) .t-textarea__inner {
        width: 100% !important;
        min-height: 48px !important;
        padding: 10px 14px;
    }
}

.chat_thread {
    position: relative;
    flex: 1;
    min-height: 0;
    width: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.chat-topbar {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 0 0 var(--app-chat-header-height);
    width: 100%;
    min-width: 0;
    padding: 0 12px 0 var(--chat-content-inset, 20px);
    box-sizing: border-box;
    border-bottom: 1px solid var(--td-component-stroke);
    background: var(--td-bg-color-container);
}

.sandbox-header-toggle {
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
    margin-left: auto;
}

.sandbox-header-toggle__btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    padding: 0;
    border: 0;
    border-radius: 5px;
    color: var(--td-text-color-placeholder);
    background: transparent;
    cursor: pointer;
    transition: background-color var(--app-motion-fast) ease, color var(--app-motion-fast) ease;

    &:hover {
        color: var(--td-text-color-primary);
        background: var(--td-bg-color-container-hover);
    }

    &:active {
        background: var(--td-bg-color-container-active);
    }
}

.chat_scroll_box {
    flex: 1;
    min-height: 0;
    width: 100%;
    padding: 8px 0 0;
    box-sizing: border-box;
    overflow-y: auto;
    // Keep native message bounce without chaining scroll to the outer page.
    overscroll-behavior-y: contain;
    scroll-padding-bottom: var(--chat-composer-height, 0px);
    scrollbar-gutter: auto;
    scrollbar-width: thin;
    scrollbar-color: rgba(148, 163, 184, 0.35) transparent;

    &:hover,
    &:focus-within {
        scrollbar-color: rgba(100, 116, 139, 0.65) transparent;
    }

    &::-webkit-scrollbar {
        width: 5px;
    }

    &::-webkit-scrollbar-track {
        background: transparent;
    }

    &::-webkit-scrollbar-thumb {
        border-radius: var(--app-radius-pill);
        background: rgba(148, 163, 184, 0.35);
        transition: background-color var(--app-motion-base) ease;
    }

    &:hover::-webkit-scrollbar-thumb,
    &:focus-within::-webkit-scrollbar-thumb {
        background: rgba(100, 116, 139, 0.65);
    }
}

// Keep the full-height message scrollbar and reserve space below the last
// message for the composer, which sits outside the bouncing scroll viewport.
.chat_scroll_content {
    display: flex;
    flex-direction: column;
    min-height: 100%;

    // Use an in-flow spacer so the content ResizeObserver also detects composer
    // height changes and keeps the last message visible when following replies.
    &::after {
        content: '';
        flex: 0 0 var(--chat-composer-height, 0px);
    }
}

.chat_composer {
    position: absolute;
    bottom: 0;
    left: 0;
    right: var(--chat-scrollbar-gutter, 0px);
    z-index: 12;
    padding: 16px 0 max(8px, env(safe-area-inset-bottom));
    background: var(--td-bg-color-container);
}

.is-embedded .chat_composer {
    padding: 0;
}

.chat_overlays {
    position: absolute;
    inset: 0 0 var(--chat-composer-height, 0px);
    pointer-events: none;

    :deep(.browser-task-preview) {
        pointer-events: auto;
    }
}

.scroll-to-bottom-btn {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    bottom: calc(100% + 8px);
    z-index: 10;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--td-bg-color-container);
    border: 1px solid var(--td-component-stroke);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    color: var(--td-text-color-secondary);
    transition: background-color var(--app-motion-base) ease, color var(--app-motion-base) ease, box-shadow var(--app-motion-base) ease;

    &:hover {
        background: var(--td-bg-color-container-hover);
        color: var(--td-text-color-primary);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    }

    &:active {
        transform: translateX(-50%) scale(0.92);
    }
}

.scroll-btn-fade-enter-active,
.scroll-btn-fade-leave-active {
    transition: opacity var(--app-motion-base) ease, transform var(--app-motion-base) ease;
}

.scroll-btn-fade-enter-from,
.scroll-btn-fade-leave-to {
    opacity: 0;
    transform: translateX(-50%) translateY(8px);
}

@keyframes contentFadeIn {
    from {
        opacity: 0;
        transform: translateY(6px);
    }

    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.msg-skeleton-list {
    display: flex;
    flex-direction: column;
    gap: 20px;
    max-width: 960px;
    padding: 16px 0;
    animation: contentFadeIn 0.3s ease-out;
}

.msg-skeleton-user {
    display: flex;
    justify-content: flex-end;
}

.msg-skeleton-bot {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-left: 4px;
}

.input-container {
    min-height: 0;
    flex-shrink: 0;
    margin: 0 auto;
    width: 100%;
    max-width: 960px;
    box-sizing: border-box;
    position: relative;

    &:not(.is-embedded) {
        padding: 0 var(--chat-content-inset, 20px);
        max-width: calc(960px + 2 * var(--chat-content-inset, 20px));
    }

    &.is-embedded {
        max-width: 100%;
        width: 100%;
        margin: 0;
        padding: 12px 16px 16px;
        min-height: auto;
        box-sizing: border-box;
        overflow-x: clip;
    }
}

.msg_list {
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-width: 960px;
    flex: 1;
    margin: 0 auto;
    width: 100%;
    box-sizing: border-box;

    &:not(.is-embedded) {
        padding: 0 var(--chat-content-inset, 20px);
        max-width: calc(960px + 2 * var(--chat-content-inset, 20px));
    }

    /*
      给每条消息加 layout/style containment：
      - 一条消息的内部布局变化不再让浏览器去 invalidate 整个文档，
        这是修掉"hover 到 session 列表也变白"那个问题的关键。
      - 不要再用 content-visibility: auto / contain-intrinsic-size：
        agent 消息真实高度差异巨大（几百 ~ 数千 px），估的占位高度会让消息进入视口时
        反复发生"占位 -> 真实高度"的大幅 layout shift + 首次 paint 滞后，
        反而在向上滚动时制造"未画完"的白屏闪烁。
        当前 handleMsgList 全流程 ~50ms，根本无需跳过渲染，老老实实正常渲染最稳。
      - 不开 contain: paint：AgentStreamDisplay 里有 tooltip / popover 等会溢出的浮层，
        paint containment 会把它们裁掉。
    */
    .msg-item-wrapper {
        contain: layout style;
        &.is-empty-segment { display: none; }
        &.is-steer-prefix { margin-bottom: -4px; }
    }

    .message-row {
        display: flex;
        flex-direction: column;
        width: 100%;

    }

    .botanswer_laoding_gif {
        width: 24px;
        height: 18px;
        margin-left: 16px;
    }

    .chat-global-wait {
        display: flex;
        align-items: center;
        min-height: 28px;
        padding-left: 4px;
    }

    .chat-global-wait__spinner {
        width: 12px;
        height: 12px;
        box-sizing: border-box;
        border: 1.5px solid var(--td-component-stroke);
        border-top-color: var(--td-text-color-secondary);
        border-radius: 50%;
        animation: wk-spin 0.8s linear infinite;
    }
}

@media (prefers-reduced-motion: reduce) {
    .chat-global-wait__spinner {
        animation: none;
    }
}

@import '../../components/css/suggested-questions.less';

.suggested-questions-container {
    transition: min-height var(--app-motion-slow) @suggested-ease;
}

.suggested-questions-inner {
    animation: contentFadeIn 0.3s ease-out;
}

.sq-fade-enter-active,
.sq-fade-leave-active {
    transition: opacity 0.25s @suggested-ease;
}

.sq-fade-enter-from,
.sq-fade-leave-to {
    opacity: 0;
}
</style>

<style lang="less">
.chat-rewind-popconfirm {
    max-width: 260px;

    .t-popconfirm__content,
    .t-popup__content {
        max-width: 260px;
        white-space: normal;
        line-height: 1.5;
    }
}

/* Chat 答案里出现的 A 股 ticker（6位.SH/SZ/BJ）会被包成 <span class="kline-ticker">；
 * hover/click 后右侧栏抽屉打开对应股票的 K 线图。 */
.kline-ticker {
    display: inline-block;
    padding: 1px 6px;
    margin: 0 1px;
    border-radius: var(--app-radius-xs);
    background: rgba(0, 82, 217, 0.08);
    color: var(--td-brand-color);
    font-family: var(--td-font-family-mono);
    font-weight: 500;
    cursor: pointer;
    transition: background 0.12s ease, transform var(--app-motion-instant) ease;
    user-select: none;
}

.kline-ticker:hover,
.kline-ticker:focus {
    background: rgba(0, 82, 217, 0.18);
    transform: translateY(-1px);
    outline: none;
}

.kline-ticker:focus-visible {
    box-shadow: 0 0 0 2px rgba(0, 82, 217, 0.4);
}

/* Chat 答案里出现的绝对日期会被包成 <span class="kline-range">；
 * hover/click 后右侧 K 线滚动到对应区间。视觉上刻意与 .kline-ticker 区分：
 * 日期是「看图上哪一段」，标的是「看哪只票」，两者不该长得一样。 */
.kline-range {
    display: inline-block;
    padding: 0 4px;
    margin: 0 1px;
    border-bottom: 1px dashed rgba(201, 146, 8, 0.7);
    border-radius: var(--app-radius-xs) var(--app-radius-xs) 0 0;
    color: #b8860b;
    cursor: pointer;
    transition: background var(--app-motion-instant) ease, border-color var(--app-motion-instant) ease;
    user-select: none;
}

/* hover 态必须**明显**——第一版只有一点点背景色变化，用户根本看不出这个日期
   是可交互的，交互感因此很模糊。这里同时改背景、把虚线底边变实线、轻微上浮，
   三个信号叠加，扫一眼就能认出「这个日期能点」。 */
.kline-range:hover,
.kline-range:focus {
    background: rgba(201, 146, 8, 0.18);
    border-bottom-color: #b8860b;
    border-bottom-style: solid;
    outline: none;
}

.kline-range:focus-visible {
    box-shadow: 0 0 0 2px rgba(201, 146, 8, 0.35);
}

/* 正文里的回答锚点：模型显式标记的时段/价位，与图上的框和线共用同一个编号。
   配色与日期标记同族（琥珀），但更"实体"——它才是主入口，日期标记是正则兜底。 */
.kline-anchor {
    display: inline-block;
    padding: 0 5px 0 2px;
    margin: 0 1px;
    border-radius: var(--app-radius-xs);
    background: rgba(201, 146, 8, 0.14);
    color: #b8860b;
    font-weight: 500;
    cursor: pointer;
    transition: background var(--app-motion-instant) ease;
    user-select: none;
}

/* 圆编号：与图上 overlay 画的圆徽章同形同色，两处印同一个符号，眼睛自己就接上了。 */
.kline-anchor__num {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    margin-right: 4px;
    border-radius: var(--app-radius-pill);
    background: rgba(201, 146, 8, 0.9);
    color: #1a1408;
    font-size: var(--app-text-2xs);
    font-weight: 700;
    line-height: 1;
}

.kline-anchor:hover,
.kline-anchor:focus {
    background: rgba(201, 146, 8, 0.26);
    outline: none;
}

.kline-anchor:focus-visible {
    box-shadow: 0 0 0 2px rgba(201, 146, 8, 0.35);
}

/* 反向联动：图上 hover 框/徽章时，正文里这句话高亮。 */
.kline-anchor.is-anchor-highlighted {
    background: rgba(201, 146, 8, 0.34);
    box-shadow: 0 0 0 1px rgba(201, 146, 8, 0.75) inset;
}

/* 无效锚点：语法没露出来、内容按文本保留，但**不给交互语义**——
   一个"点了没反应"的可点元素比不可点更让人困惑。 */
.kline-anchor[data-anchor-valid="0"] {
    padding: 0;
    background: transparent;
    color: inherit;
    font-weight: inherit;
    cursor: default;
}

/* 悬浮提示：告诉用户「悬停这个日期会发生什么」。 */
.kline-range-tip {
    position: fixed;
    z-index: 3000;
    transform: translate(-50%, calc(-100% - 8px));
    padding: 4px 8px;
    border-radius: var(--app-radius-sm);
    background: rgba(32, 26, 12, 0.94);
    color: #f6d98a;
    font-size: var(--app-text-xs);
    line-height: 1.4;
    white-space: nowrap;
    pointer-events: none;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.28);
}
</style>
