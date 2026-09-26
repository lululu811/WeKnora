import { useEffect, useState } from 'react';

interface MarketData {
  date: string;
  upCount: number;
  downCount: number;
  flatCount: number;
  limitUpCount: number;
  limitDownCount: number;
  sentimentRatio: number;
}

export function MarketSentimentBar() {
  const [data, setData] = useState<MarketData | null>(null);

  useEffect(() => {
    fetch('/api/market/overview')
      .then((r) => r.json())
      .then((env) => {
        if (env.code === 0 && env.data) {
          setData(env.data);
        }
      })
      .catch((err) => console.warn('[MarketSentimentBar]', err));
  }, []);

  if (!data || !data.date) return null;

  return (
    <div style={styles.bar}>
      <div style={styles.item}>
        <span style={styles.label}>全A情绪</span>
        <span className="num up" style={styles.val}>
          涨 {data.upCount}
        </span>
        <span style={styles.divider}>/</span>
        <span className="num down" style={styles.val}>
          跌 {data.downCount}
        </span>
        <span style={styles.divider}>/</span>
        <span className="num" style={{ ...styles.val, color: 'var(--text-muted)' }}>
          平 {data.flatCount}
        </span>
      </div>

      <div style={styles.pillGroup}>
        <span style={styles.limitUpPill}>
          <span style={styles.dotUp} /> 涨停 {data.limitUpCount}
        </span>
        <span style={styles.limitDownPill}>
          <span style={styles.dotDown} /> 跌停 {data.limitDownCount}
        </span>
      </div>

      <div style={styles.progressWrap} title={`上涨比例: ${data.sentimentRatio}%`}>
        <div style={{ ...styles.progressUp, width: `${data.sentimentRatio}%` }} />
        <div style={{ ...styles.progressDown, width: `${100 - data.sentimentRatio}%` }} />
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--sp-3)',
    fontSize: 'var(--fs-xs)',
    padding: '2px 8px',
    background: 'var(--bg-overlay)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 4,
  },
  item: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  label: {
    color: 'var(--text-muted)',
    marginRight: 2,
  },
  val: {
    fontWeight: 600,
  },
  divider: {
    color: 'var(--border-strong)',
  },
  pillGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  limitUpPill: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 3,
    padding: '1px 6px',
    borderRadius: 10,
    background: 'var(--up-soft)',
    color: 'var(--up)',
    fontWeight: 600,
  },
  dotUp: {
    width: 5,
    height: 5,
    borderRadius: '50%',
    background: 'var(--up)',
  },
  limitDownPill: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 3,
    padding: '1px 6px',
    borderRadius: 10,
    background: 'var(--down-soft)',
    color: 'var(--down)',
    fontWeight: 600,
  },
  dotDown: {
    width: 5,
    height: 5,
    borderRadius: '50%',
    background: 'var(--down)',
  },
  progressWrap: {
    width: 60,
    height: 6,
    borderRadius: 3,
    background: 'var(--bg-base)',
    display: 'flex',
    overflow: 'hidden',
    border: '1px solid var(--border-subtle)',
  },
  progressUp: {
    height: '100%',
    background: 'var(--up)',
  },
  progressDown: {
    height: '100%',
    background: 'var(--down)',
  },
};
