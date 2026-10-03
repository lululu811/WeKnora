<template>
  <div class="wechat-workbench">
    <!-- 左：文章列表 -->
    <aside class="wb-list" :class="{ 'wb-list--closed': !listOpen }">
      <div v-if="listOpen" class="wb-list__head">
        <t-select
          v-model="kbId"
          :options="baseOptions"
          placeholder="选择知识库"
          size="small"
          filterable
        />
      </div>
      <div class="wb-list__body">
        <div v-if="loadingList" class="wb-hint">{{ t('wechat.list.loading') }}</div>
        <div v-else-if="listError" class="wb-hint wb-hint--err">{{ listError }}</div>
        <div v-else-if="!articles.length" class="wb-hint">{{ t('wechat.list.empty') }}</div>
        <button
          v-for="a in articles"
          :key="a.id"
          class="wb-item"
          :class="{ 'wb-item--active': a.id === currentId }"
          type="button"
          @click="openArticle(a.id)"
        >
          <span class="wb-item__title">{{ a.title }}</span>
          <span v-if="a.enableStatus !== 'enabled'" class="wb-item__badge">
            {{ t('wechat.item.indexing') }}
          </span>
        </button>
      </div>
    </aside>

    <!-- 右：原文 + 问答 -->
    <section class="wb-main">
      <div v-if="!detail" class="wb-hint wb-hint--center">{{ t('wechat.pickHint') }}</div>

      <template v-else>
        <header class="wb-head">
          <t-button
            class="wb-head__toggle"
            size="small"
            variant="text"
            @click="listOpen = !listOpen"
          >
            {{ listOpen ? t('wechat.list.hide') : t('wechat.list.show') }}
          </t-button>
          <h2 class="wb-head__title">{{ detail.title }}</h2>
          <div class="wb-head__actions">
            <t-tag v-if="!hasVault" theme="warning" variant="light">
              {{ t('wechat.vault.off') }}
            </t-tag>
            <a
              v-if="originalUrl"
              class="wb-head__origin"
              :href="originalUrl"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ t('wechat.openOriginal') }}
            </a>
          </div>
        </header>

        <div class="wb-split">
          <!-- 左栏：原文 -->
          <div class="wb-reader" ref="readerEl">
            <div v-if="loadingArticle" class="wb-hint">{{ t('wechat.loading') }}</div>
            <div v-else-if="articleError" class="wb-hint wb-hint--err">{{ articleError }}</div>
            <div
              v-else
              ref="previewContent"
              class="markdown-body wb-reader__body"
              v-html="readerHtml"
            />
          </div>

          <!-- 右栏：问答 -->
          <div class="wb-chat">
            <div class="wb-chat__scope">
              <t-radio-group v-model="chat.scopeToDocument.value" size="small">
                <t-radio-button :value="true">{{ t('wechat.scope.doc') }}</t-radio-button>
                <t-radio-button :value="false">{{ t('wechat.scope.kb') }}</t-radio-button>
              </t-radio-group>
              <t-button
                v-if="chat.turns.value.length"
                size="small"
                variant="text"
                @click="chat.clearTurns()"
              >
                {{ t('wechat.clear') }}
              </t-button>
            </div>

            <div class="wb-chat__log">
              <div v-if="!allTurns.length" class="wb-hint">{{ t('wechat.chat.empty') }}</div>
              <div v-for="turn in allTurns" :key="turn.id" class="wb-turn">
                <div class="wb-turn__q">{{ turn.question }}</div>
                <div class="wb-turn__a" v-html="renderTurn(turn.answer, turn.references, !turn.done)"></div>
                <div v-if="turn.references.length" class="wb-turn__refs">
                  <button
                    v-for="(r, i) in turn.references"
                    :key="r.id || i"
                    class="wb-ref"
                    type="button"
                    @click="locate(r)"
                  >
                    <span class="wb-ref__score" v-if="r.score != null">
                      {{ Number(r.score).toFixed(2) }}
                    </span>
                    <span class="wb-ref__title">{{ r.knowledge_title || detail?.title }}</span>
                  </button>
                </div>
                <div v-if="turn.error" class="wb-hint wb-hint--err">{{ turn.error }}</div>
              </div>
            </div>

            <div class="wb-chat__input">
              <t-textarea
                v-model="chat.draft.value"
                :placeholder="t('wechat.chat.placeholder')"
                :autosize="{ minRows: 2, maxRows: 6 }"
                @keydown="onDraftKeydown"
              />
              <t-button
                theme="primary"
                :loading="chat.busy.value"
                :disabled="!chat.draft.value.trim()"
                @click="submit"
              >
                {{ t('wechat.send') }}
              </t-button>
            </div>
          </div>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';

