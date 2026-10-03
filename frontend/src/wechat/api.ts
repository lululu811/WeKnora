/**
 * 公众号工作台的数据层。
 *
 * 只包一层，不重复造轮子：列表/详情全部走 `@/api/knowledge-base` 已有的函数，
 * 保持与主应用其它入口（知识库页、manual-knowledge-editor）读的是同一份数据。
 */

import { getKnowledgeDetails, listKnowledgeBases, listKnowledgeFiles } from '@/api/knowledge-base';

/**
 * 列表接口的返回没有共享类型：知识库页与 manual-knowledge-editor 都是按
 * `any` 消费 knowledge 的（`src/types/` 下只有 chunker/knowledgeProcess 等，
 * 没有 Knowledge 模型）。这里跟着现状用 `any`，不去为此新建一个会和后端
 * 漂移的半吊子类型。
 */

/** 列表里带上的、供左侧导航用的最小字段集。 */
export interface ArticleRow {
  id: string;
  title: string;
  /** knowledge.source —— 公众号原文链接；手工录入的条目为 "manual"。 */
  source: string;
  parseStatus: string;
  enableStatus: string;
  createdAt?: string;
}

/**
 * 拉出可读的文章：已索引（enable_status=enabled）且属于手工类型。
 *
 * 走 knowledge_channel=wechat 过滤，把工作台和知识库页里的其它内容隔开。
 * 兜底不过滤：早期入库的条目可能没打 channel，宁可多列出来也不要让用户
 * 以为文章丢了。
 */
export async function listWechatArticles(kbId: string, page = 1, pageSize = 50): Promise<ArticleRow[]> {
  // file_type=manual 让服务端就把非手工条目滤掉；sort_by 是后端白名单枚举，
  // 写死避免把用户输入透传进去。
  const res: any = await listKnowledgeFiles(kbId, {
    page,
    page_size: pageSize,
    file_type: 'manual',
    sort_by: 'created_at',
    sort_order: 'desc',
  });

  const items: any[] = res?.data?.items || res?.data || res?.items || [];
  return items.map((k) => ({
    id: k.id,
    title: k.title || k.file_name || k.id,
    source: k.source || '',
    parseStatus: k.parse_status || '',
    enableStatus: k.enable_status || '',
    createdAt: k.created_at,
  }));
}

export async function listCandidateBases(): Promise<any[]> {
  const res: any = await listKnowledgeBases({ page: 1, page_size: 100 } as any);
  const items: any[] = res?.data?.items || res?.data || res?.items || [];
  return items;
}

/** 手动知识的完整正文存在 metadata.content 里。 */
export interface ArticleDetail {
  id: string;
  title: string;
  /** 未改写的原始 markdown —— 图片仍是相对路径。 */
  rawMarkdown: string;
  source: string;
  vaultPath: string;
  status: string;
}

export async function fetchArticleDetail(id: string): Promise<ArticleDetail> {
  const res: any = await getKnowledgeDetails(id);
  const k = (res?.data || res) as any;

  // metadata 在部分响应里是 JSON 字符串（jsonb 列经 map[string]any 序列化时
  // 会退化成字符串），两种形态都收一下。
  let meta: any = k?.metadata;
  if (typeof meta === 'string') {
    try {
      meta = JSON.parse(meta);
    } catch {
      meta = null;
    }
  }

  return {
    id: k.id,
    title: k.title || k.file_name || k.id,
    rawMarkdown: (meta?.content as string) || '',
    source: k.source || '',
    vaultPath: (meta?.vault_path as string) || '',
    status: k.enable_status || k.parse_status || '',
  };
}
