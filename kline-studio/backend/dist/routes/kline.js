import { query } from '../services/db.js';
const ADJUST_MAP = {
    none: 'v_daily',
    forward: 'v_daily_qfq',
    backward: 'v_daily_hfq',
};
const ADJUST_KEYS = new Set(Object.keys(ADJUST_MAP));
const THSCODE_RE = /^[0-9]{6}\.(SH|SZ|BJ)$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
/** DuckDB DATE → unix seconds（UTC 00:00） */
function dateToEpochSec(raw) {
    if (typeof raw === 'string') {
        const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
        if (m)
            return Math.floor(Date.UTC(+m[1], +m[2] - 1, +m[3]) / 1000);
    }
    const d = raw instanceof Date ? raw : new Date(String(raw));
    return Math.floor(d.getTime() / 1000);
}
export async function klineRoutes(app) {
    app.get('/kline', async (req, reply) => {
        const { symbol } = req.query;
        if (!symbol || !THSCODE_RE.test(symbol)) {
            reply.code(400).send({ code: 400, message: 'invalid symbol (expect 600519.SH)' });
            return;
        }
        const adjust = (req.query.adjust ?? 'none');
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
        const conditions = [`thscode = '${symbol}'`];
        if (from)
            conditions.push(`date >= '${from}'`);
        if (to)
            conditions.push(`date <= '${to}'`);
        let sql;
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
          sum(amount) as amount
        FROM ${view}
        WHERE ${conditions.join(' AND ')}
        GROUP BY ${trunc}
        ORDER BY date ASC
        LIMIT ${limit}
      `;
        }
        else {
            sql = `
        SELECT date, open, high, low, close, volume, amount
        FROM ${view}
        WHERE ${conditions.join(' AND ')}
        ORDER BY date ASC
        LIMIT ${limit}
      `;
        }
        const rows = await query(sql);
        reply.send({
            code: 0,
            data: rows.map((r) => ({
                ts: dateToEpochSec(r.date),
                open: r.open,
                high: r.high,
                low: r.low,
                close: r.close,
                volume: r.volume,
                turnover: r.amount,
            })),
        });
    });
}
