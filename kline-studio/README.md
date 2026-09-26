# kline-studio

> A 股专业级 K 线图平台 - 支持插件化指标叠加

## 🎯 项目定位

**通用 K 线图平台**，可以叠加各种个人指标插件。Zettaranc 交易体系是第一个插件。

```
kline-studio（通用 K 线平台）
├── 插件：Zettaranc 指标（B1/S1/关键K/暴力K...）✅ 已实现
├── 插件：xxx 指标（未来）
└── 基础能力：K 线展示、缩放、十字光标、技术指标...
```

## 🏗️ 架构

```
kline-studio/
├── frontend/              # React + KLineChart Pro（专业 K 线展示）
│   ├── src/pages/
│   │   ├── HomePage.tsx
│   │   └── KLinePage.tsx
│   └── src/components/
│       ├── StockHeader.tsx
│       └── SymbolList.tsx
│
├── backend/               # Fastify + DuckDB
│   └── src/
│       ├── routes/        # 基础路由（K 线、报价、代码）
│       ├── services/      # 基础服务（DuckDB、名称缓存）
│       └── plugins/       # 🆕 插件目录
│           ├── index.ts   # 插件注册表
│           └── zettaranc/ # Zettaranc 插件
│               ├── annotate.ts   # 形态标注 API
│               ├── annotator.ts  # 形态识别器
│               └── indicators.ts # 技术指标计算
│
└── package.json
```

## 🚀 快速启动

```bash
# 安装依赖
npm install

# 启动开发服务器（前端 + 后端）
npm run dev

# 访问
# 前端：http://localhost:5173
# 后端：http://localhost:4000
```

## 📡 API

### 基础 API

```bash
# K 线数据
GET /api/kline?symbol=600519.SH&adjust=forward

# 实时报价
GET /api/snapshot?symbol=600519.SH

# 股票搜索
GET /api/symbols/search?q=茅台
```

### 插件 API

```bash
# 获取所有插件
GET /api/plugins

返回：
{
  "plugins": [
    {
      "name": "zettaranc",
      "description": "Z 哥交易体系形态识别（B1/S1/关键K/暴力K）",
      "version": "1.0.0",
      "annotate": true
    }
  ],
  "count": 1
}

# Zettaranc 形态标注
GET /api/annotate?symbol=600519.SH&days=120&patterns=b1,key_k,s1

返回：
{
  "symbol": "600519.SH",
  "annotation_count": 7,
  "annotations": [
    {
      "type": "key_k",
      "date": "2026-08-06",
      "price": 1308.55,
      "text": "关键K (十字星)",
      "confidence": 0.6
    }
  ]
}
```

## 🔌 插件开发

要添加新插件，在 `backend/src/plugins/` 下创建目录：

```typescript
// plugins/my-indicator/index.ts
export async function myIndicatorRoutes(app: FastifyInstance): Promise<void> {
  app.get('/my-indicator', async (req, reply) => {
    // 你的指标逻辑
  });
}

// plugins/index.ts
import { myIndicatorRoutes } from './my-indicator/index.js';

export const registeredPlugins: Plugin[] = [
  // ...
  {
    name: 'my-indicator',
    description: '我的指标',
    version: '1.0.0',
    annotate: true,
  },
];

export async function registerPluginRoutes(app: FastifyInstance): Promise<void> {
  // ...
  await app.register(myIndicatorRoutes, { prefix: '/api' });
}
```

## 📦 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 前端 | React 18 + KLineChart Pro | 专业 K 线图库 |
| 后端 | Fastify 5 + TypeScript | 高性能 Web 框架 |
| 数据 | DuckDB | 直连本地 `~/.hithink-finance/market.duckdb` |
| 指标 | 纯 TypeScript 实现 | 无外部依赖 |

## 📊 数据源

直连同花顺 Financial-API 的本地 DuckDB 数据库：

- **路径**：`~/.hithink-finance/market.duckdb`
- **表**：`v_daily_qfq`（前复权日 K 线）、`dim_symbol`（股票代码）
- **数据**：5569 只 A 股，1033 万条记录

## 🎨 Zettaranc 插件

Z 哥交易体系形态识别：

| 形态 | 识别规则 | 含义 |
|------|----------|------|
| **B1 建仓波** | 建仓波后 J 值<13，缩量回调 | 核心买入信号 |
| **S1 信号** | 高位放量，长上影线 | 卖出预警 |
| **关键K** | 十字星 + 缩量 | 转折信号 |
| **暴力K** | 底部倍量长阳/长阴 | 突破信号 |

## 📝 开发计划

- [x] 基础 K 线展示
- [x] 插件化架构
- [x] Zettaranc 形态识别
- [ ] 前端集成标注显示
- [ ] 教学案例系统
- [ ] WeKnora 知识库集成
- [ ] 更多指标插件

## 📄 License

MIT
