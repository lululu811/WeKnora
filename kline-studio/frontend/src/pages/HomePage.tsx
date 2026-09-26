export function HomePage() {
  return (
    <div style={styles.wrap}>
      <div style={styles.card}>
        <h2 style={styles.h}>Zettaranc · 复盘终端</h2>
        <p style={styles.p}>
          从左侧股票列表选择标的，进入 K 线主图。
        </p>
        <ul style={styles.ul}>
          <li>历史 K 线：本地 DuckDB（10 年日线 · 5541 标的）</li>
          <li>实时报价：HTTP 轮询 → fuyao REST API（3s 间隔）</li>
          <li>图表引擎：KLineChart Pro（含 MA/MACD/KDJ 等指标）</li>
        </ul>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: {
    height: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 'var(--sp-6)',
  },
  card: {
    maxWidth: 520,
    padding: 'var(--sp-6)',
    background: 'var(--bg-elevated)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 6,
  },
  h: { fontSize: 'var(--fs-lg)', marginBottom: 'var(--sp-3)' },
  p: { color: 'var(--text-secondary)', marginBottom: 'var(--sp-4)' },
  ul: { paddingLeft: 'var(--sp-5)', color: 'var(--text-secondary)', lineHeight: 1.8, fontSize: 'var(--fs-sm)' },
};
