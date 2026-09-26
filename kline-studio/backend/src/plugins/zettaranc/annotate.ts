/**
 * /api/annotate — Z 哥交易体系形态标注
 *
 * GET /api/annotate?symbol=600519.SH&days=120&patterns=b1,s1,key_k
 *
 * 使用数据融合：DuckDB 历史 + Financial-API 实时
 */
import type { FastifyInstance } from 'fastify';
import { getMergedKLine } from '../../services/data-merge.js';
import { annotator, type Pattern } from './annotator.js';
import type { KLineBar } from './indicators.js';

const THSCODE_RE = /^[0-9]{6}\.(SH|SZ|BJ)$/;

export async function annotateRoutes(app: FastifyInstance): Promise<void> {
  app.get<{
    Querystring: {
      symbol?: string;
      days?: number;
      patterns?: string;
      adjust?: 'none' | 'forward' | 'backward';
    };
  }>('/annotate', async (req, reply) => {
    const {
      symbol,
      days = 120,
      patterns = 'b1,key_k,s1,violent_k',
      adjust = 'forward',
    } = req.query;

    if (!symbol || !THSCODE_RE.test(symbol)) {
      reply.code(400).send({
        code: 400,
        message: 'invalid symbol (expect 600519.SH)',
      });
      return;
    }

    const patternTypes = patterns.split(',').map(p => p.trim()).filter(Boolean);

    try {
      // 使用数据融合：DuckDB 历史 + Financial-API 实时
      const { bars, hasToday, source } = await getMergedKLine(symbol, days + 30, adjust);

      if (bars.length === 0) {
        reply.code(404).send({
          code: 404,
          message: `no data for ${symbol}`,
        });
        return;
      }

      // 运行形态识别
      const annotations = annotator.annotateAll(bars, patternTypes);

      reply.send({
        symbol,
        days: bars.length,
        has_today: hasToday,
        data_source: source,
        pattern_types: patternTypes,
        annotation_count: annotations.length,
        annotations,
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
