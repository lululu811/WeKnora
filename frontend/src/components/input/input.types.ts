/**
 * 聊天输入区（Input-field）拆分后的共享类型。
 *
 * 这里只放「跨子组件传递的形状」，不放逻辑：三个子组件都是 Input-field.vue 的
 * 纯展示层外壳，真正的数据流（会话、提及、附件、模型）仍然留在父组件里。
 */
import type { Ref } from 'vue'
import type { MentionItem } from '@/types/mention'

/** 图片附件的本地预览项（父组件 uploadedImages 的元素类型）。 */
export interface UploadedImage {
  file: File
  preview: string
}

/**
 * 父组件持有的 DOM / 组件实例引用。
 *
 * 拆分前这些 ref 指向父组件模板里的元素；拆分后元素渲染在子组件内部，但父组件
 * 脚本（自动增高、焦点恢复、光标定位）仍需要拿到同一个实例对象。子组件在挂载时
 * 把实例回写进这个 ref，父组件代码因此一行都不用改 —— 数据流保持原样。
 */
export type ElRef<T = any> = Ref<T | null | undefined>

/** 输入区子组件共用的 i18n 注入 key 前缀集合（保持与父组件一致的取词方式）。 */
export type TranslateFn = (key: string) => string

/** 提及 chip 的展示辅助函数集合，由父组件实现后注入。 */
export interface MentionChipHelpers {
  getMentionChipClass: (item: MentionItem) => string
  getMentionIcon: (item: MentionItem) => string
  getImgSrc: (url: string) => string
}

/** 发送/停止按钮的可用性状态，由父组件根据 query 与锁状态推导。 */
export interface SendButtonState {
  /** 是否正在生成回复 */
  isReplying: boolean
  /** 生成中是否允许插入 steer */
  canSteer: boolean
  /** 输入框是否有内容（空内容禁止发送） */
  hasText: boolean
  /** 会话进行中禁止改动的锁 */
  locked: boolean
}
