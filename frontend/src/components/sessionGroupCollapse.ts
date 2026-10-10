// Logic for sidebar session group collapsing/expanding (per agent or date).

export const COLLAPSED_GROUPS_STORAGE_KEY = 'weknora.sidebar.collapsedAgentGroups'

export function readCollapsedGroupsFromStorage(storage?: Storage): Set<string> {
  const store = storage ?? (typeof window !== 'undefined' ? window.localStorage : undefined)
  if (!store) return new Set()
  try {
    const raw = store.getItem(COLLAPSED_GROUPS_STORAGE_KEY)
    if (!raw) return new Set()
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? new Set(parsed.filter((k): k is string => typeof k === 'string')) : new Set()
  } catch {
    return new Set()
  }
}

export function writeCollapsedGroupsToStorage(collapsed: Set<string>, storage?: Storage): void {
  const store = storage ?? (typeof window !== 'undefined' ? window.localStorage : undefined)
  if (!store) return
  try {
    store.setItem(COLLAPSED_GROUPS_STORAGE_KEY, JSON.stringify(Array.from(collapsed)))
  } catch {
    // Quota exceeded or private browsing error — ignore
  }
}

export function toggleGroupKey(collapsed: Set<string>, key: string): Set<string> {
  const next = new Set(collapsed)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  return next
}

export function areAllGroupsCollapsed(collapsed: Set<string>, allKeys: string[]): boolean {
  if (allKeys.length === 0) return false
  return allKeys.every((key) => collapsed.has(key))
}

export function toggleAllGroupKeys(collapsed: Set<string>, allKeys: string[]): Set<string> {
  if (allKeys.length === 0) return new Set(collapsed)
  if (areAllGroupsCollapsed(collapsed, allKeys)) {
    return new Set()
  }
  return new Set(allKeys)
}

export interface SessionGroupWithItems {
  key: string
  items: Array<{ id?: string; path?: string }>
}

export function ensureActiveGroupExpanded(
  collapsed: Set<string>,
  groups: SessionGroupWithItems[],
  activePath?: string,
  revealedId?: string,
): { collapsed: Set<string>; changed: boolean } {
  if (!activePath && !revealedId) return { collapsed, changed: false }
  const target = groups.find((g) =>
    g.items.some(
      (item) =>
        (activePath && item.path === activePath) ||
        (revealedId && item.id === revealedId),
    ),
  )
  if (target && collapsed.has(target.key)) {
    const next = new Set(collapsed)
    next.delete(target.key)
    return { collapsed: next, changed: true }
  }
  return { collapsed, changed: false }
}
