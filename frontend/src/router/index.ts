import { createRouter, createWebHistory } from 'vue-router'
import { defineComponent } from 'vue'
import type { RouteLocationNormalized } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useDeploymentCapabilitiesStore } from '@/stores/deploymentCapabilities'
import { autoSetup, userInfoFromApi } from '@/api/auth'
import type { DeploymentCapabilityKey } from '@/config/deploymentCapabilities'
import { MessagePlugin } from 'tdesign-vue-next'
import i18n from '@/i18n'
import { normalizeSettingsSection } from '@/config/settingsRoute'
import { isToolboxSection, toolboxLocation } from '@/config/toolbox'
import { getRegisteredModules } from '@/modules/registry'

/** Lite /桌面 WebView 硬刷新时可能只打开 `/`，用 session 记住上次页面以便恢复 */
const LITE_LAST_PATH_KEY = 'weknora_lite_last_path'

// views/platform/index.vue always mounts the settings modal and opens it when
// the path is /platform/settings, so this route only has to own the URL.
// Rendering Settings.vue here would mount a second, independent copy.
const SettingsRouteOutlet = defineComponent({ name: 'SettingsRouteOutlet', render: () => null })

function isLiteEdition(authStore: ReturnType<typeof useAuthStore>) {
  return authStore.isLiteMode || localStorage.getItem('weknora_lite_mode') === 'true'
}

function isLiteSpaDefaultEntry(to: RouteLocationNormalized) {
  return (
    to.path === '/' ||
    to.path === '/platform' ||
    to.path === '/platform/knowledge-bases' ||
    to.name === 'knowledgeBaseList'
  )
}

function isSafeLiteRestoreTarget(path: string) {
  return path.startsWith('/platform/') && !path.startsWith('/platform/organizations')
}

