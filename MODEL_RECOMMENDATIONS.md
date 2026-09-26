# 🤖 WeKnora 模型配置指南

## 📋 当前配置状态

### ✅ 已完成的改进

1. **修复了 API Key 硬编码问题** - 从 `models.json` 中移除明文 API key
2. **创建了完整的内置模型配置** - `builtin_models.yaml` 包含推荐的模型组合

### ⚠️ 需要你操作的事项

**必须在 `.env` 文件中设置以下环境变量**：

```bash
# 阿里云百炼 API Key（必需）
DASHSCOPE_API_KEY=sk-your-api-key-here
```

---

## 🎯 推荐的模型配置

### 基础配置（最小必需）

适合：开发、测试、小规模部署

| 模型类型 | 推荐模型 | 用途 | 成本 |
|---------|---------|------|------|
| **KnowledgeQA** | qwen-plus | 主力对话模型 | 中等 |
| **Embedding** | text-embedding-v3 | 文档向量化 | 低 |
| **Rerank** | gte-rerank | 检索重排序 | 低 |

**总计**: 3 个模型，月成本约 ¥100-500（取决于使用量）

---

### 生产配置（推荐）

适合：生产环境、多用户、高并发

| 模型类型 | 推荐模型 | 用途 | 成本 |
|---------|---------|------|------|
| **KnowledgeQA** | qwen-plus | 主力对话模型 | 中等 |
| **KnowledgeQA** | qwen-turbo | 备用/快速响应 | 低 |
| **Embedding** | text-embedding-v3 | 文档向量化 | 低 |
| **Rerank** | gte-rerank | 检索重排序 | 低 |

**总计**: 4 个模型，月成本约 ¥200-1000

---

### 高级配置（可选）

适合：需要更强推理能力、图片理解等高级功能

| 模型类型 | 推荐模型 | 用途 | 成本 |
|---------|---------|------|------|
| **KnowledgeQA** | qwen-plus | 主力对话模型 | 中等 |
| **KnowledgeQA** | deepseek-v3 | 复杂推理任务 | 中等 |
| **Embedding** | text-embedding-v3 | 文档向量化 | 低 |
| **Rerank** | gte-rerank | 检索重排序 | 低 |
| **VLM** | qwen-vl-plus | 图片理解（可选） | 中等 |

**总计**: 5 个模型，月成本约 ¥500-2000

---

## 🔧 模型详细说明

### 1. KnowledgeQA（对话模型）

#### qwen-plus（推荐 ⭐⭐⭐⭐⭐）
- **提供商**: 阿里云百炼
- **上下文窗口**: 128K tokens
- **特点**: 性价比高，中文能力强，适合 RAG 场景
- **价格**: 约 ¥0.008/1K tokens（输入），¥0.02/1K tokens（输出）
- **适用场景**: 日常问答、知识检索、文档摘要

#### qwen-turbo（推荐 ⭐⭐⭐⭐）
- **提供商**: 阿里云百炼
- **上下文窗口**: 128K tokens
- **特点**: 响应快，成本低
- **价格**: 约 ¥0.003/1K tokens（输入），¥0.006/1K tokens（输出）
- **适用场景**: 快速响应、简单问答、成本敏感场景

#### deepseek-v3（可选 ⭐⭐⭐⭐）
- **提供商**: DeepSeek
- **上下文窗口**: 64K tokens
- **特点**: 强大的推理能力，数学和代码能力强
- **价格**: 约 ¥0.01/1K tokens（输入），¥0.02/1K tokens（输出）
- **适用场景**: 复杂推理、代码生成、数学问题

---

### 2. Embedding（向量嵌入模型）

#### text-embedding-v3（推荐 ⭐⭐⭐⭐⭐）
- **提供商**: 阿里云百炼
- **向量维度**: 1024（推荐）或 1536
- **特点**: 支持中英文，多语言能力强
- **价格**: 约 ¥0.0007/1K tokens
- **适用场景**: 文档向量化、语义搜索

#### bge-m3（可选 ⭐⭐⭐⭐）
- **提供商**: 智源 AI（开源）
- **向量维度**: 1024
- **特点**: 开源模型，可自部署，无 API 调用成本
- **价格**: 免费（自部署需服务器成本）
- **适用场景**: 自部署、大规模向量化、成本敏感

---

### 3. Rerank（重排序模型）

#### gte-rerank（推荐 ⭐⭐⭐⭐⭐）
- **提供商**: 阿里云百炼
- **特点**: 显著提升检索精度，支持中英文
- **价格**: 约 ¥0.0007/1K tokens
- **适用场景**: RAG 检索重排序、语义匹配

#### bge-reranker-v2-m3（可选 ⭐⭐⭐⭐）
- **提供商**: 智源 AI（开源）
- **特点**: 开源模型，可自部署
- **价格**: 免费（自部署需服务器成本）
- **适用场景**: 自部署、成本敏感

---

### 4. VLM（视觉语言模型，可选）

