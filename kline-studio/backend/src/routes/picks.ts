/**
 * /api/picks — 来自上级知识库的选股清单
 *
 * 数据源：kline-studio/data/picks.json（项目根目录下的静态 JSON 文件）
 * 用途：Zettaranc 复盘终端读取"上级 KB 推送的标的列表"，在 SymbolList
 *      「今日 picks」tab 中展示。
 *
 * GET  /api/picks          读取 picks（KB 推送后，kline-studio 前端轮询）
 * POST /api/picks          覆盖写入 picks（WeKnora agent tool 调用）
 *
 * 文件不存在或解析失败时 GET 返回空列表（不报错），保证前端 tab 始终能渲染。
 */
import type { FastifyInstance } from 'fastify';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

export interface Pick {
  ticker: string;
  exchange: string;
}

const __dirname = dirname(fileURLToPath(import.meta.url));
// 文件位于 kline-studio/data/picks.json（项目根），相对本文件需上溯 3 层：
//   backend/src/routes/picks.ts → backend/src/routes → backend/src → backend → kline-studio
const PICKS_FILE = resolve(__dirname, '../../../data/picks.json');
const PICKS_DIR = dirname(PICKS_FILE);

function isPick(x: unknown): x is Pick {
  if (typeof x !== 'object' || x === null) return false;
  const o = x as Record<string, unknown>;
  return typeof o.ticker === 'string' && typeof o.exchange === 'string';
}

export async function picksRoutes(app: FastifyInstance): Promise<void> {
  app.get('/picks', async (_req, reply) => {
    if (!existsSync(PICKS_FILE)) {
      app.log.warn(`picks.json not found at ${PICKS_FILE}`);
      reply.send({ code: 0, data: [] });
      return;
    }
    try {
      const raw = await readFile(PICKS_FILE, 'utf-8');
      const parsed: unknown = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        app.log.warn('picks.json is not an array, returning empty list');
        reply.send({ code: 0, data: [] });
        return;
      }
      const picks = parsed.filter(isPick);
      reply.send({ code: 0, data: picks });
    } catch (err) {
      app.log.warn({ err }, 'failed to read picks.json, returning empty list');
      reply.send({ code: 0, data: [] });
    }
  });

  // POST /api/picks — WeKnora agent tool 调用入口
  // Body: [{ticker, exchange}, ...]（覆盖语义：每次 POST 整体替换）
  app.post<{ Body: unknown }>('/picks', async (req, reply) => {
    const body = req.body;
    if (!Array.isArray(body)) {
      reply.code(400).send({ code: 400, message: 'body must be an array of {ticker, exchange}' });
      return;
    }
    const picks: Pick[] = body.filter(isPick);
    try {
      if (!existsSync(PICKS_DIR)) {
        await mkdir(PICKS_DIR, { recursive: true });
      }
      await writeFile(PICKS_FILE, JSON.stringify(picks, null, 2) + '\n', 'utf-8');
      app.log.info(`picks.json updated with ${picks.length} entries`);
      reply.send({ code: 0, data: { count: picks.length } });
    } catch (err) {
      app.log.error({ err }, 'failed to write picks.json');
      reply.code(500).send({ code: 500, message: 'failed to write picks.json' });
    }
  });
}