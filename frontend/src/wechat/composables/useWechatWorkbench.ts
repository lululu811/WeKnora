import { computed, ref, watch } from 'vue';

import { fetchArticleDetail, listCandidateBases, listWechatArticles, type ArticleDetail, type ArticleRow } from '../api';
import { extractOriginalUrl, rewriteVaultImages } from '../utils/vaultAssets';

/**
 * 工作台的状态中枢：知识库选择、文章列表、当前文章、以及给渲染层用的改写后正文。
 *
 * 为什么左栏渲染「入库时存下来的那份 markdown」而不是现读 vault 里的原始文件：
 * 引用高亮是在**已渲染的 DOM** 里按文本匹配的（`findMarkdownSourceRange`），
 * 拿入库时的文本去匹配才找得到。直接读原始 .md 反而会出现"明明有这段话却
 * 高亮不到"，因为入库时做过 CRLF 归一与行尾处理，两边可能已经不是同一份字节。
 */
export function useWechatWorkbench() {
  const bases = ref<any[]>([]);
  const kbId = ref('');
  const articles = ref<ArticleRow[]>([]);
  const currentId = ref('');

  const detail = ref<ArticleDetail | null>(null);
  const loadingList = ref(false);
  const loadingArticle = ref(false);
  const listError = ref('');
  const articleError = ref('');

  /** 图片引用已改写成 vault 代理地址的正文，直接喂给渲染器。 */
  const renderedMarkdown = computed(() => {
    const md = detail.value?.rawMarkdown;
    if (!md || !currentId.value) return '';
    return rewriteVaultImages(md, currentId.value);
  });

  /**
   * 原始 markdown（未改写），用于左栏的引用定位：定位器要匹配的是入库时的
   * 文本，而图片地址改不改正与文本匹配无关。
   */
  const sourceMarkdown = computed(() => detail.value?.rawMarkdown || '');

  /** 原文链接优先取 knowledge.source；早期条目没带时从正文头部兜底。 */
  const originalUrl = computed(() => {
    const s = detail.value?.source;
    if (s && s !== 'manual') return s;
    return extractOriginalUrl(detail.value?.rawMarkdown || '');
  });

  const hasVault = computed(() => Boolean(detail.value?.vaultPath));

  async function loadBases() {
    const list = await listCandidateBases();
    bases.value = list;
    if (!kbId.value && list.length) {
      // 优先挑已经装过公众号文章的那个库：空库进去会看到空列表，
      // 容易被当成"功能坏了"。
      const withContent = list.find((kb: any) => (kb.knowledge_count || 0) > 0);
      kbId.value = (withContent || list[0]).id;
    }
  }

  async function loadArticles() {
    if (!kbId.value) return;
    loadingList.value = true;
    listError.value = '';
    try {
      articles.value = await listWechatArticles(kbId.value);
    } catch (e: any) {
      listError.value = e?.message || String(e);
      articles.value = [];
    } finally {
      loadingList.value = false;
    }
  }

  async function openArticle(id: string) {
    if (!id) {
      detail.value = null;
      return;
    }
    loadingArticle.value = true;
    articleError.value = '';
    try {
      detail.value = await fetchArticleDetail(id);
      currentId.value = id;
    } catch (e: any) {
      articleError.value = e?.message || String(e);
      detail.value = null;
    } finally {
      loadingArticle.value = false;
    }
  }

  // 切库就换列表；换列表不自动选第一篇 —— 让用户自己挑，避免每换一次库
  // 右栏的会话作用域就悄悄换掉。
  watch(kbId, () => {
    currentId.value = '';
    detail.value = null;
    void loadArticles();
  });

  return {
    bases,
    kbId,
    articles,
    detail,
    currentId,
    loadingList,
    loadingArticle,
    listError,
    articleError,
    renderedMarkdown,
    sourceMarkdown,
    originalUrl,
    hasVault,
    loadBases,
    loadArticles,
    openArticle,
  };
}
