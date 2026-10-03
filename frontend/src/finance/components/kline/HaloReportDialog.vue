<template>
  <t-dialog
    v-model:visible="visible"
    :header="t('halo.title')"
    :footer="false"
    width="960px"
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
          {{ t('halo.assetType') }}：{{ assetTypeLabel }}
        </span>
        <span class="halo-report__spacer" />
        <t-button
          size="small"
          variant="text"
          :disabled="loading || syncing"
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
        <t-button size="small" variant="outline" @click="load()">{{ t('halo.retry') }}</t-button>
      </div>

      <!-- 没数据：不是错误。给同步按钮，而不是让人退回聊天框找 agent。 -->
      <div v-else-if="report && !report.ok" class="halo-report__state">
        <p class="halo-report__state-title">{{ t('halo.noDataTitle') }}</p>
        <p class="halo-report__state-detail">{{ report.reason || '' }}</p>
        <p class="halo-report__state-detail">{{ t('halo.noDataHint') }}</p>
        <p class="halo-report__state-detail is-warn">{{ t('halo.syncCostHint') }}</p>
        <t-button
          size="small"
          theme="primary"
          :loading="syncing"
          :disabled="syncing"
          @click="syncAndReload()"
        >
          {{ syncing ? t('halo.syncing') : t('halo.syncAndRetry') }}
        </t-button>
      </div>

      <!-- 报告正文 -->
      <template v-else-if="report">
        <!-- 状态说明条。必须放在最上面：读者第一眼要知道这份报告是骨架，
             而不是以为每个维度都有结论。 -->
        <p class="halo-report__note">
          <t-icon name="info-circle" size="14px" />
          {{ t('halo.skeletonNotice') }}
        </p>

        <!-- 免责声明说的正是「这一步要交给 agent」。面板手里已经有标的和锚点，
             与其把这句话留给用户自己去执行，不如直接给一个入口。 -->
        <div class="halo-sec__sync">
          <t-button size="small" variant="outline" @click="launchFullReport()">
            <template #icon><t-icon name="chat" /></template>
            {{ t('halo.launchFullReport') }}
          </t-button>
        </div>

        <!-- 核心评分 -->
        <section class="halo-sec">
          <h4 class="halo-sec__h">{{ t('halo.scoreCard') }}</h4>
          <div class="halo-scores">
            <div class="halo-score" :class="haloOk ? haloScoreClass : 'is-none'">
              <span class="halo-score__k">{{ t('halo.haloSix') }}</span>
              <span class="halo-score__v">
                {{ haloOk ? `${report.halo!.score!.toFixed(2)}/5.0` : '—' }}
              </span>
              <span class="halo-score__r">{{ report.halo?.rating || t('halo.notComputable') }}</span>
            </div>
            <div class="halo-score" :class="growthOk ? growthScoreClass : 'is-none'">
              <span class="halo-score__k">{{ t('halo.growthLabel') }}</span>
              <span class="halo-score__v">
                {{ growthOk ? `${report.growth!.score.toFixed(2)}/10` : '—' }}
              </span>
              <span class="halo-score__r">{{ report.growth?.rating || t('halo.notComputable') }}</span>
            </div>
          </div>
          <p v-if="!haloOk && report.halo?.reason" class="halo-sec__hint is-warn">
            {{ report.halo.reason }}
          </p>
          <p v-if="!growthOk && report.growth?.missing?.length" class="halo-sec__hint is-warn">
            {{ t('halo.growthMissing', { keys: report.growth.missing.join('、') }) }}
          </p>

          <!-- 部分不可计算时的同步入口。
               原来「同步年报并重试」只挂在 report.ok === false 那一个分支上
               （整份报告全空）。但更常见的是报告能出、只是某个分项缺输入——
               六维要 fixed_assets / inventory / employees 这类年报事实，
               没同步过就永远是「不可计算」。那种情况下 ok 仍是 true，
               界面上一个可点的入口都没有，人只能退回聊天框找 agent，
               恰好违背了上面那句注释的意图。 -->
          <div v-if="needsSync" class="halo-sec__sync">
            <p class="halo-sec__hint is-warn">{{ t('halo.syncCostHint') }}</p>
            <t-button size="small" theme="primary" :loading="syncing" :disabled="syncing" @click="syncAndReload()">
              {{ syncing ? t('halo.syncing') : t('halo.syncAndRetry') }}
            </t-button>
          </div>
        </section>

        <!-- HALO 六维明细 -->
        <section v-if="haloDims.length" class="halo-sec">
          <h4 class="halo-sec__h">{{ t('halo.haloSixDetail') }}</h4>
          <table class="halo-table">
            <thead>
              <tr>
                <th>{{ t('halo.dim') }}</th>
                <th class="num">{{ t('halo.raw') }}</th>
                <th class="num">{{ t('halo.weight') }}</th>
                <th class="num">{{ t('halo.score') }}</th>
                <th>{{ t('halo.basis') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in haloDims" :key="d.key">
                <td>{{ dimLabel(d.key) }}</td>
                <td class="num mono">{{ d.rawText }}</td>
                <td class="num mono">{{ (d.weight * 100).toFixed(0) }}%</td>
                <td class="num mono">{{ d.score.toFixed(2) }}</td>
                <td class="muted">{{ d.note || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <!-- 成长性子项 -->
        <section v-if="growthSubs.length" class="halo-sec">
          <h4 class="halo-sec__h">{{ t('halo.growthDetail') }}</h4>
          <table class="halo-table">
            <thead>
              <tr>
                <th>{{ t('halo.dim') }}</th>
                <th class="num">{{ t('halo.raw') }}</th>
                <th class="num">{{ t('halo.weight') }}</th>
                <th class="num">{{ t('halo.score') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(g, key) in growthSubs" :key="key">
                <td>{{ growthSubLabel(g.key) }}</td>
                <td class="num mono">{{ fmtAnchor(g.raw) }}{{ g.unit }}</td>
                <td class="num mono">{{ (g.weight * 100).toFixed(0) }}%</td>
                <td class="num mono">{{ g.score.toFixed(2) }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <!-- 七个定性维度：只有锚点，没有分数。这是骨架的本质，如实呈现。 -->
        <section v-if="slots.length" class="halo-sec">
          <h4 class="halo-sec__h">{{ t('halo.qualitativeDims') }}</h4>
          <p class="halo-sec__hint">{{ t('halo.qualitativeHint') }}</p>
          <div class="halo-slots">
            <div v-for="s in slots" :key="s.dimension" class="halo-slot">
              <div class="halo-slot__head">
                <span class="halo-slot__label">{{ s.label }}</span>
                <span class="halo-badge" :class="s.has_anchor ? 'is-ok' : 'is-warn'">
                  {{ s.has_anchor ? t('halo.hasAnchor') : t('halo.noAnchor') }}
                </span>
                <span class="halo-badge is-pending">{{ t('halo.pendingScore') }}</span>
              </div>
              <div v-if="s.has_anchor" class="halo-slot__anchors">
                <span class="halo-slot__ak">{{ t('halo.anchors') }}</span>
                <ul>
                  <li v-for="(v, k) in s.anchors" :key="k">
                    <code>{{ k }}</code>
                    <span class="mono">{{ fmtAnchor(v) }}</span>
                  </li>
                </ul>
              </div>
              <p v-else class="halo-slot__none">
                {{ t('halo.noAnchors', { keys: s.missing_anchors.join('、') }) }}
              </p>
            </div>
          </div>
        </section>

        <!-- 年报事实 -->
        <section v-if="facts.length" class="halo-sec">
          <h4 class="halo-sec__h">{{ t('halo.facts') }}</h4>
          <table class="halo-table">
            <thead>
              <tr>
                <th>{{ t('halo.dim') }}</th>
                <th>{{ t('halo.value') }}</th>
                <th class="num">{{ t('halo.page') }}</th>
                <th>{{ t('halo.sourceText') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in facts" :key="f.field">
                <td>{{ f.field }}</td>
                <td class="mono">{{ factValue(f) }}</td>
                <td class="num">{{ f.source_page ? `p${f.source_page}` : '—' }}</td>
                <td class="muted">{{ f.raw_text || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <!-- 公告 -->
        <section v-if="report.announcements?.length" class="halo-sec">
          <h4 class="halo-sec__h">{{ t('halo.announcements') }}</h4>
          <ul class="halo-ann">
            <li v-for="(a, i) in report.announcements" :key="i">
              <span class="halo-ann__date">{{ a.date || '—' }}</span>
              <span class="halo-ann__type">{{ a.doc_type || '' }}</span>
              <span>{{ a.title || '' }}</span>
            </li>
          </ul>
        </section>

        <!-- 骨架原文。归档走的就是这份内容，所以要能核对，但默认收起。 -->
        <section v-if="maskedMarkdown" class="halo-sec">
          <t-collapse :value="rawOpen" :borderless="true">
            <t-collapse-panel :value="true" :header="t('halo.rawMarkdown')">
              <p class="halo-sec__hint">{{ t('halo.rawMarkdownHint') }}</p>
              <!-- eslint-disable-next-line vue/no-v-html -- 经 renderChatMarkdown + sanitizeMarkdownHTML -->
              <div class="halo-md" v-html="maskedMarkdownHtml" />
            </t-collapse-panel>
          </t-collapse>
        </section>

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
import { computed, ref, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'

import {
  archiveHaloReport,
  fetchHaloReport,
  syncHaloReport,
  type HaloAiSlot,
  type HaloDimensionScore,
  type HaloFact,
  type HaloReport,
} from '@/finance/api/halo'
import { listKnowledgeBases } from '@/api/knowledge-base'
import { hasHaloSlots, maskHaloSlots } from '@/finance/utils/haloPlaceholders'
import { createChatMarkdownRenderer, renderChatMarkdown } from '@/utils/chatMarkdownRenderer'
import { sanitizeMarkdownHTML, safeMarkdownToHTML } from '@/utils/security'

/**
 * HALO 年报报告面板。
 *
 * **为什么是结构化渲染而不是渲染 markdown**：
 * `/halo/score` 对七个定性维度（护城河/滞胀/ESG/管理层/资金面/估值/风险）只产出
 * `{{xxx_score}}` 槽位 —— 判分是调用方（agent）的事，而这条链路上没有任何代码
 * 会填。所以把 `markdown` 直接渲染给人，看到的是一份带内部占位符的骨架。
 *
 * 面板改为直接消费返回体里的结构化字段：`halo`（六维分与逐维明细）、`growth`
 * （成长性与子项）、`ai_slots`（待判分维度 + 量化锚点）、`facts`（年报事实）。
 * 这些都是 Python 算/抽出来的真数据，不需要模型参与。
 *
 * markdown 保留但降级为折叠的「骨架原文」区 —— 归档落库用的就是它（见
 * `POST /halo/archive`），需要能核对，但不该占主位。
 *
 * 与聊天里的工具卡片是两条路：那条是 agent 判完分后展示结论，这条只呈现数据层
 * 与锚点。两者读的是同一份 /halo/score 产出。
 */
const props = defineProps<{
  thscode: string
  /** 标的名称。有它时生成的提问会带上名字，读起来比裸代码自然。 */
  name?: string
}>()

const visible = defineModel<boolean>('visible', { required: true })

const { t } = useI18n()
const router = useRouter()

/**
 * 一键开出会话，让 agent 跑 halo.analyze 出完整报告。
 *
 * 这个面板按设计只呈现数据层：六个数值维度是 Python 算的，剩七个定性维度
 * 在 /halo/score 这条链路上只产出 `{{xxx_score}}` 槽位，没有代码会填——
 * 判分是 agent 的职责。面板顶部那句免责声明说的就是这件事。
 *
 * 那句话原本只是文字，用户读完之后得自己退回新对话、重打一遍标的、
 * 再说出「halo.analyze」这个内部工具名。既然面板手里已经有标的、报告期
 * 和全部量化锚点，就该由它把这一步接上，而不是把话留给用户。
 *
 * 落点是 /platform/creatChat?q=... —— 与 Watchlist.vue 里
 * `agentWorkspace.sendToChatCallback` 用的是同一条既有通道，没有另造一条。
 */
function launchFullReport() {
  const subject = props.name ? `${props.name} (${props.thscode})` : props.thscode
  const period = report.value?.period
  visible.value = false
  void router.push({
    path: '/platform/creatChat',
    query: {
      q: `请对 ${subject}${period ? ` 的 ${period} 年报` : ''} 执行 halo.analyze，生成完整分析报告。` +
         `我已经看过面板里的六维与成长性评分，这一步要的是护城河/滞胀防御/ESG/管理层/` +
         `股东资金面/估值/风险这七个定性维度的判分与结论。`,
    },
  })
}

const markdownRenderer = createChatMarkdownRenderer()

const report = ref<HaloReport | null>(null)
const markdownHtml = ref('')
const loading = ref(false)
const loadError = ref('')
const syncing = ref(false)
const rawOpen = ref<string[]>([])

const kbs = ref<Array<{ id: string; name: string }>>([])
const kbsLoading = ref(false)
const targetKbId = ref('')
const archiving = ref(false)

// ── 结构化字段的派生视图 ────────────────────────────────────────────

const assetTypeLabel = computed(() => {
  const t0 = report.value?.asset_type
  if (!t0) return ''
  const map: Record<string, string> = { heavy: t('halo.assetHeavy'), mixed: t('halo.assetMixed'), light: t('halo.assetLight') }
  return map[t0] || t0
})

const haloOk = computed(() => report.value?.halo?.ok === true && typeof report.value.halo.score === 'number')
const growthOk = computed(() => typeof report.value?.growth?.score === 'number')

/** 报告能出、但有分项因缺输入而算不出来 —— 需要给出同步入口。 */
const needsSync = computed(() => !!report.value && (!haloOk.value || !growthOk.value))

/** HALO 六维的展示色。阈值与 Python 侧 rating_5 对齐。 */
const haloScoreClass = computed(() => {
  const s = report.value?.halo?.score ?? 0
  if (s >= 3.0) return 'is-strong'
  if (s >= 2.0) return 'is-mid'
  return 'is-weak'
})

const growthScoreClass = computed(() => {
  const s = report.value?.growth?.score ?? 0
  if (s >= 6.5) return 'is-strong'
  if (s >= 5.0) return 'is-mid'
  return 'is-weak'
})

const haloDims = computed(() => {
  const dims = report.value?.halo?.dimensions
  if (!dims) return []
  return Object.entries(dims).map(([key, d]: [string, HaloDimensionScore]) => ({
    key,
    rawText: `${fmtNumber(d.raw)}${d.unit ? ` ${d.unit}` : ''}`,
    weight: d.weight,
    score: d.score,
    note: d.note,
  }))
})

const growthSubs = computed(() => {
  const subs = report.value?.growth?.sub_scores
  if (!subs) return []
  return Object.entries(subs).map(([key, v]) => ({ key, ...v }))
})

const slots = computed<HaloAiSlot[]>(() => report.value?.ai_slots || [])
const facts = computed<HaloFact[]>(() => report.value?.facts || [])

/**
 * 骨架原文：先过 maskHaloSlots。
 *
 * 归档进知识库的就是这份 markdown（handler/halo.go:188），所以知识库里已经躺着
 * 一批带 `{{}}` 的文档；这里至少保证 UI 不会把占位符原样摊给读者。
 */
const maskedMarkdown = computed(() => {
  const md = report.value?.markdown
  return md ? maskHaloSlots(md) : ''
})

/** 骨架里确实还有槽位时才提示 —— 已经填过的报告不该再被说成骨架。 */
const isSkeleton = computed(() => hasHaloSlots(report.value?.markdown || ''))

const maskedMarkdownHtml = computed(() => {
  const md = maskedMarkdown.value
  return md
    ? renderChatMarkdown(md, {
        renderer: markdownRenderer,
        escapeMarkdown: safeMarkdownToHTML,
        sanitizeHtml: sanitizeMarkdownHTML,
      })
    : ''
})

// ── 格式化 ─────────────────────────────────────────────────────────

function fmtNumber(v: unknown): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  if (Number.isInteger(v)) return v.toLocaleString('en-US')
  return v.toFixed(4)
}

/**
 * 锚点值。
 *
 * 锚点里有三种形态：数字、文本、以及嵌套对象（ESG 的 `emissions`、风险的
 * `hard_risk_facts`）。嵌套对象摊成 `k=v` 一行。
 *
 * **不推断单位**：Python 侧把 percent 类锚点除以 100 归一成了小数（0.3253），
 * 但这个「归一契约」没有随 anchors 一起返回，前端若擅自乘回 100 就是在编造。
 * 所以原样显示，语义由维度标签和上面那句说明承担。
 */
function fmtAnchor(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return fmtNumber(v)
  if (typeof v === 'string') return v
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k}=${fmtAnchor(val)}`)
      .join('，')
  }
  return String(v)
}

function factValue(f: HaloFact): string {
  if (f.value_text) return f.value_text
  if (typeof f.value === 'number') return `${f.value.toLocaleString('en-US')}${f.unit ? ` ${f.unit}` : ''}`
  return '—'
}

function dimLabel(key: string): string {
  const map: Record<string, string> = {
    tangible_intensity: t('halo.dims.tangible'),
    fixed_intensity: t('halo.dims.fixedIntensity'),
    fixed_share: t('halo.dims.fixedShare'),
    capital_labor: t('halo.dims.capitalLabor'),
    capex_intensity: t('halo.dims.capexIntensity'),
    capex_burden: t('halo.dims.capexBurden'),
  }
  return map[key] || key
}

function growthSubLabel(key: string): string {
  const map: Record<string, string> = {
    revenue: t('halo.growthSubs.revenue'),
    profit: t('halo.growthSubs.profit'),
    quality: t('halo.growthSubs.quality'),
    sustainability: t('halo.growthSubs.sustainability'),
  }
  return map[key] || key
}

// ── 数据加载 ───────────────────────────────────────────────────────

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
 * 同步年报并重试取报告。
 *
 * `/halo/sync` 会向巨潮下载 PDF 并逐页解析，**一份年报约 1–3 分钟**（见
 * `halo.filing.sync` 的工具描述），所以这里不做「点了就以为好了」的假象：
 * 按钮转 loading 直到请求真正返回。服务端已对同一标的做互斥，重复点不会并发
 * 抓两次。
 */
async function syncAndReload() {
  if (!props.thscode) return
  syncing.value = true
  try {
    const res = await syncHaloReport({ thscode: props.thscode })
    // 抓取本身成功、但事实没落库（对账存疑/抽取为空）时，ok 仍是 false。
    // 这种情况不弹「同步成功」，而是回到 load() 由 noData 分支如实说明。
    if (res.ok) MessagePlugin.success(t('halo.syncDone'))
    await load()
  } catch (err) {
    MessagePlugin.error(`${t('halo.syncFailed')}：${err instanceof Error ? err.message : String(err)}`)
  } finally {
    syncing.value = false
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
  gap: 16px;
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

.halo-report__symbol { font-weight: 600; font-size: var(--app-text-lg); }
.halo-report__meta { font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }
.halo-report__spacer { flex: 1; }

.halo-report__state {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 24px 8px;
  color: var(--td-text-color-secondary);
  font-size: var(--app-text-md);
}
.halo-report__state.is-error { color: var(--td-error-color); }
.halo-report__state.is-warn { color: var(--td-warning-color); }
.halo-report__state-title { margin: 0; font-weight: 600; color: var(--td-text-color-primary); }
.halo-report__state-detail { margin: 0; font-size: var(--app-text-sm); }

.halo-report__note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 8px 10px;
  border-radius: var(--app-radius-xs);
  background: var(--td-bg-color-container-hover);
  font-size: var(--app-text-sm);
  color: var(--td-text-color-secondary);
}

/* ── 分节 ── */
.halo-sec { display: flex; flex-direction: column; gap: 8px; }
.halo-sec__h { margin: 0; font-size: var(--app-text-md); font-weight: 600; }
.halo-sec__hint { margin: 0; font-size: var(--app-text-sm); color: var(--td-text-color-secondary); line-height: 1.6; }
.halo-sec__hint.is-warn { color: var(--td-warning-color); }
/* 部分不可计算时的同步入口：文案在上、按钮在下，和整份全空那处的排布一致 */
.halo-sec__sync { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; margin-top: 2px; }

/* ── 核心评分卡 ── */
.halo-scores { display: flex; gap: 12px; flex-wrap: wrap; }
.halo-score {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 150px;
  padding: 10px 14px;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-sm);
}
.halo-score.is-none { opacity: 0.6; }
.halo-score__k { font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }
.halo-score__v { font-size: var(--app-text-xl); font-weight: 600; font-family: monospace; }
.halo-score__r { font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }
.halo-score.is-strong .halo-score__v { color: var(--td-error-color); }
.halo-score.is-mid .halo-score__v { color: var(--td-warning-color); }
.halo-score.is-weak .halo-score__v { color: var(--td-text-color-placeholder); }

/* ── 表格 ── */
.halo-table { width: 100%; border-collapse: collapse; font-size: var(--app-text-sm); }
.halo-table th, .halo-table td { border: 1px solid var(--td-component-stroke); padding: 5px 8px; text-align: left; }
.halo-table th { background: var(--td-bg-color-container-hover); font-weight: 600; }
.halo-table .num { text-align: right; }
.halo-table .mono { font-family: monospace; }
.halo-table .muted { color: var(--td-text-color-secondary); }

/* ── 待判分维度 ── */
.halo-slots { display: flex; flex-direction: column; gap: 8px; }
.halo-slot {
  padding: 8px 10px;
  border: 1px solid var(--td-component-stroke);
  border-radius: var(--app-radius-sm);
}
.halo-slot__head { display: flex; align-items: center; gap: 8px; }
.halo-slot__label { font-weight: 600; }
.halo-slot__anchors { margin-top: 6px; }
.halo-slot__ak { font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }
.halo-slot__anchors ul { margin: 4px 0 0; padding-left: 18px; }
.halo-slot__anchors li { display: flex; gap: 8px; font-size: var(--app-text-sm); }
.halo-slot__anchors code { color: var(--td-brand-color); }
.halo-slot__none { margin: 6px 0 0; font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }

.halo-badge {
  font-size: var(--app-text-xs);
  padding: 1px 6px;
  border-radius: var(--app-radius-xs);
  background: rgba(0, 0, 0, 0.05);
  font-weight: 600;
}
.halo-badge.is-ok { color: var(--td-success-color); }
.halo-badge.is-warn { color: var(--td-warning-color); }
.halo-badge.is-pending { color: var(--td-text-color-secondary); }

/* ── 公告 ── */
.halo-ann { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; font-size: var(--app-text-sm); }
.halo-ann li { display: flex; gap: 8px; align-items: baseline; }
.halo-ann__date { font-family: monospace; color: var(--td-text-color-secondary); flex: none; }
.halo-ann__type { color: var(--td-text-color-placeholder); flex: none; }

/* ── 骨架原文 ── */
.halo-md { font-size: var(--app-text-sm); line-height: 1.7; overflow-x: auto; }
.halo-md :deep(table) { width: 100%; border-collapse: collapse; font-size: var(--app-text-sm); }
.halo-md :deep(th), .halo-md :deep(td) { border: 1px solid var(--td-component-stroke); padding: 4px 8px; }

.halo-report__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--td-component-stroke);
}
.halo-report__pick-label { font-size: var(--app-text-sm); color: var(--td-text-color-secondary); }
.halo-report__pick { width: 280px; }
</style>
