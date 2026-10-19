import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN.ts'
import ruRU from './locales/ru-RU.ts'
import enUS from './locales/en-US.ts'
import koKR from './locales/ko-KR.ts'
import jaJP from './locales/ja-JP.ts'
import zhTW from './locales/zh-TW.ts'
import { BUILT_IN_DEFAULT, resolveDefaultLocale } from './resolveDefaultLocale.ts'
import { injectAppName } from './injectAppName.ts'
import { APP_NAME } from '@/config/appIdentity'
import { pluralRules } from './pluralizationRules.ts'

// import.meta.env 是 Vite 构建期的注入，裸 node（tsx --test）下不存在，
// 直接读会抛 "Cannot read properties of undefined"。这里做一次安全取值：
// Vite 构建下 import.meta.env 一定存在，行为与原来完全一致；
// 非 Vite 环境退化成空对象，落到下面的默认值分支。
const importMetaEnv = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {}

const messages = {
  'zh-CN': zhCN,
  'zh-TW': zhTW,
  'en-US': enUS,
  'ru-RU': ruRU,
  'ko-KR': koKR,
  'ja-JP': jaJP
}

// 产品名的 i18n 入口：文案里用 `@:{'appName'}` 引用，值来自构建期变量 VITE_APP_NAME。
// 这样品牌名只有 src/config/appIdentity.ts 一个来源，各语言包不必各自抄一遍。
injectAppName(messages, APP_NAME)

// User's explicit past choice wins; otherwise use the deployment default.
const savedLocale = localStorage.getItem('locale') || resolveDefaultLocale(
  window.__RUNTIME_CONFIG__?.DEFAULT_LOCALE,
  importMetaEnv.VITE_DEFAULT_LOCALE,
)

const i18n = createI18n({
  legacy: false,
  locale: savedLocale,
  fallbackLocale: BUILT_IN_DEFAULT,
  globalInjection: true,
  pluralRules,
  // Some translations intentionally embed `<strong>` markup (e.g. agent step summaries).
  // We render them via v-html with our own sanitization, so silence vue-i18n's HTML warning
  // to avoid flooding the console and slowing renders during history loads.
  warnHtmlMessage: false,
  missing: (locale, key) => {
    if (import.meta.env.DEV) {
      console.warn(`[i18n] Missing translation key "${key}" for locale "${locale}"`)
    }
  },
  messages
})

export default i18n
