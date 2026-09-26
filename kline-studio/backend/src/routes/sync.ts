/**
 * /api/sync — 数据归档与增量同步接口
 * - GET  /api/sync/status   获取当前本地 DuckDB 归档状态与最新日期
 * - POST /api/sync/trigger  手动触发增量对齐
 */
import type { FastifyInstance } from 'fastify';
import { syncService } from '../services/sync.js';

export async function syncRoutes(app: FastifyInstance): Promise<void> {
  app.get('/sync/status', async (_req, reply) => {
    const status = await syncService.getStatus();
    reply.send({ code: 0, data: status });
  });

  app.post('/sync/trigger', async (_req, reply) => {
    const res = await syncService.triggerSync();
    reply.send({ code: 0, data: res });
  });
}
