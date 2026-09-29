/**
 * 个股画像取数与归一（资金面 / 估值 / 板块）
 *
 * 端点：GET /api/stock-profile?symbol=600519.SH
 * 由 python-service 承接，一次返回三个区块。合并成一个接口而不是三个，
 * 是因为这张卡是**鼠标划过就触发**的：扫 5 个 ticker 打 5 次请求，
 * 拆三个接口就变成 15 个并发。
 *
 * 数据源（后端 SQL 里的真实表，改表名时同步这里）：
 *   - 资金面  special.v_limit_up_pool / v_limit_break_pool / v_dragon_tiger / v_hot_stock
 *   - 估值    financials.v_valuation_latest
 *   - 板块    index.v_index_constituents ⋈ index.v_index_universe
 *
 * 与 stock-score.ts 同一原则：**算不出就是算不出**。
 * 查不到的字段一律 null，交给渲染层显示「无数据」，绝不用 0 顶替 ——
 * 0 在金融语义里是"真的等于零"，用它冒充缺失比留空更危险。
 */

/** 资金面统计窗口。30 个自然日，约等于一个月的交易日。 */
const CAPITAL_WINDOW_DAYS = 30;

export interface StockProfile {
  symbol: string;
  capital: CapitalProfile | null;
  valuation: ValuationProfile | null;
  sectors: SectorChip[];
  /** 后端查不到的库名。渲染层据此说明「为什么这块是空的」。 */
  unavailable: string[];
}

export interface CapitalProfile {
  /** 窗口内涨停次数 */
  limitUpCount: number;
  /** 窗口内最高连板数 */
  maxContinueDays: number;
  /** 最近一次涨停日（窗口内），没有则 null */
  lastLimitUpDate: string | null;
  /** 窗口内炸板次数 */
  limitBreakCount: number;
  /** 龙虎榜上榜次数 */
  dragonCount: number;
  /** 最近一次龙虎榜净买额（元），没有则 null */
  dragonNetValue: number | null;
  /** 热门榜最近排名，没有则 null */
  hotRank: number | null;
  /** 窗口内数据涉及的最晚交易日 */
  asOf: string | null;
  sources: Record<string, string>;
}

export interface ValuationProfile {
  snapshotDate: string | null;
  /** 三个都可能是 null：亏损股 pe_ttm 为负或缺失，破净股 pb 为 0 */
  peTtm: number | null;
  pbMrq: number | null;
  psTtm: number | null;
  source: string;
}

export interface SectorChip {
  name: string;
  tag: string;
}

/** 板块标签的展示优先级。tszs 是同花顺特色指数，一票有二十多个，
 *  全铺开会把卡片撑爆且没有信息量 —— 只在计数里体现。 */
const SECTOR_TAG_ORDER = ['industry', 'region', 'cn_concept', 'tszs'];

/** 各标签最多展示几个 chip，超出只报数量。 */
const SECTOR_TAG_LIMIT: Record<string, number> = {
  industry: 2,
  region: 1,
  cn_concept: 4,
  tszs: 0,
};

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function str(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null;
}

/** 距今天数。日期串是 'YYYY-MM-DD'，按本地零点比较，避开时区把边界日子算错一天。 */
function daysAgo(dateStr: string | null, now: number): number | null {
  if (!dateStr) return null;
  const t = Date.parse(`${dateStr}T00:00:00`);
  if (!Number.isFinite(t)) return null;
  return Math.floor((now - t) / 86_400_000);
}

