/**
 * 数据融合服务 - DuckDB 历史 + Financial-API 实时
 *
 * 策略：
 * 1. 从 DuckDB 读取历史 K 线（T-1 及之前）
 * 2. 如果今天是交易日，调用 Financial-API 获取今日实时数据
 * 3. 拼接返回完整数据
 */
import { request } from 'undici';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { query } from './db.js';

const FUYAO_BASE = 'https://fuyao.aicubes.cn';

// 加载 API Key
function loadApiKey(): string | null {
  // 优先环境变量
  if (process.env.HITHINK_FINANCE_API_KEY) {
    return process.env.HITHINK_FINANCE_API_KEY;
  }
  // 从 credentials 文件加载
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

export interface KLineBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  turnover: number;
}

interface DuckDBRow {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  turnover: number | null;
}

interface FuyaoHistoricalItem {
  date_ms: number;
  open_price: number;
  high_price: number;
  low_price: number;
  close_price: number;
  volume: number;
  turnover: number;
}

const ADJUST_MAP: Record<string, string> = {
  none: 'v_daily',
  forward: 'v_daily_qfq',
  backward: 'v_daily_hfq',
};

const THSCODE_RE = /^[0-9]{6}\.(SH|SZ|BJ)$/;

/**
 * 获取融合后的 K 线数据（历史 + 今日实时）
 */
export async function getMergedKLine(
  symbol: string,
  days: number = 120,
  adjust: 'none' | 'forward' | 'backward' = 'forward'
): Promise<{ bars: KLineBar[]; hasToday: boolean; source: string }> {
  if (!THSCODE_RE.test(symbol)) {
    throw new Error(`invalid symbol: ${symbol}`);
  }

  const view = ADJUST_MAP[adjust] ?? 'v_daily_qfq';

  // 1. 从 DuckDB 读取历史数据
  const historyRows = await query<DuckDBRow>(`
    SELECT
      CAST(date AS VARCHAR) as date,
      open, high, low, close, volume, turnover
    FROM ${view}
    WHERE thscode = '${symbol}'
    ORDER BY date DESC
    LIMIT ${days + 5}
  `);

  if (historyRows.length === 0) {
    throw new Error(`no data for ${symbol}`);
  }

  // 反转为时间正序
  historyRows.reverse();

  // 检查最后一条数据的日期
  const lastDate = historyRows[historyRows.length - 1].date;
  const today = getTodayStr();
  const hasToday = lastDate === today;

  let bars: KLineBar[] = historyRows.map(row => ({
    date: row.date,
    open: row.open,
    high: row.high,
    low: row.low,
    close: row.close,
    volume: row.volume,
    turnover: row.turnover ?? 0,
  }));

  let source = 'duckdb';

  // 2. 如果今天没有数据，尝试从 Financial-API 获取
  if (!hasToday && isTradeDay() && isInTradeTime()) {
    const apiKey = loadApiKey();
    if (apiKey) {
      try {
        const todayBar = await fetchTodayKLine(symbol, apiKey, adjust);
        if (todayBar) {
          bars.push(todayBar);
          source = 'duckdb+fuyao';
        }
      } catch (err) {
        // API 失败不影响历史数据
        console.warn('[data-merge] fetch today failed:', (err as Error).message);
      }
    }
  }

  // 只返回最后 N 条
  if (bars.length > days) {
    bars = bars.slice(bars.length - days);
  }

  return {
    bars,
    hasToday: bars.length > 0 && bars[bars.length - 1].date === today,
    source,
  };
}

/**
 * 从 Financial-API 获取今日实时 K 线
 */
async function fetchTodayKLine(
  symbol: string,
  apiKey: string,
  adjust: string
): Promise<KLineBar | null> {
  const today = getTodayStr();
  const startMs = Date.parse(today + 'T00:00:00+08:00');
  const endMs = Date.now();

  const url = `${FUYAO_BASE}/api/a-share/prices/historical?` +
    `thscode=${symbol}&interval=1d&start=${startMs}&end=${endMs}&adjust=${adjust}`;

  const res = await request(url, {
    headers: { 'X-api-key': apiKey },
    headersTimeout: 10000,
    bodyTimeout: 10000,
  });

  if (res.statusCode >= 400) {
    return null;
  }

  const body = await res.body.json() as {
    code: number;
    data?: { item?: FuyaoHistoricalItem[] };
  };

  if (body.code !== 0 || !body.data?.item?.length) {
    return null;
  }

  // 只取最新的一条（今天）
  const item = body.data.item[body.data.item.length - 1];
  const itemDate = new Date(item.date_ms).toISOString().slice(0, 10);

  if (itemDate !== today) {
    return null;
  }

  return {
    date: today,
    open: item.open_price,
    high: item.high_price,
    low: item.low_price,
    close: item.close_price,
    volume: item.volume,
    turnover: item.turnover,
  };
}

/** 获取今天日期字符串 YYYY-MM-DD */
function getTodayStr(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' });
}

/** 判断今天是否是交易日（简单判断：周一到周五） */
function isTradeDay(): boolean {
  const day = new Date().getDay();
  return day >= 1 && day <= 5;
}

/** 判断当前是否在交易时间（9:15-15:05，留几分钟余量） */
function isInTradeTime(): boolean {
  const now = new Date();
  const hour = now.getHours();
  const min = now.getMinutes();
  const totalMin = hour * 60 + min;
  // 9:15 = 555, 15:05 = 905
  return totalMin >= 555 && totalMin <= 905;
}
