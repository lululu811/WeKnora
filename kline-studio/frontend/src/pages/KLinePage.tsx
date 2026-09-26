import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { KLineChartPro } from '@klinecharts/pro';
import '@klinecharts/pro/dist/klinecharts-pro.css';
import { ZettarancDatafeed } from '@/lib/datafeed';
import { klineStyles } from '@/lib/kline-theme';
import { StockHeader } from '@/components/StockHeader';
import { PicksBanner } from '@/components/PicksBanner';
import { AnnotationPanel } from '@/components/AnnotationPanel';
import { AnnotationTimeline } from '@/components/AnnotationTimeline';
import { IndicatorsPanel } from '@/components/IndicatorsPanel';
import { fetchAnnotations, type Annotation, PATTERN_CONFIG } from '@/lib/annotate-api';
import { AnnotationOverlay } from '@/lib/annotate-overlay';
import { registerAllIndicators } from '@/lib/register-indicators';
import { useStockNav } from '@/hooks/useStockNav';
import type { SymbolInfo, KLineData } from '@/types/klinecharts-pro';

// 应用启动时注册 Zettaranc 自定义指标
registerAllIndicators();

type Adjust = 'none' | 'forward' | 'backward';
const ADJUST_OPTIONS: Array<{ value: Adjust; label: string }> = [
  { value: 'none', label: '不复权' },
  { value: 'forward', label: '前复权' },
  { value: 'backward', label: '后复权' },
];

const PERIODS = [
  { multiplier: 1, timespan: 'day' as const, text: '日K' },
  { multiplier: 1, timespan: 'week' as const, text: '周K' },
  { multiplier: 1, timespan: 'month' as const, text: '月K' },
];

