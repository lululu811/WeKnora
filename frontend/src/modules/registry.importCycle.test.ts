import assert from 'node:assert/strict'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

/**
 * Regression guard for a silently-broken finance route.
 *
 * `src/router/index.ts` mounts externally-registered module routes from a
 * top-level `for (const mod of getRegisteredModules())` loop, i.e. it takes a
 * one-shot snapshot while the module body runs. That is only correct if
 * `src/finance/index.ts` (whose side effect calls `registerModule()`) is fully
 * evaluated *before* the router module body — which is what the bare
 * `import "@/finance"` at the top of `main.ts` is meant to guarantee.
 *
 * ES module semantics evaluate a module's dependencies before its own body, so
 * a *static* import chain reaching `@/router` from anywhere inside the finance
 * barrel inverts the order: `router/index.ts` runs its snapshot against an
 * empty registry and drops every `registerModule()` call that has not happened
 * yet. Nothing throws — `vite build` succeeds and the app boots — but
 * `/platform/watchlist` never matches, its lazy chunk is never requested, and
 * the page renders blank.
 *
 * That is exactly what happened via
 * finance/index.ts → components/MentionedStocksBar.vue → composables/useAgentWorkspace.ts
 * → `import router from '@/router'`.
 */

const SRC_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const EXTENSIONS = ['.ts', '.vue', '.js', '.mjs']
const IMPORT_RE =
  /(?:^|\n)\s*(?:import|export)[^;]*?from\s+['"]([^'"]+)['"]|(?:^|\n)\s*import\s+['"]([^'"]+)['"]/g

function resolveSpecifier(spec: string, fromFile: string): string | null {
  let base: string
  if (spec.startsWith('@/')) base = join(SRC_ROOT, spec.slice(2))
  else if (spec.startsWith('.')) base = resolve(dirname(fromFile), spec)
  else return null // bare package specifier, not part of the app graph

  for (const ext of EXTENSIONS) {
    if (existsSync(base + ext)) return base + ext
  }
  if (existsSync(base) && statSync(base).isFile()) return base
  for (const ext of EXTENSIONS) {
    const indexed = join(base, 'index' + ext)
    if (existsSync(indexed)) return indexed
  }
  return null
}

/** Every file reachable from `entry` through static import/re-export edges. */
function collectReachable(entry: string): { files: Set<string>; hits: string[] } {
  const hits: string[] = []
  const seen = new Set<string>()
  const stack = [entry]

  while (stack.length > 0) {
    const file = stack.pop()!
    if (seen.has(file)) continue
    seen.add(file)

    let source: string
    try {
      source = readFileSync(file, 'utf8')
    } catch {
      continue
    }

    IMPORT_RE.lastIndex = 0
    let match: RegExpExecArray | null
    while ((match = IMPORT_RE.exec(source)) !== null) {
      const spec = match[1] ?? match[2]
      if (!spec) continue
      if (spec === '@/router' || spec === '@/router/index') {
        hits.push(`${file.slice(SRC_ROOT.length + 1)} -> ${spec}`)
        continue
      }
      const next = resolveSpecifier(spec, file)
      if (next && !seen.has(next)) stack.push(next)
    }
  }

  return { files: seen, hits }
}

test('the finance barrel cannot statically reach the app router', () => {
  const entry = join(SRC_ROOT, 'finance/index.ts')
  const { hits } = collectReachable(entry)

  assert.deepEqual(
    hits,
    [],
    'finance/index.ts reaches @/router through a static import, so router/index.ts ' +
      'snapshots an empty module registry and every registerModule() call is lost ' +
      '(symptom: /platform/watchlist renders blank). Break the cycle — use a ' +
      'dynamic import() at the call site instead of a top-level import.',
  )
})

test('the finance barrel still reaches its own route component', () => {
  // Guards the fix above against over-pruning: the barrel must keep reaching
  // the module it registers, otherwise the guard would pass vacuously.
  const { files } = collectReachable(join(SRC_ROOT, 'finance/index.ts'))
  const reached = [...files].map((f) => f.slice(SRC_ROOT.length + 1))
  assert.ok(
    reached.includes('finance/proxyRoutes.ts'),
    'expected the barrel to still reach finance/proxyRoutes.ts',
  )
})
