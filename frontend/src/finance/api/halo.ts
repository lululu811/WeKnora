// HALO 报告（年报事实链路）的前端入口。
//
// 两条链路都走 **Go** `/api/v1/halo/*`，与 kline 那批浏览器直连 python-service
// 的端点不同：python-service 的 /halo/* 全部要求 API key（require_api_key），
// 那个 key 不能下发到浏览器，所以必须由 Go 侧代理。
//
// 好在不需要为它加 nginx 或 vite 规则：通用的 `location /api/` 本来就打到 Go
// 应用，vite 也有泛化的 '/api' 规则。这与 /api/kline 那批正好相反 —— 那批是
// 只读无鉴权、要绕开 Go 直连 python-service，所以必须在两处白名单里各写一遍。
import { post } from '@/utils/request'

export interface HaloReportRequest {
  thscode: string
  period?: string
  report_type?: string
  scope?: string
  /** 未传时服务端按 true 处理（面板要展示完整报告）。 */
  include_announcements?: boolean
}

// ─────────────────── 结构化字段（2026-10-03 新增）───────────────────
//
// 下面这些类型对应 `/halo/score` 返回体里**除 markdown 以外**的部分。它们之前
// 就一直在返回里（`[key: string]: unknown` 一路透传），只是没人给它们写类型 —
//
// 因为面板当时只渲染 markdown，而 markdown 对七个定性维度只吐
// `{{xxx_score}}` 槽位，于是面板展示的是一份带内部占位符的骨架。
//
// 有了这组类型，面板可以直接渲染**真实数据**：Python 算出来的 HALO 六维与
// 成长性、每个维度的量化锚点、年报抽取的事实条目。

/** HALO 六维里的一维：`scoring.score_halo` 的 dimensions[k]。 */
export interface HaloDimensionScore {
  /** 参与计算的原始值，已按 unit 归一（percent → 小数）。 */
  raw: number | null
  /** 展示单位：`%` / `万元/人` / `倍` / `亿`。 */
  unit: string
  /** 权重，0–1。 */
  weight: number
  /** 该维得分，HALO 六维是 5 分制。 */
  score: number
  /** 算分依据的人话说明（如「固定资产 12.30 亿 / 营业收入 45.60 亿」）。 */
  note?: string
}

/** `halo` 字段：Python 算的六维综合分。算不出来时 `ok: false` + `reason`。 */
export interface HaloScoreBlock {
  ok: boolean
  /** 5 分制总分。`ok: false` 时不存在。 */
  score?: number
  rating?: string
  asset_type?: string
  reason?: string
  dimensions?: Record<string, HaloDimensionScore>
}

/** `growth` 字段：10 分制成长性。数据源缺失时整体为 null。 */
export interface HaloGrowthBlock {
  score: number
  rating?: string
  sub_scores?: Record<string, { raw: number | null; unit: string; weight: number; score: number }>
  missing?: string[]
  complete?: boolean
}

/**
 * `ai_slots` 的一项：一个待判分的定性维度及其量化锚点。
 *
 * `score` 恒为 null —— 这个端点不判分，判分是调用方（agent）拿到骨架后做的事，
 * `/halo/report` 这条链路上没有任何代码会填。所以面板只能显示锚点，不能显示
 * 这个分数。
 */
export interface HaloAiSlot {
  /** 维度键：moat / stag / esg / management / shareholder / valuation / risk。 */
  dimension: string
  /** 中文标签，由 Python 侧的 AI_DIMENSIONS 给出（护城河 / ESG / …）。 */
  label: string
  /** 判分时可用的量化锚点。空对象 = 一个锚点都没取到。 */
  anchors: Record<string, unknown>
  /** 取不到的锚点名。区分「有锚点没判分」与「没锚点」。 */
  missing_anchors: string[]
  /** 量化锚点是否齐全（`missing_anchors` 为空）。 */
  has_anchor: boolean
  score: number | null
}

/** `facts` 的一项：年报原文抽取的事实，带来源页码。 */
export interface HaloFact {
  field: string
  value: number | null
  /** 已经是人类可读文本时优先用它（枚举/文本类字段）。 */
  value_text?: string
  unit?: string
  source_page?: number
  /** 年报原文片段，人工复核时用。 */
  raw_text?: string
}

