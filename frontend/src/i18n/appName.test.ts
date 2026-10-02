import assert from 'node:assert/strict'
import test from 'node:test'
import { createI18n } from 'vue-i18n'

import enUS from './locales/en-US.ts'
import jaJP from './locales/ja-JP.ts'
import koKR from './locales/ko-KR.ts'
import ruRU from './locales/ru-RU.ts'
import zhCN from './locales/zh-CN.ts'
import { injectAppName } from './injectAppName.ts'

const LOCALES = { 'zh-CN': zhCN, 'en-US': enUS, 'ru-RU': ruRU, 'ko-KR': koKR, 'ja-JP': jaJP }
const APP_NAME = '品牌名探针'

type LocaleValue = string | Record<string, unknown> | unknown[]
type Leaf = { path: string; value: string }

function collectStrings(value: LocaleValue, path = ''): Leaf[] {
  if (typeof value === 'string') return [{ path, value }]
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => collectStrings(item as LocaleValue, `${path}[${index}]`))
  }
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) =>
      collectStrings(item as LocaleValue, path ? `${path}.${key}` : key),
    )
  }
  return []
}

// vue-i18n 解析 linked message 时只认 `@:{'key'}` 这种带引号的写法；
// `@:key` 裸写法一旦后面紧跟非空白字符（中文标点就属于这种），整个 token 会被当成键名，
// 界面直接显示 "appName"。这个测试就是钉住这条语法约束。
test('语言包里的 linked message 必须用带引号的形式', () => {
  for (const [locale, messages] of Object.entries(LOCALES)) {
    for (const { path, value } of collectStrings(messages as LocaleValue)) {
      // 去掉合法的 @:{'key'} 之后，若还有残留的 @:，说明写成了裸形式。
      const leftover = value.replace(/@:\{[^}]*\}/g, '')
      assert.ok(
        !leftover.includes('@:'),
        `${locale} 的 ${path} 用了裸写法，应写成 @:{'appName'}：${value}`,
      )
    }
  }
})

test('引用了产品名的文案都能解析出真名，且不残留 @: 或键名', () => {
  const messages = injectAppName(structuredClone(LOCALES), APP_NAME)

  let checked = 0
  for (const [locale, localeMessages] of Object.entries(LOCALES)) {
    const i18n = createI18n({ legacy: false, locale, messages })
    for (const { path, value } of collectStrings(localeMessages as LocaleValue)) {
      const linked = value.match(/@:/g) ?? []
      if (linked.length === 0) continue
      const rendered = i18n.global.t(path)
      assert.ok(
        !rendered.includes('@:') && !rendered.includes('appName'),
        `${locale} 的 ${path} 未解析：${rendered}`,
      )
      assert.ok(rendered.includes(APP_NAME), `${locale} 的 ${path} 没带上产品名：${rendered}`)
      checked += 1
    }
  }
  assert.ok(checked >= 6, `至少应有 6 条文案引用产品名，实际 ${checked} 条`)
})
