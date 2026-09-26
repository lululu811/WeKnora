import { syncService } from '../services/sync.js';
export async function syncRoutes(app) {
    app.get('/sync/status', async (_req, reply) => {
        const status = await syncService.getStatus();
        reply.send({ code: 0, data: status });
    });
    app.post('/sync/trigger', async (_req, reply) => {
        const res = await syncService.triggerSync();
        reply.send({ code: 0, data: res });
    });
}
