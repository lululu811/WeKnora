# Multi-stage Dockerfile for kline-studio.
# Stage 1 builds the frontend Vite bundle into frontend/dist.
# Stage 2 builds the backend TypeScript bundle into backend/dist.
# Stage 3 ships Node 20 + the backend bundle + the frontend dist together.
#
# Build context MUST be ./kline-studio (the directory containing this file),
# not the repository root, so COPY paths below line up with the workspace.

# -------- Stage 1: frontend build --------
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /app

# Mirror override for users in mainland China.
ARG NPM_REGISTRY
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi

COPY package.json package-lock.json* ./
COPY frontend/package.json ./frontend/package.json
COPY backend/package.json ./backend/package.json

RUN npm install --no-audit --no-fund

COPY frontend ./frontend
# @klinecharts/pro declares `klinecharts` as a peerDependency. npm workspaces
# hoists it to /app/node_modules, but rollup running inside /app/frontend
# can't always resolve that path during `vite build`. Symlink the package
# next to its consumer so resolution is unambiguous.
RUN mkdir -p /app/frontend/node_modules \
 && for pkg in klinecharts lodash solid-js; do \
      [ -e "/app/node_modules/$pkg" ] && ln -sf "/app/node_modules/$pkg" "/app/frontend/node_modules/$pkg" || true; \
    done
RUN npm run build --workspace=frontend

# -------- Stage 2: backend build (incl. native duckdb rebuild) --------
FROM node:20-bookworm-slim AS backend-build
WORKDIR /app

ARG NPM_REGISTRY
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi

# Native build deps for duckdb-neo node binding.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ \
 && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json* ./
COPY frontend/package.json ./frontend/package.json
COPY backend/package.json ./backend/package.json

# 显式 workspace install：先 workspace 根装 deps，再对 backend 单独 install
# 确保 backend/node_modules/ 一定生成（之前 --ignore-scripts + workspace 模式
# 没在 backend 子目录创建 node_modules，导致 runtime COPY 失败）。
RUN npm install --no-audit --no-fund --ignore-scripts
# 对 backend 单独跑 install，把 duckdb 等 deps 装到 backend/node_modules。
RUN npm install --no-audit --no-fund --ignore-scripts --workspace=backend
# 单独重建 duckdb native binding。
RUN npm rebuild duckdb --workspace=backend || true

COPY backend ./backend
RUN npm run build --workspace=backend
# npm workspaces 把 backend 的 deps（fastify/duckdb 等）hoist 到 /app/node_modules，
# 而非 /app/backend/node_modules。这里 symlink 一份，让 runtime stage 的 COPY
# 能拿到 backend 子目录下的 node_modules 视图。
RUN ln -s /app/node_modules /app/backend/node_modules

# -------- Stage 3: runtime --------
FROM node:20-bookworm-slim AS runtime

ENV NODE_ENV=production
ENV KLINE_STUDIO_PORT=4000
ENV KLINE_STUDIO_HOST=0.0.0.0
ENV KLINE_STUDIO_FRONTEND_DIST=/app/frontend-dist

WORKDIR /app

# 仅复制 runtime 需要的产物：
#  - backend node_modules（含 duckdb native binding）
#  - backend/dist（编译后的 JS）
#  - frontend/dist（Vite 静态产物）
COPY --from=backend-build /app/backend/node_modules ./backend/node_modules
COPY --from=backend-build /app/backend/dist ./backend/dist
COPY --from=backend-build /app/backend/package.json ./backend/package.json
COPY --from=frontend-build /app/frontend/dist ./frontend-dist

# 数据持久化目录：picks.json 等运行时写入文件落在这里。
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 4000

# 健康检查
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
  CMD node -e "fetch('http://127.0.0.1:'+process.env.KLINE_STUDIO_PORT+'/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

WORKDIR /app/backend
CMD ["node", "dist/index.js"]