import { fileURLToPath, URL } from 'node:url'
import { resolve, dirname } from 'node:path'
import { existsSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'

const __dirname = dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)

const pkg = require('./package.json') as { version?: string }
const FRONTEND_VERSION = pkg.version ?? 'unknown'

function resolveFrontendCommit(): string {
  const fromEnv = process.env.VITE_FRONTEND_COMMIT || process.env.GITHUB_SHA
  if (fromEnv) {
    return fromEnv.slice(0, 7)
  }
  try {
    return execSync('git rev-parse --short HEAD', { stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim()
  } catch {
    return 'unknown'
  }
}

const FRONTEND_COMMIT = resolveFrontendCommit()

/** Dev parity with nginx: serve embed.html for /embed/:channelId (not the main SPA). */
function embedHtmlDevFallback(): Plugin {
  return {
    name: 'embed-html-dev-fallback',
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const raw = req.url ?? ''
        const qIdx = raw.indexOf('?')
        const path = qIdx >= 0 ? raw.slice(0, qIdx) : raw
        const qs = qIdx >= 0 ? raw.slice(qIdx) : ''
        if (path.startsWith('/embed/') && path !== '/embed.html' && !path.includes('.')) {
          req.url = `/embed.html${qs}`
        }
        next()
      })
    },
  }
}
const DEV_PROXY_TARGET =
  process.env.VITE_DEV_PROXY_TARGET ||
  process.env.FRONTEND_BACKEND_URL ||
  'http://localhost:8080'

function resolveVueOfficePptxEntry(): string {
  try {
    const pkgDir = dirname(require.resolve('@vue-office/pptx/package.json'))
    const candidates = [
      resolve(pkgDir, 'lib/v3/index.js'),
      resolve(pkgDir, 'lib/index.js'),
      resolve(pkgDir, 'lib/v3/vue-office-pptx.mjs'),
    ]
    const matched = candidates.find((candidate) => existsSync(candidate))
    return matched ?? '@vue-office/pptx'
  } catch {
    return '@vue-office/pptx'
  }
}

// index.html / embed.html 里的 %VITE_APP_NAME% 由 Vite 的 HTML 环境变量替换处理。
// 这里兜底，保证未设置 VITE_APP_NAME 时产物里不会残留字面量占位符；
// 组件侧读同一个值的入口是 src/config/appIdentity.ts。
process.env.VITE_APP_NAME ||= 'WeKnora'

export default defineConfig({
  define: {
    __FRONTEND_VERSION__: JSON.stringify(FRONTEND_VERSION),
    __FRONTEND_COMMIT__: JSON.stringify(FRONTEND_COMMIT),
    // 产品名。走 define 而不是 import.meta.env：后者只在 .env 文件里定义的值才可见，
    // 而 Docker 构建是把 VITE_APP_NAME 作为 build arg 传进进程环境的。
    // index.html / embed.html 的 %VITE_APP_NAME% 由 Vite 的 HTML 替换处理，
    // 两者共用同一个 process.env 值，见文件顶部的兜底赋值。
    __APP_NAME__: JSON.stringify(process.env.VITE_APP_NAME),
  },
  build: {
    modulePreload: {
      resolveDependencies(_filename, deps, { hostId }) {
        // Embed iframe bootstraps with token exchange only; defer heavy chat chunks.
        if (hostId?.includes('embed')) {
          return deps.filter((dep) => !(
            dep.includes('vendor-mermaid')
            || dep.includes('vendor-highlight')
            || dep.includes('vendor-markdown')
            || dep.includes('vendor-tdesign')
            || dep.includes('botmsg')
            || dep.includes('usermsg')
            || dep.includes('EmbedBotMessage')
            || dep.includes('EmbedUserMessage')
            || dep.includes('AgentStreamDisplay')
            || dep.includes('EmbedChatCore')
            || dep.includes('vendor-markdown')
            || dep.includes('fonts-')
          ))
        }
        return deps
      },
    },
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        embed: resolve(__dirname, 'embed.html'),
      },
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('mermaid') || id.includes('/dagre') || id.includes('cytoscape')) {
            return 'vendor-mermaid'
          }
          if (id.includes('marked') || id.includes('katex')) {
            return 'vendor-markdown'
          }
          if (id.includes('highlight.js')) {
            return 'vendor-highlight'
          }
        },
      },
    },
  },
  plugins: [
    vue(),
    vueJsx(),
    embedHtmlDevFallback(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@vue-office/pptx': resolveVueOfficePptxEntry(),
    },
  },
  server: {
    port: 5173,
    host: true,
    // 代理配置，用于开发环境
    proxy: {
      '/mcp/': {
        target: DEV_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
        // Streamable HTTP may keep an SSE response open for long-running tools.
        timeout: 3_600_000,
        proxyTimeout: 3_600_000,
      },
      '/api/kline': {
        target: process.env.VITE_PY_SERVICE_TARGET || 'http://localhost:50052',
        changeOrigin: true,
      },
      '/api/annotate': {
        target: process.env.VITE_PY_SERVICE_TARGET || 'http://localhost:50052',
        changeOrigin: true,
      },
      // 形态识别（几何形态 + 波浪）也走 python-service。
      // 不加这条会落到下面那个泛化的 '/api' 规则上、被转到 Go 应用，而那条链路
      // 需要鉴权 —— 表现为图表静默拿不到形态，没有任何报错。
      '/api/chart-pattern': {
        target: process.env.VITE_PY_SERVICE_TARGET || 'http://localhost:50052',
        changeOrigin: true,
      },
      '/api/indicators': {
        target: process.env.VITE_PY_SERVICE_TARGET || 'http://localhost:50052',
        changeOrigin: true,
      },
      '/api/symbols': {
        target: process.env.VITE_PY_SERVICE_TARGET || 'http://localhost:50052',
        changeOrigin: true,
      },
      // 自选页的批量行情快照，与 /api/kline 同一类：只读行情，浏览器直连
      // python-service（生产环境对应 nginx.conf 里那条直通正则的白名单）。
      '/api/quotes': {
        target: process.env.VITE_PY_SERVICE_TARGET || 'http://localhost:50052',
        changeOrigin: true,
      },
      '/api': {
        target: DEV_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
        // 沙箱终端等 WebSocket 升级请求也走 /api，必须开启 WS 转发，
        // 否则浏览器侧握手失败、前端表现为"一直正在连接"。
        ws: true,
        // Cube fork snapshots pause a live MicroVM; 30s axios/proxy defaults
        // abort the POST and the backend then 500s on a canceled persist.
        timeout: 180_000,
        proxyTimeout: 180_000,
      },
      '/files': {
        target: DEV_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
      }
    }
  },
  // `vite preview` 用生产构建产物(dist)本地起服务，是最接近 release 镜像的环境：
  // 同样的压缩 / 拆包 / CSS 加载顺序，可提前暴露只在生产构建出现的问题
  // （如主题变量被打包顺序覆盖）。用法：npm run build && npm run preview
  preview: {
    port: 4173,
    host: true,
    proxy: {
      '/mcp/': {
        target: DEV_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
        timeout: 3_600_000,
        proxyTimeout: 3_600_000,
      },
      '/api': {
        target: DEV_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
        ws: true,
        timeout: 180_000,
        proxyTimeout: 180_000,
      },
      '/files': {
        target: DEV_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
      }
    }
  }
})
