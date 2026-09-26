import { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWatchlist } from '@/hooks/useWatchlist';
import { useStockNav } from '@/hooks/useStockNav';

interface Symbol {
  thscode: string;
  ticker: string;
  name: string | null;
  exchange: string | null;
}

type Tab = 'watch' | 'picks' | 'SH' | 'SZ' | 'BJ';

export function SymbolList() {
  // URL ?tab=picks|watch|SH|SZ|BJ 深链：WeKnora agent tool 返回的 URL 直接打开对应 tab
  const initialTab = (() => {
    const t = new URLSearchParams(window.location.search).get('tab');
    if (t === 'picks' || t === 'watch' || t === 'SH' || t === 'SZ' || t === 'BJ') return t;
    return 'watch';
  })();
  const [tab, setTab] = useState<Tab>(initialTab);
  const [search, setSearch] = useState('');
  const [items, setItems] = useState<Symbol[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAddGroup, setShowAddGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');

  const navigate = useNavigate();
  const wl = useWatchlist();
  const { setSymbols, currentTicker, currentExchange, registerSearchInput } = useStockNav();
  const inputRef = useRef<HTMLInputElement>(null);
  const activeRowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    registerSearchInput(inputRef.current);
  }, [registerSearchInput]);

  useEffect(() => {
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        let url: string;
        if (search.trim()) {
          url = `/api/symbols/search?q=${encodeURIComponent(search.trim())}&limit=200`;
        } else if (tab === 'watch') {
          // 自选 tab：根据当前激活的 activeGroup 筛选
          const entries = wl.filteredList;
          const fetched = await Promise.all(
            entries.map((e) =>
              fetch(`/api/symbols/${e.ticker}.${e.exchange}`, { signal: ctrl.signal })
                .then((r) => r.json())
                .then((env) => env.data as Symbol | null)
                .catch(() => null),
            ),
          );
          const map = new Map(fetched.filter((s): s is Symbol => !!s).map((s) => [s.thscode, s]));
          const ordered = entries
            .map((e) => map.get(`${e.ticker}.${e.exchange}`))
            .filter((s): s is Symbol => !!s);
          setItems(ordered);
          setSymbols(
            ordered.map((s) => ({
              ticker: s.ticker,
              exchange: s.exchange ?? 'SH',
              name: s.name,
            })),
          );
          setLoading(false);
          return;
        } else if (tab === 'picks') {
          // 今日 picks tab：来自 data/picks.json（上级 KB 推送）
          const env = await (await fetch('/api/picks', { signal: ctrl.signal })).json();
          const list: { ticker: string; exchange: string }[] = env.data ?? [];
          const fetched = await Promise.all(
            list.map((p) =>
              fetch(`/api/symbols/${p.ticker}.${p.exchange}`, { signal: ctrl.signal })
                .then((r) => r.json())
                .then((env) => env.data as Symbol | null)
                .catch(() => null),
            ),
          );
          const map = new Map(fetched.filter((s): s is Symbol => !!s).map((s) => [s.thscode, s]));
          const ordered = list
            .map((p) => map.get(`${p.ticker}.${p.exchange}`))
            .filter((s): s is Symbol => !!s);
          setItems(ordered);
          setSymbols(
            ordered.map((s) => ({
              ticker: s.ticker,
              exchange: s.exchange ?? 'SH',
              name: s.name,
            })),
          );
          setLoading(false);
          return;
        } else {
          url = `/api/symbols?limit=200&exchange=${tab}`;
        }
        const res = await fetch(url, { signal: ctrl.signal });
        const env = await res.json();
        const listData: Symbol[] = env.data ?? [];
        setItems(listData);
        setSymbols(
          listData.map((s) => ({
            ticker: s.ticker,
            exchange: s.exchange ?? 'SH',
            name: s.name,
          })),
        );
      } catch (err) {
        if (err instanceof Error && err.name !== 'AbortError') {
          console.error(err);
        }
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => {
      ctrl.abort();
      clearTimeout(t);
    };
  }, [search, tab, wl.filteredList, setSymbols]);

  // 当外部通过键盘切换股票时，让列表自动滚动到当前高亮项
  useEffect(() => {
    if (activeRowRef.current) {
      activeRowRef.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [currentTicker, currentExchange]);

  const handleCreateGroup = (e: React.FormEvent) => {
    e.preventDefault();
    if (newGroupName.trim()) {
      wl.addGroup(newGroupName.trim());
      wl.setActiveGroup(newGroupName.trim());
      setNewGroupName('');
      setShowAddGroup(false);
    }
  };

  return (
    <div style={styles.wrap}>
      <div style={styles.tabs}>
        <TabButton active={tab === 'watch'} onClick={() => setTab('watch')} count={wl.list.length}>
          自选
        </TabButton>
        <TabButton active={tab === 'picks'} onClick={() => setTab('picks')}>
          今日 picks
        </TabButton>
        <TabButton active={tab === 'SH'} onClick={() => setTab('SH')}>
          沪 A
        </TabButton>
        <TabButton active={tab === 'SZ'} onClick={() => setTab('SZ')}>
          深 A
        </TabButton>
        <TabButton active={tab === 'BJ'} onClick={() => setTab('BJ')}>
          北交
        </TabButton>
      </div>

      {tab === 'watch' && (
        <div style={styles.groupBar}>
          <div style={styles.groupPills}>
            <button
              onClick={() => wl.setActiveGroup('全部')}
              style={{
                ...styles.pill,
                ...(wl.activeGroup === '全部' ? styles.pillActive : null),
              }}
            >
              全部 ({wl.list.length})
            </button>
            {wl.groups.map((grp) => {
              const count = wl.list.filter((x) => (x.group || '默认') === grp).length;
              return (
                <button
                  key={grp}
                  onClick={() => wl.setActiveGroup(grp)}
                  style={{
                    ...styles.pill,
                    ...(wl.activeGroup === grp ? styles.pillActive : null),
                  }}
                >
                  {grp} ({count})
                </button>
              );
            })}
          </div>
          <button
            onClick={() => setShowAddGroup(!showAddGroup)}
            style={styles.addGroupBtn}
            title="新建自选分组"
          >
            +
          </button>
        </div>
      )}

      {showAddGroup && tab === 'watch' && (
        <form onSubmit={handleCreateGroup} style={styles.addGroupForm}>
          <input
            placeholder="新分组名称（如：今日首板）"
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            style={styles.addGroupInput}
            autoFocus
          />
          <button type="submit" style={styles.addGroupSubmit}>
            确定
          </button>
        </form>
      )}

      <div style={styles.search}>
        <input
          ref={inputRef}
          placeholder="按 [/] 搜索代码 / 名称"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={styles.input}
        />
      </div>

      <div style={styles.tableHead}>
        <span style={{ width: 70 }}>代码</span>
        <span style={{ flex: 1 }}>名称</span>
        <span style={{ width: 32, textAlign: 'right' }}>市场</span>
        <span style={{ width: 24 }} />
      </div>

      <div style={styles.list}>
        {loading && <div style={styles.empty}>加载中…</div>}
        {!loading && items.length === 0 && (
          <div style={styles.empty}>
            {tab === 'watch'
              ? '当前分组暂无自选，点右侧 ☆ 加自选'
              : tab === 'picks'
              ? '暂无 picks，检查 data/picks.json'
              : '无匹配标的'}
          </div>
        )}
        {items.map((s) => {
          const isCurrent = s.ticker === currentTicker && s.exchange === currentExchange;
          return (
            <div key={s.thscode} ref={isCurrent ? activeRowRef : undefined}>
              <Row
                symbol={s}
                isCurrent={isCurrent}
                starred={wl.has(s.ticker, s.exchange ?? 'SH')}
                onClick={() => navigate(`/k/${s.ticker}/${s.exchange ?? 'SH'}`)}
                onToggleStar={() => wl.toggle(s.ticker, s.exchange ?? 'SH', wl.activeGroup === '全部' ? '默认' : wl.activeGroup)}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <button onClick={onClick} style={{ ...styles.tab, ...(active ? styles.tabActive : null) }}>
      {children}
      {typeof count === 'number' && <span style={styles.tabCount}>{count}</span>}
    </button>
  );
}

function Row({
  symbol,
  isCurrent,
  starred,
  onClick,
  onToggleStar,
}: {
  symbol: Symbol;
  isCurrent: boolean;
  starred: boolean;
  onClick: () => void;
  onToggleStar: () => void;
}) {
  return (
    <div
      style={{
        ...styles.row,
        ...(isCurrent ? styles.rowCurrent : null),
      }}
    >
      <button onClick={onClick} style={styles.rowMain}>
        <span style={{ width: 70 }} className="num">
          {symbol.ticker}
        </span>
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {symbol.name ?? '—'}
        </span>
        <span style={{ width: 32, textAlign: 'right', color: 'var(--text-muted)' }}>
          {symbol.exchange}
        </span>
      </button>
      <button
        onClick={onToggleStar}
        title={starred ? '从自选移除' : '加入自选'}
        style={{ ...styles.starBtn, color: starred ? '#facc15' : 'var(--text-muted)' }}
      >
        {starred ? '★' : '☆'}
      </button>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 },
  tabs: {
    display: 'flex',
    padding: '0 var(--sp-2)',
    borderBottom: '1px solid var(--border-subtle)',
  },
  tab: {
    padding: '10px 10px',
    fontSize: 'var(--fs-sm)',
    color: 'var(--text-secondary)',
    background: 'transparent',
    borderBottom: '2px solid transparent',
    transition: 'all 0.15s',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  tabActive: {
    color: 'var(--text-primary)',
    borderBottomColor: 'var(--accent)',
    fontWeight: 600,
  },
  tabCount: {
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-muted)',
    background: 'var(--bg-overlay)',
    padding: '1px 5px',
    borderRadius: 8,
    minWidth: 16,
    textAlign: 'center',
  },
  groupBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '6px 8px',
    background: 'var(--bg-base)',
    borderBottom: '1px solid var(--border-subtle)',
    gap: 4,
  },
  groupPills: {
    display: 'flex',
    overflowX: 'auto',
    gap: 4,
    scrollbarWidth: 'none',
  },
  pill: {
    padding: '2px 8px',
    fontSize: '11px',
    borderRadius: 12,
    color: 'var(--text-muted)',
    background: 'var(--bg-overlay)',
    whiteSpace: 'nowrap',
    border: '1px solid transparent',
  },
  pillActive: {
    color: '#fff',
    background: 'var(--accent)',
    borderColor: 'var(--accent)',
    fontWeight: 600,
  },
  addGroupBtn: {
    fontSize: '14px',
    width: 22,
    height: 22,
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: 'var(--text-secondary)',
    background: 'var(--bg-overlay)',
    flexShrink: 0,
  },
  addGroupForm: {
    display: 'flex',
    padding: '6px 8px',
    gap: 4,
    borderBottom: '1px solid var(--border-subtle)',
    background: 'var(--bg-overlay)',
  },
  addGroupInput: {
    flex: 1,
    padding: '4px 6px',
    fontSize: '11px',
    background: 'var(--bg-base)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 3,
    color: 'var(--text-primary)',
  },
  addGroupSubmit: {
    padding: '4px 8px',
    fontSize: '11px',
    background: 'var(--accent)',
    color: '#fff',
    borderRadius: 3,
  },
  search: {
    padding: 'var(--sp-2) var(--sp-3)',
    borderBottom: '1px solid var(--border-subtle)',
  },
  input: {
    width: '100%',
    padding: '6px 10px',
    background: 'var(--bg-base)',
    border: '1px solid var(--border-subtle)',
    borderRadius: 4,
    color: 'var(--text-primary)',
    fontSize: 'var(--fs-sm)',
  },
  tableHead: {
    display: 'flex',
    padding: '6px var(--sp-3)',
    fontSize: 'var(--fs-xs)',
    color: 'var(--text-muted)',
    borderBottom: '1px solid var(--border-subtle)',
  },
  list: { flex: 1, overflowY: 'auto' },
  row: {
    display: 'flex',
    width: '100%',
    borderBottom: '1px solid var(--border-subtle)',
    alignItems: 'stretch',
    transition: 'background 0.1s',
  },
  rowCurrent: {
    background: 'var(--bg-overlay)',
    borderLeft: '3px solid var(--accent)',
  },
  rowMain: {
    flex: 1,
    display: 'flex',
    padding: '6px var(--sp-3)',
    fontSize: 'var(--fs-sm)',
    textAlign: 'left',
    alignItems: 'center',
    color: 'var(--text-primary)',
    minWidth: 0,
  },
  starBtn: {
    width: 32,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 14,
  },
  empty: {
    padding: 'var(--sp-4)',
    color: 'var(--text-muted)',
    fontSize: 'var(--fs-sm)',
    textAlign: 'center',
  },
};
