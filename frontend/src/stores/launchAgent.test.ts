import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer, type ViteDevServer } from 'vite'
import { createPinia } from 'pinia'

/**
 * 「落点指定的 agent」在 store 层的行为。
 *
 * 这一层值得单测，因为 K 线面板的 HALO 报告按钮踩的坑**不在** UI 上：
 * 请求带不带工具，取决于 `isAgentStreamMode`，而它由 selectedAgentId +
 * isAgentEnabled 两个字段共同决定，`selectAgent()` 只对两个内置常量同步后者。
 * 于是「选中了 builtin-halo 但 agent 模式关着」会让请求走 knowledge-chat（RAG），
 * halo.analyze 连 tool schema 都进不去 —— 用户看到的就是「检索材料里没有年报数据」。
 */
let server: ViteDevServer
let useSettingsStore: typeof import('./settings').useSettingsStore
const savedStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
const items = new Map<string, string>()
before(async () => {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => items.set(key, value),
    removeItem: (key: string) => items.delete(key),
  } })
  server = await createServer({
    configFile: false,
    plugins: [{ name: 'offline-store-test', enforce: 'pre',
      resolveId(id) { if (id.endsWith('/utils/request')) return '\0offline-request' },
      load(id) { if (id === '\0offline-request') return 'const request = () => { throw new Error("Unexpected network request") }; export { request as get, request as post, request as put, request as del };' },
    }],
    optimizeDeps: { noDiscovery: true, entries: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
  })
  ;({ useSettingsStore } = await server.ssrLoadModule('/src/stores/settings.ts'))
})
after(async () => {
  await server?.close()
  if (savedStorage) Object.defineProperty(globalThis, 'localStorage', savedStorage)
  else Reflect.deleteProperty(globalThis, 'localStorage')
})

test('selectAgentForLaunch 让自定义 agent 真的走 agent 管线', () => {
  const store = useSettingsStore(createPinia())
  // 默认就是快速问答：RAG 管线，没有工具。
  assert.equal(store.selectedAgentId, 'builtin-quick-answer')
  assert.equal(store.isAgentStreamMode, false)

  store.selectAgentForLaunch('builtin-halo')

  assert.equal(store.selectedAgentId, 'builtin-halo')
  assert.equal(
    store.isAgentStreamMode,
    true,
    '选中了 HALO 但 isAgentEnabled 还是 false 时，请求会走 /knowledge-chat，工具进不了 schema',
  )
  // localStorage 里也必须是「已打开」：自定义 agent 不参与 reconcileBuiltinAgentMode
  // 的纠偏，刷新页面后会原样读回来。
  assert.equal(JSON.parse(items.get('WeKnora_settings')!).isAgentEnabled, true)
})

test('selectAgentForLaunch 幂等：已经选对时不清空用户的 KB / 文件选择', () => {
  const store = useSettingsStore(createPinia())
  store.selectAgentForLaunch('builtin-halo')
  store.selectKnowledgeBases(['kb-1'])
  store.addFile('file-1')

  store.selectAgentForLaunch('builtin-halo')

  assert.deepEqual(store.settings.selectedKnowledgeBases, ['kb-1'])
  assert.deepEqual(store.settings.selectedFiles, ['file-1'])
})

test('落点是快速问答时把 agent 模式关回去', () => {
  const store = useSettingsStore(createPinia())
  store.selectAgentForLaunch('builtin-halo')
  store.selectAgentForLaunch('builtin-quick-answer')
  assert.equal(store.isAgentStreamMode, false)
})

// 这条钉住的是**为什么**落点必须在 creatChat 里应用、而不是在跳转前 selectAgent()。
// 会话页离开时会 restoreDefaultsIfSnapshotted()，整个 settings 被换成进入会话前的
// 快照；跳转前写进去的 agent 会被原样丢掉，新会话于是按全局默认（快速问答）发出去。
test('会话页离开时的快照还原会吃掉跳转前写进去的 agent', () => {
  const store = useSettingsStore(createPinia())
  assert.equal(store.selectedAgentId, 'builtin-quick-answer')

  store.snapshotAsDefaultsIfNeeded() // 进入会话
  store.selectAgentForLaunch('builtin-halo') // 对话里点「让 agent 生成完整报告」
  store.restoreDefaultsIfSnapshotted() // onBeforeRouteLeave

  assert.equal(
    store.selectedAgentId,
    'builtin-quick-answer',
    '跳转前的选择确实会被丢掉 —— 所以落点要把 agent 写进 URL，由 creatChat 在守卫之后应用',
  )
})
