# 01 - 项目启动和配置

> 学习 WeKnora 的第一步：把服务跑起来

---

## 🎯 学习目标

- 了解 WeKnora 的部署方式
- 掌握 Docker Compose 启动流程
- 配置 LLM 提供商
- 处理 SSRF 安全问题

---

## 📋 环境准备

### 系统要求

```
操作系统：macOS / Linux / Windows (WSL2)
Docker：20.10+
Docker Compose：2.0+
内存：建议 8GB+
磁盘：建议 10GB+
```

### 克隆项目

```bash
git clone https://github.com/Tencent/WeKnora.git
cd WeKnora
```

---

## 🚀 启动方式对比

WeKnora 提供多种启动方式：

| 模式 | 依赖 | 适用场景 | 推荐度 |
|---|---|---|---|
| **Docker Compose** | Docker | 完整功能体验 | ⭐⭐⭐⭐⭐ |
| **Lite 模式** | 无（SQLite + 内存） | 快速体验 | ⭐⭐⭐⭐ |
| **开发模式** | Docker + Go | 二次开发 | ⭐⭐⭐ |

**本笔记采用 Docker Compose 模式**（功能完整，最稳定）。

---

## 🐳 Docker Compose 启动

### Step 1: 准备配置文件

```bash
# 复制环境变量模板
cp .env.example .env

# 复制模型配置（可选）
cp config/models.json.example config/models.json
```

### Step 2: 配置 LLM

编辑 `.env` 文件，配置 LLM 提供商：

```bash
# 内置对话模型
LLM_MODEL_NAME=qwen3.7-plus
LLM_BASE_URL=https://coding.dashscope.aliyuncs.com/apps/anthropic
LLM_API_KEY=sk-sp-xxx
LLM_PROVIDER=anthropic
```

**常用 LLM 提供商**：

| 提供商 | LLM_PROVIDER | LLM_BASE_URL |
|---|---|---|
| OpenAI | `openai` | `https://api.openai.com/v1` |
| Anthropic | `anthropic` | `https://api.anthropic.com` |
| 阿里云百炼 | `aliyun` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| DeepSeek | `deepseek` | `https://api.deepseek.com/v1` |
| 智谱 AI | `zhipu` | `https://open.bigmodel.cn/api/paas/v4` |

### Step 3: 配置 SSRF 白名单（重要）

如果你的网络环境使用了代理/VPN，可能会遇到 SSRF 拦截：

```bash
# 在 .env 中添加
SSRF_WHITELIST=coding.dashscope.aliyuncs.com,api.minimaxi.com,198.18.0.0/15
```

**为什么需要 SSRF 白名单**：
- WeKnora 有 SSRF 防护机制，防止恶意请求内网地址
- 某些代理/VPN 会把域名解析到保留地址段（如 `198.18.0.0/15`）
- 需要把可信域名加入白名单

### Step 4: 启动服务

```bash
# 启动所有服务
docker-compose up -d

# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f app
```

### Step 5: 访问服务

```
前端 UI：http://localhost:80（默认）或 http://localhost:8081（如果 80 端口被占用）
后端 API：http://localhost:8080
健康检查：http://localhost:8080/health
```

---

## 🔧 常见问题

### 问题 1: 前端 80 端口被占用

**现象**：
```
Error: Bind for 0.0.0.0:80 failed: port is already allocated
```

**解决**：
```bash
# 在 .env 中修改前端端口
FRONTEND_PORT=8081

# 重启前端
docker-compose up -d frontend
```

### 问题 2: Docker 凭证助手找不到

**现象**：
```
error getting credentials - err: exec: "docker-credential-desktop": executable file not found
```

**解决**：
```bash
# 添加 Docker bin 到 PATH
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
```

### 问题 3: Lite 模式启动崩溃（m1cpu）

**现象**：
```
SIGSEGV: segmentation violation
github.com/shoenig/go-m1cpu._CFunc_initialize()
```

**原因**：`go-m1cpu` 库在 Go 1.26 + macOS 上有已知 bug

**解决**：使用 Docker Compose 模式，或升级依赖

---

## 📊 服务架构

Docker Compose 启动后会运行以下服务：

```
┌─────────────────────────────────────────────────────────┐
│  WeKnora 服务架构                                        │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │   frontend   │  │     app      │  │  docreader   │ │
│  │  (nginx:80)  │──│  (Go:8080)   │──│ (gRPC:50051) │ │
│  └──────────────┘  └──────────────┘  └──────────────┘ │
│                           │                             │
│         ┌─────────────────┼─────────────────┐           │
│         ↓                 ↓                 ↓           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │   postgres   │  │    redis     │  │    minio     │ │
│  │  (MySQL/PG)  │  │   (缓存)     │  │  (对象存储)  │ │
│  └──────────────┘  └──────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────┘
```

| 服务 | 镜像 | 端口 | 作用 |
|---|---|---|---|
| **frontend** | `wechatopenai/weknora-ui` | 80/8081 | Web UI |
| **app** | `wechatopenai/weknora-app` | 8080 | 后端 API |
| **docreader** | `wechatopenai/weknora-docreader` | 50051 | 文档解析 |
| **postgres** | `paradedb/paradedb` | 5432 | 数据库 |
| **redis** | `redis:7.0-alpine` | 6379 | 缓存 |

---

## ✅ 验证启动

### 检查服务状态

```bash
docker-compose ps
```

**预期输出**：
```
NAME                STATUS
WeKnora-frontend    Up (healthy)
WeKnora-app         Up (healthy)
WeKnora-docreader   Up (healthy)
WeKnora-postgres    Up (healthy)
WeKnora-redis       Up
```

### 测试 API

```bash
# 健康检查
curl http://localhost:8080/health
# 预期：{"status":"ok"}
```

### 访问前端

打开浏览器访问 `http://localhost:8081`，应该看到 WeKnora 登录页面。

---

## 🎯 下一步

服务启动成功后，继续学习：

→ [02 - ReAct 循环概述](02-react-overview.md)

---

## 💡 思考题

1. 为什么 WeKnora 推荐使用 Docker Compose 而不是 Lite 模式？
2. SSRF 防护的作用是什么？为什么需要白名单？
3. 如果要添加一个新的 LLM 提供商，需要修改哪些配置？
