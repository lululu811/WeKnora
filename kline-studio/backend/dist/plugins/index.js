import { annotateRoutes as zettarancAnnotateRoutes } from './zettaranc/annotate.js';
// 已注册插件列表
export const registeredPlugins = [
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
export async function registerPluginRoutes(app) {
    // 注册插件列表 API
    app.get('/api/plugins', async () => {
        return {
            plugins: registeredPlugins,
            count: registeredPlugins.length,
        };
    });
    // 注册各插件的路由
    // Zettaranc 插件 - 使用 /api/annotate 路径（向后兼容）
    await zettarancAnnotateRoutes(app);
    // 未来插件可以这样注册：
    // await xxxAnnotateRoutes(app);
}
