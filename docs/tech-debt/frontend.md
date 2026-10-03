# 技术债审计 — Vue 3 前端 (`frontend/src/**`)

## 结论

- **修 `kline.*` 这一个命名空间就能消灭 47 个线上裸 key**；全仓 186 个"代码里写了 `t()` 但语言包里没有"的 key 全部躲过了 `check-i18n` 门禁（门禁只比对 5 个语言包之间是否一致，而它们确实完全一致 —— 问题在"代码引用集 ⊄ 语言包 key 集"这一维度根本没被测）。
- **`AgentStreamDisplay.vue` 里有 69 行模板逐字重复两遍**（`155-223` 与 `477-545`），改一个工具结果的展示逻辑必须记得改第二处，否则两个分支行为分叉。
- **本地鉴权是 XSS 的等价提权面**：JWT + refresh token 都在 `localStorage`，全仓 69 处 `v-html` 只要有一处漏网就是 token 全量泄漏。
- **`WikiBrowser.renderMarkdown` 把 wiki 页面内容里的 slug 原样拼进 HTML 属性**（`data-slug="${slug}"`），同一段逻辑在 `AgentStreamDisplay` 里是转义过的 —— 两份实现已经分叉。
- **业务巨型 SFC 基本没有测试**：17 个最大组件里 6 个零测试文件，且现有"组件测试"里 79/224 是拿 `readFileSync` 读源码做正则断言，改个空格就红、逻辑错了却绿。

---

## 发现

### [S2] `WikiBrowser` 的 wiki 链接 slug 未转义就拼进 HTML 属性，与 `AgentStreamDisplay` 的同名逻辑已分叉

- **位置**：`frontend/src/views/knowledge/wiki/WikiBrowser.vue:1525-1534`、`frontend/src/views/chat/components/AgentStreamDisplay.vue:894-905`
- **证据**：同一段 `[[slug|name]]` 预处理，两处实现不同：
  ```ts
  // WikiBrowser.vue:1531
  return `<a href="#" class="wiki-content-link" data-slug="${slug}">${display}</a>`
  ```
  ```ts
  // AgentStreamDisplay.vue:904 —— 同一功能，这里转义了
  return `<a href="#" class="wiki-content-link citation-wiki" data-slug="${escapeHtml(slug)}">${escapeHtml(display)}</a>`;
  ```
- **影响**：wiki 页面 `content` 是 LLM 生成 + 知识库导入的混合来源。slug 里出现 `"` 即可提前闭合 `data-slug` 属性并注入任意属性/标签。虽然 `marked.parse` 之后会过 `sanitizeMarkdownHTML`（DOMPurify 会剥掉 `on*` 属性和 `javascript:`），但这是一条**纯靠下游兜底的安全边界**，且 `display` 文本也未转义 —— 页面里 `[[a|<img src=x onerror=alert(1)>]]` 会先被拼成裸 HTML 交给 marked。当前 DOMPurify 兜住了，属于"上游漏、下游救"，但任何一次 sanitizer 配置放宽或换渲染路径都会立刻变成 XSS。同时 `handleContentClick:1556` 用 `getAttribute('data-slug')` 读回来直接 `getWikiPage(kbId, slug)`，属性被注入后可影响请求目标。
- **修复**：把 `AgentStreamDisplay.vue:893-905` 的实现（含 `escapeHtml`）抽到 `utils/wikiLinkPreprocessor.ts`，两处共用；`WikiBrowser` 改为 import。约 20 行抽离 + 2 处调用点替换。
- **工作量**：S（<半天）

### [S2] 鉴权 token 全量存 `localStorage`，与 69 处 `v-html` 组成完整提权链

- **位置**：`frontend/src/stores/auth.ts:212`、`:217`、`frontend/src/utils/request.ts:75-79`
- **证据**：
  ```ts
  // auth.ts:212
  const setToken = (tokenValue: string) => {
    token.value = tokenValue
    localStorage.setItem('weknora_token', tokenValue)
  }
  // auth.ts:217
  localStorage.setItem('weknora_refresh_token', refreshTokenValue)
  ```
  `request.ts:75` 每次请求从 `localStorage.getItem('weknora_token')` 读回。
