/**
 * 多数据库连接管理
 */
import duckdb from 'duckdb';
import { existsSync } from 'fs';

const MARKET_DB_PATH =
  process.env.ZETTARANC_MARKET_DB ??
  `${process.env.HOME}/.hithink-finance/market.duckdb`;

const INDICATORS_DB_PATH =
  process.env.ZETTARANC_INDICATORS_DB ??
  `${process.env.HOME}/.hithink-finance/indicators.duckdb`;

// 市场数据库（K 线、代码表）
let marketDb: duckdb.Database | null = null;

// 指标数据库（技术指标、Z 哥指标）
let indicatorsDb: duckdb.Database | null = null;

export function getMarketDb(): duckdb.Database {
  if (!marketDb) {
    if (!existsSync(MARKET_DB_PATH)) {
      throw new Error(`Market DB not found: ${MARKET_DB_PATH}`);
    }
    marketDb = new duckdb.Database(MARKET_DB_PATH, duckdb.OPEN_READONLY);
    console.log(`[db] connected to market db: ${MARKET_DB_PATH}`);
  }
  return marketDb;
}

export function getIndicatorsDb(): duckdb.Database {
  if (!indicatorsDb) {
    if (!existsSync(INDICATORS_DB_PATH)) {
      throw new Error(`Indicators DB not found: ${INDICATORS_DB_PATH}`);
    }
    indicatorsDb = new duckdb.Database(INDICATORS_DB_PATH, duckdb.OPEN_READONLY);
    console.log(`[db] connected to indicators db: ${INDICATORS_DB_PATH}`);
  }
  return indicatorsDb;
}

export interface QueryRow {
  [column: string]: unknown;
}

/**
 * 查询市场数据库
 */
export function queryMarket<T = QueryRow>(sql: string, params: unknown[] = []): Promise<T[]> {
  return new Promise<T[]>((resolve, reject) => {
    const db = getMarketDb();
    const cb = (err: Error | null, rows: unknown) => {
      if (err) reject(err);
      else resolve(rows as T[]);
    };
    if (params.length === 0 && !sql.includes('?')) {
      db.all(sql, cb);
    } else {
      db.all(sql, params, cb);
    }
  });
}

/**
 * 查询指标数据库
 */
export function queryIndicators<T = QueryRow>(sql: string, params: unknown[] = []): Promise<T[]> {
  return new Promise<T[]>((resolve, reject) => {
    const db = getIndicatorsDb();
    const cb = (err: Error | null, rows: unknown) => {
      if (err) reject(err);
      else resolve(rows as T[]);
    };
    if (params.length === 0 && !sql.includes('?')) {
      db.all(sql, cb);
    } else {
      db.all(sql, params, cb);
    }
  });
}

/**
 * 关闭所有数据库连接
 */
export function closeAll(): void {
  marketDb?.close();
  indicatorsDb?.close();
  marketDb = null;
  indicatorsDb = null;
}

// 兼容旧接口
export { queryMarket as query };

export function tscodeToExchange(thscode: string): 'SH' | 'SZ' | 'BJ' | null {
  if (thscode.endsWith('.SH')) return 'SH';
  if (thscode.endsWith('.SZ')) return 'SZ';
  if (thscode.endsWith('.BJ')) return 'BJ';
  return null;
}
