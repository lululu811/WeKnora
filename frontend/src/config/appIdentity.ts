/**
 * 产品名的唯一来源。
 *
 * 构建期用 `VITE_APP_NAME` 覆盖（Docker 部署由 docker-compose 的 frontend build args 传入，
 * 见 .env.example 的 `VITE_APP_NAME`），未设置时回退到 `WeKnora`。
 *
 * 值由 vite.config.ts 的 `define` 注入（同 `__FRONTEND_VERSION__`）——不能用
 * `import.meta.env.VITE_APP_NAME`，那样只有写进 `.env` 文件的值才可见，
 * 而 Docker 是把 build arg 传进进程环境的。
 *
 * 静态的 index.html / embed.html 无法 import 这个模块，那里用 Vite 的 `%VITE_APP_NAME%`；
 * 默认值在 vite.config.ts 里兜底，保证两处永远一致、且没有第 3 个来源。
 */
export const APP_NAME = __APP_NAME__
