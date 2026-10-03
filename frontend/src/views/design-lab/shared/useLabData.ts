// useLabData — real-data loader for design-lab direction samples.
//
// Fetches, via the app's existing API modules (no stores required — the
// request layer reads auth from localStorage):
//   a) the message list of a fixed rich session (LAB_SESSION_ID)
//   b) the recent session list (+ one-line last-message preview per session)
//   c) the knowledge-base list
//   d) the agent list
// Each source fails independently: one failing endpoint never blanks the rest.
import { ref } from 'vue'
import { getMessageList, getSessionsList, getSession } from '@/api/chat/index'
import { listKnowledgeBases } from '@/api/knowledge-base'
import { listAgents } from '@/api/agent'

/** 内置的富文本股票分析会话，三个方向的小样共用这份真实数据。 */
export const LAB_SESSION_ID = '46a1fabf-21d0-4f76-93ff-bb55b22fcf9a'

export interface LabMessage {
  id: string
  role: string
  content: string
  created_at?: string
  is_completed?: boolean
  knowledge_references?: any[]
}

export interface LabRecentSession {
  id: string
  title: string
  updated_at?: string
  preview: string
}

export interface LabNamedItem {
  id: string
  name: string
  description?: string
}

/** 硬编码中文相对时间（小样不走 i18n）。 */
export function labRelativeTime(iso?: string): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 2 * 86_400_000) return '昨天'
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`
  const d = new Date(t)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

function firstLine(text: string, max = 60): string {
  const flat = (text || '').replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max)}…` : flat
}

export function useLabData() {
  const messages = ref<LabMessage[]>([])
  const messagesLoading = ref(false)
  const messagesError = ref('')
  const sessionTitle = ref('')

  const recentSessions = ref<LabRecentSession[]>([])
  const sessionsLoading = ref(false)
  const sessionsError = ref('')

  const knowledgeBases = ref<LabNamedItem[]>([])
  const agents = ref<LabNamedItem[]>([])

  const loadMessages = async () => {
    messagesLoading.value = true
    messagesError.value = ''
    try {
      const res: any = await getMessageList({ session_id: LAB_SESSION_ID, limit: 50, created_at: '' })
      const batch: LabMessage[] = Array.isArray(res?.data) ? res.data : []
      // 服务端按时间倒序返回；展示统一按 created_at 升序。
      messages.value = [...batch].sort((a, b) =>
        String(a.created_at || '').localeCompare(String(b.created_at || '')),
      )
      const sessionRes: any = await getSession(LAB_SESSION_ID).catch(() => null)
      sessionTitle.value = sessionRes?.data?.title || ''
    } catch (e: any) {
      messagesError.value = e?.message || '加载失败'
    } finally {
      messagesLoading.value = false
    }
  }

  const loadRecentSessions = async () => {
    sessionsLoading.value = true
    sessionsError.value = ''
    try {
      const res: any = await getSessionsList(1, 3)
      const rows: any[] = Array.isArray(res?.data) ? res.data : []
      const previews = await Promise.all(
        rows.map((row) =>
          getMessageList({ session_id: row.id, limit: 1, created_at: '' })
            .then((r: any) => firstLine(r?.data?.[0]?.content || ''))
            .catch(() => ''),
        ),
      )
      recentSessions.value = rows.map((row, i) => ({
        id: row.id,
        title: row.title || '未命名会话',
        updated_at: row.updated_at || row.created_at,
        preview: previews[i],
      }))
    } catch (e: any) {
      sessionsError.value = e?.message || '加载失败'
    } finally {
      sessionsLoading.value = false
    }
  }

  const loadKnowledgeBases = async () => {
    try {
      const res: any = await listKnowledgeBases()
      const rows: any[] = Array.isArray(res?.data) ? res.data : []
      knowledgeBases.value = rows.map((kb) => ({ id: kb.id, name: kb.name, description: kb.description }))
    } catch {
      knowledgeBases.value = []
    }
  }

  const loadAgents = async () => {
    try {
      const res: any = await listAgents()
      const rows: any[] = Array.isArray(res?.data) ? res.data : []
      agents.value = rows.map((a) => ({ id: a.id, name: a.name, description: a.description }))
    } catch {
      agents.value = []
    }
  }

  const loadAll = () => {
    void loadMessages()
    void loadRecentSessions()
    void loadKnowledgeBases()
    void loadAgents()
  }

  return {
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
    reloadMessages: loadMessages,
  }
}
