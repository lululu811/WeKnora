import { useEffect, useState } from 'react';
import { useWatchlist } from '@/hooks/useWatchlist';
import { IntradayModal } from './IntradayModal';

interface HeaderData {
  name: string;
  ticker: string;
  exchange: string;
  last?: {
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    amount: number;
    change: number;
    changePct: number;
    prevClose: number;
  };
}

function getLimitRate(ticker: string, exchange: string, name = ''): number {
  if (name.includes('ST')) return 0.05;
  if (exchange === 'BJ' || ticker.startsWith('8') || ticker.startsWith('4') || ticker.startsWith('920')) {
    return 0.30;
  }
  if (ticker.startsWith('688') || ticker.startsWith('300') || ticker.startsWith('301')) {
    return 0.20;
  }
  return 0.10;
}

function inferSectors(ticker: string, name = ''): string[] {
  const tags: string[] = [];
  if (name.includes('酒') || ticker === '600519' || ticker === '000858') tags.push('白酒消费');
  if (name.includes('银') || ticker === '000001' || ticker === '601398') tags.push('大金融/银行');
  if (name.includes('证券') || name.includes('财富') || ticker === '300059') tags.push('证券金融');
  if (name.includes('微') || name.includes('电') || name.includes('芯') || ticker.startsWith('688')) tags.push('半导体/芯片');
  if (name.includes('能') || name.includes('锂') || ticker === '300750') tags.push('新能源/储能');
  if (name.includes('药') || name.includes('医') || name.includes('生物')) tags.push('医药生物');
  if (name.includes('车') || name.includes('汽') || ticker === '002594') tags.push('汽车制造');
  if (name.includes('算') || name.includes('智') || name.includes('软') || name.includes('信')) tags.push('人工智能/算力');
  if (tags.length === 0) tags.push('A股核心资产');
  return tags;
}