#### qwen-vl-plus（可选 ⭐⭐⭐⭐）
- **提供商**: 阿里云百炼
- **特点**: 图片理解能力强，支持中文
- **价格**: 约 ¥0.008/1K tokens（输入），¥0.02/1K tokens（输出）
- **适用场景**: 图片问答、图表分析、OCR

---

## 💰 成本估算

### 假设场景
- 每天处理 1000 次问答
- 每次问答平均 2000 tokens 输入 + 500 tokens 输出
- 每天处理 100 个文档，每个文档 5000 tokens

### 月度成本估算

| 模型 | 使用量 | 单价 | 月成本 |
|------|--------|------|--------|
| qwen-plus | 60M tokens/月 | ¥0.01/1K | ¥600 |
| text-embedding-v3 | 15M tokens/月 | ¥0.0007/1K | ¥10 |
| gte-rerank | 30M tokens/月 | ¥0.0007/1K | ¥21 |
| **总计** | - | - | **¥631/月** |

**注**: 实际成本取决于使用量，以上仅为估算。

---

## 🚀 快速开始

### 步骤 1：设置环境变量

在 `.env` 文件中添加：

```bash
# 阿里云百炼 API Key（必需）
DASHSCOPE_API_KEY=sk-your-api-key-here

# 可选：DeepSeek API Key（如果使用 deepseek-v3）
# DEEPSEEK_API_KEY=sk-your-deepseek-key-here
```

**获取 API Key**：
- 阿里云百炼：https://dashscope.console.aliyun.com/
- DeepSeek：https://platform.deepseek.com/

### 步骤 2：验证配置

```bash
# 验证配置文件
go run ./cmd/config validate

# 查看当前配置（脱敏）
go run ./cmd/config show
```

### 步骤 3：启动服务

```bash
# 启动 WeKnora
./weknora

# 或使用 Docker Compose
docker-compose up -d
```

### 步骤 4：测试模型

1. 打开 WeKnora Web UI
2. 进入「模型管理」页面
3. 确认看到以下模型：
   - ✅ Qwen Plus
   - ✅ Qwen Turbo
   - ✅ Text Embedding V3
   - ✅ GTE Rerank

---

## 📊 模型选择建议

### 按场景推荐

#### 场景 1：个人开发/学习
- **推荐配置**: 基础配置
- **预算**: ¥100-300/月
- **说明**: qwen-plus + text-embedding-v3 + gte-rerank 足够

#### 场景 2：小团队（5-10 人）
- **推荐配置**: 生产配置
- **预算**: ¥300-1000/月
- **说明**: 增加 qwen-turbo 作为备用，提升可用性

#### 场景 3：企业级部署（100+ 人）
- **推荐配置**: 高级配置
- **预算**: ¥1000-5000/月
- **说明**: 考虑自部署开源模型降低成本

#### 场景 4：成本敏感
- **推荐配置**: 自部署开源模型
- **预算**: 服务器成本（约 ¥500-2000/月）
- **模型**: bge-m3 + bge-reranker + 本地 LLM
- **说明**: 需要 GPU 服务器，但长期成本低

---

## 🔍 常见问题

### Q1: 为什么没有配置 OpenAI/Claude？

**A**: 国内用户访问 OpenAI/Claude 需要代理，且成本较高。阿里云百炼提供兼容协议，国内访问稳定，成本更低。如需使用 OpenAI/Claude，可以取消注释 `builtin_models.yaml` 中的示例配置。

### Q2: 可以只用一个模型吗？

**A**: 不建议。RAG 系统至少需要：
- 1 个 LLM 模型（问答）
- 1 个 Embedding 模型（向量化）
- 1 个 Rerank 模型（重排序，强烈推荐）

只使用一个模型会导致检索精度低、响应慢。

### Q3: 如何切换默认模型？

**A**: 修改 `builtin_models.yaml` 中的 `is_default: true` 字段，然后重启服务。

### Q4: 模型配置错了怎么办？

**A**: 
1. 使用 `weknora config validate` 验证配置
2. 检查环境变量是否正确设置
3. 查看启动日志中的错误信息
4. 重启服务使配置生效

### Q5: 如何添加更多模型？

**A**: 
1. 编辑 `config/builtin_models.yaml`
2. 添加新的模型配置
3. 重启服务
4. 在 Web UI 中确认模型已加载

---

## 📚 参考资源

- [阿里云百炼文档](https://help.aliyun.com/zh/dashscope/)
- [DeepSeek 文档](https://platform.deepseek.com/api-docs/)
- [BGE 模型](https://github.com/FlagOpen/FlagEmbedding)
- [WeKnora 配置指南](./CONFIG_IMPROVEMENTS.md)

---

## ✅ 检查清单

启动前请确认：

- [ ] 已设置 `DASHSCOPE_API_KEY` 环境变量
- [ ] 已配置至少 1 个 KnowledgeQA 模型
- [ ] 已配置至少 1 个 Embedding 模型
- [ ] 已配置至少 1 个 Rerank 模型（强烈推荐）
- [ ] 已运行 `weknora config validate` 验证配置
- [ ] 已启动服务并确认模型加载成功

---

**最后更新**: 2026-09-25  
**配置版本**: v1.0
