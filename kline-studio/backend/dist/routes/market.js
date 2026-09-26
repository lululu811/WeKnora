import { query } from '../services/db.js';
export async function marketRoutes(app) {
    app.get('/market/overview', async (_req, reply) => {
        try {
            const sql = `
        WITH top_days AS (
          SELECT distinct date FROM v_daily ORDER BY date DESC LIMIT 2
        ),
        data_two_days AS (
          SELECT thscode, date, close
          FROM v_daily
          WHERE date IN (SELECT date FROM top_days)
        ),
        changes AS (
          SELECT 
            thscode,
            close,
            lag(close) OVER (PARTITION BY thscode ORDER BY date) as prev_close
          FROM data_two_days
        )
        SELECT 
          (SELECT max(date) FROM top_days) as date,
          count(case when close > prev_close then 1 end) as up_count,
          count(case when close < prev_close then 1 end) as down_count,
          count(case when close = prev_close then 1 end) as flat_count,
          count(case when (close - prev_close) / prev_close >= 0.098 then 1 end) as limit_up_count,
          count(case when (close - prev_close) / prev_close <= -0.098 then 1 end) as limit_down_count
        FROM changes
        WHERE prev_close IS NOT NULL;
      `;
            const rows = await query(sql);
            if (rows.length === 0) {
                reply.send({
                    code: 0,
                    data: {
                        date: '',
                        upCount: 0,
                        downCount: 0,
                        flatCount: 0,
                        limitUpCount: 0,
                        limitDownCount: 0,
                        sentimentRatio: 50,
                    },
                });
                return;
            }
            const row = rows[0];
            const up = Number(row.up_count ?? 0);
            const down = Number(row.down_count ?? 0);
            const flat = Number(row.flat_count ?? 0);
            const limitUp = Number(row.limit_up_count ?? 0);
            const limitDown = Number(row.limit_down_count ?? 0);
            const total = up + down + flat;
            const sentimentRatio = total > 0 ? Math.round((up / total) * 100) : 50;
            const dateStr = row.date instanceof Date
                ? row.date.toISOString().slice(0, 10)
                : String(row.date ?? '').slice(0, 10);
            reply.send({
                code: 0,
                data: {
                    date: dateStr,
                    upCount: up,
                    downCount: down,
                    flatCount: flat,
                    limitUpCount: limitUp,
                    limitDownCount: limitDown,
                    sentimentRatio,
                },
            });
        }
        catch (err) {
            reply.code(500).send({
                code: 500,
                message: `market overview failed: ${err.message}`,
            });
        }
    });
}