import { createChatMarkdownRenderer, renderChatMarkdown } from '@/utils/chatMarkdownRenderer';
import { sanitizeMarkdownHTML, safeMarkdownToHTML } from '@/utils/security';
import { renderDocumentPreviewMarkdown } from '@/utils/documentPreviewMarkdown';
import { findMarkdownSourceRange } from '@/utils/markdownSourceLocate';
import { highlightRanges } from '@/utils/sourceLocatorDom';

import { useWechatWorkbench } from '../composables/useWechatWorkbench';
import { useDocumentChat } from '../composables/useDocumentChat';
import { hydrateVaultImages } from '../utils/vaultImageAuth';

const { t } = useI18n();

const wb = useWechatWorkbench();
const chat = useDocumentChat();
const previewContent = ref<HTMLElement | null>(null);
// 列表是可折叠侧栏而非常驻第三列：应用本身左侧还有 210px 的全局导航，
// 内容区只剩 ~1067px。默认收起 —— 用户要的是「左原文、右提问」两栏，
// 列表常驻会把问答栏挤出屏幕；需要换文章时再展开。
const listOpen = ref(false);
const readerEl = ref<HTMLElement | null>(null);

const {
  bases, kbId, articles, detail, currentId,
  loadingList, loadingArticle, listError, articleError,
  renderedMarkdown, hasVault, originalUrl,
  loadBases, openArticle,
} = wb;

const baseOptions = computed(() =>
  bases.value.map((b: any) => ({ label: b.name, value: b.id })),
);

/** 渲染层用 sanitizer 过的 HTML，不直接 v-html 原始 markdown。 */
const readerHtml = computed(() =>
  renderedMarkdown.value ? renderDocumentPreviewMarkdown(renderedMarkdown.value) : '',
);

const allTurns = computed(() =>
  chat.liveTurn.value ? [...chat.turns.value, chat.liveTurn.value] : chat.turns.value,
);

// renderChatMarkdown 的 escapeMarkdown / sanitizeHtml 都是**必填的函数**，
// 不是开关：传布尔值进去渲染器会拿它当函数调，抛
// "t.escapeMarkdown is not a function"，而这个异常发生在 computed 里，会把整个
// 视图掀掉 —— 表现是问完一句整页空白，比没有回答还糟。
const chatMarkdownRenderer = createChatMarkdownRenderer();

function renderTurn(markdown: string, references: any[] = [], streaming = false): string {
  if (!markdown) return '';
  try {
    return renderChatMarkdown(markdown, {
      renderer: chatMarkdownRenderer,
      escapeMarkdown: safeMarkdownToHTML,
      sanitizeHtml: sanitizeMarkdownHTML,
      streaming,
      knowledgeReferences: references as any,
    });
  } catch (e) {
    // 兜底：渲染器出错时退回纯文本，宁可样式差一点也别让整页消失。
    console.error('[wechat] answer render failed, falling back to plain text', e);
    return `<p>${markdown.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]!))}</p>`;
  }
}

/**
 * t-textarea 的 @keydown 拿到的**不是原生 KeyboardEvent**，而是 TDesign 自己
 * 发射的 (value, { e: KeyboardEvent }) —— 见 Input-field.vue:2385 的 onKeydown。
 * 直接写 @keydown.enter 会让 Vue 的 withKeys 收到一个字符串，在 `'key' in event`
 * 那一行抛 "Cannot use 'in' operator"，回车静默失灵。所以修饰符用不了，
 * 只能在 JS 里自己判。
 *
 * keyCode 229 / isComposing 是中文输入法的组合态：候选词没上屏时按回车是选词，
 * 不能当发送。Shift+回车留给换行。
 */
function onDraftKeydown(_val: string, ctx: { e: KeyboardEvent }) {
  const ev = ctx?.e;
  if (!ev) return;
  if (ev.isComposing || ev.keyCode === 229) return;
  if (ev.keyCode === 13 && !ev.shiftKey) {
    ev.preventDefault();
    submit();
  }
}

function submit() {
  const q = chat.draft.value;
  chat.draft.value = '';
  void chat.ask(q, { kbId: kbId.value, knowledgeId: currentId.value || undefined });
}

/**
 * 点引用 → 在左栏定位并高亮。
 *
 * 走的是和聊天页同一条路径（`findMarkdownSourceRange` 按文本匹配），而不是
 * source_locators 的 offset —— 手工知识入库时那张表是 NULL，且高亮的精度
 * 取决于文本匹配本身，不取决于有没有 offset 表。
 */
function locate(ref: any) {
  const root = previewContent.value;
  if (!root) return;
  const snippet: string = ref?.content || ref?.matched_content || '';
  if (!snippet) return;
  const match = findMarkdownSourceRange(root, snippet, '');
  if (!match) return;
  highlightRanges(match.ranges?.length ? match.ranges : [match.range]);
  const el = root.querySelector<HTMLElement>('::highlight(source-locate)');
  void el; // CSS Custom Highlight 不产生元素，用 getSelection 之外的 API 定位
  (match.range.startContainer.parentElement as HTMLElement | null)?.scrollIntoView({
    behavior: 'smooth',
    block: 'center',
  });
}

