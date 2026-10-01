/**
 * hoverDebounce — 悬浮触发的防抖。
 *
 * 用在「鼠标停到正文里的日期上 → 右侧 K 线滚到该区间」这条链路上。
 *
 * 为什么必须防抖：滚图会改变右侧可见的区间。没有防抖时，把鼠标从正文移向
 * 滚动条的路上会**路过**好几个日期，每路过一个就滚一次，图会来回跳。
 * 防抖把语义从「鼠标碰到」改成「鼠标停下来」，只有后者才是「我要看这一段」。
 *
 * 抽成独立模块而不是写在组件里，是因为它是这条链路里唯一有状态、也最容易写错的
 * 部分（重复 schedule 要重置计时、cancel 后不能补触发），而组件本身在
 * node:test 下挂不起来。
 */
/** setTimeout 的句柄。显式命名而不是 `ReturnType<typeof setTimeout>`：后者把
 *  契约挂在实现细节上，换运行时（node / 浏览器）会跟着漂。 */
type TimerHandle = ReturnType<typeof setTimeout>;

export interface HoverDebounce<A extends unknown[]> {
  /** 重新计时。重复调用只保留最后一次。 */
  schedule(...args: A): void;
  /** 取消未触发的调用。已经触发过的不受影响。 */
  cancel(): void;
  /** 是否有待触发的调用（测试与调试用）。 */
  readonly pending: boolean;
}

export function createHoverDebounce<A extends unknown[]>(
  delayMs: number,
  fire: (...args: A) => void,
): HoverDebounce<A> {
  let timer: TimerHandle | null = null;

  const cancel = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  return {
    schedule(...args: A) {
      // 先清掉上一次：这是防抖的核心——每次新的 hover 都把窗口往后推，
      // 只有连续停留满 delayMs 才会真的触发。
      cancel();
      timer = setTimeout(() => {
        timer = null;
        fire(...args);
      }, delayMs);
    },
    cancel,
    get pending() {
      return timer !== null;
    },
  };
}