function hasPendingOIDCCallback() {
  if (typeof window === 'undefined') return false
  const hash = window.location.hash || ''
  return hash.includes('oidc_result=') || hash.includes('oidc_error=')
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: "/",
      redirect: "/platform/knowledge-bases",
    },
    {
      path: "/login",
      name: "login",
      component: () => import("../views/auth/Login.vue"),
      meta: { requiresAuth: false, requiresInit: false }
    },
    // 大盘预览大屏：顶级全屏路由，render full-bleed（outside the platform
    // sidebar shell），与 design-lab 的全屏先例同一处理方式。它挂在 /platform
    // 之外而不是 /platform/dashboard，因为大屏自己占满视口，放进平台壳里会
    // 被侧边栏挤成"带边距的仪表盘"，一屏无滚动的前提就没了。
    // 返回入口在组件内（goBack 回工作台），不进 /platform 侧边栏。
    {
      path: "/dashboard",
      name: "marketDashboard",
      component: () => import("@/finance/views/MarketDashboard.vue"),
      meta: { requiresAuth: true, requiresInit: true }
    },
    // Embed chat is a separate entry (embed.html + embed-main.ts), not this SPA.
    {
      path: "/register",
      name: "registerByInvite",
      // Share-link landing page reuses the Login form: the same Vue
      // component renders both modes and detects ?token=xxx on mount
      // to switch into invite-register flow. Avoids a parallel page
      // that would duplicate the OIDC / language-switch / styling
      // surface for one extra field.
      component: () => import("../views/auth/Login.vue"),
      meta: { requiresAuth: false, requiresInit: false }
    },
    {
      path: "/onboarding/workspace",
      name: "workspaceOnboarding",
      component: () => import("../views/auth/WorkspaceOnboarding.vue"),
      meta: { requiresAuth: true, requiresInit: false, requiresTenant: false }
    },
    {
      path: "/join",
      name: "joinOrganization",
      // 重定向到组织列表页，并将 code 参数转换为 invite_code
      redirect: (to) => {
        const code = to.query.code as string
        return {
          path: '/platform/organizations',
          query: code ? { invite_code: code } : {}
        }
      },
      meta: { requiresInit: true, requiresAuth: true }
    },
    {
      path: "/knowledgeBase",
      name: "home",
      component: () => import("../views/knowledge/KnowledgeBase.vue"),
      meta: { requiresInit: true, requiresAuth: true }
    },
    {
      path: "/platform",
      name: "Platform",
      redirect: "/platform/knowledge-bases",
      component: () => import("../views/platform/index.vue"),
      meta: { requiresInit: true, requiresAuth: true },
      children: [
        {
          path: "tenant",
          redirect: "/platform/settings"
        },
        {
          path: "settings",
          name: "settings",
          component: SettingsRouteOutlet,
          meta: { requiresInit: true, requiresAuth: true }
        },
        {
          path: "home",
          name: "welcome",
          component: () => import("../views/home/Welcome.vue"),
          meta: { requiresInit: true, requiresAuth: true }
        },
        {
          path: "knowledge-bases",
          name: "knowledgeBaseList",
          component: () => import("../views/knowledge/KnowledgeBaseList.vue"),
          meta: { requiresInit: true, requiresAuth: true }
        },
        {
          path: "knowledge-bases/:kbId",
          name: "knowledgeBaseDetail",
          component: () => import("../views/knowledge/KnowledgeBase.vue"),
          meta: { requiresInit: true, requiresAuth: true }
        },
        {
          path: "knowledge-search",
          // 旧路径保留为重定向，打开全局命令面板（⌘K），带上可选的 q 参数
          redirect: (to) => {
            const q = to.query.q
            return {
              path: '/platform/knowledge-bases',
              query: typeof q === 'string' ? { cmdk: q } : { cmdk: '' },
            }
          },
        },
        {
          path: "artifacts",
          name: "artifactLibrary",
          component: () => import("../views/artifacts/ArtifactLibrary.vue"),
          meta: { requiresInit: true, requiresAuth: true, requiredCapability: 'settings.sandbox' }
        },
        {
          path: "toolbox/:section?",
          name: "toolbox",
          component: () => import("../views/toolbox/Toolbox.vue"),
          meta: { requiresInit: true, requiresAuth: true }
        },
        {
          path: "agents",
          name: "agentList",
          component: () => import("../views/agent/AgentList.vue"),
          meta: { requiresInit: true, requiresAuth: true, requiredCapability: 'agents' }
        },
        {
          path: "integrations",
          redirect: (to) => {
            const tab = typeof to.query.tab === 'string' ? to.query.tab : undefined
            const incoming = typeof to.query.section === 'string' ? to.query.section : 'integrations'
            const rest = { ...to.query }
            delete rest.tab
            return {
              path: '/platform/settings',
              query: {
                ...rest,
                section: normalizeSettingsSection(incoming, tab),
              },
            }
          },
          meta: { requiresInit: true, requiresAuth: true }
        },
        {
          path: "creatChat",
          name: "globalCreatChat",
          component: () => import("../views/creatChat/creatChat.vue"),
          meta: { requiresInit: true, requiresAuth: true }
        },
        {
          path: "knowledge-bases/:kbId/creatChat",
          name: "kbCreatChat",
          component: () => import("../views/creatChat/creatChat.vue"),
          meta: { requiresInit: true, requiresAuth: true }
        },
        {
          path: "chat/:chatid",
          name: "chat",
          component: () => import("../views/chat/index.vue"),
          meta: { requiresInit: true, requiresAuth: true }
        },
        {
          path: "organizations",
          name: "organizationList",
          component: () => import("../views/organization/OrganizationList.vue"),
          meta: { requiresInit: true, requiresAuth: true, requiredCapability: 'organizations' }
        },
        // Compatibility redirects for /platform/system/* URLs. System
        // administration surfaces live as dedicated sections inside the
        // standard Settings modal; keep stable URLs for bookmarks and
        // external links.
        {
          path: "system",
          redirect: { path: "/platform/settings", query: { section: "system-global" } },
          meta: { requiresInit: true, requiresAuth: true, requiresSystemAdmin: true },
        },
        {
          path: "system/settings",
          name: "systemSettings",
          redirect: { path: "/platform/settings", query: { section: "system-global" } },
          meta: { requiresInit: true, requiresAuth: true, requiresSystemAdmin: true },
        },
        {
          path: "system/admins",
          name: "systemAdmins",
          redirect: { path: "/platform/settings", query: { section: "system-global" } },
          meta: { requiresInit: true, requiresAuth: true, requiresSystemAdmin: true },
        },
        {
          path: "system/queues",
          name: "systemQueues",
          redirect: { path: "/platform/settings", query: { section: "runtime-queues" } },
          meta: { requiresInit: true, requiresAuth: true, requiresSystemAdmin: true },
        },
      ],
    },
    // Dev-only pages: markdown rendering test + design-lab direction samples
    // (design-lab views render full-bleed, outside the platform sidebar shell).
    ...(import.meta.env.DEV ? [
      {
        path: '/platform/dev/markdown',
        name: 'markdownTest',
        component: () => import('../views/dev/MarkdownTestPage.vue'),
        meta: { requiresAuth: false, requiresInit: false }
      },
      {
        path: '/platform/design-lab/a',
        name: 'designLabA',
        component: () => import('../views/design-lab/a/index.vue'),
        meta: { requiresAuth: false, requiresInit: false }
      },
      {
        path: '/platform/design-lab/b',
        name: 'designLabB',
        component: () => import('../views/design-lab/b/index.vue'),
        meta: { requiresAuth: false, requiresInit: false }
      },
      {
        path: '/platform/design-lab/c',
        name: 'designLabC',
        component: () => import('../views/design-lab/c/index.vue'),
        meta: { requiresAuth: false, requiresInit: false }
      },
      // 菜单合并方案对比页（个股追踪 tab 化）。用 ?variant=a|b|c 切换候选，
      // 三个候选共享同一份真实数据与同一套页面骨架，只比"出现在哪里 / 要几步 /
      // 谁在做的事"。与 a/b/c 方向样例无关，故单列一条。
      {
        path: '/platform/design-lab/finance',
        name: 'designLabFinance',
        component: () => import('../views/design-lab/finance/index.vue'),
        meta: { requiresAuth: false, requiresInit: false }
      },
      // 菜单合并后的真实页面（个股追踪四 tab），走一条不鉴权的 dev 路由。
      //
      // 为什么需要它：合并后这一页 requiresAuth，而「大盘」与「权重 ETF」两个 tab
      // 的数据其实全部来自 python-service（不鉴权）。没有这条 dev 路由，
      // 未登录状态下就没法验收那两个 tab —— 每次验收都要先建号或登录。
      // 仅 DEV 存在，生产构建里这条路由不存在。
      {
        path: '/platform/dev/tracking',
        name: 'devTracking',
        component: () => import('@/finance/views/Tracking.vue'),
        meta: { requiresAuth: false, requiresInit: false }
      },
      // 大盘工作台新增三块（板块榜 / 连板梯队 / 期股联动）的 dev 预览。
      // 与 devTracking 的区别：那一条验的是合并后的页面（有鉴权），
      // 这一条只验三个组件本身（无鉴权），用于未登录状态下验收。
      {
        path: '/platform/design-lab/board',
        name: 'designLabBoard',
        component: () => import('../views/design-lab/board/index.vue'),
        meta: { requiresAuth: false, requiresInit: false }
      },
    ] : []),
  ],
});

