/**
 * /api/indicators — 技术指标查询
 *
 * GET /api/indicators?symbol=600519.SH&days=120&categories=momentum,overlap,zettaranc
 *
 * 从 indicators.duckdb 读取预计算指标
 */
import type { FastifyInstance } from 'fastify';
import { queryIndicators } from '../services/db.js';

const THSCODE_RE = /^[0-9]{6}\.(SH|SZ|BJ)$/;

// 指标分类定义
const CATEGORY_COLUMNS: Record<string, string[]> = {
  momentum: [
    'momentum_rsi_6', 'momentum_rsi_14', 'momentum_rsi_24',
    'momentum_macd_12_26_9_macd', 'momentum_macd_12_26_9_signal', 'momentum_macd_12_26_9_hist',
    'momentum_kdj_9_3_3_k', 'momentum_kdj_9_3_3_d', 'momentum_kdj_9_3_3_j',
  ],
  overlap: [
    'overlap_sma_5', 'overlap_sma_10', 'overlap_sma_20', 'overlap_sma_60',
    'overlap_ema_5', 'overlap_ema_10', 'overlap_ema_20', 'overlap_ema_60',
    'overlap_wma_20',
  ],
  volume: [
    'volume_obv', 'volume_ad', 'volume_mfi_14', 'volume_cmf_20', 'volume_vwap',
  ],
  trend: [
    'trend_psar', 'trend_adx_14',
    'trend_aroon_25_aroonup', 'trend_aroon_25_aroondown',
    'trend_supertrend_10_3_0_trend', 'trend_supertrend_10_3_0_value',
  ],
  volatility: [
    'volatility_bbands_20_2_0_upper', 'volatility_bbands_20_2_0_middle', 'volatility_bbands_20_2_0_lower',
    'volatility_atr_14',
  ],
  zettaranc: [
    'zettaranc_zg_white_10',   // 白线（EMA10）
    'zettaranc_dg_yellow_14',  // 黄线（EMA14）
    'zettaranc_bbi',           // BBI（牵牛绳）
    'zettaranc_brick_value',   // 砖形图
    'zettaranc_rsl_short_3',   // 短期 RSL
    'zettaranc_rsl_long_21',   // 长期 RSL
  ],
  candles: [
    // 常用 K 线形态
    'candles_cdl_doji_0',
    'candles_cdl_hammer_0',
    'candles_cdl_hangingman_0',
    'candles_cdl_engulfing_0',
    'candles_cdl_morningstar_0',
    'candles_cdl_eveningstar_0',
    'candles_cdl_3whitesoldiers_0',
    'candles_cdl_3blackcrows_0',
  ],
};

export async function indicatorsRoutes(app: FastifyInstance): Promise<void> {
  app.get<{
    Querystring: {
      symbol?: string;
      days?: number;
      categories?: string;
    };
  }>('/indicators', async (req, reply) => {
    const { symbol, days = 120, categories = 'zettaranc' } = req.query;

    if (!symbol || !THSCODE_RE.test(symbol)) {
      reply.code(400).send({ code: 400, message: 'invalid symbol' });
      return;
    }

    // 解析要查询的分类
    const cats = categories.split(',').map(c => c.trim()).filter(c => c in CATEGORY_COLUMNS);
    if (cats.length === 0) {
      reply.code(400).send({ code: 400, message: 'no valid categories' });
      return;
    }

    // 收集要查询的列
    const columns: string[] = ['date'];
    for (const cat of cats) {
      columns.push(...CATEGORY_COLUMNS[cat]);
    }

    try {
      const sql = `
        SELECT ${columns.join(', ')}
        FROM v_indicators_daily
        WHERE thscode = '${symbol}'
        ORDER BY date DESC
        LIMIT ${days}
      `;
      const rows = await queryIndicators(sql);

      if (rows.length === 0) {
        reply.code(404).send({ code: 404, message: `no indicators for ${symbol}` });
        return;
      }

      // 反转为时间正序
      rows.reverse();

      reply.send({
        code: 0,
        symbol,
        categories: cats,
        count: rows.length,
        data: rows,
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

  // 获取所有分类和指标列表
  app.get('/indicators/catalog', async () => {
    return {
      categories: Object.fromEntries(
        Object.entries(CATEGORY_COLUMNS).map(([cat, cols]) => [cat, { count: cols.length, columns: cols }])
      ),
    };
  });
}
