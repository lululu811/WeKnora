import { request } from 'undici';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
const FUYAO_BASE = 'https://fuyao.aicubes.cn';
const FUYAO_KEY = process.env.HITHINK_FINANCE_API_KEY ?? loadKeyFromCredentials();
function loadKeyFromCredentials() {
    try {
        const path = join(process.env.HOME ?? '', 'Library/Application Support/hithink-finance/credentials.env');
        const text = readFileSync(path, 'utf8');
        const m = text.match(/HITHINK_FINANCE_API_KEY=(.+)/);
        return m ? m[1].trim() : null;
    }
    catch {
        return null;
    }
}
export async function snapshotRoutes(app) {
    app.get('/snapshot', async (req, reply) => {
        if (!FUYAO_KEY) {
            reply.code(503).send({
                code: 503,
                message: 'HITHINK_FINANCE_API_KEY not configured',
            });
            return;
        }
        const symbols = req.query.symbols.trim();
        if (!symbols) {
            reply.code(400).send({ code: 400, message: 'symbols required' });
            return;
        }
        const url = `${FUYAO_BASE}/api/a-share/prices/snapshot?thscodes=${encodeURIComponent(symbols)}`;
        try {
            const res = await request(url, {
                headers: { 'X-api-key': FUYAO_KEY },
                headersTimeout: 5000,
                bodyTimeout: 5000,
            });
            if (res.statusCode >= 400) {
                reply.code(502).send({
                    code: 502,
                    message: `fuyao upstream ${res.statusCode}`,
                });
                return;
            }
            const body = (await res.body.json());
            reply.send({ code: 0, data: body.data ?? [] });
        }
        catch (err) {
            reply.code(502).send({
                code: 502,
                message: `upstream error: ${err.message}`,
            });
        }
    });
}