function normalizeCapital(raw: any, now: number): CapitalProfile | null {
  if (!raw) return null;
  const sources: Record<string, string> = (raw.sources && typeof raw.sources === 'object')
    ? raw.sources
    : {};

  const inWindow = (rows: any[] | null | undefined, field: string) =>
    (Array.isArray(rows) ? rows : []).filter((r) => {
      const d = daysAgo(str(r?.[field]), now);
      return d != null && d <= CAPITAL_WINDOW_DAYS;
    });

  const ups = inWindow(raw.limit_up, 'trade_date');
  const breaks = inWindow(raw.limit_break, 'trade_date');
  const dragons = inWindow(raw.dragon_tiger, 'trade_date');
  // v_hot_stock 用的是 capture_date，不是 trade_date
  const hots = inWindow(raw.hot, 'capture_date');

  // 四类全空 → 这只票在窗口内没有任何资金异动，是**结论**不是缺失
  if (!ups.length && !breaks.length && !dragons.length && !hots.length) return null;

  const dates = [...ups, ...breaks, ...dragons, ...hots]
    .map((r) => str(r.trade_date) || str(r.capture_date))
    .filter((d): d is string => d != null)
    .sort();

  const continueDays = ups.map((r) => num(r.continue_day_cnt)).filter((n): n is number => n != null);

  return {
    limitUpCount: ups.length,
    maxContinueDays: continueDays.length ? Math.max(...continueDays) : 0,
    lastLimitUpDate: str(ups[0]?.trade_date),
    limitBreakCount: breaks.length,
    dragonCount: dragons.length,
    // 后端已按日期倒序，第一条即最近一次
    dragonNetValue: num(dragons[0]?.net_value),
    hotRank: num(hots[0]?.rank),
    asOf: dates.length ? dates[dates.length - 1] : null,
    sources,
  };
}

function normalizeValuation(raw: any): ValuationProfile | null {
  if (!raw) return null;
  return {
    snapshotDate: str(raw.snapshot_date),
    peTtm: num(raw.pe_ttm),
    pbMrq: num(raw.pb_mrq),
    psTtm: num(raw.ps_ttm),
    source: str(raw.source) ?? 'financials.v_valuation_latest',
  };
}

/**
 * 板块归属裁剪：按标签分组限额展示，返回「前 N 个」+ 每组被折叠的数量。
 * 全 A 平均每只票有 20 个板块，全铺开会让 320px 的卡变成标签墙。
 */
export function pickSectors(sectors: SectorChip[]): { shown: SectorChip[]; hidden: number } {
  const byTag = new Map<string, SectorChip[]>();
  for (const s of sectors) {
    if (!s?.name) continue;
    const list = byTag.get(s.tag) ?? [];
    list.push(s);
    byTag.set(s.tag, list);
  }
  const shown: SectorChip[] = [];
  let hidden = 0;
  for (const tag of SECTOR_TAG_ORDER) {
    const list = byTag.get(tag);
    if (!list) continue;
    const limit = SECTOR_TAG_LIMIT[tag] ?? 0;
    shown.push(...list.slice(0, limit));
    hidden += Math.max(0, list.length - limit);
  }
  return { shown, hidden };
}

/** 归一后端响应。任何字段缺失都归一为 null，不抛错。 */
export function normalizeProfile(raw: any, symbol: string): StockProfile {
  const now = Date.now();
  const rawSectors: SectorChip[] = Array.isArray(raw?.sectors) ? raw.sectors : [];
  return {
    symbol,
    capital: normalizeCapital(raw?.capital, now),
    valuation: normalizeValuation(raw?.valuation),
    sectors: rawSectors.filter((s) => typeof s?.name === 'string'),
    unavailable: Array.isArray(raw?.unavailable) ? raw.unavailable : [],
  };
}

/** 按代码缓存画像。与 K 线同一套 60s TTL —— 资金面按日更新，秒级刷新没意义。 */
const PROFILE_TTL = 60_000;
const profileCache = new Map<string, { at: number; data: StockProfile }>();

export function getCachedProfile(symbol: string): StockProfile | null {
  const hit = profileCache.get(symbol);
  if (hit && Date.now() - hit.at < PROFILE_TTL) return hit.data;
  return null;
}

export function setCachedProfile(symbol: string, data: StockProfile): void {
  profileCache.set(symbol, { at: Date.now(), data });
}
