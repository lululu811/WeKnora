/**
 * 战法主图 / 副图的共享配色。
 *
 * 单独抽出来的原因：`theme.ts`（KLineChart 的 styles）和 `indicators.ts`（自定义
 * 指标自己的 draw）都都要画那几条线。两边各写一份颜色，改了一边忘了另一边，
 * 就会出现"线在、但颜色对不上"这种极难排查的错位——而且白底/深底两套色一多，
 * 漂移会成倍增长。
 *
 * **关于"白线"**：战法主图第一条线（DEMA 10）历史上是 `#FFFFFF`，模式也因此叫
 * 「白黄+BBI」。白线画在白底上完全隐形，所以浅色主题下它必须换成深板岩色。
 * 2026-09-27 起模式改称「**双线+BBI**」——颜色是为了白底可读性做的调整，
 * 不该让"白/黄"这两个已经不成立的颜色字继续留在界面上。
 */

export interface ZettarancPalette {
  /** 战法主图第一条线（DEMA 10）。深色底=白，浅色底=深板岩。 */
  white: string;
  /** 战法主图第二条线（LongBBI）。 */
  yellow: string;
  /** 牵牛绳（多空分界）。 */
  orange: string;
  /** MACD/KDJ 等副图的辅助线。 */
  sky: string;
  purple: string;
  /** 中性/平局色。浅色底下需要压深才有对比度。 */
  neutral: string;
  /** 覆盖层（九转数字、形态气泡）里的中性文字。 */
  overlayNeutral: string;
  /** 副图辅助线（MACD 的 signal、KDJ 的 D、RSL 等）。 */
  auxAmber: string;
  auxSky: string;
  auxOrange: string;
  /** 关键K 徽章色。浅色底下亮蓝只有 2.4:1，同样要压深。 */
  keyBlue: string;
  /** 本地 K 线形态徽章（乌云压顶 / 曙光初现 / 十字星 / 早晨之星）。 */
  patternCloud: string;
  patternDawn: string;
  patternDoji: string;
  patternMorningStar: string;
  /**
   * 5 星持股评分色，从 5 分到 1 分。
   * 用在**悬停卡片**（StockCitationFloat）上而不是画布，但同样要跟主题走：
   * 白底上原来的 4 分橙 / 3 分黄 / 2 分青 / 1 分绿只有 1.9~2.8:1，基本看不清。
   */
  scoreStars: [string, string, string, string, string];
  /**
   * 神奇九转的 1~8 倒数文字色。这两个数字是**裸文字直接画在画布上**（只有「9」有
   * 方框徽章），所以浅色底下必须自己够深：原来的 #fca5a5 / #86efac 在米底上只有
   * 1.77:1 / 1.31:1，等于看不见。
   */
  td9Up: string;
  td9Down: string;
  /** KDJ 的 J 线。浅色底下 #f472b6 只有 2.48:1。 */
  kdjJ: string;
  /** 涨。浅色底下必须加深，否则米底上只有 3.5:1。 */
  up: string;
  /** 跌。浅色底下更严重——亮绿在米底上只有 2.4:1，基本看不见。 */
  down: string;
}

const dark: ZettarancPalette = {
  white: '#FFFFFF',
  yellow: '#FFD700',
  orange: '#FF8C00',
  sky: '#00BFFF',
  purple: '#C084FC',
  neutral: '#9ca3af',
  overlayNeutral: '#c9d1d9',
  auxAmber: '#f59e0b',
  auxSky: '#38bdf8',
  auxOrange: '#fb923c',
  keyBlue: '#3b82f6',
  // 深色底上这两个本来就极清楚（9.7:1 / 13.1:1），维持原样。
  td9Up: '#fca5a5',
  td9Down: '#86efac',
  kdjJ: '#f472b6',
  patternCloud: '#06b6d4',
  patternDawn: '#f43f5e',
  patternDoji: '#38bdf8',
  patternMorningStar: '#e11d48',
  // 深色底 #181d26 上这 5 色实测 4.5~8.8:1，全部够用，维持原样。
  scoreStars: ['#ef4444', '#f97316', '#eab308', '#14b8a6', '#10b981'],
  // 深色底上亮红/亮绿本来就够清楚（4.9:1 / 7.3:1），维持原样。
  up: '#ef4444',
  down: '#10b981',
}

// 浅色底下每条线都换成 600~800 档的深色，并整体偏暖。这不是审美偏好，先是
// 对比度问题：原来那套亮色在浅底上的实测对比度是 #FFD700≈1.5:1、#38bdf8≈1.9:1、
// #eab308≈2.0:1、#00BFFF≈2.2:1，全部低于可读阈值，副图会整片发白。
// 画布定成暖米色 #FAF7F0 之后，纯冷调的石板蓝（如 #0369a1）在米底上会显脏，
// 所以这里的蓝/紫/琥珀一律换成同色相的暖偏版本——色相不变，只是降明度加暖。
// 红/绿是语义色（涨跌 / 买卖信号），#ef4444 / #10b981 在两种背景下都成立，
// 因此不进这个调色板。
const light: ZettarancPalette = {
  white: '#2A2520',
  yellow: '#A85B12',
  orange: '#B5430F',
  sky: '#1F5F7A',
  purple: '#6B4A9E',
  neutral: '#6B6259',
  overlayNeutral: '#4A4239',
  auxAmber: '#8A6410',
  auxSky: '#256B85',
  auxOrange: '#C25A16',
  // 暖米底 #FAF7F0 上：亮绿只有 2.4:1（看不清），亮红 3.5:1（偏低）。
  // 色相完全不变——仍是「红涨绿跌」——只是各加深一档到 5:1 / 4.5:1。
  keyBlue: '#2563A8',
  td9Up: '#c81e1e',
  td9Down: '#15803d',
  kdjJ: '#be185d',
  patternCloud: '#0E6E7E',
  patternDawn: '#A32C4B',
  patternDoji: '#256B85',
  patternMorningStar: '#9F1239',
  // 白底上全部拉到 4.9:1 以上（原来最低只有 1.9:1）。
  scoreStars: ['#b91c1c', '#c2410c', '#a16207', '#0f766e', '#047857'],
  up: '#dc2626',
  down: '#047857',
};

let current: ZettarancPalette = dark;

/** 画布主题切换时调用一次；指标是在画布上现画的，不需要重新注册。 */
export function setZettarancPalette(isDark: boolean): void {
  current = isDark ? dark : light;
}

export function zettarancPalette(): ZettarancPalette {
  return current;
}