- **影响**：`localStorage` 对同源任意 JS 可见、无 httpOnly 保护。上面 3 条 v-html 类缺陷（WikiBrowser slug、kline error message、Excel sheet name）任意一条被绕过即等于长期凭据 + refresh token 泄漏，攻击者可在 XSS 内静默续期。`utils/security.ts` 的 sanitizer 是唯一防线，而 `utils/security.test.ts` 只有 38 行 3 个用例，全部测的是 `protectProviderImageSrcInHTML` 的占位图替换 —— **没有任何一条用例断言 XSS 向量（`<script>` / `onerror` / `javascript:`）被剥离**。
- **修复**：分两步。①先补 `security.test.ts`：对 `sanitizeHTML` / `sanitizeMarkdownHTML` 各加一组 payload 断言（`<img src=x onerror=alert(1)>`、`<a href="javascript:alert(1)">`、`data:text/html;base64,...`），把当前净化行为钉死。②token 迁到 httpOnly cookie + `withCredentials` 属后端联动，单独立项，不要在纯前端 PR 里混做。
- **工作量**：M（1-3天，含后端 cookie 改造）

### [S2] i18n 门禁测的是"语言包之间一致"，没测"代码引用的 key 存在"——186 个裸 key 在线

- **位置**：`frontend/src/i18n/localeKeyAudit.test.ts:24-40`、`frontend/src/i18n/localeKeyAudit.ts:388-394`
- **证据**：5 个语言包 key 集合确实完全一致（我实测各 6538 个，零差异），`npm run check-i18n` 13 个用例全绿。但门禁构造待检集的方式是"以 en-US 为基准，筛出被引用的 key"：
  ```ts
  // localeKeyAudit.ts:388
  export function collectReferencedLocaleKeys(root: unknown, usage: I18nUsage): Set<string> {
    const referenced = new Set<string>()
    for (const key of collectLocaleKeys(root)) {       // ← 先遍历语言包已有的 key
      if (isLocaleKeyUsed(key, usage)) referenced.add(key)
    }
    return referenced
  }
  ```
  方向反了：代码里 `t('kline.volumeLabel')` 但语言包没有 `kline.volumeLabel` 时，这个 key 永远进不了待检集。实测 `usage.staticKeys` 有 5744 个，其中 **186 个带点号的 key 在语言包里完全不存在**，分布：
  | 命名空间 | 缺失数 |
  |---|---|
  | `kline` | 47 |
  | `stockCitation` | 26 |
  | `watchlist` | 25 |
  | `system` | 12 |
  | `settings` | 9 |
  | `input` | 7 |
  | `contextualGuide` | 7 |
  | `datasource` | 7 |
  | 其余（error/chat/file/agent/...） | 46 |
- **影响**：线上直接显示 raw key。例：`finance/components/kline/KLineWorkspace.vue:98` 的成交量标签在所有语言下都渲染成字面量 `kline.volumeLabel`，`:697` 的成交额单位渲染成 `kline.unitShou`。更糟的是"参数化提示语"：`KLineWorkspace.vue:406`
  ```html
  <p v-if="loadError.kind === 'request'" class="empty__hint"
     v-html="t('kline.requestRejectedHint', { message: loadError.message })" />
  ```
  `kline.requestRejectedHint` 不存在 → vue-i18n 返回 key 本身，**`{message}` 插值直接丢失**；同时 `loadError.message` 的来源是 `finance/components/kline/datafeed.ts:47-56` `extractErrorMessage()`，会原样取服务端 `body.error` / `body.message` 塞进 `v-html`（未经 escape）。服务端一旦返回含 HTML 的错误体就是 XSS。正确 key 实际叫 `kline.requestRejected`（`i18n/locales/en-US.ts:7754`）。
  旁证：`i18n/index.ts:26` 设了 `fallbackLocale: 'zh-CN'`，但**没有 `missing` handler**，缺失时静默回落成 key 串，控制台也不会 warn —— 线上完全无声。
