/**
 * 智能体多态工作台（Workspace-per-Agent）类型定义
 */

export type WorkspaceType = 'kline' | 'none';

export interface PickItem {
  ticker: string;
  exchange: string;
  name?: string;
  pattern?: string;
  price?: number;
  reason?: string;
}
