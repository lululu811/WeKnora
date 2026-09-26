/**
 * 插件注册表 - 管理所有指标插件
 *
 * 每个插件可以注册自己的路由和标注能力
 */
import type { FastifyInstance } from 'fastify';
import { annotateRoutes as zettarancAnnotateRoutes } from './zettaranc/annotate.js';

export interface Plugin {
  name: string;
  description: string;
  version: string;
  annotate?: boolean;  // 是否提供形态标注
}

// 已注册插件列表
export const registeredPlugins: Plugin[] = [
  {
    name: 'zettaranc',
    description: 'Z 哥交易体系形态识别（B1/S1/关键K/暴力K）',
    version: '1.0.0',
    annotate: true,
  },
];

/**
 * 注册所有插件路由
 */
export async function registerPluginRoutes(app: FastifyInstance): Promise<void> {
  // 注册插件列表 API
  app.get('/api/plugins', async () => {
    return {
      plugins: registeredPlugins,
      count: registeredPlugins.length,
    };
  });

  // 注册各插件的路由
  // Zettaranc 插件 - 使用 /api 前缀
  await app.register(zettarancAnnotateRoutes, { prefix: '/api' });

  // 未来插件可以这样注册：
  // await app.register(xxxAnnotateRoutes, { prefix: '/api' });
}