- **修复**：①给 `createI18n` 加 `missing` handler，`import.meta.env.DEV` 下 `console.warn(key)`，让缺失可见。②新增一条门禁用例：`findUsedKeysMissingInLocales(usage.staticKeys, localeKeysByName)`（把待检集换成"代码里写的"而不是"语言包里有的"），这条会立刻红并列出全部 186 个。③按命名空间批量补 key，`kline` 一组就能消掉最大头。
- **工作量**：M（1-3天；补 186 个 key × 5 语言是主体工作量）

### [S2] `AgentStreamDisplay.vue` 有 69 行模板逐字重复两遍

- **位置**：`frontend/src/views/chat/components/AgentStreamDisplay.vue:155-223` 与 `:477-545`
- **证据**：用 `difflib` 逐行比对两段（各 421 行），**最长的连续相同块正好 69 行，字节级完全一致**（`A == B` 为 True）。重复内容是整个工具结果卡片体：
  ```html
  <span v-if="getSandboxDiffStat(event)" class="sandbox-diff-stat"> ... </span>
  <div v-if="... search_knowledge ..." v-html="getSearchResultsSummary(event)"></div>
  <div v-if="... web_search ..." v-html="t('agent.webSearchFound', {...})"></div>
  <div v-if="... grep_chunks ..." v-html="getGrepResultsSummary(event.tool_data)"></div>
  <div v-if="... read_document ..." v-html="getKnowledgeChunksSummary(event.tool_data)"></div>
  <SandboxCommandProgress v-if="... shell_exec ..." />
  <div v-if="... attachment_parsing ..." v-html="getAttachmentParsingSummary(event)"></div>
  <div v-if="isEventExpanded(...)" class="action-details">
    <ToolResultRenderer :display-type="resolveToolDisplayType(event)" ... />
  ```
  两份分别服务于"已折叠的中间步骤树"（`:20` 起）和展开态（`:450` 起），各自还有约 30 行各自独有的差异代码。
- **影响**：该组件 3989 行、96 个 `v-if`、14 个 ref。给某个工具结果加一个展示分支（比如给 `web_search` 加耗时）必须记得改两处；`src/views/chat/components/AgentStreamDisplay.style.test.mjs:56` 就是这么写的 —— 断言的是**某一行的正则**：
  ```js
  assert.match(source, /class="thinking-inline-markdown" v-html="renderMarkdownContent\(event\.content\)"/)
  ```
  只锁了第一份副本的位置，第二份改了不会被发现。这是"改一处必漏另一处"的具体形态。
- **修复**：把工具结果摘要与详情抽成子组件 `ToolCallResultBody.vue`，props 接 `event`，两份调用点各留 `<ToolCallResultBody :event="event" />`。预计可从 3989 行降到 2900 行左右，模板分支从 96 降到约 50。
- **工作量**：M（1-3天）

### [S2] i18n key 拼错到另一个命名空间，导致错误提示静默退化成英文原文

- **位置**：`frontend/src/api/tenant/index.ts:218, 233, 265, 326`、`frontend/src/views/chat/components/botmsg.vue:441, 452, 467`、`frontend/src/components/doc-content.vue:1560`、`frontend/src/views/knowledge/KnowledgeBase.vue:2188`
- **证据**：语言包里的真实 key 在别的命名空间下：
  | 代码写的 | 语言包实际位置 |
  |---|---|
  | `error.tenant.createApiKeyFailed` | `integrations.api.createApiKeyFailed`（`zh-CN.ts:577`） |
  | `error.tenant.listApiKeysFailed` | `integrations.api.loadApiKeysFailed`（`zh-CN.ts:578`） |
  | `chat.emptyContentWarning` | 不存在（`chat` 命名空间共 212 个 key，无此项） |
  | `chat.editorOpened` | `agentStream.saveToKb.editorOpened`（`zh-CN.ts:6318`） |
  | `file.downloadFailed` | `file` 命名空间只有 1 个 key `file.upload` |
  | `knowledge.untitledDocument` | `knowledge` 命名空间 0 个 key，实际在 `knowledgeBase.moveToFolder.untitledDocument`（`zh-CN.ts:1056`） |
