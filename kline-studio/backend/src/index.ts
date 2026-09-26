import Fastify from 'fastify';
import cors from '@fastify/cors';
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

const app = Fastify({ logger: true });

await app.register(cors, {
  origin: ['http://localhost:5173', 'http://127.0.0.1:5173'],
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

app
  .listen({ port: PORT, host: '127.0.0.1' })
  .then(() => app.log.info(`kline-studio backend on http://127.0.0.1:${PORT}`))
  .catch((err) => {
    app.log.error(err);
    process.exit(1);
  });
