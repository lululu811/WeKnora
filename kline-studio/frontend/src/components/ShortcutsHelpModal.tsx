import React from 'react';
import { useStockNav } from '@/hooks/useStockNav';

export function ShortcutsHelpModal() {
  const { showHelp, setShowHelp } = useStockNav();

  if (!showHelp) return null;

  return (
    <div style={styles.mask} onClick={() => setShowHelp(false)}>
      <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <h3 style={styles.title}>⌨️ Zettaranc 极速复盘 · 键盘流操作指南</h3>
          <button onClick={() => setShowHelp(false)} style={styles.closeBtn}>
            ✕
          </button>
        </div>

        <div style={styles.body}>
          <div style={styles.section}>
            <h4 style={styles.secTitle}>📈 股票流转与巡检</h4>
            <div style={styles.row}>
              <kbd style={styles.kbd}>↓</kbd> / <kbd style={styles.kbd}>PageDown</kbd>
              <span style={styles.desc}>秒级切换至下一只股票（自动顺延当前自选/板块列表）</span>
            </div>
            <div style={styles.row}>
              <kbd style={styles.kbd}>↑</kbd> / <kbd style={styles.kbd}>PageUp</kbd>
              <span style={styles.desc}>秒级切换至上一只股票</span>
            </div>
          </div>

          <div style={styles.section}>
            <h4 style={styles.secTitle}>⏱️ K线周期切换</h4>
            <div style={styles.row}>
              <kbd style={styles.kbd}>1</kbd>
              <span style={styles.desc}>切换为 <b>日K线</b></span>
            </div>
            <div style={styles.row}>
              <kbd style={styles.kbd}>2</kbd>
              <span style={styles.desc}>切换为 <b>周K线</b>（DuckDB 原生聚合）</span>
            </div>
            <div style={styles.row}>
              <kbd style={styles.kbd}>3</kbd>
              <span style={styles.desc}>切换为 <b>月K线</b>（DuckDB 原生聚合）</span>
            </div>
          </div>

          <div style={styles.section}>
            <h4 style={styles.secTitle}>🔍 全局导航与系统</h4>
            <div style={styles.row}>
              <kbd style={styles.kbd}>/</kbd> 或 <kbd style={styles.kbd}>Cmd / Ctrl + K</kbd>
              <span style={styles.desc}>即刻聚焦左侧标的搜索框</span>
            </div>
            <div style={styles.row}>
              <kbd style={styles.kbd}>?</kbd>
              <span style={styles.desc}>打开 / 关闭本快捷键帮助面板</span>
            </div>
            <div style={styles.row}>
              <kbd style={styles.kbd}>ESC</kbd>
              <span style={styles.desc}>取消搜索聚焦 / 关闭当前浮层</span>
            </div>
          </div>
        </div>

        <div style={styles.footer}>
          <span>💡 提示：纯键盘操作可在 10 秒内巡检完 20 只自选龙头股形态。</span>
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
    width: 480,
    background: 'var(--bg-elevated)',
    border: '1px solid var(--border-strong)',
    borderRadius: 8,
    boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 16px',
    borderBottom: '1px solid var(--border-subtle)',
  },
  title: {
    fontSize: 'var(--fs-md)',
    fontWeight: 600,
    color: 'var(--text-primary)',
  },
  closeBtn: {
    fontSize: 14,
    color: 'var(--text-muted)',
    padding: '2px 6px',
    cursor: 'pointer',
  },
  body: {
    padding: '16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  secTitle: {
    fontSize: '11px',
    fontWeight: 600,
    color: 'var(--accent)',
    letterSpacing: 0.5,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 'var(--fs-sm)',
    color: 'var(--text-secondary)',
  },
  kbd: {
    display: 'inline-block',
    padding: '2px 6px',
    fontSize: '11px',
    fontFamily: 'var(--font-mono)',
    color: 'var(--text-primary)',
    background: 'var(--bg-base)',
    border: '1px solid var(--border-strong)',
    borderRadius: 4,
    boxShadow: '0 1px 0 rgba(255,255,255,0.1)',
  },
  desc: {
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-secondary)',
  },
  footer: {
    padding: '10px 16px',
    background: 'var(--bg-base)',
    borderTop: '1px solid var(--border-subtle)',
    fontSize: '11px',
    color: 'var(--text-muted)',
  },
};
