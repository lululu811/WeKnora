/**
 * 标注时间轴组件 - 在 K 线图下方显示标注时间线
 *
 * 设计理念：
 * - 横向时间轴，从左到右表示时间从早到晚
 * - 每个标注点用图标 + 颜色显示在对应位置
 * - 点击标注点可以跳转或显示详情
 * - 完全独立于 KLineChart API，稳定可靠
 */
import { useMemo, useRef, useState } from 'react';
import type { Annotation } from '@/lib/annotate-api';
import { PATTERN_CONFIG } from '@/lib/annotate-api';

interface AnnotationTimelineProps {
  annotations: Annotation[];
  enabledPatterns: Set<string>;
  dateRange?: { from: string; to: string };
  onAnnotationClick?: (ann: Annotation) => void;
}

export function AnnotationTimeline({
  annotations,
  enabledPatterns,
  dateRange,
  onAnnotationClick,
}: AnnotationTimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState<Annotation | null>(null);

  // 计算日期范围
  const { minDate, maxDate, totalDays } = useMemo(() => {
    if (dateRange) {
      const from = new Date(dateRange.from);
      const to = new Date(dateRange.to);
      return {
        minDate: from,
        maxDate: to,
        totalDays: Math.max(1, (to.getTime() - from.getTime()) / 86400000),
      };
    }
    if (annotations.length === 0) {
      const now = new Date();
      return { minDate: new Date(now.getTime() - 365 * 86400000), maxDate: now, totalDays: 365 };
    }
    const dates = annotations.map((a) => new Date(a.date).getTime());
    const min = new Date(Math.min(...dates));
    const max = new Date(Math.max(...dates));
    const pad = Math.max(30, (max.getTime() - min.getTime()) / 86400000 / 20);
    return {
      minDate: new Date(min.getTime() - pad * 86400000),
      maxDate: new Date(max.getTime() + pad * 86400000),
      totalDays: Math.max(1, (max.getTime() - min.getTime()) / 86400000 + pad * 2),
    };
  }, [annotations, dateRange]);

  // 日期转 x 坐标百分比
  const dateToX = (dateStr: string): number => {
    const t = new Date(dateStr).getTime();
    const ratio = (t - minDate.getTime()) / (totalDays * 86400000);
    return Math.max(0, Math.min(100, ratio * 100));
  };

  // 按类型分组
  const filtered = annotations.filter((a) => enabledPatterns.has(a.type));

  // 按年月分组用于标签
  const monthLabels = useMemo(() => {
    const labels: { x: number; text: string }[] = [];
    const startMonth = new Date(minDate.getFullYear(), minDate.getMonth(), 1);
    let cursor = new Date(startMonth);
    while (cursor <= maxDate) {
      labels.push({
        x: dateToX(cursor.toISOString().slice(0, 10)),
        text: `${cursor.getFullYear()}/${String(cursor.getMonth() + 1).padStart(2, '0')}`,
      });
      cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
    }
    return labels;
  }, [minDate, maxDate]);

  // 按类型统计
  const stats = useMemo(() => {
    const counts: Record<string, number> = {};
    filtered.forEach((a) => {
      counts[a.type] = (counts[a.type] || 0) + 1;
    });
    return counts;
  }, [filtered]);

  return (
    <div style={styles.container} ref={containerRef}>
      {/* 顶部统计 */}
      <div style={styles.header}>
        <div style={styles.title}>📍 标注时间轴</div>
        <div style={styles.stats}>
          {Object.entries(stats).map(([type, count]) => {
            const cfg = PATTERN_CONFIG[type];
            if (!cfg) return null;
            return (
              <span key={type} style={{ ...styles.stat, color: cfg.color }}>
                {cfg.icon} {count}
              </span>
            );
          })}
        </div>
      </div>

      {/* 时间轴主体 */}
      <div style={styles.timelineWrap}>
        {/* 月份标签 */}
        <div style={styles.monthLabels}>
          {monthLabels.map((m, i) => (
            <span
              key={i}
              style={{
                ...styles.monthLabel,
                left: `${m.x}%`,
              }}
            >
              {m.text}
            </span>
          ))}
        </div>

        {/* 标注点 */}
        <div style={styles.timeline}>
          {/* 基准线 */}
          <div style={styles.baseline} />

          {/* 标注点 */}
          {filtered.map((ann, i) => {
            const cfg = PATTERN_CONFIG[ann.type];
            if (!cfg) return null;
            const x = dateToX(ann.date);
            const isUp = ann.type.startsWith('b');

            return (
              <div
                key={`${ann.date}-${ann.type}-${i}`}
                style={{
                  ...styles.marker,
                  left: `${x}%`,
                  color: cfg.color,
                  borderColor: cfg.color,
                  transform: `translate(-50%, ${isUp ? '-100%' : '0%'})`,
                  top: isUp ? '10%' : '60%',
                }}
                onMouseEnter={() => setHovered(ann)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => onAnnotationClick?.(ann)}
                title={`${cfg.label} | ${ann.date} | ¥${ann.price.toFixed(2)}`}
              >
                {cfg.icon}
              </div>
            );
          })}
        </div>

        {/* 悬浮提示 */}
        {hovered && (
          <div
            style={{
              ...styles.tooltip,
              left: `${dateToX(hovered.date)}%`,
              top: '100%',
            }}
          >
            <div style={{ fontWeight: 600, color: PATTERN_CONFIG[hovered.type]?.color }}>
              {PATTERN_CONFIG[hovered.type]?.label} ({hovered.type.toUpperCase()})
            </div>
            <div>日期: {hovered.date}</div>
            <div>价格: ¥{hovered.price.toFixed(2)}</div>
            <div>置信度: {(hovered.confidence * 100).toFixed(0)}%</div>
            {hovered.metadata.j_value !== undefined && (
              <div>J 值: {hovered.metadata.j_value}</div>
            )}
            {hovered.metadata.wave_gain !== undefined && (
              <div>建仓波涨幅: {hovered.metadata.wave_gain}%</div>
            )}
            {hovered.metadata.volume_ratio !== undefined && (
              <div>量比: {hovered.metadata.volume_ratio}</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    background: 'rgba(15, 15, 15, 0.95)',
    border: '1px solid #2a2a2a',
    borderRadius: 6,
    padding: '8px 12px',
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
  stats: {
    display: 'flex',
    gap: 12,
    fontSize: 11,
  },
  stat: {
    fontWeight: 500,
  },
  timelineWrap: {
    position: 'relative',
    height: 70,
    padding: '0 4px',
  },
  monthLabels: {
    position: 'relative',
    height: 16,
    marginBottom: 4,
  },
  monthLabel: {
    position: 'absolute',
    fontSize: 9,
    color: '#666',
    transform: 'translateX(-50%)',
  },
  timeline: {
    position: 'relative',
    height: 50,
  },
  baseline: {
    position: 'absolute',
    top: '50%',
    left: 0,
    right: 0,
    height: 1,
    background: '#333',
  },
  marker: {
    position: 'absolute',
    width: 18,
    height: 18,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 12,
    fontWeight: 'bold',
    background: 'rgba(0,0,0,0.8)',
    border: '2px solid',
    borderRadius: '50%',
    cursor: 'pointer',
    userSelect: 'none',
  },
  tooltip: {
    position: 'absolute',
    background: 'rgba(20, 20, 20, 0.98)',
    border: '1px solid #444',
    borderRadius: 4,
    padding: '6px 10px',
    fontSize: 11,
    color: '#ccc',
    transform: 'translateX(-50%)',
    marginTop: 4,
    minWidth: 160,
    zIndex: 100,
    lineHeight: 1.6,
  },
};