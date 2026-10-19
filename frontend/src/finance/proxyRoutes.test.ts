import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  FINANCE_PROXY_ROUTES,
  FINANCE_PROXY_ROUTES_REGEX,
} from './proxyRoutes'

/**
 * 三份清单必须一致：数组 → 文档常量 → nginx.conf 实际配置。
 *
 * 这份清单现在是三处各写一遍的（`FINANCE_PROXY_ROUTES` 数组喂给
 * vite.config.ts；`FINANCE_PROXY_ROUTES_REGEX` 只是文档常量；
 * `frontend/nginx.conf` 里的 location 正则是生产真正生效的那份）。
 * nginx.conf 是静态配置，没法 import JS，所以只能靠约定 + 这条测试兜住。
 *
 * 漏同步一条的表现不是报错，是**静默少一块数据**：请求落到通用 /api/ 规则，
 * 被 Go 后端的 API key 闸门拦成 401，而 python-service 根本没收到请求；
 * 前端又把失败吞成空数组，组件按空态渲染。2026-10-19 这次就漏了 13 条
 * （commodities / fund-holdings / ladder-detail / … / squeeze-status）。
 */

const nginxConf = readFileSync(new URL('../../nginx.conf', import.meta.url), 'utf8')

/** 从 nginx.conf 里抠出 python-service 那条 location 的正则。 */
function extractNginxFinancePattern(conf: string): string {
  // 末尾是 `... {`，所以用「非贪婪到空白+{」来收尾：
  // 贪婪匹配会把结尾的 `?$` 一起吞进捕获组，导致正则少掉锚点而永不命中。
  const match = conf.match(/location\s+~\s+(\^\/api\/\(kline\|[^\s]+?)\s+\{/m)
  assert.ok(match, 'nginx.conf 里找不到 python-service 的 location 正则')
  return match[1]
}

const nginxPattern = extractNginxFinancePattern(nginxConf)

test('nginx.conf 的反代白名单覆盖 FINANCE_PROXY_ROUTES 的每一条', () => {
  const re = new RegExp(nginxPattern)
  const missing = FINANCE_PROXY_ROUTES.filter((route) => !re.test(route))
  assert.deepEqual(
    missing,
    [],
    `这些路径在 proxyRoutes.ts 里登记了，但 nginx.conf 的白名单没放行：\n${missing.join('\n')}\n`
      + '生产 nginx 下它们会落到通用 /api/ 规则，被 Go 后端鉴权拦成 401，'
      + '前端表现为「静默少一块数据」而不是报错。补 nginx.conf 那条 location 正则。',
  )
})

test('FINANCE_PROXY_ROUTES_REGEX 与 nginx.conf 实际生效的正则完全一致', () => {
  assert.equal(
    nginxPattern,
    FINANCE_PROXY_ROUTES_REGEX,
    '文档常量与 nginx.conf 不一致了：改了一处忘了改另一处。',
  )
})

test('FINANCE_PROXY_ROUTES_REGEX 自身覆盖全部路由', () => {
  const re = new RegExp(FINANCE_PROXY_ROUTES_REGEX)
  const missing = FINANCE_PROXY_ROUTES.filter((route) => !re.test(route))
  assert.deepEqual(missing, [], `文档常量漏了：\n${missing.join('\n')}`)
})

test('反代白名单是显式枚举，没有把 /api/market 整段放开', () => {
  // 写成 /api/market/.* 会把将来任何挂在 /api/market 下的新端点
  // （包括可能出现的写端点）一并直通到 python-service。
  assert.ok(
    !/market\/\.\*|market\/\(\.\*\)/.test(nginxPattern),
    'nginx 白名单把 /api/market 整段放开了，必须显式枚举。',
  )
})
