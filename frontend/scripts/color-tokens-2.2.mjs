#!/usr/bin/env node
// 阶段 2.2：把 .vue <style> / .less 中的硬编码 hex 收敛为 theme.css 令牌。
// 只处理样式块，script 里的 canvas/图表色天然不进入作用域。
import { readdirSync, readFileSync, writeFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const SRC = new URL('../src/', import.meta.url).pathname

const SKIP_DIRS = ['views/design-lab/', 'finance/', 'assets/theme/']
const SKIP_FILES = [
  'components/AgentAvatar.vue',
  'components/SpaceAvatar.vue',
  'components/css/chat-hljs.less',
  'components/css/chat-hljs-dark.less',
]

// 灰阶取 TDesign 标准 14 档（#f3f3f3 → #181818），3 位缩写归一到 6 位。
// 6 位色值 → 令牌。键一律带引号，避免被当成数字字面量丢掉前导 0。
const RAW = {
  // 灰阶（#f3f3f3 → #181818）
  'f3f3f3': 'td-gray-color-1', 'f2f2f2': 'td-gray-color-1',
  'e6e6e6': 'td-gray-color-2', 'eaeaea': 'td-gray-color-2', 'ebebeb': 'td-border-level-2-color',
  'd9d9d9': 'td-gray-color-3', 'dcdcdc': 'td-gray-color-3', 'e7e7e7': 'td-border-level-2-color',
  'cccccc': 'td-gray-color-4', 'cfcfcf': 'td-gray-color-4',
  'bbbbbb': 'td-gray-color-5', 'aaaaaa': 'td-gray-color-6',
  '888888': 'td-gray-color-8', '898989': 'td-gray-color-8',
  '777777': 'td-gray-color-9', '7a7a7a': 'td-gray-color-9',
  '666666': 'td-text-color-secondary',
  '555555': 'td-gray-color-11', '4d4d4d': 'td-gray-color-11', '464646': 'td-gray-color-11',
  '333333': 'td-text-color-primary',
  '1f1f1f': 'td-gray-color-13', '181818': 'td-gray-color-14',
  // 文字占位
  '999999': 'td-text-color-placeholder', 'a6a6a6': 'td-text-color-placeholder',
  // 背景
  'ffffff': 'td-bg-color-container',
  'fafafa': 'td-bg-color-page', 'f8f8f8': 'td-bg-color-page', 'f7f7f7': 'td-bg-color-page',
  'f5f5f5': 'td-bg-color-page', 'f5f7fa': 'td-bg-color-page', 'f8fafc': 'td-bg-color-page',
  'f0f0f0': 'td-bg-color-secondarycontainer', 'f0f2f5': 'td-bg-color-secondarycontainer',
  // 边框
  'e5e5e5': 'td-border-level-1-color', 'e0e0e0': 'td-border-level-1-color',
  'd4d4d4': 'td-border-level-1-color', 'c8c8c8': 'td-border-level-1-color',
  'eef2f7': 'td-border-level-2-color', 'e7ebf0': 'td-border-level-1-color',
  'eef1f5': 'td-border-level-1-color', 'eceff3': 'td-border-level-1-color',
  'dfe3e8': 'td-border-level-1-color', 'eff1f3': 'td-border-level-2-color',
  'c9d1d9': 'td-border-level-1-color',
}
const MAP = new Map(
  Object.entries(RAW).map(([hex, token]) => [hex, `var(--${token})`]),
)
// 3 位缩写
for (const m of [...MAP.keys()]) {
  if (m.length === 6 && new Set(m[0] + m[2] + m[4]).size === 1) MAP.set(m[0] + m[1] + m[2], MAP.get(m))
}

const HEX = /#([0-9a-fA-F]{3,8})\b/g
const HEX_ONLY = /^([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/
const normalize = (h) => h.length === 3 ? h[0] + h[0] + h[1] + h[1] + h[2] + h[2] : h

function* walk(dir) {
  for (const n of readdirSync(dir)) {
    if (n === 'node_modules') continue
    const f = join(dir, n)
    if (statSync(f).isDirectory()) yield* walk(f)
    else if (/\.(vue|less)$/.test(n)) yield f
  }
}

/** 只在 <style> 块内替换；返回 [新文本, 替换数, 遗留数]。 */
function convertStyle(text) {
  let hits = 0
  const left = new Set()
  const out = text.replace(/(<style[^>]*>)([\s\S]*?)(<\/style>)/g, (_m, open, body, close) => {
    const nBody = body.replace(HEX, (whole, h) => {
      if (!HEX_ONLY.test(h)) return whole
      const key = normalize(h).toLowerCase()
      const var_ = MAP.get(key)
      if (!var_) { left.add(key); return whole }
      hits++
      return var_
    })
    if (!nBody.includes('TODO 2.2') && left.size) {
      return open + nBody.replace(/\s*$/, '\n') +
        `\n/* TODO 2.2: ${left.size} 处色值未收敛（图表/状态/语义不明，保留原值）：${[...left].join(', ')} */\n` + close
    }
    return open + nBody + close
  })
  return [out, hits, left.size]
}

const report = []
for (const file of walk(SRC)) {
  const rel = relative(SRC, file)
  if (SKIP_DIRS.some((d) => rel.startsWith(d)) || SKIP_FILES.includes(rel)) continue
  const src = readFileSync(file, 'utf8')
  const [out, hits] = rel.endsWith('.less')
    ? (() => { let h = 0; const s = src.replace(HEX, (w, x) => {
        if (!HEX_ONLY.test(x)) return w
        const v = MAP.get(normalize(x).toLowerCase()); if (!v) return w; h++; return v })
      return [s, h, 0] })()
    : convertStyle(src)
  if (hits) { writeFileSync(file, out); report.push(`${hits}\t${rel}`) }
}
report.sort((a, b) => Number(b.split('\t')[0]) - Number(a.split('\t')[0]))
console.log(report.join('\n'))
console.log('---')
console.log('files:', report.length, 'replacements:', report.reduce((s, r) => s + Number(r.split('\t')[0]), 0))
