import { onBeforeUnmount, watch, type Ref } from 'vue'
import {
  KLINE_RANGE_CLASS,
  KLINE_RANGE_FROM_ATTR,
  KLINE_RANGE_TO_ATTR,
  KLINE_RANGE_MD_ATTR,
} from '@/utils/klineRangeInjector'
import { KLINE_ANCHOR_CLASS, KLINE_ANCHOR_INDEX_ATTR } from '@/utils/klineAnchors'
import type { DateRangeFocus } from '@/composables/useAgentWorkspace'

/**
 * 正文标记（日期区间 / 回答锚点）的事件绑定。
 *
 * 三类标记（ticker、日期区间、锚点）都挂在同一个聊天容器上，但**只用一个
 * MutationObserver**：流式打字期间容器变动极频繁，每个标记各起一个 observer
 * 会让同一片 DOM 被反复全树扫描几遍。绑定函数各自独立（职责不同），
 * 观察与调度只做一次。
 */

const RANGE_BOUND_ATTR = 'data-kline-range-bound'
const ANCHOR_BOUND_ATTR = 'data-kline-anchor-bound'

export interface KLineRangeHandler {
  /**
   * 鼠标停到日期上（或键盘聚焦）。
   *
   * 与 `onActivate` 分开是为了让调用方能对两者用不同的节流策略：hover 会被
   * 鼠标划过触发很多次，必须防抖；click/Enter 是明确的一次性动作，不能防抖
   * （否则点了没反应）。
   */
  onHover?: (range: DateRangeFocus, el: HTMLElement) => void
  onLeave?: (el: HTMLElement) => void
  onActivate: (range: DateRangeFocus, el: HTMLElement) => void
}

export interface KLineAnchorHandler {
  onHover?: (index: number, el: HTMLElement) => void
  onLeave?: (el: HTMLElement) => void
  onActivate: (index: number, el: HTMLElement) => void
}

/**
 * 从一个 `.kline-range` 元素上读出日期诉求。
 *
 * 三个属性都可能不存在：整段被 DOMPurify 剥过、或 marked 把属性拆散了。
 * 一个属性都读不到时返回 null，让调用方跳过——宁可不联动，也不要拿 NaN 去滚图。
 */
function readRangeFocus(el: Element): DateRangeFocus | null {
  const fromRaw = el.getAttribute(KLINE_RANGE_FROM_ATTR)
  const toRaw = el.getAttribute(KLINE_RANGE_TO_ATTR)
  const md = el.getAttribute(KLINE_RANGE_MD_ATTR) || undefined
  const from = fromRaw !== null && Number.isFinite(Number(fromRaw)) ? Number(fromRaw) : undefined
  const to = toRaw !== null && Number.isFinite(Number(toRaw)) ? Number(toRaw) : undefined
  if (from === undefined && to === undefined && !md) return null
  return { from, to, md }
}

/** 从一个 `.kline-anchor` 元素上读出锚点编号。读不到时返回 null。 */
function readAnchorIndex(el: Element): number | null {
  const raw = el.getAttribute(KLINE_ANCHOR_INDEX_ATTR)
  if (raw === null) return null
  const n = Number(raw)
  return Number.isInteger(n) && n > 0 ? n : null
}

/**
 * 给容器内所有未绑定的 `.kline-range` 挂 click/Enter/Space，返回新绑数量。
 * 抽成独立函数是为了能脱离 Vue 直接测绑定逻辑。
 */