- **影响**：全部是失败路径提示 —— 创建 API Key 失败、复制空回答、下载失败。`api/tenant/index.ts:233` 的写法 `error.message || t('error.tenant.createApiKeyFailed')` 在 `error.message` 为空时正好落进这条失效分支，用户看到的是 `error.tenant.createApiKeyFailed` 字面量而不是"创建 API Key 失败"。这类缺陷只有真触发失败路径才暴露，测试不会碰到。
- **修复**：逐个改正 key 路径（12 处左右，都是替换字符串），改完跑上面那条新的门禁用例即可全覆盖。
- **工作量**：S（<半天）

### [S3] 17 个巨型 SFC 缺测试，且现有"组件测试"多数是源码正则断言

- **位置**：`frontend/src/views/knowledge/wiki/WikiBrowser.vue`、`frontend/src/views/knowledge/components/FAQEntryManager.vue`、`frontend/src/components/doc-content.vue`、`frontend/src/components/ModelEditorDialog.vue`、`frontend/src/views/integrations/ApiIntegrationSettings.vue`、`frontend/src/views/settings/TenantMembers.vue`
- **证据**：6 个最大组件**零测试文件**。有测试的那几个，测试手法是读源码做正则：
  ```js
  // src/components/Input-field.agent-switch.test.mjs:13-15
  const inputField = readFileSync(new URL('./Input-field.vue', import.meta.url), 'utf8')
  const sendButton = readFileSync(new URL('./input/InputSendButton.vue', import.meta.url), 'utf8')
  const settingsStore = readFileSync(new URL('../stores/settings.ts', import.meta.url), 'utf8')
  ```
  再 `inputField.slice(inputField.indexOf('const createSession ='), ...)` 切出代码段丢进 `node:vm` 跑。全仓 224 个测试文件里 79 个用了 `readFileSync` + `assert.match(source, ...)` 这种模式。
  项目**没有装 `@vue/test-utils`**（`package.json` devDependencies 里没有），所以没有任何一个测试真正 mount 过 SFC。
- **影响**：这类测试的失败模式是反的 —— 格式化改个空格就红，而真 bug（比如上面那 69 行重复块的第二份被改坏）全绿。`WikiBrowser.vue` 6593 行、64 个 ref、74 个 v-if 里有 3 个 `v-html` 渲染路径（`:565` / `:635` / `:157`），一条都没有测试覆盖。
- **修复**：先补 `@vue/test-utils` + `happy-dom`，挑 3 个渲染风险最高且有真实逻辑的组件做 mount 测试（`WikiBrowser` 的 `renderMarkdown`、`doc-content` 的 `processMarkdown`、`ModelEditorDialog` 的 provider 切换）。已有正则测试不必删，但应改名为 `*.guard.test.mjs` 表明其性质是"结构守卫"而非行为测试。
- **工作量**：L（>3天）

### [S3] `TenantMembers.vue` 同一套分页逻辑写了两遍

- **位置**：`frontend/src/views/settings/TenantMembers.vue:810-838` 与 `:915-943`
- **证据**：两段结构完全同构，只换了变量名（`members*` / `invitations*`）：
  ```ts
  const total = resp.data.total ?? 0
  const ps = resp.data.page_size ?? membersPageSize.value
  const safePs = Math.max(1, ps)
  const maxPage = Math.max(1, Math.ceil(total / safePs))
  if (membersPage.value > maxPage) { membersPage.value = maxPage; loading.value = false; await loadMembers(); return }
  ```
  另一段 `invitationsLoading.value = false; await loadInvitations()`。全仓 `Math.ceil(total / ...)` 分页计算只出现 3 次（此处 2 次 + `KnowledgeBase.vue:1925`），但只有这 2 次是完整复制。
