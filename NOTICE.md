# NOTICE

## 本仓库是什么

这是一个 **社区 fork**，基于 [Tencent/WeKnora](https://github.com/Tencent/WeKnora) 的二次开发版本。

- **上游项目**：WeKnora（腾讯开源的知识库 RAG 框架）
- **上游仓库**：https://github.com/Tencent/WeKnora
- **上游版权**：Copyright (C) 2025 Tencent. All rights reserved.

本仓库 **非官方版本**，与腾讯公司无隶属关系，不代表腾讯的官方立场或背书。

## 许可

### 上游部分

沿用上游 `LICENSE` 原文，**未作任何修改**。上游代码以 **MIT License** 发布，版权归 Tencent 所有。

MIT 许可允许任何人使用、复制、修改、合并、发布、再分发及销售本项目副本，条件是**在所有副本或实质性部分中保留版权声明与许可声明**。本仓库完整保留了 `LICENSE` 文件，满足该条件。

### 第三方组件

上游包含若干以**不同协议**发布的第三方组件，其声明见：

- `THIRD_PARTY_NOTICES.md` —— 第三方组件清单与对应源码获取方式
- `licenses/` —— 第三方协议全文与来源清单（`licenses/sources/modules.tsv`）

其中值得注意的：

| 组件 | 协议 | 说明 |
|---|---|---|
| `go-sql-driver/mysql` | MPL-2.0 | 弱 copyleft，修改该组件文件时须以 MPL 开源 |
| `go-m1cpu` | MPL-2.0 | 同上 |
| OpenCC | Apache-2.0 | Apache-2.0 保留声明与 NOTICE 义务 |
| Wails | MIT | — |

**MPL-2.0 是文件级弱 copyleft**：它只约束这几个特定组件自身，不影响本仓库其余代码的许可选择。使用者需自行确保所用第三方组件符合其原始协议条款。

### 本 fork 新增部分

本仓库在 MIT 许可下继续发布本 fork 新增的代码。

## 商标

"WeKnora" 名称及相关标识的权利归原权利人所有。本仓库使用该项目名仅为描述代码来源，**不主张任何商标权利**。

## 数据

本仓库 **不包含任何金融数据**。`hithink-finance` 的本地 DuckDB 库（行情、财报、指数等）属于独立分发的数据产品，需使用者自行获取与准备，详见 [README.md](README.md)。

默认部署不挂载任何外部数据目录，未配置 `HITHINK_DB_DIR` 时金融相关工具不可用，但知识库与对话主流程不受影响。

## 贡献

欢迎 issue 与 PR。参与贡献即表示你同意你的贡献以与本仓库相同的 MIT 许可发布。
