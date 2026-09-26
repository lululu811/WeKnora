# WeKnora知识库迁移指南

## 📋 迁移概览

### 源数据
- **位置**: `/Users/chenlei/003_knowledge/knowledge_base`
- **知识库数量**: 18个(排除course-knowledge)
- **总文件数**: 28,348个(过滤后)
  - wiki层: 9,326个(结构化知识)
  - raw层: 17,034个(原始素材)
  - 跳过: 1,988个(低价值内容)

### 目标环境
- **WeKnora**: 已部署,运行在 http://localhost:8080
- **前端**: http://localhost:8081
- **向量库**: Postgres (ParadeDB),已配置
- **知识图谱**: Neo4j,未启用(可选)

---

## 🚀 快速开始

### 步骤1: 确认WeKnora API访问

首先需要确认WeKnora API的认证方式。有两种可能:

**方案A: 无认证(本地开发模式)**
```bash
# 测试API是否可访问
curl http://localhost:8080/health
# 如果返回 {"status":"ok"},说明API可访问
```

**方案B: 需要API Key**
```bash
# 在WeKnora前端创建API Key
# 访问 http://localhost:8081
# 登录 → 设置 → API Keys → 创建新Key
```

### 步骤2: 配置迁移脚本

编辑 `scripts/migrate_all_to_weknora.py`:

```python
# 第12-14行,配置WeKnora连接信息
WEKNORA_BASE_URL = "http://localhost:8080"
WEKNORA_API_KEY = "your_api_key_here"  # 如果无认证,留空即可
```

### 步骤3: 完善迁移脚本(接入真实API)

当前脚本使用MOCK客户端,需要替换为真实API调用。

**需要实现的API调用:**

1. **创建知识库**
```python
POST /api/v1/knowledge-bases
Body: {
  "name": "research-reports",
  "description": "从Obsidian迁移的金融研究知识库",
  "type": "document"
}
```

2. **上传文件创建Knowledge**
```python
POST /api/v1/knowledge-bases/{kb_id}/knowledge/file
Form-Data:
  - file: (multipart file)
  - metadata: (JSON string)
  - channel: "api"
```

**参考代码:**
```python
import requests

class WeKnoraClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.headers = {}
        if api_key:
            self.headers['X-API-Key'] = api_key
    
    def create_knowledge_base(self, name: str, description: str) -> str:
        url = f"{self.base_url}/api/v1/knowledge-bases"
        data = {
            "name": name,
            "description": description,
            "type": "document"
        }
        response = requests.post(url, json=data, headers=self.headers)
        response.raise_for_status()
        return response.json()['data']['id']
    
    def create_knowledge(self, kb_id: str, file_path: str, 
                        metadata: dict, tags: list) -> str:
        url = f"{self.base_url}/api/v1/knowledge-bases/{kb_id}/knowledge/file"
        
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {
                'metadata': json.dumps(metadata),
                'channel': 'api',
                'tags': ','.join(tags)
            }
            response = requests.post(url, files=files, data=data, 
                                   headers=self.headers)
        
        response.raise_for_status()
        return response.json()['data']['id']
```

### 步骤4: 运行迁移

```bash
# 确保WeKnora正在运行
docker ps | grep WeKnora

# 运行迁移脚本
python3 scripts/migrate_all_to_weknora.py

# 预计时间: 2-4小时(取决于网络和文件大小)
```

### 步骤5: 验证迁移结果

1. **访问前端**: http://localhost:8081
2. **查看知识库**: 应该看到18个知识库
3. **测试检索**: 搜索几个关键概念,验证检索效果

---

## 🔧 可选优化

### 优化1: 启用Neo4j知识图谱

如果需要构建实体关系图谱(基于wiki_links):

```bash
# 启动Neo4j
docker-compose up -d neo4j

# 修改.env
NEO4J_ENABLE=true

# 重启WeKnora app
docker-compose restart app
```

### 优化2: 切换到Qdrant向量库

如果Postgres性能不足(26K条目应该够用):

```bash
# 启动Qdrant
docker-compose up -d qdrant

# 修改.env
RETRIEVE_DRIVER=qdrant

# 重启WeKnora app
docker-compose restart app
```

### 优化3: 配置混合检索 + Rerank

```bash
# 在.env中配置Rerank模型
RERANK_MODEL_NAME=bge-reranker-v2-m3
RERANK_PROVIDER=generic
RERANK_BASE_URL=http://localhost:11434/v1
```

---

## 📊 迁移统计

### 各知识库规模

| 知识库 | wiki | raw | 总计 |
|--------|------|-----|------|
| research-reports | 5,835 | 8,306 | 14,141 |
| fundamental-research | 1,140 | 2,520 | 3,660 |
| mo_knowledge | 1,164 | 1,599 | 2,763 |
| qishui_knowledege | 190 | 1,889 | 2,079 |
| zettaranc-knowledge | 110 | 558 | 668 |
| 其他13个 | 887 | 2,162 | 3,049 |
| **总计** | **9,326** | **17,034** | **26,360** |

### 预计资源消耗

- **向量库存储**: 约5-10GB (26K条目 × 200-400KB/条目)
- **Embedding时间**: 约2-4小时(使用云端API)
- **Postgres负载**: 低(26K条目完全可承受)

---

## 🐛 故障排查

### 问题1: API返回401 Unauthorized
**原因**: 需要API Key
**解决**: 在前端创建API Key,配置到脚本中

### 问题2: 文件上传失败
**原因**: 文件格式不支持或文件过大
**解决**: 检查文件类型,WeKnora支持Markdown/PDF/Word等

### 问题3: Embedding失败
**原因**: Embedding模型未配置或API Key无效
**解决**: 检查.env中的EMBEDDING_*配置

### 问题4: 检索效果差
**原因**: chunk参数不合适或缺少Rerank
**解决**: 调整chunk_size/overlap,配置Rerank模型

---

## 📝 后续工作

1. **验证检索质量**: 测试常见问题,检查检索结果
2. **调优参数**: 根据实际效果调整chunk和检索参数
3. **定期同步**: 设计增量同步流程(每周/每月)
4. **知识图谱**: 如果需要,启用Neo4j并重建图谱

---

## 🔗 参考资源

- WeKnora文档: https://github.com/Tencent/WeKnora
- WeKnora API: http://localhost:8080/swagger/index.html
- 迁移脚本: `scripts/migrate_all_to_weknora.py`
- 配置文件: `.env`, `docker-compose.yml`
