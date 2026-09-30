# 跨人物知识库对比方案

## 目标

把本地 Obsidian 知识库导入 WeKnora，配一个**中立**智能体，横向对比多个交易者/分析员在同一主题上的观点与分歧。

不新增知识内容 —— 全部来自本地已有的库，对比发生在检索与回答阶段。

## 术语表

讨论中确立的术语，避免后续混淆：

| 术语 | 定义 | 不是什么 |
|---|---|---|
| **归属（provenance）** | 这段知识是谁说的 | 不是「谁写的文档」 |
| **理念（belief）** | 为什么这么做，可证伪的世界观 | 不是可执行指令。例：「趋势是唯一一把尺子」 |
| **规则（rule）** | 具体怎么做，可执行 | 不是世界观。例：「止损点设在买入逻辑失效的价格」 |
| **方法论（methodology）** | 一整套体系，由多条规则组成 | 不是单条规则。例：简放 3L 体系、Al Brooks 价格行为 |
| **成品库** | 本地库的 `wiki/`，已编译的概念页/实体页/摘要页 | — |
| **原文库** | 本地库的 `raw/`，未消化的原始转录与文章 | — |

**归属与性质是两个正交维度。** 同一个人的理念和规则都是他的，但跨人比较时必须先按性质分层，再按归属标注：

```
        │ 理念          │ 规则            │ 方法论
简放    │ 趋势是唯一…    │ 止损点设…       │ 3L交易体系
mo      │ 概率思维       │ 仓位控制…       │ —
```

## 关键事实（均经代码或数据验证）

1. **多库检索是一次统一融合，不是循环。** `knowledgebase_search.go:156` 一次性加载 `params.KnowledgeBaseIDs` 全部库，统一做检索、融合、rerank、`MatchCount` 截断。因此**按人分库不会损失对比能力**，反而能通过「只查该查的库」提高精度。
2. **`metadata` 不可用于检索过滤。** `internal/types/search.go:240-264` 的 `SearchParams` 只有 `KnowledgeIDs` / `TagIDs` / `KnowledgeBaseIDs` 三个过滤维度。`{"type": "wiki"}` 这类标签纯属描述。**这是成品与原文必须分库的唯一理由。**
3. **`type: wiki` 不是可用的知识库类型。** 常量 `KnowledgeBaseTypeWiki`（`knowledgebase.go:35`）在生产代码零引用，前端建库只提供 `document` / `faq`。wiki 实际是挂在 document 库上的 `indexing_strategy.wiki_enabled` 开关，走「原始文档 → LLM 重新编译成页面」的管道（`wiki_ingest.go`），**没有 markdown 导入路径**，`frontmatter` 全仓无解析，分类树由 LLM 现场发明（`wiki_ingest_taxonomy.go:24`）。每篇文档 4-6 次 LLM 调用。
4. **`docreader` 不剥离 frontmatter、不处理 `[[双链]]`。** `markdown_parser.py:231-244` 只做表格规范化。实测既有库中 chunk 内容原样以 YAML frontmatter 开头。
5. **本地 16 个库分属 4 个赛道，不同质。** 跨赛道横切不成立（一个讲 ECB 货币政策、一个讲止损点，无法对比）。
6. **2 个库是空壳**（sanxian、chenliitaz），导入无价值。
7. **11 个库无 git**（含 tutufang）。父目录 `knowledge_base/.git` 只有 1 次提交、2 个文件，未跟踪任何库内容。

## 本地库现状

| 库 | 赛道 | wiki/ | raw/ | 处置 |
|---|---|---|---|---|
| mo_knowledge | A股交易 | 1173 | 1714 | 试点 |
| jianfang-knowledge | A股交易 | 282 | 51 | 后续 |
| changsishan-knowledge | A股交易 | 55 | 246 | 后续 |
| fupeng-knowledge | 宏观/大宗 | 139 | 728 | 后续 |
| qishui_knowledege | 地缘政治 | 210 | **2360** | 后续 |
| kongshanlieren-knowledge | 产业/地缘 | 288 | 183 | 后续 |
| fuzong-knowledge | 主题投资 | 253 | 113 | **待定归属** |
| sanxian_knowledege | — | 0 | — | 空壳，不导 |
| chenliitaz_knowledge | — | 2 | — | 空壳，不导 |

`raw/` 与 `wiki/` 比例差异极大（1:0.2 到 1:11），统一策略不可行，分库才能各自处理。

## 方案

### 技术参数（沿用既有 `Zettaranc A股交易体系` 库配置）

- 类型：`document`
- 嵌入模型：`milkey/wemm-embedding-2b:Q4_K_M`（`source=local`）—— **多库检索要求共享同一模型，建库时必须显式指定**
- 切分：`chunk_size: 512`、`chunk_overlap: 80`、分隔符 `["\n\n", "\n", "。", "！", "？", ";", "；"]`
- 归属标记：`--metadata entity=<人名>`

### 库结构

每人两库：

- `<人名>-成品`：只导 `wiki/`，Agent 默认绑定
- `<人名>-原文`：导 `raw/`，**Agent 不绑**，需要溯源时在聊天界面手动勾选

### 导入前处理

剥离 frontmatter 的 `sources:` 字段（长文件路径是纯噪音，吃掉 `chunk_size` 预算且可能误召回）。`title:` / `type:` / `tags:` 保留 —— 它们对检索有正向作用。

### 智能体

- **Agent 1「交易纪律研究」**（中立）→ 绑 mo + jianfang + changsishan 的成品库
- **Agent 2「宏观地缘研究」**（中立）→ 绑 fupeng + qishui + kongshanlieren 的成品库

`kb_selection_mode: selected`，`system_prompt` 用**内联字段**（`internal/types/agent.go:326` 优先级高于 `system_prompt_id`），**无需改代码或重启服务**。

既有的 Z哥 智能体不动，继续 `selected` 绑原库。

## 执行方式

```bash
# 1. 建库
weknora kb create --name "mo-成品" \
  --embedding-model "milkey/wemm-embedding-2b:Q4_K_M"

# 2. 导入（先剥离 frontmatter 的 sources 字段）
weknora doc upload --recursive <本地库>/wiki --glob "*.md" \
  --kb <kb-id> --metadata entity=mo
```

## 风险

1. **本地库改了 WeKnora 不会跟着变。** 6 个源库中有多个无 git，重跑导入是唯一的同步手段，且是全量覆盖。
2. **跨库引用的 embedding 模型必须一致。** 换模型等于全部重建向量。
3. **检索噪音不可过滤。** 成品库与原文库一旦混装，无法在检索层拆开（见事实 2）。
4. **横切价值依赖同质性。** 赛道内部才有可比性；赛道之间强行横切会得到答非所问的结果。

## 待定

- fuzong-knowledge（主题投资）归属哪个 Agent，或暂不导入
- Agent 2 的 system prompt 全文
- 原文库是否真的需要建（可能长期闲置）

## 变更记录

- 2026-09-30：初稿，方案讨论收敛
