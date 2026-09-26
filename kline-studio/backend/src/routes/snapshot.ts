/**
 * /api/snapshot — 实时行情代理
 *
 * 浏览器 → 本后端 → fuyao.aicubes.cn REST API
 * 原因：浏览器直连 fuyao 会暴露 HITHINK_FINANCE_API_KEY，
 * 后端统一持有 key、做限流、做缓存。
 *
 * GET /api/snapshot?symbols=600519.SH,000001.SZ
 */
import type { FastifyInstance } from 'fastify';
import { request } from 'undici';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const FUYAO_BASE = 'https://fuyao.aicubes.cn';
const FUYAO_KEY = process.env.HITHINK_FINANCE_API_KEY ?? loadKeyFromCredentials();

function loadKeyFromCredentials(): string | null {
  try {
    const path = join(
      process.env.HOME ?? '',
      'Library/Application Support/hithink-finance/credentials.env',
    );
    const text = readFileSync(path, 'utf8');
    const m = text.match(/HITHINK_FINANCE_API_KEY=(.+)/);
    return m ? m[1].trim() : null;
  } catch {
    return null;
  }
}

export interface SnapshotPayload {
  thscode: string;
  ticker: string;
  name: string;
  last_price: number;
  price_change: number;
  price_change_ratio_pct: number;
  prev_price: number;
  open_price: number;
  high_price: number;
  low_price: number;
  volume: number;
  turnover: number;
  captured_at?: string;
}

export async function snapshotRoutes(app: FastifyInstance): Promise<void> {
  app.get<{ Querystring: { symbols: string } }>(
    '/snapshot',
    async (req, reply) => {
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
        const body = (await res.body.json()) as {
          data?: SnapshotPayload[];
        };
        reply.send({ code: 0, data: body.data ?? [] });
      } catch (err) {
        reply.code(502).send({
          code: 502,
          message: `upstream error: ${(err as Error).message}`,
        });
      }
    },
  );
}
