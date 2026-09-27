import { onBeforeUnmount, watch, type Ref } from 'vue'
import { bindKLineTickerElements, type KLineTickerHandler } from '@/utils/klineTickerInjector'

/**
 * 监听 root 容器的 DOM 变化，把新增的 `.kline-ticker` 元素绑上 hover/click
 * 事件。每次 throttle 一下，避免流式渲染期间每字符都跑全树扫描。
 */
export function useKLineTickerObserver(
  rootRef: Ref<HTMLElement | null | undefined>,
  handler: KLineTickerHandler,
) {
  let observer: MutationObserver | null = null
  let rafScheduled = false

  const flush = () => {
    rafScheduled = false
    if (!rootRef.value) return
    bindKLineTickerElements(rootRef.value, handler)
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
      // 只关心 chat 流式文本里新增 ticker 节点；其它变化（属性调整、文本
      // 修改）通过 full-scan 处理，开销在 rAF 内合并。
      let needsScan = false
      for (const m of mutations) {
        if (m.type === 'childList' && m.addedNodes.length > 0) {
          needsScan = true
          break
        }
        if (m.type === 'characterData') {
          needsScan = true
          break
        }
      }
      if (needsScan) schedule()
    })
    observer.observe(root, {
      childList: true,
      subtree: true,
      characterData: true,
    })
    // 初次扫描（容器可能已有 ticker）。
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