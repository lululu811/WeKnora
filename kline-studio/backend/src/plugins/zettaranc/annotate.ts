/**
 * /api/annotate — Z 哥交易体系形态标注
 *
 * GET /api/annotate?symbol=600519.SH&days=120&patterns=b1,s1,key_k
 *
 * 从 DuckDB 读取 K 线数据，运行形态识别算法，返回标注结果
 */
import type { FastifyInstance } from 'fastify';
import { query } from '../../services/db.js';
import { annotator, type Pattern } from './annotator.js';
import type { KLineBar } from './indicators.js';

const THSCODE_RE = /^[0-9]{6}\.(SH|SZ|BJ)$/;

interface KLineRow {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  turnover: number | null;
}

export async function annotateRoutes(app: FastifyInstance): Promise<void> {
  app.get<{
    Querystring: {
      symbol?: string;
      days?: number;
      patterns?: string;
    };
  }>('/annotate', async (req, reply) => {
    const { symbol, days = 120, patterns = 'b1,key_k,s1,violent_k' } = req.query;

    if (!symbol || !THSCODE_RE.test(symbol)) {
      reply.code(400).send({
        code: 400,
        message: 'invalid symbol (expect 600519.SH)',
      });
      return;
    }

    // 解析要检测的形态
    const patternTypes = patterns.split(',').map(p => p.trim()).filter(Boolean);

    try {
      // 从 DuckDB 读取前复权日 K 线
      // 注意：duckdb-node 对 LIMIT 参数化有问题，直接拼接（已验证 symbol 格式）
      const rows = await query<KLineRow>(`
        SELECT
          CAST(date AS VARCHAR) as date,
          open,
          high,
          low,
          close,
          volume,
          turnover
        FROM v_daily_qfq
        WHERE thscode = '${symbol}'
        ORDER BY date DESC
        LIMIT ${days}
      `);

      if (rows.length === 0) {
        reply.code(404).send({
          code: 404,
          message: `no data for ${symbol}`,
        });
        return;
      }

      // 反转为时间正序
      rows.reverse();

      // 转换为 KLineBar 格式
      const bars: KLineBar[] = rows.map(row => ({
        date: row.date,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume,
        turnover: row.turnover ?? undefined,
      }));

      // 运行形态识别
      const annotations = annotator.annotateAll(bars, patternTypes);

      reply.send({
        symbol,
        days: rows.length,
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
