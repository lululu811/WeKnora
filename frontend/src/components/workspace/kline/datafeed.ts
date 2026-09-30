import type { DatafeedSubscribeCallback, KLineData, Period, SymbolInfo } from './types';

export type Adjust = 'none' | 'forward' | 'backward';

export interface ZettarancDatafeedOptions {
  adjust?: Adjust;
  onDataLoaded?: (data: KLineData[]) => void;
  /**
   * 取不到任何行情时触发。单独开一个回调而不是让 onDataLoaded 收到空数组，
   * 是因为"数据加载成功但结果是空的"和"根本没加载"在 UI 上要区别对待：
   * 前者要显示"本地无该标的行情"，后者是加载中。
   */
  onNoData?: (symbol: SymbolInfo) => void;
  /**
   * **请求本身失败**时触发（网络中断 / 网关 5xx / 响应不是合法 JSON）。
   *
   * 与 onNoData 严格区分，两者含义完全相反：
   * - onNoData = 服务端明确回答"这只票本地没有行情"，提示用户换代码或补数据；
   * - onError  = 根本没问到服务端，代码可能是好的，是链路或服务端出了问题。
   *
   * 为什么必须分开：旧实现把 fetch 失败 `.catch(() => ({ code: -1, data: [] }))`
   * 吞成空数组，于是走 onNoData。nginx 502、DuckDB 被锁、容器重启换 IP——
   * 这些故障统统显示成"本地无该标的行情数据"，而数据其实好好地躺在库里。
   * 这个假象会把排查方向整个带偏（去查代码表/数据同步，而不是查代理层）。
   */
  onError?: (symbol: SymbolInfo, message: string) => void;
}

/**
 * 从服务端的错误响应体里挖出可读信息。
 *
 * python-service 的错误体是 `{"detail": {"success": false, "error": "...", "detail": "..."}}`
 * （FastAPI HTTPException 会在外层再包一层 detail），而成功体是 `{"code": 0, "data": [...]}`。
 * 两种形状都不一样，所以这里按"哪个有内容就用哪个"的顺序取。
 */
function extractErrorMessage(body: any): string {
  const candidates = [
    body?.error,
    body?.detail?.error,
    body?.detail?.message,
    body?.message,
    typeof body?.detail === 'string' ? body.detail : undefined,
  ];
  return candidates.find((c) => typeof c === 'string' && c.trim())?.trim() ?? '';
}

/** 把 fetch 抛出的异常压成一句能直接展示的话。 */
function describeNetworkError(err: unknown): string {
  if (err instanceof DOMException && err.name === 'AbortError') return '请求超时';
  if (err instanceof TypeError) {
    // fetch 只有在网络层失败（断网、DNS 失败、连接被拒、CORS）时才会抛 TypeError。
    return '无法连接行情服务（网络中断或服务未启动）';
  }
  const msg = err instanceof Error ? err.message : String(err);
  return msg || '未知网络错误';
}

export class ZettarancDatafeed {
  private adjust: Adjust;
  private onDataLoaded?: (data: KLineData[]) => void;
  private onNoData?: (symbol: SymbolInfo) => void;
  private onError?: (symbol: SymbolInfo, message: string) => void;

  constructor(opts: ZettarancDatafeedOptions = {}) {
    this.adjust = opts.adjust ?? 'forward';
    this.onDataLoaded = opts.onDataLoaded;
    this.onNoData = opts.onNoData;
    this.onError = opts.onError;
  }

  setAdjust(adj: Adjust) {
    this.adjust = adj;
  }

  async searchSymbols(search = ''): Promise<SymbolInfo[]> {
    const q = encodeURIComponent(search.trim());
    const res = await fetch(`/api/symbols/search?q=${q}`);
    if (!res.ok) return [];
    const json = await res.json();
    const rows = json.data || [];
    return rows.map((s: any) => ({
      exchange: s.exchange ?? 'SH',
      market: 'stocks',
      name: s.name ?? s.ticker,
      shortName: s.name ?? s.ticker,
      ticker: s.ticker,
      priceCurrency: 'cny',
      type: 'stock',
    }));
  }

  async getHistoryKLineData(
    symbol: SymbolInfo,
    period: Period,
    _from: number,
    _to: number,
  ): Promise<KLineData[]> {
    const timespan = period?.timespan;
    const periodParam = timespan === 'week' ? 'week' : timespan === 'month' ? 'month' : 'day';
    const symbolStr = `${symbol.ticker}.${symbol.exchange}`;

    // 1) 发请求。网络层失败与"没数据"是两回事，这里只处理前者。
    let resp: Response;
    try {
      resp = await fetch(
        `/api/kline?symbol=${encodeURIComponent(symbolStr)}&adjust=${this.adjust}&period=${periodParam}&limit=5000`,
      );
    } catch (err) {
      this.onError?.(symbol, describeNetworkError(err));
      return [];
    }

    // 2) 解析响应体。解析失败说明拿到的不是 JSON——nginx 502 错误页、
    //    登录页 HTML、容器没起来返回的纯文本，都会走到这里。
    let body: any = null;
    try {
      body = await resp.json();
    } catch {
      this.onError?.(symbol, `行情服务返回了非 JSON 响应（HTTP ${resp.status}）`);
      return [];
    }

    // 3) HTTP 层错误。只有 404 表示"服务端确认这只票没有行情"，
    //    其余（400 参数非法 / 500 / 502 网关 / 503 数据源未就绪）都是故障，
    //    绝不能报成"本地无此标的行情"。
    if (!resp.ok) {
      if (resp.status === 404) {
        this.onNoData?.(symbol);
      } else {
        const detail = extractErrorMessage(body);
        this.onError?.(symbol, detail || `行情服务返回 HTTP ${resp.status}`);
      }
      return [];
    }

    // 4) 业务层错误（HTTP 200 但 code !== 0）。
    if (body?.code !== 0) {
      this.onError?.(symbol, extractErrorMessage(body) || `行情服务返回 code=${body?.code}`);
      return [];
    }

    const klineRows: any[] = Array.isArray(body?.data) ? body.data : [];

    const dataList: KLineData[] = klineRows.map((r: any) => ({
      timestamp: r.ts * 1000,
      open: r.open,
      high: r.high,
      low: r.low,
      close: r.close,
      volume: r.volume,
      turnover: r.turnover,
    }));

    if (dataList.length === 0) {
      // 请求成功、服务端也答了，只是答案里一根 K 线都没有。
      // 这才是 onNoData 的唯一触发点：代码不存在（模型幻觉出的代码段，
      // 或本地代码表没收录），或者行情还没同步到它。
      this.onNoData?.(symbol);
      return dataList;
    }

    this.onDataLoaded?.(dataList);
    return dataList;
  }

  subscribe(
    _symbol: SymbolInfo,
    _period: Period,
    _callback: DatafeedSubscribeCallback,
  ): void {
    // 纯复盘模式，不开启盘中推送
  }

  unsubscribe(_symbol: SymbolInfo, _period: Period): void {
    // 纯复盘模式
  }
}