export function StockHeader({ ticker, exchange }: { ticker: string; exchange: string }) {
  const [data, setData] = useState<HeaderData | null>(null);
  const [showNoteEditor, setShowNoteEditor] = useState(false);
  const [showIntraday, setShowIntraday] = useState(false);
  const wl = useWatchlist();
  const thscode = `${ticker}.${exchange}`;
  const isStarred = wl.has(ticker, exchange);

  const currentNote = wl.getNote(thscode);
  const [localNote, setLocalNote] = useState(currentNote);

  useEffect(() => {
    setLocalNote(wl.getNote(thscode));
  }, [thscode, wl]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [sym, kline] = await Promise.all([
        fetch(`/api/symbols/${thscode}`).then((r) => r.json()),
        fetch(`/api/kline?symbol=${thscode}&adjust=forward&limit=2`).then((r) => r.json()),
      ]);
      if (cancelled) return;
      const rows = kline.data as Array<{
        ts: number;
        open: number;
        high: number;
        low: number;
        close: number;
        volume: number;
        turnover: number;
      }>;
      const last = rows.length > 0 ? rows[rows.length - 1] : undefined;
      const prev = rows.length > 1 ? rows[rows.length - 2] : undefined;
      let lastRow: HeaderData['last'];
      if (last) {
        const prevClose = prev ? prev.close : last.open;
        const change = prev ? last.close - prev.close : 0;
        const changePct = prevClose !== 0 ? (change / prevClose) * 100 : 0;
        lastRow = {
          date: new Date(last.ts * 1000).toISOString().slice(0, 10),
          open: last.open,
          high: last.high,
          low: last.low,
          close: last.close,
          volume: last.volume,
          amount: last.turnover,
          change,
          changePct,
          prevClose,
        };
      }
      setData({
        name: sym.data?.name ?? ticker,
        ticker,
        exchange,
        last: lastRow,
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [ticker, exchange, thscode]);

  if (!data) {
    return (
      <header style={styles.header}>
        <span className="num" style={styles.code}>
          {ticker}
        </span>
        <span style={styles.muted}>加载中…</span>
      </header>
    );
  }

  // 涨跌停判定
  const limitRate = getLimitRate(ticker, exchange, data.name);
  const prevClose = data.last?.prevClose ?? 0;
  const upLimit = prevClose > 0 ? +(prevClose * (1 + limitRate)).toFixed(2) : 0;
  const downLimit = prevClose > 0 ? +(prevClose * (1 - limitRate)).toFixed(2) : 0;
  const isLimitUp = data.last && upLimit > 0 && data.last.close >= upLimit - 0.005;
  const isLimitDown = data.last && downLimit > 0 && data.last.close <= downLimit + 0.005;

  const changeColor = data.last
    ? data.last.change >= 0
      ? 'var(--up)'
      : 'var(--down)'
    : 'var(--text-muted)';
  const changeSign = data.last && data.last.change >= 0 ? '+' : '';
  const sectors = inferSectors(ticker, data.name);

  const handleSaveNote = () => {
    wl.setNote(thscode, localNote);
    setShowNoteEditor(false);
  };

  return (
    <header style={styles.header}>
      <div style={styles.topRow}>
        <div style={styles.symbolBlock}>
          <span className="num" style={styles.code}>
            {data.ticker}
          </span>
          <span style={styles.name}>{data.name}</span>
          <span style={styles.exchange}>{data.exchange}</span>

          {/* 涨跌停徽章 */}
          {isLimitUp && (
            <span style={styles.limitUpBadge}>
              🔥 涨停 (+{(limitRate * 100).toFixed(0)}%)
            </span>
          )}
          {isLimitDown && (
            <span style={styles.limitDownBadge}>
              ❄️ 跌停 (-{(limitRate * 100).toFixed(0)}%)
            </span>
          )}

          {/* 所属题材概念 Tag */}
          <div style={styles.sectors}>
            {sectors.map((sec) => (
              <span key={sec} style={styles.sectorTag}>
                {sec}
              </span>
            ))}
          </div>
        </div>

        <div style={styles.spacer} />

        {/* 顶部辅助操作栏：分时联动、复盘笔记、加自选 */}
        <div style={styles.actionBlock}>
          <button
            onClick={() => setShowIntraday(true)}
            style={styles.toolBtn}
            title="查看当日还原分时走势"
          >
            ⚡ 分时走势
          </button>

          <button
            onClick={() => setShowNoteEditor(!showNoteEditor)}
            style={{
              ...styles.toolBtn,
              ...(currentNote ? styles.toolBtnActive : null),
            }}
            title="记录或查看个股复盘笔记"
          >
            📝 {currentNote ? '已记笔记' : '复盘笔记'}
          </button>

          <button
            onClick={() => wl.toggle(ticker, exchange)}
            title={isStarred ? '从自选移除' : '加入自选'}
            style={{ ...styles.starBtn, color: isStarred ? '#facc15' : 'var(--text-muted)' }}
          >
            {isStarred ? '★' : '☆'}
          </button>
        </div>
      </div>

      {data.last && (
        <div style={styles.bottomRow}>
          <div style={styles.priceBlock}>
            <span className="num" style={{ ...styles.price, color: changeColor }}>
              {data.last.close.toFixed(2)}
            </span>
            <span className="num" style={{ ...styles.change, color: changeColor }}>
              {changeSign}
              {data.last.change.toFixed(2)} ({changeSign}
              {data.last.changePct.toFixed(2)}%)
            </span>
            <button
              onClick={() => setShowIntraday(true)}
              style={styles.dateLabelBtn}
              title="点击查看当日分时"
            >
              📅 {data.last.date}
            </button>
          </div>

          <div style={styles.ohlcBlock}>
            <OhlcCell label="今开" value={data.last.open} />
            <OhlcCell label="最高" value={data.last.high} />
            <OhlcCell label="最低" value={data.last.low} />
            <OhlcCell label="涨停价" value={upLimit} isUp />
            <OhlcCell label="跌停价" value={downLimit} isDown />
            <OhlcCell label="成交量" value={data.last.volume} formatter={formatVolume} />
            <OhlcCell label="成交额" value={data.last.amount} formatter={formatAmount} />
          </div>
        </div>
      )}

      {/* 复盘笔记编辑浮层 */}
      {showNoteEditor && (
        <div style={styles.noteEditor}>
          <textarea
            value={localNote}
            onChange={(e) => setLocalNote(e.target.value)}
            placeholder="写下对该股的复盘观点（如：突破前高平台，关注回踩 5 日线承接；缩量首板等待二板确认…）"
            style={styles.noteInput}
            rows={2}
          />
          <div style={styles.noteActions}>
            <button onClick={handleSaveNote} style={styles.noteSaveBtn}>
              保存笔记
            </button>
            <button onClick={() => setShowNoteEditor(false)} style={styles.noteCancelBtn}>
              收起
            </button>
          </div>
        </div>
      )}

      {/* 历史分时走势还原弹窗 */}
      {showIntraday && data.last && (
        <IntradayModal
          ticker={ticker}
          exchange={exchange}
          name={data.name}
          date={data.last.date}
          open={data.last.open}
          high={data.last.high}
          low={data.last.low}
          close={data.last.close}
          prevClose={data.last.prevClose}
          volume={data.last.volume}
          amount={data.last.amount}
          onClose={() => setShowIntraday(false)}
        />
      )}
    </header>
  );
}

function OhlcCell({
  label,
  value,
  formatter,
  isUp,
  isDown,
}: {
  label: string;
  value: number;
  formatter?: (n: number) => string;
  isUp?: boolean;
  isDown?: boolean;
}) {
  return (
    <div style={styles.ohlcCell}>
      <span style={styles.ohlcLabel}>{label}</span>
      <span
        className="num"
        style={{
          ...styles.ohlcValue,
          ...(isUp ? styles.up : null),
          ...(isDown ? styles.down : null),
        }}
      >
        {formatter ? formatter(value) : value.toFixed(2)}
      </span>
    </div>
  );
}

function formatVolume(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
  return v.toFixed(0);
}

function formatAmount(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
  return v.toFixed(2);
}

const styles: Record<string, React.CSSProperties> = {
  header: {
    display: 'flex',
    flexDirection: 'column',
    gap: 'var(--sp-2)',
    padding: 'var(--sp-2) var(--sp-3)',
    borderBottom: '1px solid var(--border-subtle)',
    marginBottom: 'var(--sp-2)',
  },
  topRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--sp-4)',
    flexWrap: 'wrap',
  },
  symbolBlock: { display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', flexWrap: 'wrap' },
  code: {
    fontSize: 20,
    fontWeight: 700,
    color: 'var(--text-primary)',
    letterSpacing: 0.3,
  },
  name: { fontSize: 'var(--fs-md)', color: 'var(--text-secondary)', fontWeight: 500 },
  exchange: {
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-muted)',
    padding: '1px 5px',
    border: '1px solid var(--border-subtle)',
    borderRadius: 3,
  },
  limitUpBadge: {
    fontSize: '11px',
    fontWeight: 600,
    color: 'var(--up)',
    background: 'var(--up-soft)',
    padding: '2px 6px',
    borderRadius: 10,
    border: '1px solid var(--up)',
  },
  limitDownBadge: {
    fontSize: '11px',
    fontWeight: 600,
    color: 'var(--down)',
    background: 'var(--down-soft)',
    padding: '2px 6px',
    borderRadius: 10,
    border: '1px solid var(--down)',
  },
  sectors: {
    display: 'flex',
    gap: 4,
  },
  sectorTag: {
    fontSize: '10px',
    color: 'var(--accent)',
    background: 'rgba(88, 166, 255, 0.1)',
    padding: '1px 6px',
    borderRadius: 10,
    border: '1px solid rgba(88, 166, 255, 0.2)',
  },
  spacer: { flex: 1 },
  actionBlock: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  toolBtn: {
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-secondary)',
    background: 'var(--bg-overlay)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 4,
    padding: '4px 8px',
    transition: 'all 0.15s',
  },
  toolBtnActive: {
    color: 'var(--accent)',
    borderColor: 'var(--accent)',
  },
  starBtn: {
    fontSize: 20,
    width: 30,
    height: 30,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'transparent',
    border: '1px solid var(--border-subtle)',
    borderRadius: 4,
  },
  bottomRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 'var(--sp-5)',
    flexWrap: 'wrap',
  },
  priceBlock: { display: 'flex', alignItems: 'baseline', gap: 'var(--sp-3)' },
  price: {
    fontSize: 22,
    fontWeight: 700,
    letterSpacing: 0.5,
  },
  change: { fontSize: 'var(--fs-md)' },
  dateLabelBtn: {
    fontSize: 'var(--fs-xs)',
    color: 'var(--accent)',
    marginLeft: 'var(--sp-1)',
    background: 'transparent',
    cursor: 'pointer',
    borderBottom: '1px dashed var(--accent)',
    paddingBottom: 1,
  },
  ohlcBlock: {
    display: 'flex',
    gap: 'var(--sp-4)',
    padding: '0 var(--sp-3)',
    borderLeft: '1px solid var(--border-subtle)',
    flexWrap: 'wrap',
  },
  ohlcCell: { display: 'flex', flexDirection: 'column', gap: 2 },
  ohlcLabel: { fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' },
  ohlcValue: {
    fontSize: 'var(--fs-sm)',
    color: 'var(--text-primary)',
  },
  noteEditor: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    background: 'var(--bg-overlay)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 6,
    padding: 8,
    marginTop: 4,
  },
  noteInput: {
    width: '100%',
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-primary)',
    background: 'var(--bg-base)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 4,
    padding: 6,
    resize: 'none',
  },
  noteActions: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: 8,
  },
  noteSaveBtn: {
    fontSize: '11px',
    padding: '3px 8px',
    borderRadius: 3,
    background: 'var(--accent)',
    color: '#fff',
  },
  noteCancelBtn: {
    fontSize: '11px',
    padding: '3px 8px',
    borderRadius: 3,
    background: 'transparent',
    color: 'var(--text-muted)',
  },
  up: { color: 'var(--up)' },
  down: { color: 'var(--down)' },
  muted: { color: 'var(--text-muted)' },
};
