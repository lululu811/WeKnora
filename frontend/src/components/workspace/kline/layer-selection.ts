/**
 * layer-selection — 图层里「按项勾选」的开关状态。
 *
 * ## 为什么存「关掉的那些」而不是「打开的那些」
 *
 * 默认全选，例外才需要手动关。所以内部存的是**禁用集**：没被记过的项一律算可见。
 * 存反了（存启用集）的话，任何新出现的项名都会静默不显示 —— 用户看到的是
 * 「明明识别出来了却没画」，且没有任何报错可查。
 *
 * ## 为什么按**名字**而不是下标记住
 *
 * 形态名是稳定的，下标不是：换一只票、换一个周期，同一批形态的下标全变。
 * 存下标等于每次切票都把用户的选择洗掉。
 *
 * ## 一个刻意接受的边界
 *
 * 「全不选」关掉的是**当前已知的**那些名字，不是「以后所有名字」。所以关掉
 * 双顶/旗形之后，切到一只识别出「头肩底」的票，头肩底仍然会显示 —— 因为用户
 * 从没对「头肩底」表过态。反过来（用一个 "none" 哨兵值把未来的一起关掉）会让
 * 「我不想看某几类」这条偏好变成「我不想看任何形态」，那是另一个意思。
 */

/** 一个图层当前可选的全部项。 */
export interface LayerOption {
  /** 稳定标识：形态名 / 标注类型 key。 */
  value: string
  /** 展示名。 */
  label: string
  /** 悬浮说明（可选）。 */
  desc?: string
  /** 该项在当前图上实际的命中数量（可选，用于置灰或提示）。 */
  count?: number
  /**
   * 分组名（可选）。留空归入不分组的那一组。
   *
   * 气泡那边两类来源混在一起（战法标注 4 类 + 蜡烛形态 24 类），
   * 平铺成 28 行没法看，必须分组。
   */
  group?: string
}

export interface LayerSelection {
  /** 被用户显式关掉的项。空数组 = 全选。 */
  disabled: string[]
}

export const SELECTION_ALL: LayerSelection = { disabled: [] }

/** 该项当前是否可见。没被记过 = 可见。 */
export function isOptionEnabled(selection: LayerSelection, value: string): boolean {
  return !selection.disabled.includes(value)
}

/** 翻转一项。 */
export function toggleOption(selection: LayerSelection, value: string): LayerSelection {
  return selection.disabled.includes(value)
    ? { disabled: selection.disabled.filter((v) => v !== value) }
    : { disabled: [...selection.disabled, value] };
}

/** 全选：清空禁用集。 */
export function selectAllOptions(): LayerSelection {
  return SELECTION_ALL;
}

/**
 * 全不选：把**当前已知**的项全部关掉。
 * 未列出的名字（以后才出现的形态）不在其中，仍按默认可见处理。
 */
export function clearAllOptions(options: readonly LayerOption[]): LayerSelection {
  return { disabled: options.map((o) => o.value) };
}

/** 当前可见的项数。 */
export function enabledCount(options: readonly LayerOption[], selection: LayerSelection): number {
  return options.filter((o) => isOptionEnabled(selection, o.value)).length;
}

/** 按选择过滤一组带 value 的条目。 */
export function filterBySelection<T extends { value: string }>(
  items: readonly T[],
  selection: LayerSelection,
): T[] {
  return items.filter((i) => isOptionEnabled(selection, i.value));
}

/** 是否处于「全选」——空图（没有可选形态）时也算，按钮不该显示 0/0 之外的歧义态。 */
export function isAllSelected(options: readonly LayerOption[], selection: LayerSelection): boolean {
  return options.every((o) => isOptionEnabled(selection, o.value));
}

// ---------------------------------------------------------------------------
// 持久化
//
// 用户「不想看旗形」不是针对某一只票的，所以跨切票、跨刷新都要留住。
// 存储不可用（隐私模式、配额满）时一律退回全选：宁可丢偏好，也不要因为
// 写不进去就报错崩掉工具栏。
// ---------------------------------------------------------------------------

const STORAGE_PREFIX = 'weknora_kline_layer_';

export function loadSelection(storageKey: string): LayerSelection {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + storageKey);
    if (!raw) return SELECTION_ALL;
    const parsed = JSON.parse(raw);
    if (parsed && Array.isArray(parsed.disabled)) {
      return { disabled: parsed.disabled.filter((v: unknown) => typeof v === 'string') };
    }
  } catch {
    // 坏数据/不可用 -> 全选
  }
  return SELECTION_ALL;
}

export function saveSelection(storageKey: string, selection: LayerSelection): void {
  try {
    localStorage.setItem(STORAGE_PREFIX + storageKey, JSON.stringify(selection));
  } catch {
    // 存不进去就算了，不影响本次会话的显示。
  }
}
