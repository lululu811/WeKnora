import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { KLineChartPro } from '@klinecharts/pro';
import '@klinecharts/pro/dist/klinecharts-pro.css';
import { ZettarancDatafeed } from '@/lib/datafeed';
import { klineStyles } from '@/lib/kline-theme';
import { StockHeader } from '@/components/StockHeader';
import { useStockNav } from '@/hooks/useStockNav';
import type { SymbolInfo } from '@/types/klinecharts-pro';

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
  const { ticker, exchange } = useParams<{ ticker: string; exchange: string }>();
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<KLineChartPro | null>(null);
  const [adjust, setAdjust] = useState<Adjust>('forward');
  const [periodIdx, setPeriodIdx] = useState<number>(0);

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

  // 销毁并重建 chart（应对 ticker / exchange / adjust / periodIdx 变化）
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

    chartRef.current = new KLineChartPro({
      container: containerRef.current,
      symbol,
      period: PERIODS[periodIdx],
      datafeed: new ZettarancDatafeed({ adjust }),
      styles: klineStyles,
      mainIndicators: ['MA'],
      subIndicators: ['VOL', 'MACD', 'KDJ'],
      periods: PERIODS,
      drawingBarVisible: true,
      theme: 'dark',
    });

    return () => {
      chartRef.current = null;
    };
  }, [ticker, exchange, adjust, periodIdx]);

  if (!ticker || !exchange) return null;

  return (
    <div style={styles.wrap}>
      <StockHeader ticker={ticker} exchange={exchange} />
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
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    padding: 'var(--sp-2) var(--sp-3)',
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
  chart: { flex: 1, minHeight: 0 },
};
