/**
 * 共享类型：KnowledgeBase 详情页拆分出来的子组件（KBDetailHeader / KBDocList）
 * 与父组件之间的契约。这里只放跨组件边界传递的结构，业务逻辑一律留在父组件。
 */

/** 详情页顶部的视图标签页。wiki / graph 仅在 wiki 型 KB 上存在。 */
export const KB_DETAIL_TABS = ['documents', 'wiki', 'graph', 'gallery'] as const
export type KbDetailTab = typeof KB_DETAIL_TABS[number]

/** 面包屑下拉里可选的 KB 条目（只用到 id / name，type 保留给未来分组）。 */
export interface KbDetailOption {
  id: string
  name: string
  type?: string
}

/** 单个视图标签页的展示契约，由父组件按 isWiki / wikiStatus 动态组装。 */
export interface KbDetailTabItem {
  key: KbDetailTab
  icon: string
  label: string
  tip: string
  /** 后台仍有索引任务时，标签上显示 loading 指示。 */
  indexing?: boolean
}

/** 文档筛选面板里的一个筛选项（文件类型 / 解析状态 / 来源）。 */
export interface KbDetailFilterOption {
  label: string
  value: string
}

/** 标签筛选用的标签条目。 */
export interface KbDetailTagOption {
  id: string
  name: string
}

/** 面包屑里的目录层级，由父组件 folderBreadcrumbs 计算后传入。 */
export interface KbDetailFolderCrumb {
  path: string
  name: string
}