export function KLinePage() {
  const params = useParams<{ ticker: string; exchange: string }>();
  // URL 可能是 `/k/600519.SH`（thscode 合并形式），React Router 会把整段
  // `600519.SH` 当作 `:ticker`，`:exchange` 留空。这里兼容两种写法。
  const { ticker: rawTicker, exchange: rawExchange } = params;
  let ticker = rawTicker;
  let exchange = rawExchange;
  if (!exchange && rawTicker && rawTicker.includes('.')) {
    const [t, e] = rawTicker.split('.');
    if (e && (e === 'SH' || e === 'SZ' || e === 'BJ')) {
      ticker = t;
      exchange = e;
    }
  }
  console.log('[kline-page]', { rawTicker, rawExchange, ticker, exchange, url: location.href })
  const [searchParams] = useSearchParams();
  // ?embedded=1 — 被外站（如 WeKnora 右侧栏 iframe）嵌入时隐藏头部/横幅，
  // 让 K 线图占满容器。Layout 仍保留（左侧 sidebar 是 Layout 里的）。
  const embedded = useMemo(
    () => searchParams.get('embedded') === '1',
    [searchParams],
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<KLineChartPro | null>(null);
  const [adjust, setAdjust] = useState<Adjust>('forward');
  const [periodIdx, setPeriodIdx] = useState<number>(0);

  // 标注状态
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [annotationsVisible, setAnnotationsVisible] = useState(true);
  const [enabledPatterns, setEnabledPatterns] = useState<Set<string>>(
    new Set(Object.keys(PATTERN_CONFIG))
  );
  const [dataSource, setDataSource] = useState<string>('');
  const [hasToday, setHasToday] = useState(false);
  const [loading, setLoading] = useState(false);
  const overlayRef = useRef<AnnotationOverlay | null>(null);
  const [bars, setBars] = useState<KLineData[]>([]);

  const { nextStock, prevStock, focusSearch, setShowHelp } = useStockNav();

  // 键盘快捷键监听
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const isInput =
        target &&
        (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);

      if (isInput) {
        if (e.key === 'Escape') {
          target.blur();
        }
        return;
      }

      if (e.key === 'ArrowDown' || e.key === 'PageDown') {
        e.preventDefault();
        nextStock();
      } else if (e.key === 'ArrowUp' || e.key === 'PageUp') {
        e.preventDefault();
        prevStock();
      } else if (e.key === '1') {
        e.preventDefault();
        setPeriodIdx(0);
      } else if (e.key === '2') {
        e.preventDefault();
        setPeriodIdx(1);
      } else if (e.key === '3') {
        e.preventDefault();
        setPeriodIdx(2);
      } else if (e.key === '/' || (e.key === 'k' && (e.ctrlKey || e.metaKey))) {
        e.preventDefault();
        focusSearch();
      } else if (e.key === '?' || (e.shiftKey && e.key === '/')) {
        e.preventDefault();
        setShowHelp(true);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [nextStock, prevStock, focusSearch, setShowHelp]);

  // 加载标注数据
  useEffect(() => {
    if (!ticker || !exchange) return;

    const loadAnnotations = async () => {
      setLoading(true);
      try {
        const symbol = `${ticker}.${exchange}`;
        const result = await fetchAnnotations(symbol, 200);
        setAnnotations(result.annotations);
        setDataSource(result.data_source);
        setHasToday(result.has_today);
      } catch (err) {
        console.error('Failed to load annotations:', err);
        setAnnotations([]);
      } finally {
        setLoading(false);
      }
    };

    loadAnnotations();
  }, [ticker, exchange]);

  // 加载 K 线数据（用于标注计算）
  useEffect(() => {
    if (!ticker || !exchange) return;

    const loadBars = async () => {
      try {
        const symbol = `${ticker}.${exchange}`;
        // 加载 1 年数据（足够标注计算）
        const res = await fetch(`/api/kline/merged?symbol=${symbol}&days=365`);
        const json = await res.json();
        if (json.code === 0 && json.data) {
          const barsData: KLineData[] = json.data.map((r: any) => ({
            timestamp: r.ts * 1000,
            open: r.open,
            high: r.high,
            low: r.low,
            close: r.close,
            volume: r.volume,
            turnover: r.turnover,
          }));
          setBars(barsData);
          console.log('[bars] loaded', barsData.length, 'bars for', symbol);
        }
      } catch (err) {
        console.error('Failed to load bars:', err);
      }
    };

    loadBars();
  }, [ticker, exchange]);

  // 切换标注类型
  const handlePatternToggle = (pattern: string) => {
    setEnabledPatterns((prev) => {
      const next = new Set(prev);
      if (next.has(pattern)) {
        next.delete(pattern);
      } else {
        next.add(pattern);
      }
      return next;
    });
  };

  // 过滤后的标注
  const filteredAnnotations = annotations.filter((ann) => enabledPatterns.has(ann.type));

  // 销毁并重建 chart
  useEffect(() => {
    if (!containerRef.current || !ticker || !exchange) return;

    const symbol: SymbolInfo = {
      exchange,
      market: 'stocks',
      name: ticker,
      shortName: ticker,
      ticker,
      priceCurrency: 'cny',
      type: 'stock',
    };

    containerRef.current.innerHTML = '';
    chartRef.current = null;

    // 销毁旧的 overlay
    overlayRef.current?.destroy();
    overlayRef.current = null;

    chartRef.current = new KLineChartPro({
      container: containerRef.current,
      symbol,
      period: PERIODS[periodIdx],
      datafeed: new ZettarancDatafeed({ adjust }),
      styles: klineStyles,
      mainIndicators: ['MA', 'ZG_WHITE', 'DG_YELLOW', 'BBI'],
      subIndicators: ['VOL', 'MACD', 'KDJ', 'Z_BBI', 'Z_BRICK', 'Z_RSL_SHORT', 'Z_RSL_LONG'],
      periods: PERIODS,
      drawingBarVisible: true,
      theme: 'dark',
    });

    // 创建标注叠加层
    if (containerRef.current) {
      overlayRef.current = new AnnotationOverlay(containerRef.current);
    }

    return () => {
      overlayRef.current?.destroy();
      overlayRef.current = null;
      chartRef.current = null;
    };
  }, [ticker, exchange, adjust, periodIdx]);

  // 标注或数据变化时更新叠加层
  useEffect(() => {
    if (overlayRef.current && bars.length > 0 && annotations.length > 0) {
      overlayRef.current.setData(bars, annotations, enabledPatterns);
    }
  }, [bars, annotations, enabledPatterns]);

  if (!ticker || !exchange) return null;

  return (
    <div style={styles.wrap}>
      {!embedded && <StockHeader ticker={ticker} exchange={exchange} />}
      {!embedded && <PicksBanner />}
      <div style={styles.toolbar}>
        <div style={styles.group}>
          <span style={styles.label}>周期</span>
          {PERIODS.map((p, idx) => (
            <button
              key={p.timespan}
              onClick={() => setPeriodIdx(idx)}
              style={{
                ...styles.btn,
                ...(periodIdx === idx ? styles.btnActive : null),
              }}
              title={`按数字键 ${idx + 1} 快速切换`}
            >
              {p.text}
            </button>
          ))}
        </div>

        <div style={styles.divider} />

        <div style={styles.group}>
          <span style={styles.label}>复权</span>
          {ADJUST_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setAdjust(opt.value)}
              style={{
                ...styles.btn,
                ...(adjust === opt.value ? styles.btnActive : null),
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <div style={styles.spacer} />

        <div style={styles.shortcutTips}>
          <span>[↑/↓] 切股</span>
          <span>[1-3] 周期</span>
          <span>[/] 搜索</span>
          <button onClick={() => setShowHelp(true)} style={styles.helpBtn} title="查看快捷键说明">
            ?
          </button>
        </div>
      </div>
      <div ref={containerRef} style={styles.chart} />
      {/* 标注面板 */}
      {!loading && annotations.length > 0 && (
        <AnnotationPanel
          annotations={annotations}
          visible={annotationsVisible}
          onToggle={() => setAnnotationsVisible(!annotationsVisible)}
          enabledPatterns={enabledPatterns}
          onPatternToggle={handlePatternToggle}
          dataSource={dataSource}
          hasToday={hasToday}
        />
      )}
      {/* 标注时间轴 */}
      {!loading && annotations.length > 0 && annotationsVisible && (
        <AnnotationTimeline
          annotations={filteredAnnotations}
          enabledPatterns={enabledPatterns}
          onAnnotationClick={(ann) => {
            console.log('点击标注:', ann);
          }}
        />
      )}
      {/* Zettaranc 指标面板 */}
      <IndicatorsPanel
        symbol={ticker && exchange ? `${ticker}.${exchange}` : ''}
        visible={annotationsVisible}
      />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    padding: 'var(--sp-2) var(--sp-3)',
    position: 'relative',
  },
  toolbar: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--sp-3)',
    padding: '0 var(--sp-2) var(--sp-2)',
    flexWrap: 'wrap',
  },
  group: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--sp-1)',
  },
  divider: {
    width: 1,
    height: 14,
    background: 'var(--border-subtle)',
  },
  label: { fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginRight: 2 },
  btn: {
    padding: '3px 8px',
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-secondary)',
    background: 'transparent',
    border: '1px solid var(--border-subtle)',
    borderRadius: 3,
    transition: 'all 0.15s',
  },
  btnActive: {
    color: '#fff',
    background: 'var(--accent)',
    borderColor: 'var(--accent)',
    fontWeight: 600,
  },
  spacer: { flex: 1 },
  shortcutTips: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--sp-3)',
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-muted)',
  },
  helpBtn: {
    width: 18,
    height: 18,
    borderRadius: '50%',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    border: '1px solid var(--border-subtle)',
    color: 'var(--text-secondary)',
    fontSize: '11px',
  },
  chart: { flex: 1, minHeight: 0, position: 'relative' },
};