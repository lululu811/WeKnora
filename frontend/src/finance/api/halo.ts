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

/**
 * `/halo/score` 的返回体。
 *
 * 只列出面板用到的字段，其余原样透传 —— 服务端返回的是完整分析结果（含
 * ai_slots / facts / narratives），面板按需取用即可，不必在前端再复制一份
 * 类型定义。
 */
export interface HaloReport {
  ok: boolean
  reason?: string
  thscode?: string
  period?: string | null
  report_type?: string
  scope?: string
  asset_type?: string | null
  /** 预渲染的报告骨架（服务端 render_markdown 的产出）。 */
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
