/**
 * DuckDB 单例 —— 直连本地 marketdb（来自 Financial-API 的 hithink-finance）
 * schema: v_symbol / v_daily / v_daily_qfq / v_daily_hfq
 */
import duckdb from 'duckdb';
import { existsSync } from 'node:fs';
const DB_PATH = process.env.ZETTARANC_MARKET_DB ??
    `${process.env.HOME}/.hithink-finance/market.duckdb`;
if (!existsSync(DB_PATH)) {
    console.warn(`[db] marketdb not found at ${DB_PATH}; some endpoints will 500`);
}
export const db = new duckdb.Database(DB_PATH, duckdb.OPEN_READONLY);
/**
 * duckdb-node 行为：只要传第二个参数就启用 prepared-statement 模式。
 * 若 SQL 里没有 `?` 但仍传 `[]`，会报 "excess parameters"。
 * 因此无占位符时改用直接 all(sql, cb)。
 */
export function query(sql, params = []) {
    return new Promise((resolve, reject) => {
        const cb = (err, rows) => {
            if (err)
                reject(err);
            else
                resolve(rows);
        };
        if (params.length === 0 && !sql.includes('?')) {
            db.all(sql, cb);
        }
        else {
            db.all(sql, params, cb);
        }
    });
}
export function tscodeToExchange(thscode) {
    if (thscode.endsWith('.SH'))
        return 'SH';
    if (thscode.endsWith('.SZ'))
        return 'SZ';
    if (thscode.endsWith('.BJ'))
        return 'BJ';
    return null;
}
