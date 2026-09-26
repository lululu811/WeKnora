/**
 * /api/kline — 历史 K 线（只读本地 DuckDB）
 *
 * GET /api/kline?symbol=600519.SH&from=2020-01-01&to=2026-09-01&adjust=none|forward|backward
 *
 * 注意 1：DuckDB Node 绑定对视图 + 参数化 `?` 不友好。这里用 whitelisted
 *         校验后的值做字符串拼接，避免注入。
 * 注意 2：DATE → epoch seconds 必须用 UTC 解析，否则会被本地时区吃掉 8 小时，
 *         导致 KLineChart Pro 把日期画成 1970。
 */
import type { FastifyInstance } from 'fastify';
import { query } from '../services/db.js';

const ADJUST_MAP = {
  none: 'v_daily',
  forward: 'v_daily_qfq',
  backward: 'v_daily_hfq',
} as const;

type Adjust = keyof typeof ADJUST_MAP;
const ADJUST_KEYS = new Set(Object.keys(ADJUST_MAP) as Adjust[]);

const THSCODE_RE = /^[0-9]{6}\.(SH|SZ|BJ)$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export interface KLineRow {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  turnover: number;
}

/** DuckDB DATE → unix seconds（UTC 00:00） */
function dateToEpochSec(raw: unknown): number {
  if (typeof raw === 'string') {
    const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return Math.floor(Date.UTC(+m[1], +m[2] - 1, +m[3]) / 1000);
  }
  const d = raw instanceof Date ? raw : new Date(String(raw));
  return Math.floor(d.getTime() / 1000);
}

export async function klineRoutes(app: FastifyInstance): Promise<void> {
  app.get<{
    Querystring: {
      symbol?: string;
      from?: string;
      to?: string;
      adjust?: string;
      limit?: number;
      period?: string;
    };
  }>('/kline', async (req, reply) => {
    const { symbol } = req.query;
    if (!symbol || !THSCODE_RE.test(symbol)) {
      reply.code(400).send({ code: 400, message: 'invalid symbol (expect 600519.SH)' });
      return;
    }
    const adjust = (req.query.adjust ?? 'none') as Adjust;
    if (!ADJUST_KEYS.has(adjust)) {
      reply.code(400).send({ code: 400, message: 'adjust must be none|forward|backward' });
      return;
    }
    const period = req.query.period ?? 'day';
    if (!['day', 'week', 'month'].includes(period)) {
      reply.code(400).send({ code: 400, message: 'period must be day|week|month' });
      return;
    }
    const view = ADJUST_MAP[adjust];

    const from = req.query.from;
    const to = req.query.to;
    if (from && !DATE_RE.test(from)) {
      reply.code(400).send({ code: 400, message: 'from must be YYYY-MM-DD' });
      return;
    }
    if (to && !DATE_RE.test(to)) {
      reply.code(400).send({ code: 400, message: 'to must be YYYY-MM-DD' });
      return;
    }
    const limit = Math.min(req.query.limit ?? 5000, 20000);

    const conditions: string[] = [`thscode = '${symbol}'`];
    if (from) conditions.push(`date >= '${from}'`);
    if (to) conditions.push(`date <= '${to}'`);

    let sql: string;
    if (period === 'week' || period === 'month') {
      const trunc = period === 'week' ? "date_trunc('week', date)" : "date_trunc('month', date)";
      sql = `
        SELECT 
          ${trunc} as date,
          arg_min(open, date) as open,
          max(high) as high,
          min(low) as low,
          arg_max(close, date) as close,
          sum(volume) as volume,
          sum(turnover) as turnover
        FROM ${view}
        WHERE ${conditions.join(' AND ')}
        GROUP BY ${trunc}
        ORDER BY date ASC
        LIMIT ${limit}
      `;
    } else {
      sql = `
        SELECT date, open, high, low, close, volume, turnover
        FROM ${view}
        WHERE ${conditions.join(' AND ')}
        ORDER BY date ASC
        LIMIT ${limit}
      `;
    }
    const rows = await query<{
      date: unknown;
      open: number;
      high: number;
      low: number;
      close: number;
      volume: number;
      turnover: number;
    }>(sql);

    reply.send({
      code: 0,
      data: rows.map((r) => ({
        ts: dateToEpochSec(r.date),
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
        volume: r.volume,
        turnover: r.turnover,
      })),
    });
  });
}
