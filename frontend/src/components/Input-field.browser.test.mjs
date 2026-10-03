import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const inputField = readFileSync(new URL('./Input-field.vue', import.meta.url), 'utf8')
// 输入区拆成子组件后，按钮标记与工具栏样式各自住进了新文件；
// 这里跟着代码一起搬家，守卫的意图保持不变。
const inputToolbar = readFileSync(new URL('./input/InputToolbar.vue', import.meta.url), 'utf8')
const toolbarCss = readFileSync(new URL('./input/css/input-toolbar.less', import.meta.url), 'utf8')
const chatPage = readFileSync(new URL('../views/chat/index.vue', import.meta.url), 'utf8')

test('composer browser button is disabled and opens settings when the extension is offline', () => {
  const button = inputToolbar.slice(
    inputToolbar.indexOf('class="control-btn browser-source-btn"'),
    inputToolbar.indexOf('<BrowserIcon class="control-icon" />'),
  )
  assert.match(button, /disabled: browserConnection\.knownOffline/)
  assert.match(button, /active: isLocalBrowserEnabled && browserConnection\.online/)
  assert.match(button, /:aria-disabled="browserConnection\.knownOffline"/)
  // 状态仍然由 settings store 驱动
  assert.match(inputField, /:is-local-browser-enabled="settingsStore\.isLocalBrowserEnabled"/)
  assert.match(inputField, /if \(browserConnection\.knownOffline\) \{\s*openBrowserConnectionSettings\(\)/)
  assert.match(inputField, /router\.push\(toolboxLocation\('browserconnection'\)\)/)
  assert.match(inputField, /browserConnection\.watchStatus\(\)/)
  // 断线提示的取词随 tooltip 一起搬到了工具栏
  assert.match(inputToolbar, /\$t\('localBrowser\.reconnectHint'\)|\$t\(browserSourceUnavailableHint\)/)
})

test('mention button matches icon controls and the stream artifact count badge', () => {
  const css = toolbarCss.slice(toolbarCss.indexOf('.kb-btn {'), toolbarCss.indexOf('.kb-btn-text'))
  assert.match(css, /width: 28px/)
  assert.match(css, /&:hover:not\(\.disabled\):not\(\.active\)/)
  assert.doesNotMatch(css, /box-shadow: inset/)
  const count = toolbarCss.slice(toolbarCss.indexOf('.kb-count {'), toolbarCss.indexOf('.kb-btn-text'))
  assert.match(count, /top: -2px/)
  assert.match(count, /right: -2px/)
  assert.match(count, /min-width: 14px/)
  assert.match(count, /height: 14px/)
  assert.match(count, /font-size: (?:10px|var\(--app-text-2xs\))/)
  assert.match(count, /border-radius: 7px/)
  assert.match(count, /font-variant-numeric: tabular-nums/)
  assert.doesNotMatch(count, /border: 2px solid/)
})

test('a selected browser source keeps brand color on hover', () => {
  const css = toolbarCss.slice(toolbarCss.indexOf('.browser-source-btn {'))
  assert.match(css, /&:hover:not\(\.disabled\):not\(\.active\)/)
  assert.match(css, /&\.active \{[\s\S]*&:hover \{[\s\S]*color: var\(--td-brand-color\)/)
  assert.doesNotMatch(css.slice(0, css.indexOf('&.active')), /&:hover:not\(\.disabled\) \{/)
})

test('a chat turn does not request the local browser while the extension is known offline', () => {
  assert.match(
    chatPage,
    /local_browser_enabled:\s*!props\.embeddedMode && agentEnabled && useSettingsStoreInstance\.isLocalBrowserEnabled && !useBrowserConnectionStore\(\)\.knownOffline/,
  )
})
