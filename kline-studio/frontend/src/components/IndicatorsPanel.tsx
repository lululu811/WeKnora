/**
 * Zettaranc 指标面板 - 显示当前股票的最新指标值
 *
 * 从 indicators API 读取，实时显示白线/黄线/BBI/砖形图/RSL 等
 */
import { useEffect, useState } from 'react';

const API_BASE = '/api';

interface IndicatorRow {
  date: string;
  zettaranc_zg_white_10: number;
  zettaranc_dg_yellow_14: number;
  zettaranc_bbi: number;
  zettaranc_brick_value: number;
  zettaranc_rsl_short_3: number;
  zettaranc_rsl_long_21: number;
}

interface IndicatorsResponse {
  code: number;
  symbol: string;
  data: IndicatorRow[];
}

interface IndicatorsPanelProps {
  symbol: string;
  visible: boolean;
}

interface IndicatorMeta {
  key: keyof Omit<IndicatorRow, 'date'>;
  label: string;
  description: string;
  color: string;
  format: (v: number) => string;
  judge: (v: number, price: number) => string;
}

const INDICATORS: IndicatorMeta[] = [
  {
    key: 'zettaranc_zg_white_10',
    label: '白线',
    description: '10 日 EMA - Z 哥趋势线',
    color: '#FFFFFF',
    format: (v) => v ? v.toFixed(2) : '-',
    judge: (v, price) => {
      if (!v) return '';
      return price > v ? '✅ 多头' : '❌ 空头';
    },
  },
  {
    key: 'zettaranc_dg_yellow_14',
    label: '黄线',
    description: '14 日 EMA - Z 哥大哥线',
    color: '#FFD700',
    format: (v) => v ? v.toFixed(2) : '-',
    judge: (v, price) => {
      if (!v) return '';
      return price > v ? '✅ 多头' : '❌ 空头';
    },
  },
  {
    key: 'zettaranc_bbi',
    label: 'BBI',
    description: '多均线平均 - 牵牛绳',
    color: '#FF8C00',
    format: (v) => v ? v.toFixed(2) : '-',
    judge: (v, price) => {
      if (!v) return '';
      return price > v ? '✅ 站上' : '❌ 跌破';
    },
  },
  {
    key: 'zettaranc_brick_value',
    label: '砖形图',
    description: '4 天情绪循环',
    color: '#FF4444',
    format: (v) => v ? (v > 0 ? '红砖' : v < 0 ? '绿砖' : '无') : '-',
    judge: (v) => {
      if (!v) return '';
      return v > 0 ? '🟥 多头' : '🟩 空头';
    },
  },
  {
    key: 'zettaranc_rsl_short_3',
    label: '短期 RSL',
    description: '3 日相对强度',
    color: '#00BFFF',
    format: (v) => v ? (v > 0 ? '+' + v.toFixed(2) : v.toFixed(2)) : '-',
    judge: (v) => {
      if (!v) return '';
      return v > 0 ? '💪 强' : '⚠️ 弱';
    },
  },
  {
    key: 'zettaranc_rsl_long_21',
    label: '长期 RSL',
    description: '21 日相对强度',
    color: '#9370DB',
    format: (v) => v ? (v > 0 ? '+' + v.toFixed(2) : v.toFixed(2)) : '-',
    judge: (v) => {
      if (!v) return '';
      return v > 0 ? '💪 强' : '⚠️ 弱';
    },
  },
];

export function IndicatorsPanel({ symbol, visible }: IndicatorsPanelProps) {
  const [data, setData] = useState<IndicatorRow | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [latestPrice, setLatestPrice] = useState<number>(0);

  useEffect(() => {
    if (!visible || !symbol) return;

    const loadIndicators = async () => {
      setLoading(true);
      setError(null);
      try {
        // 获取指标
        const res = await fetch(`${API_BASE}/indicators?symbol=${encodeURIComponent(symbol)}&days=10&categories=zettaranc`);
        const json = (await res.json()) as IndicatorsResponse;
        if (json.code === 0 && json.data.length > 0) {
          const latest = json.data[json.data.length - 1];
          setData(latest);
        } else {
          setError('无指标数据');
        }

        // 获取最新价格
        try {
          const klineRes = await fetch(`${API_BASE}/kline/merged?symbol=${encodeURIComponent(symbol)}&days=10`);
          const klineJson = await klineRes.json();
          if (klineJson.code === 0 && klineJson.data.length > 0) {
            setLatestPrice(klineJson.data[klineJson.data.length - 1].close);
          }
        } catch (e) {
          // 忽略价格获取失败
        }
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setLoading(false);
      }
    };

    loadIndicators();
  }, [symbol, visible]);

  if (!visible) return null;

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.title}>📊 Zettaranc 指标</span>
        <span style={styles.date}>
          {data ? `数据日期: ${String(data.date).slice(0, 10)}` : '加载中...'}
        </span>
      </div>

      {loading && <div style={styles.loading}>加载中...</div>}
      {error && <div style={styles.error}>⚠️ {error}</div>}

      {data && !error && (
        <div style={styles.grid}>
          {INDICATORS.map((ind) => {
            const value = data[ind.key];
            const judge = ind.judge(value, latestPrice);
            return (
              <div key={ind.key} style={styles.cell}>
                <div style={{ ...styles.label, color: ind.color }}>
                  {ind.label}
                </div>
                <div style={styles.value}>
                  {ind.format(value)}
                </div>
                {judge && (
                  <div style={styles.judge}>{judge}</div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {data && (
        <div style={styles.priceRow}>
          <span>最新价: </span>
          <span style={styles.price}>¥{latestPrice.toFixed(2)}</span>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    background: 'rgba(15, 15, 15, 0.95)',
    border: '1px solid #2a2a2a',
    borderRadius: 6,
    padding: '10px 12px',
    marginTop: 8,
    fontSize: 11,
    color: '#aaa',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  title: {
    fontSize: 12,
    fontWeight: 600,
    color: '#fff',
  },
  date: {
    fontSize: 10,
    color: '#666',
  },
  loading: {
    padding: 12,
    textAlign: 'center',
    color: '#888',
  },
  error: {
    padding: 12,
    color: '#f44',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(6, 1fr)',
    gap: 6,
  },
  cell: {
    padding: '6px 4px',
    background: 'rgba(255,255,255,0.03)',
    borderRadius: 4,
    textAlign: 'center',
  },
  label: {
    fontSize: 10,
    fontWeight: 600,
    marginBottom: 2,
  },
  value: {
    fontSize: 12,
    fontFamily: 'ui-monospace, monospace',
    color: '#fff',
    marginBottom: 2,
  },
  judge: {
    fontSize: 9,
    color: '#888',
  },
  priceRow: {
    marginTop: 8,
    paddingTop: 6,
    borderTop: '1px solid #222',
    textAlign: 'right',
    fontSize: 11,
  },
  price: {
    fontWeight: 600,
    color: '#FFD700',
    marginLeft: 4,
  },
};