/**
 * KnowledgeBaseList 拆分后的共享类型与纯函数。
 *
 * 这些定义原先内联在 KnowledgeBaseList.vue 的 <script setup> 里，抽出后
 * 供 list/KBCard.vue 与 list/KBListHeader.vue 复用。全部是纯类型 / 纯函数，
 * 不含响应式状态、不含 store 依赖，因此可以安全地被多个组件 import。
 */

/** 知识库主体。字段与后端 list 接口返回一一对应。 */
export interface KB {
  id: string;
  name: string;
  description?: string;
  updated_at?: string;
  created_at?: string;
  pinned_at?: string;
  embedding_model_id?: string;
  summary_model_id?: string;
  type?: 'document' | 'faq';
  showMore?: boolean;
  vlm_config?: { enabled?: boolean; model_id?: string };
  extract_config?: { enabled?: boolean };
  storage_provider_config?: { provider?: string };
  storage_config?: { provider?: string; bucket_name?: string }; // legacy
  question_generation_config?: { enabled?: boolean; question_count?: number };
  knowledge_count?: number;
  chunk_count?: number;
  isProcessing?: boolean;
  processing_count?: number;
  share_count?: number;
  is_pinned?: boolean;
  // creator_id is the owner-id matched against authStore.user.id when
  // gating the per-card more-menu (Settings / Delete). Empty for legacy
  // KBs created before PR 5; those fall back to the role gate.
  creator_id?: string;
  // creator_name 由后端 list 接口回填，仅用于卡片右下角来源徽章的 tooltip。
  creator_name?: string;
}

/** 列表分组的折叠态 key。与模板里各分组标题一一对应。 */
export type KbSectionKey =
  | 'pinned'
  | 'mine'
  | 'tenantOthers'
  | 'sharedByMe'
  | 'sharedEditable'
  | 'sharedReadonly'

/** 共享知识库在「全部」聚合视图里的形态：后端 merge 之后仍是扁平 KB 对象。 */
export interface SharedKbCardItem extends KB {
  /** 聚合视图里由共享来源带下来的权限，用于决定分组与可编辑性。 */
  permission?: string;
  /** 共享来源组织名，显示在卡片右下角。 */
  org_name?: string;
  source_from_agent?: { agent_id?: string; agent_name?: string };
}

/**
 * KBCard 的呈现模式。
 *
 * 拆分前同一张卡片模板在 4 个 v-for 分支里各写了一遍，彼此只有若干细节差异
 * （是否收藏星、计数兜底值、feature badge 组合、来源徽章、更多菜单是否受控）。
 * 这里把这 4 种形态显式建模为 4 个模式，避免继续复制粘贴。
 */
export type KbCardMode =
  /** 「全部」tab 里 isMine 的卡片：完整功能（收藏 / 更多菜单 / 来源徽章 / 共享徽章）。 */
  | 'all-mine'
  /** 「我的」tab 里的卡片：同 all-mine，但更多菜单走受控展开（active-more 高亮）。 */
  | 'mine-mine'
  /** 「全部」tab 里的共享卡片：可收藏、可看详情、有组织来源，feature badge 更精简。 */
  | 'all-shared'
  /** 按空间筛选下的共享卡片：最简形态，无收藏星、无更多菜单、只有一个类型徽章。 */
  | 'space-shared'

/**
 * isInitialized 判定知识库是否完成初始化（可进入详情而不是被拉去配模型）。
 *
 * LLM（summary）模型始终必需；embedding 模型只在启用了向量或关键词索引时必需。
 *
 * 参数只声明真正读到的字段，KBCard 传入的是放宽过 type 的视图模型，这里保持一致。
 */
export function isInitialized(kb: {
  summary_model_id?: string
  embedding_model_id?: string
  indexing_strategy?: { vector_enabled?: boolean; keyword_enabled?: boolean }
}): boolean {
  if (!kb.summary_model_id || kb.summary_model_id === '') return false
  const strategy = kb.indexing_strategy
  const needsEmbedding = !strategy || strategy.vector_enabled || strategy.keyword_enabled
  if (needsEmbedding && (!kb.embedding_model_id || kb.embedding_model_id === '')) return false
  return true
}

/** 是否 Wiki 型知识库：决定标题左侧是否挂 KbWikiBadge。 */
export function isWikiKb(kb: unknown): boolean {
  return !!(kb as { indexing_strategy?: { wiki_enabled?: boolean } } | null | undefined)?.indexing_strategy
    ?.wiki_enabled
}