- **影响**：删成员、删邀请这类操作会改 `total`，"页码越界就回退重查"这个分支必须在两个列表里都正确。两份代码目前一致，但任何一侧新增"服务端返回 page_size 上限"之类的处理都会漏另一侧。属于低频但真会出问题的重复。
- **修复**：抽 `applyPageEnvelope(resetPage, resp, state)` 到 `composables/useListUrlState.ts`（该文件已存在，正好是这类 URL/分页状态的归属地），两处各缩到 3 行。
- **工作量**：S（<半天）

### [S3] 设置抽屉的 section 骨架在 27 个组件里各写一遍

- **位置**：`frontend/src/components/settings/SettingDrawer.vue`（已存在 623 行的基座）、以及 26 个使用方
- **证据**：`setting-drawer__section-title` 在 27 个 `.vue` 里出现 **119 次**。各使用方还各自带一份 inline fallback 文案：
  ```html
  <!-- VectorStoreSettings.vue:176 与 WebSearchSettings.vue:168 与 McpServiceDialog.vue:83 -->
  <h4 class="setting-drawer__section-title">{{ t('vectorStoreSettings.basicSection', '基本信息') }}</h4>
  <h4 class="setting-drawer__section-title">{{ t('webSearchSettings.basicSection', '基本信息') }}</h4>
  <h4 class="setting-drawer__section-title">{{ t('mcpServiceDialog.basicSection', '基本信息') }}</h4>
  ```
  `VectorStoreSettings.vue` 里同一个 `basicSection` 块在 `:176` 和 `:219` 又各写了一遍。
- **影响**：`t(key, '默认值')` 这种"语言包缺 key 就显示中文硬编码"的写法，在上面第 3 条已确认的语言包缺 key 环境下是**双重保险失效** —— key 拼错时不会显示 raw key，而是静默显示中文，英文用户看到中文界面。全仓共 54 处这种兜底写法，集中在 4 个设置类组件（`ParserEngineSettings` / `McpServiceDialog` / `VectorStoreSettings` / `WebSearchSettings`）。
- **修复**：给 `SettingDrawer.vue` 加 `#section` 具名 slot（`<slot name="section" :title="...">`），各使用方改成 `<template #section="{ title }">`。这一步同时让 inline 中文 fallback 有机会被清掉。
- **工作量**：M（1-3天，27 个文件的模板改动机械但量大）

### [S3] 硬编码中文 UI 文案绕过 `t()`，集中在设置类页面

- **位置**：`frontend/src/components/ModelEditorDialog.vue:1380-1415`、`frontend/src/components/document-preview.vue:335`、`frontend/src/components/UserMenu.vue:516`、`frontend/src/components/menu.vue:1184`
- **证据**：
  ```js
  // ModelEditorDialog.vue:1380-1415 —— 5 条遗留调试日志打进生产
  console.log('开始检查Ollama服务状态...')
  console.log('Ollama服务状态检查完成:', result.available)
  console.log('点击跳转到Ollama设置按钮')
  console.log('调用uiStore.openSettings')
  console.log('uiStore.openSettings调用完成')
  ```
  ```js
  // document-preview.vue:335 —— 直接写死中文 HTML
  markdownHtml.value = '<p style="color: var(--td-text-color-disabled); ...">文档内容为空</p>';
  ```
  全仓（排除 `i18n/locales/`）有 1191 行含中文字符；剔除注释后仍是用户可见文案的有 159 处，集中在 `finance/`（`KLineWorkspace.vue` 85 处、`Watchlist.vue` 37 处）、`ModelEditorDialog.vue` 39 处、`FAQEntryManager.vue` 56 处。
