/**
 * 股票名缓存 —— 启动时一次性从 fuyao 拉全 A 股 + 指数代码表到内存 Map。
 *
 * 数据量：~5500 个标的，一次请求 ~100KB，2-3 页拉完，秒级。
 * 增量更新：本次不实现（每天一次全量替换即可）
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { request } from 'undici';

const FUYAO_BASE = 'https://fuyao.aicubes.cn';

interface TickerItem {
  thscode: string;
  ticker: string;
  name: string;
  exchange: string | null;
  asset_type: string;
}

interface TickersListEnvelope {
  data: { timestamp: number; item: TickerItem[] };
}

class NameCache {
  private map = new Map<string, string>();
  private loaded = false;
  private loadingPromise: Promise<void> | null = null;

  private get apiKey(): string | null {
    return process.env.HITHINK_FINANCE_API_KEY ?? loadKeyFromCredentials();
  }

  /** 启动时调用一次：拉满 SH+SZ+BJ 的 a-share 代码表 */
  async loadAll(): Promise<void> {
    if (this.loaded) return;
    if (this.loadingPromise) return this.loadingPromise;
    this.loadingPromise = this.doLoad().catch((err) => {
      console.warn('[name-cache] load failed:', err);
    });
    await this.loadingPromise;
    this.loadingPromise = null;
  }

  private async doLoad(): Promise<void> {
    const key = this.apiKey;
    if (!key) {
      console.warn('[name-cache] no API key; names will fall back to ticker');
      this.loaded = true;
      return;
    }
    const headers = { 'X-api-key': key };
    const assetTypes = ['a-share', 'a-share-index'];
    for (const assetType of assetTypes) {
      let offset = 0;
      const limit = 5000;
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const url =
          `${FUYAO_BASE}/api/meta/tickers/list?exchange=SH,SZ,BJ` +
          `&asset_type=${assetType}&limit=${limit}&offset=${offset}`;
        const res = await request(url, {
          headers,
          headersTimeout: 10000,
          bodyTimeout: 10000,
        });
        if (res.statusCode !== 200) {
          console.warn(`[name-cache] fuyao ${res.statusCode} for ${assetType}@${offset}`);
          break;
        }
        const body = (await res.body.json()) as TickersListEnvelope;
        const items = body.data?.item ?? [];
        for (const it of items) {
          if (it.name) this.map.set(it.thscode, it.name);
        }
        if (items.length < limit) break;
        offset += limit;
      }
    }
    this.loaded = true;
    console.log(`[name-cache] loaded ${this.map.size} symbol names`);
  }

  /** 同步查表：thscode -> name | undefined */
  get(thscode: string): string | undefined {
    return this.map.get(thscode);
  }
  /** 全量 thscode -> name 列表（用于中文名搜索等） */
  entries(): Array<[string, string]> {
    return Array.from(this.map.entries());
  }
}

function loadKeyFromCredentials(): string | null {
  try {
    const file = join(
      process.env.HOME ?? '',
      'Library/Application Support/hithink-finance/credentials.env',
    );
    const text = readFileSync(file, 'utf8');
    const m = text.match(/HITHINK_FINANCE_API_KEY=(.+)/);
    return m ? m[1].trim() : null;
  } catch {
    return null;
  }
}

export const nameCache = new NameCache();