/**
 * `/halo/score` 的返回体。
 *
 * 结构化字段（halo / growth / ai_slots / facts）现在有了显式类型，面板直接渲染
 * 它们；`markdown` 保留但降级为「骨架原文」折叠区与归档来源 —— 它对七个定性
 * 维度只有 `{{xxx_score}}` 槽位，不适合直接给人看。
 *
 * `[key: string]: unknown` 保留：`narratives` / `environment_disclosure` /
 * `asset_type_signals` 等字段目前面板不用，透传即可，不必在这里逐一建模。
 */
export interface HaloReport {
  ok: boolean
  reason?: string
  thscode?: string
  period?: string | null
  report_type?: string
  scope?: string
  asset_type?: string | null
  asset_type_basis?: string
  halo?: HaloScoreBlock
  growth?: HaloGrowthBlock | null
  ai_slots?: HaloAiSlot[]
  facts?: HaloFact[]
  /** 预渲染的**骨架**（服务端 render_markdown 的产出，含未填的 `{{}}` 槽位）。 */
  markdown?: string
  announcements?: Array<{ title?: string; date?: string; doc_type?: string }>
  [key: string]: unknown
}

export interface HaloArchiveResult {
  knowledge_id: string
  title: string
  /** created = 首次归档；updated = 同一 (标的, 报告期) 的原地更新。 */
  action: 'created' | 'updated'
  status: string
  thscode: string
  period: string
  note: string
}

/**
 * 取报告（只读，不落库）。
 *
 * **「没数据」不是错误**：服务端对 ok=false 仍返回 200，调用方要用 `ok` 判断并
 * 显示「先同步年报」。这条与归档端点刻意不同 —— 归档要写库，所以它必须拒绝。
 */
export async function fetchHaloReport(req: HaloReportRequest): Promise<HaloReport> {
  const res = await post<{ success: boolean; data: HaloReport }>('/api/v1/halo/report', req)
  return res.data
}

/** 归档到知识库。同一 (标的, 报告期) 重复归档为原地更新，不产生副本。 */
export async function archiveHaloReport(
  knowledgeBaseId: string,
  req: HaloReportRequest & { publish?: boolean },
): Promise<HaloArchiveResult> {
  const res = await post<{ success: boolean; data: HaloArchiveResult }>('/api/v1/halo/archive', {
    ...req,
    knowledge_base_id: knowledgeBaseId,
  })
  return res.data
}

export interface HaloSyncRequest {
  thscode: string
  report_type?: string
  /**
   * 强制重跑，忽略缓存。默认 false —— 年报一年只变一次，缓存键
   * (thscode, report_type, year)，日常同步几乎必然命中。
   */
  force?: boolean
}

export interface HaloSyncResult {
  ok: boolean
  reason?: string
  thscode?: string
  report_type?: string
  /** 事实状态统计：verified / disputed / pending 的计数。 */
  status_summary?: Record<string, number>
}

/**
 * 同步专用超时。
 *
 * axios 实例默认只有 30 秒（`utils/request.ts`），而一次同步是分钟级 —— 不覆盖
 * 就会**必然**超时，用户永远看不到服务端真正的结果。
 *
 * 取 11 分钟而不是 10：Go 侧 `syncTimeout` 是 10 分钟，浏览器要留出余量，
 * 这样服务端超时返回的是带原因的 AppError，而不是一句 axios 的
 * "timeout of NNN000ms exceeded" —— 后者对用户毫无信息量。
 */
const SYNC_TIMEOUT_MS = 11 * 60 * 1000

/**
 * 同步巨潮年报并把事实落库。
 *
 * **慢**：要下载 PDF（单份 1–10 MB）并逐页解析，一份年报约 1–3 分钟。调用方
 * 必须给足超时并显示 loading，不能当成普通查询。
 *
 * 走 Go 代理而非浏览器直连 python-service：后者的 /halo/* 全部要求 API key，
 * 那个 key 不能下发到浏览器（见本文件头部注释）。
 *
 * **副作用**：会向巨潮发起外部请求并落盘。服务端对同一标的做互斥（重复触发返回
 * 409，不会并发抓两次），不同标的的并发由全局上限 2 兜住。
 */
export async function syncHaloReport(req: HaloSyncRequest): Promise<HaloSyncResult> {
  const res = await post<{ success: boolean; data: HaloSyncResult }>(
    '/api/v1/halo/sync',
    {
      report_type: 'annual',
      force: false,
      ...req,
    },
    { timeout: SYNC_TIMEOUT_MS },
  )
  return res.data
}
