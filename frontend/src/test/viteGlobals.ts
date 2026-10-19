/**
 * 测试进程的运行环境替身。
 *
 * `npm test` 走的是裸 node（tsx --test），不是 Vite 构建产物，所以三样东西拿不到：
 *
 *  1. Vite `define` 注入的构建期常量（vite.config.ts 的 define 段）：
 *     __APP_NAME__ / __FRONTEND_VERSION__ / __FRONTEND_COMMIT__。
 *     任何间接 import 到 src/config/appIdentity.ts 的用例都会在模块求值时抛
 *     `ReferenceError: __APP_NAME__ is not defined`。
 *
 *  2. 浏览器 API：localStorage / window / document。
 *     src/i18n/index.ts 在模块求值期读 localStorage 与 window.__RUNTIME_CONFIG__；
 *     src/composables/useModalShell.ts 在运行时读 document.querySelector。
 *     node 22 的 localStorage 藏在 --experimental-webstorage 后面，且开启后还必须
 *     给 --localstorage-file，否则一访问就抛「invalid localStorage location」——
 *     测试不该依赖这两个运行期开关，所以在这里补最小替身。
 *
 * 这些替身只提供「不抛异常且返回空结果」的最小行为：真正需要 DOM 语义
 * （渲染、事件派发、焦点）的用例仍应走 jsdom 一类的真实环境。
 *
 * 通过 `npm test` 的 --import 钩子在测试文件加载前执行。
 */
const g = globalThis as unknown as Record<string, unknown>

g.__APP_NAME__ ??= process.env.VITE_APP_NAME ?? 'WeKnora'
g.__FRONTEND_VERSION__ ??= process.env.VITE_FRONTEND_VERSION ?? '0.0.0-test'
g.__FRONTEND_COMMIT__ ??= process.env.VITE_FRONTEND_COMMIT ?? 'test'

if (typeof g.localStorage === 'undefined') {
  const store = new Map<string, string>()
  g.localStorage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size
    },
  }
}

if (typeof g.window === 'undefined') {
  const listeners = new Map<string, Set<(e: unknown) => void>>()
  g.window = {
    __RUNTIME_CONFIG__: undefined,
    navigator: { language: 'zh-CN', languages: ['zh-CN', 'en'] },
    location: { href: 'http://localhost/', origin: 'http://localhost', pathname: '/' },
    addEventListener(type: string, fn: (e: unknown) => void) {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type)!.add(fn)
    },
    removeEventListener(type: string, fn: (e: unknown) => void) {
      listeners.get(type)?.delete(fn)
    },
    dispatchEvent(event: { type: string }) {
      listeners.get(event.type)?.forEach((fn) => fn(event))
      return true
    },
    setInterval,
    clearInterval,
    setTimeout,
    clearTimeout,
  }
}

if (typeof g.document === 'undefined') {
  g.document = {
    querySelector: () => null,
    querySelectorAll: () => [] as unknown[],
    getElementById: () => null,
    createElement: () => ({
      style: {},
      setAttribute() {},
      appendChild() {},
      remove() {},
    }),
    body: { appendChild() {}, removeChild() {} },
    addEventListener() {},
    removeEventListener() {},
  }
}
