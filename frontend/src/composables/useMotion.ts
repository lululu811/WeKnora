/**
 * useMotion — Direction A「午后的工作室」unified motion composable.
 *
 * Provides the three basic animations from the design brief:
 * 1. useSendFlight — main action feedback (composer "投递" flight)
 * 2. useSessionTransition — state transition origin (cross-fade)
 * 3. useStaggerRise — first entry (cards rise with stagger)
 *
 * All animations respect prefers-reduced-motion and stay within the motion
 * budget: ≤300ms duration, ≤12px displacement (except signature moments).
 */
import { animate } from 'motion-v'
import { onBeforeUnmount, ref, type Ref } from 'vue'

/** Reactive reduced-motion flag. */
export function useReducedMotion(): Ref<boolean> {
    const reduced = ref(false)
    if (typeof window !== 'undefined') {
        const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
        reduced.value = mq.matches
        const handler = (e: MediaQueryListEvent) => { reduced.value = e.matches }
        mq.addEventListener('change', handler)
        onBeforeUnmount(() => mq.removeEventListener('change', handler))
    }
    return reduced
}

/**
 * Send flight: the composer "投递" animation.
 * Clones the text content, animates it shrinking/sliding toward the target,
 * then removes the clone. Returns a trigger function.
 */
export function useSendFlight(options: {
    /** Element to clone and animate (usually the composer's textarea). */
    sourceRef: Ref<HTMLElement | null>
    /** Target element to fly toward (usually the message list container). */
    targetRef: Ref<HTMLElement | null>
    /** Flight direction: 'down' when messages are below composer, 'up' when above. */
    direction?: 'up' | 'down'
    /** Duration in ms. Default 200 (motion budget). */
    duration?: number
}) {
    const reduced = useReducedMotion()
    let flightEl: HTMLElement | null = null

    const cleanup = () => {
        if (flightEl?.parentNode) flightEl.parentNode.removeChild(flightEl)
        flightEl = null
    }

    const trigger = (text: string) => {
        if (reduced.value) return
        const source = options.sourceRef.value
        if (!source) return

        cleanup()

        // Create flight clone
        flightEl = document.createElement('div')
        flightEl.textContent = text.length > 80 ? `${text.slice(0, 80)}…` : text
        flightEl.style.cssText = `
            position: fixed;
            left: ${source.getBoundingClientRect().left + 16}px;
            top: ${source.getBoundingClientRect().top + 14}px;
            padding: 6px 10px;
            border-radius: 8px;
            background: var(--td-bg-color-secondarycontainer, #F3EAD9);
            color: var(--td-text-color-secondary, #8A7B6D);
            font-size: 13px;
            line-height: 18px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            pointer-events: none;
            z-index: 1000;
            max-width: 400px;
        `
        document.body.appendChild(flightEl)

        const direction = options.direction ?? 'down'
        const displacement = direction === 'down' ? 56 : -56
        const duration = (options.duration ?? 200) / 1000

        animate(
            flightEl,
            { opacity: [1, 0], y: [0, displacement], scale: [1, 0.85] },
            { duration, ease: [0.16, 1, 0.3, 1] }
        ).finished.then(cleanup)
    }

    onBeforeUnmount(cleanup)
    return { trigger }
}

/**
 * Session transition: horizontal cross-fade for session switches.
 * Fades out the old content (8px left) while fading in the new (8px right).
 */
export function useSessionTransition(options?: {
    duration?: number
}) {
    const reduced = useReducedMotion()
    const duration = (options?.duration ?? 180) / 1000

    const transition = async (
        container: HTMLElement,
        updateContent: () => void | Promise<void>
    ) => {
        if (reduced.value) {
            await updateContent()
            return
        }

        // Fade out old content (8px left)
        await animate(
            container,
            { opacity: [1, 0], x: [0, -8] },
            { duration: duration / 2, ease: [0.65, 0, 0.35, 1] }
        ).finished

        await updateContent()

        // Fade in new content (8px right)
        await animate(
            container,
            { opacity: [0, 1], x: [8, 0] },
            { duration: duration / 2, ease: [0.16, 1, 0.3, 1] }
        ).finished
    }

    return { transition }
}

/**
 * Session enter: the enter half of the session cross-fade, for reactive flows
 * where the leave half is the skeleton/empty swap rather than a manual update.
 * Slides the message column in from 8px right when history finishes loading.
 */
export function useSessionEnter(options?: {
    duration?: number
}) {
    const reduced = useReducedMotion()
    const duration = (options?.duration ?? 180) / 1000

    const enter = (el: HTMLElement | null) => {
        if (reduced.value || !el) return
        animate(
            el,
            { opacity: [0, 1], x: [8, 0] },
            { duration, ease: [0.16, 1, 0.3, 1] }
        )
    }

    return { enter }
}

/**
 * Stagger rise: first-entry animation for lists.
 * Each element rises 12px with a stagger delay. Call after mount.
 */
export function useStaggerRise(options?: {
    stagger?: number
    duration?: number
    displacement?: number
}) {
    const reduced = useReducedMotion()
    const stagger = (options?.stagger ?? 60) / 1000
    const duration = (options?.duration ?? 260) / 1000
    const displacement = options?.displacement ?? 12

    const rise = (elements: HTMLElement[] | NodeListOf<HTMLElement>) => {
        if (reduced.value) return
        Array.from(elements).forEach((el, i) => {
            animate(
                el,
                { opacity: [0, 1], y: [displacement, 0] },
                { duration, delay: i * stagger, ease: [0.16, 1, 0.3, 1] }
            )
        })
    }

    return { rise }
}
