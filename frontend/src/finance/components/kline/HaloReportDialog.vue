<template>
  <t-dialog
    v-model:visible="visible"
    :header="t('halo.title')"
    :footer="false"
    width="880px"
    top="6vh"
    dialog-class-name="halo-report-dialog"
    destroy-on-close
  >
    <div class="halo-report">
      <!-- 头部：标的与报告期。报告期可能为空（该票没有已落库的事实），
           所以用 v-if 而不是显示一个占位符 —— 「没有」和「未知」要分清。 -->
      <div class="halo-report__head">
        <span class="halo-report__symbol">{{ thscode }}</span>
        <span v-if="report?.period" class="halo-report__meta">
          {{ t('halo.period') }}：{{ report.period }}
        </span>
        <span v-if="report?.asset_type" class="halo-report__meta">
          {{ t('halo.assetType') }}：{{ report.asset_type }}
        </span>
        <span class="halo-report__spacer" />
        <t-button
          size="small"
          variant="text"
          :disabled="loading"
          @click="load()"
        >
          {{ t('halo.refresh') }}
        </t-button>
      </div>

      <!-- 加载中 -->
      <div v-if="loading" class="halo-report__state">
        <t-loading size="small" />
        <span>{{ t('halo.loading') }}</span>
      </div>

      <!-- 加载失败（链路问题，与「没数据」是两回事） -->
      <div v-else-if="loadError" class="halo-report__state is-error">
        <p class="halo-report__state-title">{{ t('halo.loadFailed') }}</p>
        <p class="halo-report__state-detail">{{ loadError }}</p>
      </div>

      <!-- 没数据：不是错误。面板要给出下一步动作，而不是弹失败提示。 -->
      <div v-else-if="report && !report.ok" class="halo-report__state">
        <p class="halo-report__state-title">{{ t('halo.noDataTitle') }}</p>
        <p class="halo-report__state-detail">{{ report.reason || '' }}</p>
        <p class="halo-report__state-detail">{{ t('halo.noDataHint') }}</p>
      </div>

      <!-- 报告正文 -->
      <template v-else-if="report">
        <p class="halo-report__note">{{ t('halo.llmNote') }}</p>
        <!-- eslint-disable-next-line vue/no-v-html -- 经 renderChatMarkdown + sanitizeMarkdownHTML -->
        <div ref="markdownEl" class="halo-report__body" v-html="markdownHtml" />

        <div class="halo-report__actions">
          <span class="halo-report__pick-label">{{ t('halo.pickKb') }}</span>
          <t-select
            v-model="targetKbId"
            class="halo-report__pick"
            :placeholder="t('halo.kbPlaceholder')"
            :loading="kbsLoading"
            filterable
            size="small"
          >
            <t-option v-for="kb in kbs" :key="kb.id" :value="kb.id" :label="kb.name" />
          </t-select>
          <t-button
            size="small"
            theme="primary"
            :loading="archiving"
            :disabled="!targetKbId || archiving"
            @click="archive()"
          >
            {{ archiving ? t('halo.archiving') : t('halo.archive') }}
          </t-button>
        </div>
      </template>
    </div>
  </t-dialog>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { MessagePlugin } from 'tdesign-vue-next'

import { archiveHaloReport, fetchHaloReport, type HaloReport } from '@/finance/api/halo'
import { listKnowledgeBases } from '@/api/knowledge-base'
import { createChatMarkdownRenderer, renderChatMarkdown } from '@/utils/chatMarkdownRenderer'
import { sanitizeMarkdownHTML, safeMarkdownToHTML } from '@/utils/security'

/**
 * HALO 年报报告面板。
 *
 * 与聊天里的工具卡片是两条路：那条是 agent 决定调 halo.analyze 后展示工具结果，
 * 这条是用户主动打开、按当前标的取报告并归档。两者读的是同一份 /halo/score 产出，
 * 所以报告内容不会出现「聊天里一个样、面板里另一个样」。
 *
 * 归档目标知识库由用户选，不写死：知识库是租户数据，组件里硬编码一个 ID 会让
 * 这段代码只能在某一台机器上工作。
 */
const props = defineProps<{
  thscode: string
}>()

const visible = defineModel<boolean>('visible', { required: true })

const { t } = useI18n()

const markdownRenderer = createChatMarkdownRenderer()

const report = ref<HaloReport | null>(null)
const markdownHtml = ref('')
const markdownEl = ref<HTMLElement | null>(null)
const loading = ref(false)
const loadError = ref('')

