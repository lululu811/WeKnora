/**
 * `import.meta.env` 的安全取值。
 *
 * `import.meta.env` 是 Vite 在构建期注入的对象，只在 Vite 产出的代码里存在。
 * 裸 node（`tsx --test`）下它是 undefined，任何一处直接读都会抛
 * "Cannot read properties of undefined"——测试只要间接 import 到这些模块
 * 就会在模块求值期炸掉。
 *
 * 这里统一做一次可选访问：
 *   · Vite 构建下 `import.meta.env` 一定存在，取值与直接读完全一致；
 *   · 非 Vite 环境退化成空对象，调用方落到自己的默认值分支。
 *
 * 用法：`import { importMetaEnv } from '@/utils/importMetaEnv'`
 */
export const importMetaEnv = (import.meta as unknown as {
  env?: Record<string, string | undefined>
}).env ?? {}
