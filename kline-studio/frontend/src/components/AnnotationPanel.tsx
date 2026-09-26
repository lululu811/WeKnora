/**
 * 标注面板组件 - 显示形态标注列表和开关
 */
import { useState } from 'react';
import type { Annotation } from '@/lib/annotate-api';
import { PATTERN_CONFIG } from '@/lib/annotate-api';

interface AnnotationPanelProps {
  annotations: Annotation[];
  visible: boolean;
  onToggle: () => void;
  enabledPatterns: Set<string>;
  onPatternToggle: (pattern: string) => void;
  dataSource?: string;
  hasToday?: boolean;
}

export function AnnotationPanel({
  annotations,
  visible,
  onToggle,
  enabledPatterns,
  onPatternToggle,
  dataSource,
  hasToday,
}: AnnotationPanelProps) {
  const [expanded, setExpanded] = useState(true);

  // 按类型分组
  const grouped = annotations.reduce((acc, ann) => {
    if (!acc[ann.type]) acc[ann.type] = [];
    acc[ann.type].push(ann);
    return acc;
  }, {} as Record<string, Annotation[]>);

  return (
    <div style={styles.container}>
      {/* 顶部切换栏 */}
      <div style={styles.header}>
        <button onClick={onToggle} style={styles.toggleBtn}>
          {visible ? '📍 隐藏标注' : '📍 显示标注'}
        </button>
        {dataSource && (
          <span style={styles.source}>
            {dataSource === 'duckdb+fuyao' ? '📡 实时' : '💾 离线'}
            {hasToday && <span style={styles.liveDot} title="包含今日数据" />}
          </span>
        )}
        <span style={styles.count}>{annotations.length} 个标注</span>
      </div>

      {visible && (
        <>
          {/* 类型过滤 */}
          <div style={styles.filters}>
            {Object.entries(PATTERN_CONFIG).map(([type, cfg]) => (
              <button
                key={type}
                onClick={() => onPatternToggle(type)}
                style={{
                  ...styles.filterBtn,
                  background: enabledPatterns.has(type) ? cfg.color + '33' : 'transparent',
                  borderColor: enabledPatterns.has(type) ? cfg.color : '#444',
                  color: enabledPatterns.has(type) ? cfg.color : '#666',
                }}
              >
                {cfg.icon} {cfg.label}
              </button>
            ))}
          </div>

          {/* 标注列表 */}
          {expanded && (
            <div style={styles.list}>
              {Object.entries(grouped).map(([type, anns]) => {
                const cfg = PATTERN_CONFIG[type];
                if (!cfg || !enabledPatterns.has(type)) return null;
                return (
                  <div key={type} style={styles.group}>
                    <div style={{ ...styles.groupTitle, color: cfg.color }}>
                      {cfg.icon} {cfg.label} ({anns.length})
                    </div>
                    {anns.slice(-10).reverse().map((ann, i) => (
                      <div key={i} style={styles.item}>
                        <span style={styles.itemDate}>{ann.date}</span>
                        <span style={styles.itemPrice}>¥{ann.price.toFixed(2)}</span>
                        <span style={styles.itemConf}>
                          {(ann.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'absolute',
    top: 10,
    right: 10,
    width: 240,
    maxHeight: 'calc(100% - 20px)',
    background: 'rgba(20, 20, 20, 0.95)',
    border: '1px solid #333',
    borderRadius: 6,
    overflow: 'hidden',
    fontSize: 12,
    zIndex: 100,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '6px 10px',
    background: '#1a1a1a',
    borderBottom: '1px solid #333',
  },
  toggleBtn: {
    padding: '3px 8px',
    fontSize: 11,
    color: '#fff',
    background: '#FF6B00',
    border: 'none',
    borderRadius: 3,
    cursor: 'pointer',
  },
  source: {
    fontSize: 10,
    color: '#888',
    display: 'flex',
    alignItems: 'center',
    gap: 3,
  },
  liveDot: {
    display: 'inline-block',
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: '#0f0',
    animation: 'pulse 1.5s infinite',
  },
  count: {
    flex: 1,
    textAlign: 'right',
    color: '#666',
    fontSize: 10,
  },
  filters: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 4,
    padding: '6px 10px',
    borderBottom: '1px solid #333',
  },
  filterBtn: {
    padding: '2px 6px',
    fontSize: 10,
    border: '1px solid',
    borderRadius: 3,
    cursor: 'pointer',
  },
  list: {
    maxHeight: 400,
    overflowY: 'auto',
    padding: '4px 0',
  },
  group: {
    padding: '4px 10px',
  },
  groupTitle: {
    fontSize: 11,
    fontWeight: 600,
    marginBottom: 2,
  },
  item: {
    display: 'flex',
    gap: 8,
    padding: '1px 0',
    fontSize: 10,
    color: '#aaa',
  },
  itemDate: {
    flex: 1,
    color: '#ccc',
  },
  itemPrice: {
    color: '#888',
    fontVariantNumeric: 'tabular-nums',
  },
  itemConf: {
    color: '#666',
    width: 30,
    textAlign: 'right',
  },
};
