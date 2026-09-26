import { Outlet } from 'react-router-dom';
import { SymbolList } from './SymbolList';
import { MarketSentimentBar } from './MarketSentimentBar';
import { ShortcutsHelpModal } from './ShortcutsHelpModal';
import { useStockNav } from '@/hooks/useStockNav';

export function Layout() {
  const { setShowHelp } = useStockNav();

  return (
    <div style={styles.shell}>
      <header style={styles.topbar}>
        <div style={styles.brand}>
          <span style={styles.brandMark}>Z</span>
          <span style={styles.brandName}>Zettaranc</span>
          <span style={styles.brandTag}>复盘终端</span>
        </div>

        <div style={styles.spacer} />

        {/* 顶部市场情绪晴雨表 */}
        <MarketSentimentBar />

        <div style={styles.spacer} />

        <div style={styles.actions}>
          <button
            onClick={() => setShowHelp(true)}
            style={styles.helpBtn}
            title="查看复盘快捷键指南"
          >
            ⌨️ 快捷键指南
          </button>
          <div style={styles.status}>
            <span className="dot" style={styles.dot} />
            <span style={{ color: 'var(--text-secondary)' }}>DuckDB 秒级聚合</span>
          </div>
        </div>
      </header>

      <div style={styles.body}>
        <aside style={styles.sidebar}>
          <SymbolList />
        </aside>
        <main style={styles.main}>
          <Outlet />
        </main>
      </div>

      {/* 快捷键指南模态弹窗 */}
      <ShortcutsHelpModal />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    width: '100vw',
    background: 'var(--bg-base)',
  },
  topbar: {
    display: 'flex',
    alignItems: 'center',
    height: 44,
    padding: '0 var(--sp-4)',
    background: 'var(--bg-elevated)',
    borderBottom: '1px solid var(--border-subtle)',
    flexShrink: 0,
    gap: 'var(--sp-3)',
  },
  brand: { display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' },
  brandMark: {
    width: 22,
    height: 22,
    borderRadius: 4,
    background: 'var(--accent)',
    color: '#fff',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontWeight: 700,
    fontSize: 13,
  },
  brandName: { fontWeight: 600, fontSize: 'var(--fs-md)' },
  brandTag: { color: 'var(--text-muted)', fontSize: 'var(--fs-xs)' },
  spacer: { flex: 1 },
  actions: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--sp-3)',
  },
  helpBtn: {
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-secondary)',
    background: 'var(--bg-overlay)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 4,
    padding: '4px 8px',
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
  status: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--sp-2)',
    fontSize: 'var(--fs-xs)',
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: 'var(--down)',
    display: 'inline-block',
  },
  body: { display: 'flex', flex: 1, minHeight: 0 },
  sidebar: {
    width: 280,
    flexShrink: 0,
    background: 'var(--bg-elevated)',
    borderRight: '1px solid var(--border-subtle)',
    display: 'flex',
    flexDirection: 'column',
  },
  main: { flex: 1, minWidth: 0, background: 'var(--bg-base)' },
};