- **影响**：英文/日文/韩文/俄文界面下这些位置固定显示中文，是"这个产品只做了中文"的直接观感来源。另外 `ModelEditorDialog` 那 5 条 `console.log` 每次点 Ollama 检测都往生产控制台刷。
- **修复**：先删 `ModelEditorDialog.vue:1380-1415` 的调试日志（5 行，零风险）。`document-preview.vue:335` 那条改走 `t('common.noData')`（`doc-content.vue:1856` 已经这么用了，同一语义两套写法）。finance 模块的中文集中在 K 线领域术语（"砖型图""牵牛绳"），需要产品决策是否翻译，可先只处理 finance 之外的部分（约 60 处）。
- **工作量**：M（1-3天；finance 部分另计）

### [S3] `excelHtml` 把工作表名原样拼进 HTML，靠 `sanitizeHTML` 兜底

- **位置**：`frontend/src/components/document-preview.vue:300-309`
- **证据**：
  ```ts
  workbook.SheetNames.forEach((name, sheetIdx) => {
    html += `<div class="excel-sheet">`
    if (workbook.SheetNames.length > 1) {
      html += `<div class="excel-sheet-name">${name}</div>`   // ← name 未转义
    }
    html += sheetHtml
  })
  excelHtml.value = sanitizeHTML(html)   // ← :309，兜底在这里
  ```
  `name` 来自用户上传的 xlsx/xls/csv 的 sheet 名。相邻的 `renderArtifactFileIcon.ts:8` 就做对了：
  ```ts
  // Only this restricted extension label is interpolated, never the filename.
  const label = /^[a-z0-9]{1,4}$/i.test(ext) ? ext.toUpperCase() : 'FILE'
  ```
  同文件的 `renderText` 路径（`:322`）也用 `hljs.highlight()` 而非裸拼接。
- **影响**：一个 sheet 名为 `<img src=x onerror=...>` 的表格，预览时会执行。当前 `sanitizeHTML` 剥掉了 `onerror` 所以不可利用，但这是第二条"上游漏、下游救"的边，和第 1 条同类。上传 xlsx 是知识库的常规路径，这条路径的输入完全由攻击者控制。
- **修复**：`document-preview.vue:304` 改为 `escapeHTML(name)`（`utils/security.ts` 已导出）。1 行。
- **工作量**：S（<半天）

---

## 量化

### 规模

| 指标 | 数值 |
|---|---|
| 生产代码行数（`src/`，排除 `.test.*`） | 268,930 行 / 626 文件 |
| 测试代码行数 | 23,973 行 / 224 文件 |
| `.vue` 文件 / `.ts`+`.mjs`+`.js` 文件 | 247 / 566 |
| 测试代码 : 生产代码 | 1 : 11.2 |

> 注：主进程给的 278k 是含 `dist`/其他目录的口径；此处为 `src/` 下按扩展名精确统计。测试行数 23,973 远低于主进程口径的 193k（那是 Go 全仓），前端测试规模实际是这个量级。

### 安全

| 指标 | 数值 |
|---|---|
| `v-html` 出现次数 | 69（17 个文件） |
| 走 `sanitizeHTML` / `sanitizeMarkdownHTML` | 47（68%） |
| 走 `hljs` / `escapeHtml` 自转义 | 12（17%） |
| 走 vue-i18n `t()`（含 2 处 key 已失效） | 5 |
| 裸字符串常量（`ArtifactFileIcon.iconSvg`） | 2 |
| 依赖下游 sanitizer 兜底 | 3（WikiBrowser slug、Excel sheet name、kline error message） |
| `v-html` 无任何净化且绑定运行时数据 | 1（`kline.requestRejectedHint`，且该 key 不存在） |
| `utils/security.test.ts` | 38 行 / 3 用例 / **0 条 XSS payload 断言** |
| 敏感数据存储位置 | `localStorage` × 2（`weknora_token`、`weknora_refresh_token`） |
| `postMessage` 目标 `'*'` 回退 | 1 处（`api/embed/index.ts:494`，仅非敏感握手消息；`isTrustedParentMessage` 有 origin + source 双重校验） |
| 动态 `<component :is>` | 6 处，值均来自内部常量或三元表达式，无用户输入 |
| `window.open` 动态 URL | 1 处存疑（`AgentStreamDisplay.vue:2266`，URL 来自 markdown 的 `data-url` 属性） |

