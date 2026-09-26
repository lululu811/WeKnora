/**
 * /api/symbols — 股票目录
 * - GET /api/symbols                列出（分页，可选 exchange）
 * - GET /api/symbols/search?q=      模糊搜索（ticker / 中文名 / 代码）
 * - GET /api/symbols/:thscode       详情
 *
 * name 来源是 nameCache（启动时一次性从 fuyao 拉的 7707 个名字），
 * 不依赖 DuckDB 本地的 v_symbol.name（未填充）。
 */
import type { FastifyInstance } from 'fastify';
import { query } from '../services/db.js';
import { nameCache } from '../services/name-cache.js';

const THSCODE_RE = /^[0-9]{6}\.(SH|SZ|BJ)$/;
const EXCHANGE_VALUES = new Set(['SH', 'SZ', 'BJ']);

export interface SymbolRow {
  thscode: string;
  ticker: string;
  name: string | null;
  exchange: string | null;
  asset_type: string | null;
  updated_at: string;
}

export function symbolInfoToPro(s: SymbolRow) {
  return {
    exchange: s.exchange ?? 'SH',
    market: 'stocks',
    name: s.name ?? s.ticker,
    shortName: s.name ?? s.ticker,
    ticker: s.ticker,
    priceCurrency: 'cny',
    type: 'stock',
  };
}

function withName<T extends { thscode: string; name: string | null }>(row: T) {
  const cached = nameCache.get(row.thscode);
  return cached ? { ...row, name: cached } : row;
}

export async function symbolsRoutes(app: FastifyInstance): Promise<void> {
  app.get<{ Querystring: { limit?: number; offset?: number; exchange?: string } }>(
    '/symbols',
    async (req, reply) => {
      const limit = Math.min(req.query.limit ?? 2000, 5000);
      const offset = req.query.offset ?? 0;
      const exchange = req.query.exchange;
      if (exchange && !EXCHANGE_VALUES.has(exchange)) {
        reply.code(400).send({ code: 400, message: 'invalid exchange' });
        return;
      }
      const where = exchange ? `WHERE exchange = '${exchange}'` : '';
      const rows = await query<SymbolRow>(
        `SELECT * FROM v_symbol ${where} ORDER BY thscode LIMIT ${limit} OFFSET ${offset}`,
      );
      reply.send({ code: 0, data: rows.map(withName) });
    },
  );

  app.get<{ Querystring: { q?: string; limit?: number } }>(
    '/symbols/search',
    async (req, reply) => {
      const q = (req.query.q ?? '').trim();
      const limit = Math.min(req.query.limit ?? 30, 100);
      if (!q) {
        reply.send({ code: 0, data: [] });
        return;
      }
      const safe = q.slice(0, 32).replace(/['%_\\]/g, '');
      if (!safe) {
        reply.send({ code: 0, data: [] });
        return;
      }
      const qLower = safe.toLowerCase();
      const isAscii = /^[\x20-\x7E]+$/.test(safe);
      const like = `%${safe}%`;

      // ASCII（ticker / thscode）走 SQL；中文 / 非 ASCII 走 nameCache 全量
      const candidateCodes: string[] = isAscii
        ? (
            await query<SymbolRow>(
              `SELECT * FROM v_symbol
               WHERE ticker LIKE '${like}' ESCAPE '\\'
                  OR thscode LIKE '${like}' ESCAPE '\\'
               LIMIT ${limit * 4}`,
            )
          ).map((r) => r.thscode)
        : nameCache
            .entries()
            .filter(([, n]) => n.toLowerCase().includes(qLower))
            .slice(0, limit * 4)
            .map(([c]) => c);

      if (candidateCodes.length === 0) {
        reply.send({ code: 0, data: [] });
        return;
      }

      // 用候选 thscode 批量查回完整 SymbolRow
      const placeholders = candidateCodes.map((c) => `'${c}'`).join(',');
      const rows = await query<SymbolRow>(
        `SELECT * FROM v_symbol WHERE thscode IN (${placeholders})`,
      );

      // Node 端排序：ticker 完全匹配 > ticker 前缀 > 名字包含
      const ranked = rows.map((r) => {
        const cachedName = nameCache.get(r.thscode) ?? r.name ?? '';
        const exactTicker = r.ticker.toLowerCase() === qLower;
        const tickerPrefix = r.ticker.toLowerCase().startsWith(qLower);
        const nameContains = cachedName.toLowerCase().includes(qLower);
        let rank = 3;
        if (exactTicker) rank = 0;
        else if (tickerPrefix) rank = 1;
        else if (nameContains) rank = 2;
        return { row: r, rank, name: cachedName };
      });
      ranked.sort((a, b) => a.rank - b.rank || a.row.ticker.localeCompare(b.row.ticker));
      const enriched = ranked.slice(0, limit).map(({ row, name }) => ({
        ...row,
        name,
      }));
      reply.send({ code: 0, data: enriched });
    },
  );

  app.get<{ Params: { thscode: string } }>(
    '/symbols/:thscode',
    async (req, reply) => {
      if (!THSCODE_RE.test(req.params.thscode)) {
        reply.code(400).send({ code: 400, message: 'invalid thscode' });
        return;
      }
      const rows = await query<SymbolRow>(
        `SELECT * FROM v_symbol WHERE thscode = '${req.params.thscode}' LIMIT 1`,
      );
      if (rows.length === 0) {
        reply.code(404).send({ code: 404, message: 'symbol not found' });
        return;
      }
      reply.send({ code: 0, data: withName(rows[0]) });
    },
  );
}
