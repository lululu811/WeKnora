/**
 * 把产品名注入各语言包，供文案以 linked message 引用：`@:{'appName'}`。
 *
 * 抽成纯函数（而不是写在 i18n/index.ts 里）是为了可测：index.ts 会读 localStorage 与
 * `__APP_NAME__`，在 node 里跑不起来；这个函数只吃参数，测试可以直接喂语言包与名字。
 *
 * 注意文案必须写成**带引号**的形式 `@:{'appName'}`。`@:appName` 这种裸写法只在后面
 * 紧跟空格或字符串结束时才有效，遇到中文标点（`@:appName，`）vue-i18n 会把标点一起当成
 * 键名，结果界面上直接显示 "appName" 五个字母。见 appName.test.ts。
 */
export function injectAppName<T extends Record<string, unknown>>(messages: T, appName: string): T {
  for (const locale of Object.keys(messages)) {
    ;(messages[locale] as Record<string, unknown>).appName = appName
  }
  return messages
}
