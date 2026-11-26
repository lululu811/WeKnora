import { importMetaEnv } from './importMetaEnv'

export function getApiBaseUrl(): string {
  // LocalHub plugin patch (2026-04-29): respect vite's BASE_URL so that
  // axios calls work at `/app/weknora/` (LocalHub reverse proxy). Without
  // this · axios hits `/api/v1/...` at LocalHub root · gets 404 "Cannot
  // POST". Strip trailing slash so axios doesn't produce `/app/weknora//api/v1/...`.
  // See: plugins/weknora/patches/api-base-baseurl.patch
  // LocalHub patch + upstream 的空安全取法合流：本地用 importMetaEnv 包装器
  // 统一做一次可选访问（等价于 upstream 的 import.meta.env?.BASE_URL），
  // 裸 node（tsx --test）下 import.meta.env 为 undefined 也不会炸。
  const base = (importMetaEnv.BASE_URL || '/').replace(/\/+$/, '');
  return base;
}
