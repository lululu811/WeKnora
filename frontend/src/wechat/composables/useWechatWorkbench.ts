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

  // 探测默认库期间不要让 watch 再插一脚，否则两个并发请求会互相覆盖
  // articles.value，探测结果不可信。
  let probing = false;

  async function loadBases() {
    const list = await listCandidateBases();
    bases.value = list;
    if (!kbId.value && list.length) {
      // 只按 knowledge_count 挑「非空库」是不够的：工作台列的是 file_type=manual，
      // 一个装了几千篇上传文档的库在这里是空的，用户会以为功能坏了。
      // 所以逐个探测到真有文章的那个为止，最多试 5 个。
      const ordered = [...list].sort(
        (a, b) => (b.knowledge_count || 0) - (a.knowledge_count || 0),
      );
      probing = true;
      try {
        for (const candidate of ordered.slice(0, 5)) {
          kbId.value = candidate.id;
          if ((await loadArticles()).length) return;
        }
        // 全都没有：停在最后一个试过的库上，列表区显示"该知识库还没有公众号文章"。
      } finally {
        probing = false;
      }
    }
  }

  async function loadArticles(): Promise<ArticleRow[]> {
    if (!kbId.value) return [];
    loadingList.value = true;
    listError.value = '';
    try {
      articles.value = await listWechatArticles(kbId.value);
      return articles.value;
    } catch (e: any) {
      listError.value = e?.message || String(e);
      articles.value = [];
      return [];
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
  // 右栏的会话作用域就悄悄换掉。探测默认库期间不响应（探测自己会调）。
  watch(kbId, () => {
    if (probing) return;
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
