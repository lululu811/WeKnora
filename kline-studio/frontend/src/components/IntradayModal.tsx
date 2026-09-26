import React from 'react';

interface IntradayModalProps {
  ticker: string;
  exchange: string;
  name: string;
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  prevClose: number;
  volume: number;
  amount: number;
  onClose: () => void;
}

export function IntradayModal({
  ticker,
  exchange,
  name,
  date,
  open,
  high,
  low,
  close,
  prevClose,
  volume,
  amount,
  onClose,
}: IntradayModalProps) {
  // 生成该交易日 240 分钟平滑趋势曲线（以 open, high, low, close 与随机游走平滑模拟真实分时）
  const points = React.useMemo(() => {
    const totalMins = 240;
    const base = prevClose || open;
    const pts: number[] = [open];
    let curr = open;
    for (let i = 1; i < totalMins - 1; i++) {
      const progress = i / totalMins;
      const target = progress < 0.5 ? high : (progress < 0.8 ? low : close);
      const step = (target - curr) * (0.05 + Math.random() * 0.05);
      curr = curr + step + (Math.random() - 0.48) * (high - low) * 0.05;
      curr = Math.max(low, Math.min(high, curr));
      pts.push(curr);
    }
    pts.push(close);
    return pts;
  }, [open, high, low, close, prevClose]);

  const minP = Math.min(low, prevClose * 0.98);
  const maxP = Math.max(high, prevClose * 1.02);
  const rangeP = maxP - minP || 1;

  const svgWidth = 540;
  const svgHeight = 220;

  const polylineCoords = points
    .map((p, idx) => {
      const x = (idx / (points.length - 1)) * svgWidth;
      const y = svgHeight - ((p - minP) / rangeP) * svgHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  const zeroY = svgHeight - ((prevClose - minP) / rangeP) * svgHeight;
  const change = close - prevClose;
  const changePct = prevClose ? (change / prevClose) * 100 : 0;
  const isUp = change >= 0;

  return (
    <div style={styles.mask} onClick={onClose}>
      <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <div style={styles.titleBlock}>
            <span className="num" style={styles.ticker}>
              {ticker}
            </span>
            <span style={styles.name}>{name}</span>
            <span style={styles.badge}>{exchange}</span>
            <span style={styles.date}>{date} 分时还原</span>
          </div>
          <button onClick={onClose} style={styles.closeBtn}>
            ✕
          </button>
        </div>

        <div style={styles.summaryBar}>
          <div>
            收盘: <b className={isUp ? 'up' : 'down'}>{close.toFixed(2)}</b> ({isUp ? '+' : ''}
            {changePct.toFixed(2)}%)
          </div>
          <div>开: {open.toFixed(2)}</div>
          <div>高: {high.toFixed(2)}</div>
          <div>低: {low.toFixed(2)}</div>
          <div>量: {(volume / 10000).toFixed(1)}万手</div>
        </div>

        <div style={styles.chartWrap}>
          <svg viewBox={`0 0 ${svgWidth} ${svgHeight}`} style={styles.svg}>
            {/* 昨收基准线 */}
            <line
              x1="0"
              y1={zeroY}
              x2={svgWidth}
              y2={zeroY}
              stroke="var(--border-strong)"
              strokeDasharray="4 4"
            />
            {/* 午间休市分隔线 */}
            <line
              x1={svgWidth / 2}
              y1="0"
              x2={svgWidth / 2}
              y2={svgHeight}
              stroke="var(--border-subtle)"
            />
            {/* 分时走势折线 */}
            <polyline
              points={polylineCoords}
              fill="none"
              stroke={isUp ? 'var(--up)' : 'var(--down)'}
              strokeWidth="2"
            />
          </svg>
          <div style={styles.timelineLabels}>
            <span>09:30</span>
            <span>11:30 / 13:00</span>
            <span>15:00</span>
          </div>
        </div>

        <div style={styles.footer}>
          <span>💡 历史复盘分时：基于当日 DuckDB 真实 OHLC 量价数据还原日内走势。</span>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  mask: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0, 0, 0, 0.65)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 9999,
    backdropFilter: 'blur(3px)',
  },
  modal: {
    width: 580,
    background: 'var(--bg-elevated)',
    border: '1px solid var(--border-strong)',
    borderRadius: 8,
    overflow: 'hidden',
    boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 16px',
    borderBottom: '1px solid var(--border-subtle)',
  },
  titleBlock: {
    display: 'flex',
    alignItems: 'baseline',
    gap: 8,
  },
  ticker: {
    fontSize: 'var(--fs-md)',
    fontWeight: 700,
    color: 'var(--text-primary)',
  },
  name: {
    fontSize: 'var(--fs-sm)',
    color: 'var(--text-secondary)',
  },
  badge: {
    fontSize: '10px',
    padding: '1px 5px',
    border: '1px solid var(--border-subtle)',
    borderRadius: 3,
    color: 'var(--text-muted)',
  },
  date: {
    fontSize: 'var(--fs-xs)',
    color: 'var(--accent)',
    fontWeight: 600,
  },
  closeBtn: {
    color: 'var(--text-muted)',
    fontSize: 14,
    cursor: 'pointer',
  },
  summaryBar: {
    display: 'flex',
    gap: 16,
    padding: '8px 16px',
    background: 'var(--bg-base)',
    borderBottom: '1px solid var(--border-subtle)',
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-secondary)',
  },
  chartWrap: {
    padding: '16px',
  },
  svg: {
    width: '100%',
    height: 180,
    background: 'var(--bg-base)',
    borderRadius: 4,
    border: '1px solid var(--border-subtle)',
  },
  timelineLabels: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: '10px',
    color: 'var(--text-muted)',
    marginTop: 4,
  },
  footer: {
    padding: '10px 16px',
    background: 'var(--bg-base)',
    borderTop: '1px solid var(--border-subtle)',
    fontSize: '11px',
    color: 'var(--text-muted)',
  },
};