watch(currentId, () => chat.resetLive());

// 正文是 v-html 渲染的，图片不会自动经过 hydrate；每次换文章或重新渲染后
// 都要补一次。hydrate 自身幂等，重复调用不会重复请求。
const blobUrls = ref<string[]>([]);
async function hydrate() {
  blobUrls.value.forEach((u) => URL.revokeObjectURL(u));
  blobUrls.value = await hydrateVaultImages(previewContent.value);
}
watch([renderedMarkdown, currentId], () => void hydrate(), { flush: 'post' });
onBeforeUnmount(() => blobUrls.value.forEach((u) => URL.revokeObjectURL(u)));

onMounted(async () => {
  await loadBases();
  if (articles.value.length) await openArticle(articles.value[0].id);
});
</script>

<style scoped>
.wechat-workbench {
  display: flex;
  height: 100%;
  min-height: 0;
  background: var(--td-bg-color-page);
}
.wb-list {
  /* 不能只写 width + flex:0 0 auto —— flex-basis:auto 时内容会赢过 width，
     长标题把这一列撑到 337px，阅读区被挤到只剩 230px（标题被压成一列）。
     固定 basis 并允许收缩才稳。 */
  flex: 0 0 260px;
  width: 260px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--td-component-border);
  min-height: 0;
}
.wb-list--closed { flex-basis: 0; width: 0; border-right: 0; overflow: hidden; }
.wb-list__head { padding: 12px; border-bottom: 1px solid var(--td-component-border); }
.wb-list__body { flex: 1; overflow-y: auto; padding: 6px; }
.wb-item {
  display: block; width: 100%; text-align: left; cursor: pointer;
  border: 0; background: transparent; padding: 8px 10px; border-radius: 6px;
  color: var(--td-text-color-primary); font-size: 13px; line-height: 1.5;
}
.wb-item:hover { background: var(--td-component-border); }
.wb-item--active { background: var(--td-brand-color-light); }
.wb-item__title { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wb-item__badge { font-size: 11px; color: var(--td-warning-color); }
.wb-main { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.wb-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 12px 16px; border-bottom: 1px solid var(--td-component-border);
}
.wb-head__toggle { flex: 0 0 auto; margin-right: 4px; }
.wb-head__title { margin: 0; font-size: 15px; font-weight: 600; }
.wb-head__actions { display: flex; align-items: center; gap: 10px; }
.wb-head__origin { font-size: 13px; color: var(--td-brand-color); }
.wb-split { flex: 1; display: flex; min-height: 0; }
.wb-reader {
  /* 58/42 在只剩 ~1067px 的内容区里会把阅读区压到 230px。改成 flex:1 1 0
     加 min-width 下限，按剩余空间平分。 */
  flex: 1 1 0;
  min-width: 340px;
  overflow-y: auto;
  padding: 20px 24px;
  border-right: 1px solid var(--td-component-border);
}
.wb-reader__body { max-width: 780px; margin: 0 auto; }
/* 公众号原文链接是一长串无空格 token，不打断会把整栏撑宽。 */
.wb-reader__body :deep(a) { overflow-wrap: anywhere; }
.wb-reader__body :deep(img) { max-width: 100%; height: auto; border-radius: 4px; }
.wb-chat { flex: 1 1 0; min-width: 300px; display: flex; flex-direction: column; min-height: 0; }
.wb-chat__scope {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px; border-bottom: 1px solid var(--td-component-border);
}
.wb-chat__log { flex: 1; overflow-y: auto; padding: 12px; }
.wb-turn { margin-bottom: 16px; }
.wb-turn__q { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.wb-turn__a { font-size: 13px; line-height: 1.7; }
.wb-turn__refs { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.wb-ref {
  display: flex; gap: 8px; align-items: baseline; cursor: pointer; text-align: left;
  border: 1px solid var(--td-component-border); background: transparent;
  border-radius: 4px; padding: 4px 8px; font-size: 12px; color: var(--td-text-color-primary);
}
.wb-ref:hover { border-color: var(--td-brand-color); }
.wb-ref__score { color: var(--td-brand-color); font-variant-numeric: tabular-nums; }
.wb-ref__title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wb-chat__input {
  display: flex; gap: 8px; align-items: flex-end;
  padding: 12px; border-top: 1px solid var(--td-component-border);
}
.wb-hint { padding: 16px; color: var(--td-text-color-secondary); font-size: 13px; }
.wb-hint--center { display: flex; align-items: center; justify-content: center; height: 100%; }
.wb-hint--err { color: var(--td-error-color); }
</style>
