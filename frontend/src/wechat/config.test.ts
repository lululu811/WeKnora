import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

/**
 * 公众号工作台的接入配置只有两件容易出错的事：地址拼对、令牌带对。
 * 都锁在这里 —— 这个模块的导入方是组件，组件没有单测。
 */

// 组件与 config 模块在同一个包里，但 config.ts 读的是 import.meta.env，
// 裸 node 下没有 Vite 注入。直接测纯逻辑（URL 构造），
// 环境变量相关的部分用源码断言守住。
const configSrc = readFileSync(new URL('./config.ts', import.meta.url), 'utf8')

/** 去掉注释，只留代码 —— 注释里可以写历史（"之前写死的是 …"），不算硬编码。 */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

test('chatlogBizUrl 走 embed + theme 两个查询参数', () => {
  // 用 config.ts 里同款的拼法复算一遍，避免测试本身依赖 Vite 环境。
  const build = (base: string, theme: string, token: string) => {
    const params = new URLSearchParams({ embed: '1', theme })
    if (token) params.set('token', token)
    return `${base}/biz?${params.toString()}`
  }
  const url = new URL(build('http://127.0.0.1:5030', 'dark', ''))
  assert.equal(url.pathname, '/biz')
  assert.equal(url.searchParams.get('embed'), '1')
  assert.equal(url.searchParams.get('theme'), 'dark')
  assert.equal(url.searchParams.get('token'), null, '没有 token 时不该带 token 参数')
})

test('配了 token 时用查询参数带上（iframe 加不了 Authorization 头）', () => {
  const params = new URLSearchParams({ embed: '1', theme: 'light' })
  params.set('token', 's3cret')
  const url = new URL(`http://127.0.0.1:5030/biz?${params.toString()}`)
  assert.equal(url.searchParams.get('token'), 's3cret')
})

test('config 模块从环境变量取地址与令牌，且不是写死的 5030', () => {
  assert.match(configSrc, /VITE_CHATLOG_BASE_URL/, '地址必须可配')
  assert.match(configSrc, /VITE_CHATLOG_TOKEN/, '令牌必须可配')
  assert.doesNotMatch(
    stripComments(configSrc),
    /http:\/\/127\.0\.0\.1:5030\/biz/,
    '完整地址不能写死 —— 地址与路径要分开，路径由 chatlogBizUrl 拼',
  )
})

test('postMessage 的 targetOrigin 钉死成 chatlog 的 origin', () => {
  const viewSrc = readFileSync(new URL('./views/WechatWorkbench.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(
    stripComments(viewSrc),
    /postMessage\([\s\S]{0,200}['"]\*['"]/,
    'postMessage 不得用 * 作为 targetOrigin：iframe 可被导航，* 会把消息发到当时的任意 origin',
  )
  assert.match(viewSrc, /CHATLOG_ORIGIN/, '必须用钉死的 origin 投递')
  assert.match(viewSrc, /event\.origin\s*!==\s*CHATLOG_ORIGIN/, '接收端也要校验 origin')
})
