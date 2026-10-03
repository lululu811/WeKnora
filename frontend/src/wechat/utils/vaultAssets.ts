/**
 * Vault 图片引用重写。
 *
 * 一篇从 vault 导出的公众号文章，图片是同级 `images/` 下的相对引用：
 *
 *     ![](images/img_001.jpeg)
 *
 * 原文不改、图片不复制（见 `internal/application/service/knowledge_vault.go`
 * 的设计说明），代价是渲染时得有人把这个相对引用变成一个能取到字节的 URL。
 * 那就是本模块：`/api/v1/knowledge/{id}/vault-asset?ref=<相对引用>`。
 *
 * 服务端只接受**相对引用**并把它限制在该条目自己的目录内，因此这里生成的
 * query 必须是纯相对路径 —— 一旦传了绝对路径，代理会按越界处理并返回 404。
 */

const VAULT_ASSET_PATH = '/api/v1/knowledge';

/** 已经是绝对地址的引用交给原样渲染，服务端也不会去 vault 里找它。 */
const ABSOLUTE_REF = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/i;

/**
 * 构造一篇文档的 vault 图片 URL。
 *
 * @param knowledgeId 知识条目 id（服务端据此查出 vault_path 并定位目录）
 * @param ref 条目内的相对引用，例如 `images/fig.png`
 */
export function vaultAssetUrl(knowledgeId: string, ref: string): string {
  const path = `${VAULT_ASSET_PATH}/${encodeURIComponent(knowledgeId)}/vault-asset`;
  return `${path}?ref=${encodeURIComponent(ref)}`;
}

/** 该引用是否应被改写成 vault 代理地址。 */
function shouldRewrite(ref: string): boolean {
  if (!ref) return false;
  // 已经是代理地址的不重复改写，否则会套娃。
  if (ref.startsWith('/api/v1/')) return false;
  // 绝对地址（http/https/data/blob/协议相对）原样保留。
  if (ABSOLUTE_REF.test(ref)) return false;
  return true;
}

/**
 * 改写 markdown 里的图片引用。返回新字符串，不修改入参。
 *
 * 覆盖三种写法：
 *   - `![](images/a.png)`
 *   - `![alt](images/a.png "可选标题")`
 *   - `![alt](<images/a b.png>)`  —— 路径含空格时 markdown 的尖括号形式
 * 另外也处理裸 HTML `<img src="images/a.png">`，因为部分导出会产出这种混排。
 *
 * 已知边界：未加尖括号且带空格的相对路径（`![](images/图 1.png)`）不会被改写
 * —— 简单形式按 `[^)\s]+` 匹配，到空格就停，剩下的被当成 title 丢掉。这种写法
 * 按 CommonMark 本来就不是合法 destination，实测语料里也不存在（713 篇中带空格
 * 的相对路径 0 处，带空格的都是远程 URL + 合法 title）。想支持得写尖括号形式。
 */
export function rewriteVaultImages(markdown: string, knowledgeId: string): string {
  if (!markdown || !knowledgeId) return markdown;

  // 顺序有意义：先吃掉带 title / 尖括号的完整形式，再吃简单形式，
  // 最后补 HTML —— 否则简单形式会先把复杂形式切坏。
  const withTitle = /!\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]+)(\s+["'(][^)]*)?\s*\)/g;
  const simple = /!\[([^\]]*)\]\(\s*([^)\s]+)(\s+["'(][^)]*)?\s*\)/g;
  const html = /(<img\b[^>]*?\bsrc\s*=\s*)(["'])([^"']+)\2/gi;

  const rewriteTarget = (raw: string): string => {
    const angled = raw.startsWith('<') && raw.endsWith('>');
    const inner = angled ? raw.slice(1, -1) : raw;
    if (!shouldRewrite(inner)) return raw;
    const url = vaultAssetUrl(knowledgeId, inner);
    return angled ? `<${url}>` : url;
  };

  return markdown
    .replace(withTitle, (_m, alt, target, title) => `![${alt}](${rewriteTarget(target)}${title || ''})`)
    .replace(simple, (_m, alt, target, title) => `![${alt}](${rewriteTarget(target)}${title || ''})`)
    .replace(html, (_m, prefix, quote, src) =>
      shouldRewrite(src) ? `${prefix}${quote}${vaultAssetUrl(knowledgeId, src)}${quote}` : _m,
    );
}

/**
 * 从导出文件的 front matter 风格头部里抽原文链接。
 *
 * 导出器写的是一行 blockquote：
 *
 *     > 原文链接: https://mp.weixin.qq.com/s?__biz=...
 *
 * 抽出来给「点回原文」用 —— 导入时它已经进了 knowledge.source，这里是从
 * 正文兜底，防止某些条目入库时没带 source。
 */
export function extractOriginalUrl(markdown: string): string {
  const m = /原文链接[：:]\s*(\S+)/.exec(markdown || '');
  return m ? m[1] : '';
}

/** 从同款头部里抽公众号名。 */
export function extractAccountName(markdown: string): string {
  const m = /公众号[：:]\s*(\S+)/.exec(markdown || '');
  return m ? m[1] : '';
}
