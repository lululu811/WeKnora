import { get } from "../utils/request";

/**
 * 一个工作台（业务域）的定义，由后端 `internal/types/workbench.go` 注册。
 *
 * 这是词表的唯一真相源：前端只保留组件**实现**（workspace/registry.ts），
 * 不保留 workbench / component 的映射表，避免 Go 与 TS 两份词表漂移。
 */
export interface WorkbenchDef {
  id: string;
  display_name: string;
  description?: string;
  /** 该工作台允许渲染的前端组件 key，例如 ['kline'] */
  components: string[];
  /** 该域的工具白名单；空数组表示不在 agent 自身 AllowedTools 之外额外限制 */
  allowed_tools: string[];
}

/**
 * 标记「在所有工作台可见」的 agent。
 *
 * 此类 agent 不能作为会话起点：会话绑定其首条消息的 agent，工作台再由该 agent
 * 推导；若首条消息用的是 shared agent，会话将没有工作台、也就没有面板。
 */
export const WORKBENCH_SHARED = 'shared';

/** 拉取全部已注册工作台。部署级静态数据，加载一次即可。 */
export function listWorkbenches(): Promise<{ data: WorkbenchDef[] }> {
  return get<{ data: WorkbenchDef[] }>('/api/v1/system/workspaces');
}