### 巨型 SFC

| 文件 | 总行 | 模板 | script | style | ref | v-if | v-for | watch | deep watch | 测试文件 |
|---|---|---|---|---|---|---|---|---|---|---|
| `AgentEditorModal.vue` | 6718 | 1-1844 | 1845-5022 | 5024-6642 | 65 | 134 | 28 | 21 | 1 | 1 |
| `WikiBrowser.vue` | 6593 | 17-792 | 793-4877 | 4879-6551 | 64 | 74 | 9 | 15 | 2 | **0** |
| `FAQEntryManager.vue` | 5175 | 23-758 | 759-2639 | 2641-5175 | — | — | — | — | 1 | **0** |
| `AgentStreamDisplay.vue` | 3989 | 1-629 | 630-3135 | 3137-3981 | 14 | 96 | 4 | 7 | 2 | 1（正则） |
| `knowledge-processing-timeline.vue` | 3765 | 1538-1551 | 1-1536 | 2044-3765 | 15 | 50 | 11 | 3 | — | 1 |
| `Input-field.vue` | 3655 | 2710-2822 | 1-2709 | 2908-3655 | 26 | 8 | 3 | 10 | 1 | 4（均正则/vm） |
| `doc-content.vue` | 3326 | 1596-1569* | 1-1569 | 2194-3225 | 41 | 63 | 7 | 13 | 1 | **0** |
| `OrganizationSettingsModal.vue` | 3168 | 10-750 | 751-1845 | 1847-2873 | — | — | — | — | — | 1 |
| `ModelEditorDialog.vue` | 3118 | 18-607 | 608-2186 | 2188-2949 | — | — | — | — | 1 (`flush:'sync'`) | **0** |
| `DataSourceEditorDialog.vue` | 2942 | 1343-1363* | 1-1341 | 1997-2884 | — | — | — | — | 2 | 1 |
| `ApiIntegrationSettings.vue` | 2624 | 12-672 | 673-1744 | 1746-2624 | — | — | — | — | — | **0** |
| `SandboxConfigEditorDrawer.vue` | 2577 | 33-760 | 761-1780 | 1782-2561 | — | — | — | — | — | 2 |
| `KnowledgeBase.vue` | 2510 | 2263-2420* | 1-2261 | 2499-2507 | 44 | 15 | 2 | 17 | 2 | 2 |
| `TenantMembers.vue` | 2491 | 42-514 | 515-1467 | 1469-2318 | 31 | 39 | 3 | 7 | — | **0** |
| `UploadConfirmDialog.vue` | 2488 | 62-639 | 640-1590 | 1592-2467 | — | — | — | — | — | 1 |
| `SandboxSkillsPanel.vue` | 2407 | 38-621 | 622-1503 | 1505-2207 | — | — | — | — | — | 1 |
| `KnowledgeBaseEditorModal.vue` | 2321 | 167-552 | 553-1859 | 1861-2321 | 17 | 52 | 5 | 5 | — | 1 |

\* 该文件 `<template>` 在 `<script>` 之后，`awk` 按首次匹配定位导致边界读数异常；`doc-content` / `DataSourceEditorDialog` / `KnowledgeBase.vue` 的实际模板位于文件尾部。

**结构结论**：template : script : style 三段比例高度一致（约 25% : 35% : 40%），意味着 `script setup` 里 1200-3200 行的逻辑 + 1500-2100 行的 scoped CSS 全部挤在同一个文件里，无法按关注点切分。**17 个文件里 6 个零测试。**

