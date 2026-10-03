/**
 * vault 图片的鉴权加载。
 *
 * 问题：vault-asset 路由按 Contributor + KB 写权限鉴权（返回的是 vault 磁盘上的
 * 原始字节），而浏览器给 `<img src>` 发请求时**不会附带 Authorization 头** ——
 * 令牌在 localStorage 里，只有 XHR/fetch 会带上。直接写 `<img src="/api/...">`
 * 的结果是稳定 401，表现为一片破图，而且破得很安静（alt 文本照常显示）。
 *
 * 这里改成：渲染后遍历容器里的图片，用带鉴权头的 fetch 取回字节，转成
 * blob: URL 塞回去。这和 DocumentPreview 对 PDF 的做法一致（它也是
 * previewKnowledgeFile 取 Blob 再 createObjectURL）。
 *
 * 没有选择"给 img 加签名 URL"：那要在服务端再造一套签发/校验，而仓库已有的
 * resource-grant 机制（serveResourceGrants）绑在 ResourceCatalog 上，只对
 * 走 WeKnora 自己存储的资源有效；vault 文件从不进存储，借用它反而绕远。
 */

const AUTH_HEADERS = (): Record<string, string> => {
  const token = localStorage.getItem('weknora_token') || '';
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const tenant = localStorage.getItem('weknora_selected_tenant_id') || '';
  if (tenant) headers['X-Tenant-ID'] = tenant;
  return headers;
};

const VAULT_IMG_RE = /\/api\/v1\/knowledge\/[^/]+\/vault-asset/;

/**
 * 把容器里所有指向 vault 代理的图片换成 blob: URL。
 *
 * 重复调用安全：已加载的（src 已是 blob:）会跳过，所以路由切换、文章切换、
 * 以及重渲染都不需要额外清理。
 *
 * @returns 本次新建的 object URL 列表，调用方在卸载时 revoke
 */
export async function hydrateVaultImages(container: HTMLElement | null): Promise<string[]> {
  if (!container) return [];
  const imgs = Array.from(container.querySelectorAll<HTMLImageElement>('img[src]'));
  const created: string[] = [];

  await Promise.all(
    imgs.map(async (img) => {
      const src = img.getAttribute('src') || '';
      if (!VAULT_IMG_RE.test(src)) return;
      if (src.startsWith('blob:') || img.dataset.vaultLoaded === '1') return;
      try {
        const res = await fetch(src, { headers: AUTH_HEADERS() });
        if (!res.ok) {
          img.dataset.vaultError = String(res.status);
          return;
        }
        const url = URL.createObjectURL(await res.blob());
        created.push(url);
        // 组件可能已经卸载或文章已切换：这时别再往游离的节点上塞 URL，
        // 否则 blob 泄漏且没人会 revoke 它。
        if (!img.isConnected) {
          URL.revokeObjectURL(url);
          created.pop();
          return;
        }
        img.src = url;
        img.dataset.vaultLoaded = '1';
      } catch {
        img.dataset.vaultError = 'network';
      }
    }),
  );

  return created;
}