function bindKLineRangeElements(root: ParentNode, handler: KLineRangeHandler): number {
  let bound = 0
  const nodes = root.querySelectorAll<HTMLElement>(`.${KLINE_RANGE_CLASS}:not([${RANGE_BOUND_ATTR}])`)
  nodes.forEach((el) => {
    el.setAttribute(RANGE_BOUND_ATTR, '1')
    el.setAttribute('role', 'button')
    el.setAttribute('tabindex', '0')
    const activate = () => {
      const range = readRangeFocus(el)
      if (range) handler.onActivate(range, el)
    }
    const hover = () => {
      const range = readRangeFocus(el)
      if (range) handler.onHover?.(range, el)
    }
    el.addEventListener('click', (event) => {
      event.preventDefault()
      activate()
    })
    // hover 与键盘 focus 走同一条通道：两者都是「用户正在看这一段」的意图，
    // 键盘用户拿不到 mouseenter，只绑 hover 会让这个功能对他们完全不可用。
    el.addEventListener('mouseenter', hover)
    el.addEventListener('focus', hover)
    el.addEventListener('mouseleave', () => handler.onLeave?.(el))
    el.addEventListener('blur', () => handler.onLeave?.(el))
    el.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        activate()
      }
    })
    bound += 1
  })
  return bound
}

/** 给容器内所有未绑定的 `.kline-anchor` 挂事件，返回新绑数量。 */
function bindKLineAnchorElements(root: ParentNode, handler: KLineAnchorHandler): number {
  let bound = 0
  const nodes = root.querySelectorAll<HTMLElement>(`.${KLINE_ANCHOR_CLASS}:not([${ANCHOR_BOUND_ATTR}])`)
  nodes.forEach((el) => {
    el.setAttribute(ANCHOR_BOUND_ATTR, '1')
    // 无效锚点（模型写错了日期/价格）只渲染成文本，不给它交互语义——
    // 一个「点了没反应」的可点元素比不可点更让人困惑。
    if (el.getAttribute('data-anchor-valid') !== '1') return

    el.setAttribute('role', 'button')
    el.setAttribute('tabindex', '0')
    const index = () => readAnchorIndex(el)
    const activate = () => {
      const i = index()
      if (i !== null) handler.onActivate(i, el)
    }
    const hover = () => {
      const i = index()
      if (i !== null) handler.onHover?.(i, el)
    }
    el.addEventListener('click', (event) => {
      event.preventDefault()
      activate()
    })
    el.addEventListener('mouseenter', hover)
    el.addEventListener('focus', hover)
    el.addEventListener('mouseleave', () => handler.onLeave?.(el))
    el.addEventListener('blur', () => handler.onLeave?.(el))
    el.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        activate()
      }
    })
    bound += 1
  })
  return bound
}

export interface KLineMarkerHandlers {
  range: KLineRangeHandler
  anchor: KLineAnchorHandler
}

/**
 * 监听 root 的 DOM 变化，把新增的标记元素绑上事件。
 *
 * 流式打字期间正文是逐段插入的，标记必须一落地就可交互，所以靠
 * MutationObserver 增量发现 + rAF 合并，而不是等回答结束。
 */
export function useKLineMarkerObserver(
  rootRef: Ref<HTMLElement | null | undefined>,
  handlers: KLineMarkerHandlers,
) {
  let observer: MutationObserver | null = null
  let rafScheduled = false

  const flush = () => {
    rafScheduled = false
    const root = rootRef.value
    if (!root) return
    // 一次扫描里把两类标记都绑掉，避免同一片 DOM 被扫两遍。
    bindKLineRangeElements(root, handlers.range)
    bindKLineAnchorElements(root, handlers.anchor)
  }

  const schedule = () => {
    if (rafScheduled) return
    rafScheduled = true
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(flush)
    } else {
      setTimeout(flush, 16)
    }
  }

  const ensureObserver = (root: HTMLElement) => {
    if (observer) return
    observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === 'childList' && m.addedNodes.length > 0) {
          schedule()
          return
        }
        if (m.type === 'characterData') {
          schedule()
          return
        }
      }
    })
    observer.observe(root, { childList: true, subtree: true, characterData: true })
    schedule()
  }

  watch(
    rootRef,
    (root) => {
      if (root) ensureObserver(root)
    },
    { immediate: true, flush: 'post' },
  )

  onBeforeUnmount(() => {
    if (observer) {
      observer.disconnect()
      observer = null
    }
  })
}