// 持久化 auto-setup / login 返回的认证信息到 store
function persistLoginResponse(authStore: ReturnType<typeof useAuthStore>, response: any) {
  const activeTenant = response.active_tenant || response.tenant
  if (response.user && response.token) {
    const homeTenantId = response.user.tenant_id ?? activeTenant?.id ?? ''
    authStore.setUser(userInfoFromApi(response.user, homeTenantId))
    authStore.setToken(response.token)
    if (response.refresh_token) {
      authStore.setRefreshToken(response.refresh_token)
    }
    if (activeTenant) {
      authStore.setTenant({
        id: String(activeTenant.id) || '',
        name: activeTenant.name || '',
        owner_id: response.user.id || '',
        created_at: activeTenant.created_at || new Date().toISOString(),
        updated_at: activeTenant.updated_at || new Date().toISOString()
      })
    } else {
      authStore.setTenant(null)
    }
    if (Array.isArray(response.memberships)) {
      authStore.setMemberships(response.memberships)
    }
  }
}

async function hydrateSessionFromToken(authStore: ReturnType<typeof useAuthStore>) {
  const token = localStorage.getItem('weknora_token')
  if (!token) return false

  if (!authStore.token) {
    authStore.setToken(token)
  }

  const storedRefreshToken = localStorage.getItem('weknora_refresh_token')
  if (storedRefreshToken && !authStore.refreshToken) {
    authStore.setRefreshToken(storedRefreshToken)
  }

  // /auth/me 的落库逻辑只在 auth store 里维护一份（user / tenant / memberships /
  // capabilities），这里只负责先把 token 放进 store。请求本身与启动、侧栏共用去重。
  return authStore.refreshFromAuthMe()
}

let autoSetupAttempted = false
let liteDeepLinkRestoreDone = false

// 启动时：把已注册的外部模块路由动态挂到 /platform 下。
// 注册发生在 main.ts 顶部 `import '@/finance'` 的副作用，早于 router 创建。
for (const mod of getRegisteredModules()) {
  if (mod.routeRedirect) {
    router.addRoute('Platform', {
      path: mod.path,
      name: mod.routeName,
      redirect: mod.routeRedirect,
      meta: { requiresInit: true, requiresAuth: true, ...mod.routeMeta },
    })
  } else if (mod.routeName && mod.routeComponent) {
    router.addRoute('Platform', {
      path: mod.path,
      name: mod.routeName,
      component: mod.routeComponent,
      meta: { requiresInit: true, requiresAuth: true, ...mod.routeMeta },
    })
  }
}

