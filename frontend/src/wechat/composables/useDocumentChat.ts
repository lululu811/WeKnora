import { computed, ref } from 'vue';

import { useStream } from '@/api/chat/streame';
import { createSessions } from '@/api/chat';

/** 一轮问答。references 存的是后端 types.SearchResult 的子集。 */
export interface Turn {
  id: string;
  question: string;
  answer: string;
  references: any[];
  /** 还在流式生成中。渲染器据此决定要不要按「可能截断的半截 Markdown」处理。 */
  done: boolean;
  error?: string;
}

/**
 * 右栏的文档作用域问答。
 *
 * 作用域是**每次请求**带的 `knowledge_ids`，不是绑在 session 上：后端
 * `CreateKnowledgeQARequest.KnowledgeIDs` 每次都会覆盖 `LastRequestState`
 * （`internal/handler/session/qa.go` 把两者合并后交给检索）。所以同一个会话
 * 可以先问这一篇、再把 knowledge_ids 清空问全库 —— 这就是"默认锁定当前
 * 文档、右上角一键扩到全库"要的效果，不需要两套会话。
 */
export function useDocumentChat() {
  const sessionId = ref('');
  const turns = ref<Turn[]>([]);
  const draft = ref('');
  const creating = ref(false);

  const stream = useStream();
  /** 当前正在写的那一轮（流式期间答案由 chunk 累加进来）。 */
  const liveTurn = ref<Turn | null>(null);
  /** true = 只问当前这一篇；false = 问整个知识库。 */
  const scopeToDocument = ref(true);

  const busy = computed(() => stream.isStreaming.value || creating.value);

  async function ensureSession(): Promise<string> {
    if (sessionId.value) return sessionId.value;
    creating.value = true;
    try {
      const res: any = await createSessions({
        agent_config: { enabled: false, knowledge_bases: [] },
      } as any);
      const id = res?.data?.id || res?.id;
      if (!id) throw new Error('创建会话未返回 id');
      sessionId.value = id;
      return id;
    } finally {
      creating.value = false;
    }
  }

  /** 切换文章时丢弃上一轮的流式状态；会话本身留着，可以继续追问同一篇。 */
  function resetLive() {
    liveTurn.value = null;
    stream.stopStream();
  }

  function clearTurns() {
    resetLive();
    turns.value = [];
  }

  async function ask(text: string, opts: { kbId: string; knowledgeId?: string }) {
    const question = text.trim();
    if (!question || busy.value) return;

    const sid = await ensureSession();
    const turn: Turn = { id: `t${Date.now()}`, question, answer: '', references: [], done: false };
    liveTurn.value = turn;

    stream.onChunk((data: any) => {
      switch (data?.response_type) {
        case 'answer':
          turn.answer += data.content || '';
          break;
        case 'references': {
          const refs =
            data.knowledge_references || data.data?.references || data.data?.knowledge_references;
          if (Array.isArray(refs)) turn.references = refs;
          break;
        }
        case 'error':
          turn.error = data?.data?.error || data?.error || '生成失败';
          turn.done = true;
          turns.value = [...turns.value, turn];
          liveTurn.value = null;
          break;
        case 'complete':
        case 'stop':
          turn.done = true;
          turns.value = [...turns.value, turn];
          liveTurn.value = null;
          break;
        default:
          break;
      }
    });

    // 作用域怎么表达，是个反直觉的语义：
    //
    // 后端 session_knowledge_qa.go 在组装检索目标时写着
    //   「Skip if this KB is already fully searched without a tag scope」
    //   if fullKBSet[k.KnowledgeBaseID] && len(tagIDsByKB[...]) == 0 { continue }
    // 也就是**同时**传 knowledge_base_ids 和属于同一个 KB 的 knowledge_ids 时，
    // 整库目标优先，文档级过滤被静默丢弃 —— 不报错，只是搜了全库。
    //
    // 所以锁定单篇时必须**只传 knowledge_ids、完全不传 knowledge_base_ids**
    // （qa.go:881 的校验允许 knowledge_ids 单独存在）。
    const scopedToDoc = scopeToDocument.value && !!opts.knowledgeId;

    await stream.startStream({
      session_id: sid,
      query: question,
      knowledge_base_ids: scopedToDoc ? [] : opts.kbId ? [opts.kbId] : [],
      knowledge_ids: scopedToDoc ? [opts.knowledgeId!] : undefined,
      // 关掉 agent：这一栏要做的是"就着这篇文章问答"，不是"让 agent 去做事"。
      agent_enabled: false,
      method: 'POST',
      // 只给基路径。useStream 内部会拼 `/${session_id}`（streame.ts:83-86），
      // 这里再带一次就成了 /knowledge-chat/{sid}/{sid}，直接 404。
      url: '/api/v1/knowledge-chat',
    });
  }

  return {
    sessionId,
    turns,
    liveTurn,
    draft,
    busy,
    scopeToDocument,
    creating,
    error: stream.error,
    ask,
    clearTurns,
    resetLive,
  };
}
