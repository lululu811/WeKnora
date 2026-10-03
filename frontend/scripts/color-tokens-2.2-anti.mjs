#!/usr/bin/env node
// 阶段 2.2 补丁：`#fff` 作为「文字/图标描边」时不能用 --td-bg-color-container
// （暗色模式下该令牌翻深，珊瑚实底上的图标会变成深色）。实底上的文字/填充
// 一律 --td-text-color-anti（始终高对比），背景声明保持 bg-color-container。
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

function* walk(dir) {
  for (const n of readdirSync(dir)) {
    if (n === 'node_modules') continue
    const f = join(dir, n)
    if (statSync(f).isDirectory()) yield* walk(f)
    else if (/\.(vue|less)$/.test(n)) yield f
  }
}

const NUL = '\u0000'
let files = 0
let hits = 0
for (const file of walk(SRC)) {
  const rel = relative(SRC, file)
  if (SKIP_DIRS.some((d) => rel.startsWith(d)) || SKIP_FILES.includes(rel)) continue
  const src = readFileSync(file, 'utf8')
  // 先把 background 声明挡开，避免 `color:` 的负向匹配误伤 `background-color:`。
  const masked = src.replace(
    /(^|[\s{;])((?:background|background-color)\s*:\s*var\(--td-bg-color-container\))/g,
    (_m, p1, p2) => p1 + NUL + p2,
  )
  let n = 0
  const out = masked.replace(
    /^([\s{;]*)(-|webkit-)?(color|fill)\s*:\s*var\(--td-bg-color-container\)/gm,
    (m, pre, pfx, prop) => {
      n++
      return pre + (pfx || '') + prop + ': var(--td-text-color-anti)'
    },
  )
  if (n) {
    writeFileSync(file, out.split(NUL).join(''))
    files++
    hits += n
  }
}
console.log('color/fill -> text-color-anti:', hits, 'in', files, 'files')
