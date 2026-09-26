import { query } from '../services/db.js';
import { annotator } from '../services/annotator.js';
const THSCODE_RE = /^[0-9]{6}\.(SH|SZ|BJ)$/;
export async function annotateRoutes(app) {
    app.get('/annotate', async (req, reply) => {
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
            const rows = await query(`
        SELECT
          CAST(date AS VARCHAR) as date,
          open,
          high,
          low,
          close,
          volume,
          turnover
        FROM v_daily_qfq
        WHERE thscode = ?
        ORDER BY date DESC
        LIMIT ?
      `, [symbol, days]);
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
            const bars = rows.map(row => ({
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
        }
        catch (err) {
            app.log.error(err);
            reply.code(500).send({
                code: 500,
                message: 'internal error',
                error: err instanceof Error ? err.message : String(err),
            });
        }
    });
}
