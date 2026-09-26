import Fastify from 'fastify';
import cors from '@fastify/cors';
import staticPlugin from '@fastify/static';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { existsSync } from 'node:fs';
import { symbolsRoutes } from './routes/symbols.js';
import { klineRoutes } from './routes/kline.js';
import { mergedKLineRoutes } from './routes/kline-merged.js';
import { snapshotRoutes } from './routes/snapshot.js';
import { marketRoutes } from './routes/market.js';
import { syncRoutes } from './routes/sync.js';
import { indicatorsRoutes } from './routes/indicators.js';
import { picksRoutes } from './routes/picks.js';
import { registerPluginRoutes } from './plugins/index.js';
import { nameCache } from './services/name-cache.js';

const PORT = Number(process.env.KLINE_STUDIO_PORT ?? 4000);
const HOST = process.env.KLINE_STUDIO_HOST ?? '0.0.0.0';
// 逗号分隔的 CORS origin 白名单。开发默认允许 Vite (5173)；Docker 编排
// 下 WeKnora frontend 通过容器名访问，不走 CORS，但保留 escape hatch。
const CORS_ORIGINS = (process.env.KLINE_STUDIO_CORS_ORIGINS ??
  'http://localhost:5173,http://127.0.0.1:5173').split(',').map((s) => s.trim()).filter(Boolean);

const __dirname = dirname(fileURLToPath(import.meta.url));
// frontend dist 在容器镜像里位于 /app/frontend-dist，本地 dev 时不存在。
const FRONTEND_DIST = process.env.KLINE_STUDIO_FRONTEND_DIST
  ?? resolve(__dirname, '../../frontend/dist');

const app = Fastify({ logger: true });

await app.register(cors, {
  origin: CORS_ORIGINS,
  credentials: true,
});

// 启动时一次性拉全 A 股代码表到内存（秒级）
await nameCache.loadAll();

// 基础路由
await app.register(symbolsRoutes, { prefix: '/api' });
await app.register(klineRoutes, { prefix: '/api' });
await app.register(mergedKLineRoutes, { prefix: '/api' });
await app.register(snapshotRoutes, { prefix: '/api' });
await app.register(marketRoutes, { prefix: '/api' });
await app.register(syncRoutes, { prefix: '/api' });
await app.register(indicatorsRoutes, { prefix: '/api' });
await app.register(picksRoutes, { prefix: '/api' });

// 插件路由（Zettaranc 等）
await registerPluginRoutes(app);

app.get('/health', async () => ({ ok: true, ts: Date.now() }));

// 当镜像包含 frontend/dist 时，由 backend 直接 serve 静态文件 + SPA fallback，
// 省掉一个 nginx 反向代理。dev 模式下 frontend 仍由 Vite (5173) 提供，
// 这里 FRONTEND_DIST 不存在则跳过。
if (existsSync(FRONTEND_DIST)) {
  await app.register(staticPlugin, { root: FRONTEND_DIST, prefix: '/' });
  // SPA fallback：非 /api/* 的请求都返回 index.html，让 react-router 接管。
  app.setNotFoundHandler((req, reply) => {
    if (req.url.startsWith('/api/') || req.url === '/health') {
      reply.code(404).send({ error: 'not found', path: req.url });
      return;
    }
    reply.sendFile('index.html');
  });
  app.log.info(`serving frontend from ${FRONTEND_DIST}`);
} else {
  app.log.info(`no frontend dist at ${FRONTEND_DIST} (dev mode — use Vite on :5173)`);
}

app
  .listen({ port: PORT, host: HOST })
  .then(() => app.log.info(`kline-studio backend on http://${HOST}:${PORT}`))
  .catch((err) => {
    app.log.error(err);
    process.exit(1);
  });