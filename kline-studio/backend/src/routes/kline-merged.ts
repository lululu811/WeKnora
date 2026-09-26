/**
 * /api/kline/merged — 融合 K 线（DuckDB 历史 + Financial-API 实时）
 *
 * GET /api/kline/merged?symbol=600519.SH&days=120&adjust=forward
 *
 * 返回完整的 K 线数据，包含今日实时（如果是交易日且在交易时段）
 */
import type { FastifyInstance } from 'fastify';
import { getMergedKLine } from '../services/data-merge.js';

const THSCODE_RE = /^[0-9]{6}\.(SH|SZ|BJ)$/;

export async function mergedKLineRoutes(app: FastifyInstance): Promise<void> {
  app.get<{
    Querystring: {
      symbol?: string;
      days?: number;
      adjust?: 'none' | 'forward' | 'backward';
    };
  }>('/kline/merged', async (req, reply) => {
    const { symbol, days = 120, adjust = 'forward' } = req.query;

    if (!symbol || !THSCODE_RE.test(symbol)) {
      reply.code(400).send({
        code: 400,
        message: 'invalid symbol (expect 600519.SH)',
      });
      return;
    }

    try {
      const { bars, hasToday, source } = await getMergedKLine(symbol, days, adjust);

      // 转换为前端格式（ts 用 unix seconds）
      const data = bars.map(bar => ({
        ts: Math.floor(Date.parse(bar.date + 'T00:00:00+08:00') / 1000),
        date: bar.date,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume,
        turnover: bar.turnover,
      }));

      reply.send({
        code: 0,
        symbol,
        has_today: hasToday,
        source,
        count: data.length,
        data,
      });
    } catch (err) {
      app.log.error(err);
      reply.code(500).send({
        code: 500,
        message: 'internal error',
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });
}
