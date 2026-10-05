/**
 * `zettaranc.screener` 结果的可信度字段解析。
 *
 * 为什么要单独一个模块：这张卡片此前只读 `stocks`，把后端算好的一整套
 * 可信度字段全丢了。用户在界面上只看到「当前没有推送标的。」，无法区分：
 *
 *   - 扫了 5,571 只，确实一个都没命中
 *   - 命中了，但被风险过滤（负债率/流动比率/排除 ST）全筛掉
 *   - 有票因缺指标数据根本没参与筛选（no_indicator_count）
 *   - 价量数据没并上，「放量突破」这类形态信号整体失效
 *   - 候选集被截断
 *
 * 后端对最后一种尤其在意，main.py 的注释原话是「必须让调用方知道，
 * 否则『没选出票』会被读成『市场里没有』」——而这正是界面上发生的事。
 *
 * 本模块只做**纯数据到事实的映射**，不碰 i18n、不碰 Vue：返回带 i18n key
 * 的事实列表，由组件负责翻译。这样逻辑可以被 `tsx --test` 直接测
 * （全仓 0 个 .vue 测试，组件测不了，但纯逻辑可以）。
 *
 * 与 KLineStudioResult.vue 共用 `resolveScreenerPayload` 是刻意的：
 * `pickList` 读 stocks、这里读 scanned，两处若各写一遍解析，迟早会像
 * 今天的 register #20 那样漂移成"一个能跑一个不能跑"。
 */

/** 后端返回体的可信度字段子集（`python-service/main.py` 的 `/zettaranc/screen`）。 */
export interface ScreenerPayload {
  stocks?: unknown;
  /** 本次筛选覆盖的候选集大小。板块/涨停池限定后小于全市场。 */
  universe?: unknown;
  /** 实际参与筛选的标的数，不是清单长度。 */
  scanned?: unknown;
  /** 清单长度（后端历史字段，保留以兼容）。 */
  scanned_from_universe?: unknown;
  /** 策略命中数（风险过滤之前）。 */
  matched?: unknown;
  /** 因缺指标数据被丢掉的标的数。 */
  no_indicator_count?: unknown;
  /** 数据不完整的标的数。 */
  incomplete?: unknown;
  /** 价量维度是否并上。false 表示放量突破类信号本次全部失效。 */
  price_available?: unknown;
  /** 实际并上的价量行数，0 同样是"形态信号失效"的信号。 */
  price_merged_rows?: unknown;
  /** 候选集是否被截断。 */
  truncated?: unknown;
  /** 被风险过滤剔除的标的（数组或计数皆可）。 */
  risk_rejects?: unknown;
  warnings?: unknown;
}

export type ScreenerFactTone = 'info' | 'warn';

export interface ScreenerFact {
  /** `chat.klineStudio.coverage.*` 下的 i18n key。 */
  key: string;
  params?: Record<string, string | number>;
  tone: ScreenerFactTone;
}

export interface ScreenerCoverage {
  /** 实际参与筛选的标的数；后端没给则为 null（不是 0，别混为一谈）。 */
  scanned: number | null;
  /** 候选集大小。 */
  universe: number | null;
  /** 策略命中数（风险过滤之前）。 */
  matched: number | null;
  facts: ScreenerFact[];
}

/**
 * 拆掉 Go 工具那层 `{strategy, limit, data: ...}` 包装。
 * 兼容历史上 `stocks` 直接挂在顶层的形态。
 */
export function resolveScreenerPayload(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
  const outer = raw as Record<string, unknown>;
  const inner = outer.data;
  if (inner && typeof inner === 'object' && !Array.isArray(inner)) {
    return inner as Record<string, unknown>;
  }
  return outer;
}

/** 只有有限非负整数才算数；null/undefined/NaN/负数一律当"没给"。 */
function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

function count(value: unknown): number | null {
  if (Array.isArray(value)) return value.length;
  return num(value);
}

function strList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === 'string' && v.trim() !== '');
}

/**
 * 把可信度字段映射成事实列表。只在数据**真的支持**该说法时才产出条目——
 * 没给 scanned 就不要编一句"扫描了 N 只"，那正是这套字段当初被藏起来的
 * 原因：宁可少说，不可说错。
 */
export function readScreenerCoverage(raw: unknown): ScreenerCoverage {
  const p = resolveScreenerPayload(raw) as ScreenerPayload;

  // 刻意**不做** `scanned ?? scanned_from_universe` 的回退。
  // main.py 记着同一个教训：旧实现把 scanned 报成 len(names)，那是**清单长度**
  // 不是实际扫描数，调用方无从判断哪些票没被扫到。scanned_from_universe 与
  // universe 也不是一回事（前者是全清单，universe 是板块/涨停池限定后的候选集）。
  // 宁可报"未知"，也不拿一个语义相近的数字顶替——那正是这套字段被藏起来的原因。
  const scanned = num(p.scanned);
  const universe = num(p.universe);
  const matched = num(p.matched);
  const facts: ScreenerFact[] = [];

  if (scanned !== null && universe !== null) {
    facts.push({
      key: 'scanned',
      params: { scanned, universe },
      tone: 'info',
    });
  } else if (scanned !== null) {
    facts.push({ key: 'scannedOnly', params: { scanned }, tone: 'info' });
  }

  const rejected = count(p.risk_rejects);
  if (rejected !== null && rejected > 0) {
    facts.push({ key: 'riskRejected', params: { count: rejected }, tone: 'info' });
  }

  const noIndicator = num(p.no_indicator_count);
  if (noIndicator !== null && noIndicator > 0) {
    facts.push({ key: 'noIndicator', params: { count: noIndicator }, tone: 'warn' });
  }

  const priceAvailable = p.price_available;
  const priceRows = num(p.price_merged_rows);
  if (priceAvailable === false || (priceRows !== null && priceRows === 0)) {
    // main.py 明确要求透出：0 意味着放量突破类形态信号本次全部失效。
    facts.push({ key: 'priceUnavailable', tone: 'warn' });
  }

  const incomplete = num(p.incomplete);
  if (incomplete !== null && incomplete > 0) {
    facts.push({ key: 'incomplete', params: { count: incomplete }, tone: 'info' });
  }

  if (p.truncated === true) {
    facts.push({ key: 'truncated', tone: 'warn' });
  }

  for (const w of strList(p.warnings)) {
    facts.push({ key: 'warning', params: { text: w }, tone: 'warn' });
  }

  return { scanned, universe, matched, facts };
}