// 路由守卫：检查认证状态和系统初始化状态
router.beforeEach(async (to, from, next) => {
  const authStore = useAuthStore()

  // OIDC 回跳登录结果依赖 App.vue 在挂载后消费 URL hash。
  // 如果这里先按“未登录”拦截到 /login，会导致回调结果没有机会落盘。
  if (hasPendingOIDCCallback()) {
    next()
    return
  }

  // Preserve bookmarks for tools that have moved out of Settings.
  if (to.path === '/platform/settings' && isToolboxSection(to.query.section)) {
    next({ ...toolboxLocation(to.query.section,
      typeof to.query.sandboxId === 'string' ? to.query.sandboxId : undefined), replace: true })
    return
  }

  // Lite：硬刷新后若落在默认首页，恢复本次会话中最后访问的 /platform 子路径
  if (!liteDeepLinkRestoreDone) {
    liteDeepLinkRestoreDone = true
    if (isLiteEdition(authStore)) {
      const saved = sessionStorage.getItem(LITE_LAST_PATH_KEY)
      if (saved && isSafeLiteRestoreTarget(saved) && isLiteSpaDefaultEntry(to)) {
        if (saved !== to.fullPath) {
          next(saved)
          return
        }
      }
    }
  }

  // Tenantless onboarding still requires a valid user token even though it
  // deliberately skips the normal tenant/system-initialization gates.
  if (to.path === '/onboarding/workspace') {
    if (!authStore.isLoggedIn) {
      const restored = await hydrateSessionFromToken(authStore)
      if (!restored) {
        next('/login')
        return
      }
    }
    if (authStore.hasValidTenant) {
      next('/platform/knowledge-bases')
    } else {
      next()
    }
    return
  }

  // 如果访问的是登录页面或初始化页面，直接放行
  if (to.meta.requiresAuth === false || to.meta.requiresInit === false) {
    // 如果已登录用户访问登录页面，重定向到知识库列表页面
    if (to.path === '/login' && authStore.isLoggedIn) {
      next(authStore.hasValidTenant ? '/platform/knowledge-bases' : '/onboarding/workspace')
      return
    }
    next()
    return
  }

  // 检查用户认证状态
  if (to.meta.requiresAuth !== false) {
    if (!authStore.isLoggedIn) {
      const restored = await hydrateSessionFromToken(authStore)
      if (restored) {
        next(
          !authStore.hasValidTenant && to.meta.requiresTenant !== false
            ? '/onboarding/workspace'
            : to.fullPath,
        )
        return
      }

      if (!autoSetupAttempted) {
        autoSetupAttempted = true
        localStorage.removeItem('weknora_auto_setup_failed')
        try {
          const response = await autoSetup()
          if (response.success) {
            persistLoginResponse(authStore, response)
            authStore.setLiteMode(true)
            next(to.fullPath)
            return
          }
        } catch {
          // Auto-setup may be unavailable outside the native Lite shell.
        }
      }
      next('/login')
      return
    }
  }

  if (to.meta.requiresTenant !== false && !authStore.hasValidTenant) {
    next('/onboarding/workspace')
    return
  }

  // 部署能力只描述“后端是否提供该功能”，不反映服务健康或是否已配置。
  // 探测失败时 Store 会 fail-open，真正的权限和可用性仍由后端接口校验。
  const deploymentCapabilities = useDeploymentCapabilitiesStore()
  await deploymentCapabilities.ensureLoaded()
  const requiredCapability = to.meta.requiredCapability as DeploymentCapabilityKey | undefined
  if (requiredCapability && !deploymentCapabilities.isSupported(requiredCapability)) {
    MessagePlugin.warning(i18n.global.t('settings.capabilityUnavailable'))
    next('/platform/knowledge-bases')
    return
  }

  // SystemAdmin gate — checked AFTER auth so a non-admin who's logged
  // out gets redirected to /login first (consistent with how the rest
  // of the auth flow works), and only an authenticated non-admin sees
  // the bounce. This is UI-only; the server enforces the real check.
  if (to.meta.requiresSystemAdmin === true) {
    if (!authStore.isSystemAdmin) {
      next('/platform/knowledge-bases')
      return
    }
  }

  next()
})

router.afterEach((to) => {
  if (!isLiteEdition(useAuthStore())) return
  if (to.path === '/login') return
  if (!to.path.startsWith('/platform')) return
  sessionStorage.setItem(LITE_LAST_PATH_KEY, to.fullPath)
})

export default router