### 状态管理

| 指标 | 数值 |
|---|---|
| Pinia store 数（不含测试文件） | 24 |
| 最大 3 个 store | `organization.ts` 852 / `settings.ts` 631 / `auth.ts` 623 |
| `provide`/`inject` 点 | 7（全在 `composables/`，成对封装，无裸跨层传递） |
| 组件直接改 store 内部 ref | 0（`authStore` 走 setter；`DataSourceEditorDialog` 改的是组件本地 `form.value`） |
| `deep: true` watch | 29 处 |
| `deep + flush:'sync'` | 1 处（`ModelEditorDialog.vue:1547`，监听 15 字段含 `apiKey` / `extraConfig` 整个对象，同步触发 `invalidateConnectionTest`） |
| `setInterval` 泄漏 | 0（全仓 `setInterval` 句柄均有 `clearInterval`；`OllamaSettings.vue:296` 的 `progressInterval` 在 `:302/309/315` 三条路径都清了） |
| API 调用散落 | 组件内 0 处 `axios.*`，5 处裸 `fetch`（`ApiIntegrationSettings.vue` 2 处为 playground 有意绕开实例，其余 3 处为 blob 下载，均合理） |

### 重复

| 指标 | 数值 |
|---|---|
| `setting-drawer__section-title` 出现次数 | 119（27 个文件） |
| `t(key, '中文默认值')` 兜底写法 | 54 处（4 个文件：`ParserEngineSettings` / `McpServiceDialog` / `VectorStoreSettings` / `WebSearchSettings`） |
| `AgentStreamDisplay.vue` 逐字重复块 | 1 处，69 行完全一致 |
| 完整复制的分页实现 | 2 份（`TenantMembers.vue:810/915`） |
| 各写一遍的知识库配置抽屉 | 8 个（`KBParserSettings` 556 / `KBChunkingSettings` 679 / `KBAdvancedSettings` 433 / `KBShareSettings` 336 / `KBModelConfig` 227 / `KBVectorStoreSettings` 286 / `KBStorageSettings` 69 / `KBChunkingDebug` 725，共 3311 行） |

### i18n

| 指标 | 数值 |
|---|---|
| 语言包 | 5（`en-US` 7821 / `zh-CN` 7823 / `ja-JP` 7821 / `ko-KR` 7820 / `ru-RU` 7821 行） |
| 叶子 key 数 | 各 6538，**五语言完全一致，零缺失零冗余** |
| 代码中 `t()` 静态 key 总数 | 5,744 |
| **其中语言包里不存在的** | **186**（`kline` 47 / `stockCitation` 26 / `watchlist` 25 / `system` 12 / `settings` 9 / 其余 67） |
| 门禁覆盖 | `npm run check-i18n` 13 用例全绿 —— **上述 186 个全部漏检** |
| 硬编码中文 UI 文案 | 159 处（finance 122 / 其余 37） |
| `missing` handler | 无（缺失时静默回落为 key 串，控制台无 warn） |
| 日期格式化 | 走 `toLocaleString` / 手工格式化，未见硬编码 locale 串 |

### 测试

| 指标 | 数值 |
|---|---|
| 测试文件总数 | 224 |
| 分布 | `utils/` 78 / `components/` 21 / `stores/` 15 / `composables/` 13 / `views/chat/components` 12 / 其余 85 |
| 用 `readFileSync` + `assert.match(source,...)` 的 | **79（35%）** |
| 用 `@vue/test-utils` 真正 mount SFC 的 | **0**（该依赖未安装） |
| 17 个巨型 SFC 中零测试的 | 6（`WikiBrowser` / `FAQEntryManager` / `doc-content` / `ModelEditorDialog` / `ApiIntegrationSettings` / `TenantMembers`） |
| 真正跑过并通过的 i18n 门禁 | `localeKeyAudit.test.ts` 13/13（但覆盖方向错，见 S2 第 3 条） |
