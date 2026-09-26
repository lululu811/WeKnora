import { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'zettaranc.watchlist.v1';
const GROUPS_KEY = 'zettaranc.watchlist.groups.v1';
const NOTES_KEY = 'zettaranc.stock.notes.v1';

export interface WatchEntry {
  ticker: string;
  exchange: string;
  addedAt: number;
  group?: string;
}

const DEFAULT_GROUPS = ['默认', '核心龙头', '观察池', '趋势波段'];

const DEFAULTS: WatchEntry[] = [
  { ticker: '600519', exchange: 'SH', addedAt: 0, group: '核心龙头' }, // 贵州茅台
  { ticker: '000001', exchange: 'SZ', addedAt: 0, group: '默认' }, // 平安银行
  { ticker: '300750', exchange: 'SZ', addedAt: 0, group: '核心龙头' }, // 宁德时代
];

function loadEntries(): WatchEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULTS;
    return parsed.filter(
      (e): e is WatchEntry =>
        typeof e === 'object' &&
        e !== null &&
        typeof (e as WatchEntry).ticker === 'string' &&
        typeof (e as WatchEntry).exchange === 'string',
    ).map(e => ({
      ...e,
      group: e.group || '默认'
    }));
  } catch {
    return DEFAULTS;
  }
}

function loadGroups(): string[] {
  try {
    const raw = localStorage.getItem(GROUPS_KEY);
    if (!raw) return DEFAULT_GROUPS;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : DEFAULT_GROUPS;
  } catch {
    return DEFAULT_GROUPS;
  }
}

function loadNotes(): Record<string, string> {
  try {
    const raw = localStorage.getItem(NOTES_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function useWatchlist() {
  const [list, setList] = useState<WatchEntry[]>(loadEntries);
  const [groups, setGroups] = useState<string[]>(loadGroups);
  const [activeGroup, setActiveGroup] = useState<string>('全部');
  const [notes, setNotes] = useState<Record<string, string>>(loadNotes);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
    } catch {}
  }, [list]);

  useEffect(() => {
    try {
      localStorage.setItem(GROUPS_KEY, JSON.stringify(groups));
    } catch {}
  }, [groups]);

  useEffect(() => {
    try {
      localStorage.setItem(NOTES_KEY, JSON.stringify(notes));
    } catch {}
  }, [notes]);

  const has = useCallback(
    (ticker: string, exchange: string) =>
      list.some((e) => e.ticker === ticker && e.exchange === exchange),
    [list],
  );

  const add = useCallback((ticker: string, exchange: string, group = '默认') => {
    setList((cur) => {
      if (cur.some((e) => e.ticker === ticker && e.exchange === exchange)) return cur;
      return [...cur, { ticker, exchange, addedAt: Date.now(), group }];
    });
  }, []);

  const remove = useCallback((ticker: string, exchange: string) => {
    setList((cur) => cur.filter((e) => !(e.ticker === ticker && e.exchange === exchange)));
  }, []);

  const toggle = useCallback(
    (ticker: string, exchange: string, defaultGroup = '默认') => {
      if (has(ticker, exchange)) remove(ticker, exchange);
      else add(ticker, exchange, defaultGroup);
    },
    [has, add, remove],
  );

  const addGroup = useCallback((name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setGroups((prev) => (prev.includes(trimmed) ? prev : [...prev, trimmed]));
  }, []);

  const removeGroup = useCallback((name: string) => {
    if (name === '默认') return;
    setGroups((prev) => prev.filter((g) => g !== name));
    // 将该分组股票重置为默认
    setList((prev) =>
      prev.map((item) => (item.group === name ? { ...item, group: '默认' } : item)),
    );
    setActiveGroup('全部');
  }, []);

  const setEntryGroup = useCallback((ticker: string, exchange: string, group: string) => {
    setList((prev) =>
      prev.map((item) =>
        item.ticker === ticker && item.exchange === exchange ? { ...item, group } : item,
      ),
    );
  }, []);

  const setNote = useCallback((thscode: string, note: string) => {
    setNotes((prev) => ({ ...prev, [thscode]: note }));
  }, []);

  const getNote = useCallback((thscode: string) => notes[thscode] || '', [notes]);

  // 过滤后的当前自选列表
  const filteredList =
    activeGroup === '全部'
      ? list
      : list.filter((item) => (item.group || '默认') === activeGroup);

  return {
    list,
    filteredList,
    groups,
    activeGroup,
    setActiveGroup,
    addGroup,
    removeGroup,
    setEntryGroup,
    has,
    add,
    remove,
    toggle,
    notes,
    getNote,
    setNote,
  };
}
