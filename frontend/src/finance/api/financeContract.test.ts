import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import test from 'node:test'

/**
 * 金融接口的线格式契约 —— 前端侧。
 *
 * 契约在 `python-service/contracts/finance_api.json`（放在后端那边，因为
 * 它描述的是后端发出来的线格式；后端是这份格式的主人）。
 *
 * 这个用例盯的是**前端以为后端会发什么**。两边各写一份时，字段改名或删除
 * 不会有人发现 —— 前端拿到空值，界面照常渲染成「今天没数据」，后端日志里
 * 一切正常。不是崩溃，是静默失真：用户看到的是错的答案，而系统看起来是好的。
 *
 * 两个方向都要看：
 *   · 前端多要一个字段 = 后端不会发它 —— **危险方向**，界面会静默少一块。
 *   · 后端多发一个字段 = 前端没声明 —— 无害，但要看得见，记在契约的
 *     backend_only 里。
 *
 * 接口类型不一定住在 api 目录（/api/market/auction 的类型在组件里），所以
 * 扫的是整个 src/finance 目录。
 */

// 路径都从 process.cwd() 推（测试在 frontend/ 下跑），与 core-chart.test.ts 同款，
// 不靠 import.meta.url 层层数相对段 —— 那种写法数错一层就是 ENOENT。
const FRONTEND_ROOT = process.cwd()
const REPO_ROOT = resolve(FRONTEND_ROOT, '..')
const CONTRACT_PATH = join(REPO_ROOT, 'python-service', 'contracts', 'finance_api.json')

interface ContractEntry {
  method: string
  ts_interface: string | null
  fields?: string[]
  backend_only?: string[]
  query?: Record<string, unknown>
  body?: Record<string, unknown>
}

interface Contract {
  version: number
  endpoints: Record<string, ContractEntry>
}

// JSON.parse 返回 any，Object.entries 在 any 上会把元素推成 unknown ——
// 显式声明契约的形状，用例里就不用到处断言。
const contract = JSON.parse(readFileSync(CONTRACT_PATH, 'utf8')) as Contract

function collectSourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) {
      collectSourceFiles(full, out)
    } else if ((name.endsWith('.ts') || name.endsWith('.vue')) && !name.endsWith('.test.ts')) {
      out.push(full)
    }
  }
  return out
}

/** 每个 interface 的顶层字段名。 */
function readInterfaces(): Map<string, string[]> {
  const out = new Map<string, string[]>()
  for (const file of collectSourceFiles(join(FRONTEND_ROOT, 'src/finance'))) {
    const src = readFileSync(file, 'utf8')
    const re = /^(?:export\s+)?interface\s+(\w+)\s*\{/gm
    let m: RegExpExecArray | null
    while ((m = re.exec(src)) !== null) {
      const name = m[1]
      let depth = 1
      let i = m.index + m[0].length
      while (i < src.length && depth > 0) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') depth--
        i++
      }
      const body = src.slice(m.index + m[0].length, i - 1)
      const fields: string[] = []
      for (const line of body.split('\n')) {
        const fm = /^\s{2,6}([A-Za-z_]\w*)\??\s*:/.exec(line)
        if (fm) fields.push(fm[1])
      }
      if (!out.has(name)) out.set(name, fields)
    }
  }
  return out
}

const interfaces = readInterfaces()

test('契约里的每个端点都能在前端找到对应的接口声明', () => {
  const missing: string[] = []
  for (const [path, entry] of Object.entries(contract.endpoints)) {
    const name = entry.ts_interface
    if (name && !interfaces.has(name)) missing.push(`${path} -> ${name}`)
  }
  assert.deepEqual(missing, [], `契约引用了前端没有的接口类型：\n${missing.join('\n')}`)
})

test('前端声明的字段与契约一致', () => {
  const problems: string[] = []
  for (const [path, entry] of Object.entries(contract.endpoints)) {
    const name = entry.ts_interface
    if (!name) continue
    const declared = new Set(interfaces.get(name) ?? [])
    const contractFields = new Set(entry.fields ?? [])
    const backendOnly = new Set(entry.backend_only ?? [])

    // 危险方向：前端要、后端不发。
    const missing = [...declared].filter(f => !contractFields.has(f))
    if (missing.length) {
      problems.push(`${path}（${name}）前端要用但契约里没有：${missing.join(', ')}`)
    }
    // 无害方向：后端发、前端没声明 —— 必须记进 backend_only 并写明原因。
    const extra = [...contractFields].filter(f => !declared.has(f) && !backendOnly.has(f))
    if (extra.length) {
      problems.push(`${path}（${name}）契约里有但前端没声明：${extra.join(', ')}（应记进 backend_only）`)
    }
  }
  assert.deepEqual(problems, [], `\n${problems.join('\n')}`)
})

test('接口声明本身没有空字段的接口（解析没跑偏）', () => {
  // 解析器要是正则写错了，会解析出空字段列表，契约就会「看起来一致」。
  const empties: string[] = []
  for (const [name, fields] of interfaces) {
    if (fields.length === 0) empties.push(name)
  }
  assert.deepEqual(empties, [], `这些接口解析出 0 个字段：${empties.join(', ')}`)
})