const kbs = ref<Array<{ id: string; name: string }>>([])
const kbsLoading = ref(false)
const targetKbId = ref('')
const archiving = ref(false)

function renderReport(md: string) {
  markdownHtml.value = md.trim()
    ? renderChatMarkdown(md, {
        renderer: markdownRenderer,
        escapeMarkdown: safeMarkdownToHTML,
        sanitizeHtml: sanitizeMarkdownHTML,
      })
    : ''
}

async function load() {
  if (!props.thscode) return
  loading.value = true
  loadError.value = ''
  report.value = null
  markdownHtml.value = ''
  try {
    const data = await fetchHaloReport({ thscode: props.thscode })
    report.value = data
    // 没数据时服务端不给 markdown（早返回路径除外），不必渲染。
    if (data?.ok && data.markdown) renderReport(data.markdown)
    await nextTick()
  } catch (err) {
    // 链路失败与「没数据」必须分开显示：前者要重试，后者要先去同步年报。
    loadError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

/**
 * 拉可选的知识库列表。
 *
 * 响应形状在不同端点上不完全一致（数组 / {data: []} / {data: {items: []}}），
 * 这里按三种都兼容 —— 归档目标选错比少一个下拉项严重得多，所以宁可宽一点。
 */
async function loadKbs() {
  if (kbs.value.length > 0) return
  kbsLoading.value = true
  try {
    const res = (await listKnowledgeBases()) as unknown
    const unwrapped =
      (res as { data?: unknown })?.data !== undefined ? (res as { data?: unknown }).data : res
    const items = Array.isArray(unwrapped)
      ? unwrapped
      : ((unwrapped as { items?: unknown[] })?.items ??
         (unwrapped as { knowledge_bases?: unknown[] })?.knowledge_bases ??
         [])
    kbs.value = (items as Array<Record<string, unknown>>)
      .filter((kb) => kb && typeof kb.id === 'string')
      .map((kb) => ({ id: String(kb.id), name: String(kb.name ?? kb.id) }))
  } catch {
    kbs.value = []
  } finally {
    kbsLoading.value = false
  }
}

async function archive() {
  if (!targetKbId.value) {
    MessagePlugin.warning(t('halo.needKb'))
    return
  }
  archiving.value = true
  try {
    const result = await archiveHaloReport(targetKbId.value, { thscode: props.thscode })
    // 幂等：重复归档是原地更新，提示语必须说清是哪一种，否则用户会以为多了
    // 一份文档。
    const msg = result.action === 'updated' ? t('halo.archiveUpdated') : t('halo.archiveOk')
    MessagePlugin.success(msg)
  } catch (err) {
    MessagePlugin.error(
      `${t('halo.archiveFailed')}：${err instanceof Error ? err.message : String(err)}`,
    )
  } finally {
    archiving.value = false
  }
}

// 打开时取报告；换标的重取。关闭不清理 —— 下次打开同一个标的时能立刻显示上次
// 的结果，而 load() 里会先清空再取，不会显示上一个标的的内容。
watch(
  () => [visible.value, props.thscode] as const,
  ([open, code], prev) => {
    if (!open) return
    const codeChanged = prev && prev[1] !== code
    if (report.value && !codeChanged && report.value.thscode === code) return
    void loadKbs()
    void load()
  },
  { immediate: true },
)
</script>

<style scoped>
.halo-report {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 72vh;
  overflow: auto;
}

.halo-report__head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.halo-report__symbol {
  font-weight: 600;
  font-size: var(--app-text-lg);
}

.halo-report__meta {
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

.halo-report__spacer {
  flex: 1;
}

.halo-report__state {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 24px 8px;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-md);
}

.halo-report__state.is-error {
  color: var(--td-error-color);
}

.halo-report__state-title {
  margin: 0;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.halo-report__state-detail {
  margin: 0;
  font-size: var(--app-text-sm);
}

.halo-report__note {
  margin: 0;
  padding: 8px 10px;
  border-radius: var(--app-radius-xs);
  background: var(--td-bg-color-container-hover);
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

.halo-report__body {
  font-size: var(--app-text-md);
  line-height: 1.7;
  overflow-x: auto;
}

.halo-report__body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--app-text-sm);
}

.halo-report__body :deep(th),
.halo-report__body :deep(td) {
  border: 1px solid var(--td-component-stroke);
  padding: 4px 8px;
}

.halo-report__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--td-component-stroke);
}

.halo-report__pick-label {
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

.halo-report__pick {
  width: 280px;
}
</style>
