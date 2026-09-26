#!/usr/bin/env python3
"""
Obsidian → WeKnora 迁移脚本
将 research-reports/wiki/ 下的 Obsidian 知识库迁移到 WeKnora
"""
import os
import re
import yaml
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime

# 配置
OBSIDIAN_ROOT = Path("/Users/chenlei/003_knowledge/knowledge_base/research-reports")
WIKI_DIR = OBSIDIAN_ROOT / "wiki"
WEKNORA_BASE_URL = "http://localhost:18080"
WEKNORA_API_KEY = "your_api_key_here"  # 替换成你的API key
KNOWLEDGE_BASE_NAME = "金融研究知识库-wiki"
KNOWLEDGE_BASE_DESC = "从Obsidian迁移的金融研究知识库,包含概念、实体、资料摘要和综合分析"

# 模拟WeKnora客户端(实际用时替换成真实的SDK调用)
class WeKnoraClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        # TODO: 初始化真实的HTTP客户端

    def create_knowledge_base(self, name: str, description: str) -> str:
        """创建知识库,返回knowledge_base_id"""
        print(f"[MOCK] 创建知识库: {name}")
        return "mock_kb_id_12345"

    def create_knowledge(self, kb_id: str, title: str, content: str,
                        metadata: Dict, tags: List[str], source_file: str) -> str:
        """创建Knowledge条目,返回knowledge_id"""
        print(f"[MOCK] 创建Knowledge: {title} (from {source_file})")
        return f"mock_k_{hash(title)}"

def parse_frontmatter(content: str) -> Tuple[Dict, str]:
    """
    解析Obsidian Markdown的frontmatter
    返回: (frontmatter_dict, body_content)
    """
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        frontmatter = yaml.safe_load(parts[1])
        body = parts[2].strip()
        return frontmatter or {}, body
    except yaml.YAMLError as e:
        print(f"  [WARN] YAML解析失败: {e}")
        return {}, content

def extract_wiki_links(content: str) -> List[str]:
    """
    提取Obsidian的[[双向链接]]
    返回: 链接目标列表
    """
    # 匹配 [[链接]] 或 [[链接|显示文本]]
    pattern = r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
    matches = re.findall(pattern, content)
    return [m.strip() for m in matches]

def extract_first_paragraph(body: str) -> str:
    """提取正文第一段作为description"""
    lines = body.split('\n')
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('>'):
            return line[:200]  # 限制长度
    return ""

def should_skip_file(frontmatter: Dict, body: str) -> Tuple[bool, str]:
    """
    判断是否应该跳过该文件
    返回: (是否跳过, 原因)
    """
    # 规则1: 只导入finished状态
    status = frontmatter.get('status', '')
    if status and status != 'finished':
        return True, f"status={status}"

    # 规则2: 排除"待补充"占位符
    if '待补充' in body and len(body.strip()) < 100:
        return True, "placeholder content"

    # 规则3: 排除空内容
    if len(body.strip()) < 50:
        return True, "empty content"

    return False, ""

def extract_raw_metadata(file_path: Path, frontmatter: Dict, body: str) -> Dict:
    """
    从raw/文件提取元数据(日期、作者、类型等)
    """
    metadata = {}

    # 从文件名提取日期(如: 04-14_军工更新.md → 2026-04-14)
    filename = file_path.stem
    date_match = re.match(r'(\d{2})-(\d{2})_(.+)', filename)
    if date_match:
        month, day, title = date_match.groups()
        # 从父目录推断年份(如: 02-papers/2026/2026-04/)
        parent_path = str(file_path.parent)
        year_match = re.search(r'/(20\d{2})/', parent_path)
        if year_match:
            year = year_match.group(1)
            metadata['date'] = f"{year}-{month}-{day}"
        metadata['raw_title'] = title

    # 从内容提取作者和类型
    if '**作者**:' in body:
        author_match = re.search(r'\*\*作者\*\*:\s*(.+?)(?:\n|$)', body)
        if author_match:
            metadata['author'] = author_match.group(1).strip()

    if '**类型**:' in body:
        type_match = re.search(r'\*\*类型\*\*:\s*(.+?)(?:\n|$)', body)
        if type_match:
            metadata['content_type'] = type_match.group(1).strip()

    # 提取ID
    if '**ID**:' in body:
        id_match = re.search(r'\*\*ID\*\*:\s*(\d+)', body)
        if id_match:
            metadata['source_id'] = id_match.group(1)

    return metadata

def migrate_wiki_file(file_path: Path, wiki_type: str, client: WeKnoraClient,
                     kb_id: str) -> Optional[Dict]:
    """
    迁移单个wiki文件
    返回: 迁移结果信息
    """
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"  [ERROR] 读取失败: {e}")
        return None

    # 解析frontmatter
    frontmatter, body = parse_frontmatter(content)

    # 应用过滤规则
    should_skip, reason = should_skip_file(frontmatter, body)
    if should_skip:
        print(f"  [SKIP] {file_path.name}: {reason}")
        return None

    # 提取信息
    title = frontmatter.get('title', file_path.stem)
    tags = frontmatter.get('tags', [])
    if isinstance(tags, str):
        tags = [tags]

    # 添加类型标签
    tags.append(f"wiki/{wiki_type}")
    tags.append("status/finished")  # 标记为finished

    # 提取双向链接
    wiki_links = extract_wiki_links(body)

    # 构建metadata
    metadata = {
        'source': f'research-reports/wiki/{wiki_type}',
        'source_file': str(file_path.relative_to(OBSIDIAN_ROOT)),
        'type': frontmatter.get('type', wiki_type),
        'status': frontmatter.get('status', 'finished'),
        'created': frontmatter.get('created', ''),
        'last_updated': frontmatter.get('last_updated', ''),
        'aliases': frontmatter.get('aliases', []),
        'wiki_links': wiki_links,  # 双向链接列表
        'complexity': frontmatter.get('complexity', ''),
        'entity_type': frontmatter.get('entity_type', ''),
    }

    # 如果是concept/entity,添加sources信息
    if 'sources' in frontmatter:
        metadata['original_sources'] = frontmatter['sources']

    # 提取description
    description = extract_first_paragraph(body)

    # 调用WeKnora API创建Knowledge
    knowledge_id = client.create_knowledge(
        kb_id=kb_id,
        title=title,
        content=body,
        metadata=metadata,
        tags=tags,
        source_file=str(file_path.relative_to(OBSIDIAN_ROOT))
    )

    return {
        'knowledge_id': knowledge_id,
        'title': title,
        'type': wiki_type,
        'tags': tags,
        'wiki_links_count': len(wiki_links),
    }

def migrate_raw_file(file_path: Path, client: WeKnoraClient,
                    kb_id: str) -> Optional[Dict]:
    """
    迁移单个raw/文件
    返回: 迁移结果信息
    """
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"  [ERROR] 读取失败: {e}")
        return None

    # 解析frontmatter(如果有的话)
    frontmatter, body = parse_frontmatter(content)

    # 应用基本过滤(排除空内容)
    if len(body.strip()) < 100:
        print(f"  [SKIP] {file_path.name}: empty content")
        return None

    # 提取元数据
    raw_metadata = extract_raw_metadata(file_path, frontmatter, body)

    # 标题
    title = raw_metadata.get('raw_title', file_path.stem)
    if len(title) > 100:
        title = title[:100]

    # 标签
    tags = ["raw-source", "daily-update"]
    if 'author' in raw_metadata:
        tags.append(f"author/{raw_metadata['author']}")
    if 'content_type' in raw_metadata:
        tags.append(f"type/{raw_metadata['content_type']}")

    # 构建metadata
    metadata = {
        'source': 'research-reports/raw',
        'source_file': str(file_path.relative_to(OBSIDIAN_ROOT)),
        'layer': 'raw',  # 标记为raw层
        **raw_metadata
    }

    # 提取description
    description = extract_first_paragraph(body)

    # 调用WeKnora API创建Knowledge
    knowledge_id = client.create_knowledge(
        kb_id=kb_id,
        title=title,
        content=body,
        metadata=metadata,
        tags=tags,
        source_file=str(file_path.relative_to(OBSIDIAN_ROOT))
    )

    return {
        'knowledge_id': knowledge_id,
        'title': title,
        'type': 'raw-source',
        'date': raw_metadata.get('date', ''),
        'author': raw_metadata.get('author', ''),
    }

def main():
    print("=" * 60)
    print("Obsidian → WeKnora 迁移工具")
    print("=" * 60)

    # 初始化客户端
    client = WeKnoraClient(WEKNORA_BASE_URL, WEKNORA_API_KEY)

    # 创建知识库
    print("\n[1/4] 创建知识库...")
    kb_id = client.create_knowledge_base(KNOWLEDGE_BASE_NAME, KNOWLEDGE_BASE_DESC)
    print(f"  知识库ID: {kb_id}")

    # ========== 第一层:迁移wiki文件 ==========
    print("\n[2/4] 迁移wiki文件(核心知识)...")
    wiki_results = {
        'concepts': [],
        'entities': [],
        'sources': [],
        'syntheses': [],
    }
    wiki_skipped = 0

    wiki_types = ['concepts', 'entities', 'sources', 'syntheses']
    for wiki_type in wiki_types:
        wiki_dir = WIKI_DIR / wiki_type
        if not wiki_dir.exists():
            print(f"  [SKIP] {wiki_type} 目录不存在")
            continue

        md_files = list(wiki_dir.glob("*.md"))
        print(f"\n  处理 {wiki_type}: {len(md_files)} 个文件")

        for i, md_file in enumerate(md_files, 1):
            if i % 100 == 0:
                print(f"    进度: {i}/{len(md_files)}")

            result = migrate_wiki_file(md_file, wiki_type, client, kb_id)
            if result:
                wiki_results[wiki_type].append(result)
            else:
                wiki_skipped += 1

    # ========== 第二层:迁移raw文件(原始素材) ==========
    print("\n[3/4] 迁移raw文件(原始素材)...")
    raw_results = []
    raw_skipped = 0

    raw_dir = OBSIDIAN_ROOT / "raw"
    if raw_dir.exists():
        # 遍历所有子目录
        for subdir in ['02-papers', '03-papers-yy', '09-archive']:
            subdir_path = raw_dir / subdir
            if not subdir_path.exists():
                continue

            md_files = list(subdir_path.rglob("*.md"))
            # 排除INDEX.md
            md_files = [f for f in md_files if f.name != 'INDEX.md']

            print(f"\n  处理 {subdir}: {len(md_files)} 个文件")

            for i, md_file in enumerate(md_files, 1):
                if i % 100 == 0:
                    print(f"    进度: {i}/{len(md_files)}")

                result = migrate_raw_file(md_file, client, kb_id)
                if result:
                    raw_results.append(result)
                else:
                    raw_skipped += 1
    else:
        print("  [SKIP] raw/ 目录不存在")

    # ========== 统计 ==========
    print("\n[4/4] 迁移统计:")
    print("\n  === wiki层(核心知识) ===")
    wiki_total = 0
    for wiki_type, items in wiki_results.items():
        count = len(items)
        wiki_total += count
        links = sum(item['wiki_links_count'] for item in items)
        print(f"    {wiki_type}: {count} 个文件, {links} 个双向链接")
    print(f"    跳过: {wiki_skipped} 个文件")

    print("\n  === raw层(原始素材) ===")
    raw_count = len(raw_results)
    print(f"    导入: {raw_count} 个文件")
    print(f"    跳过: {raw_skipped} 个文件")

    # 按作者统计
    if raw_results:
        authors = {}
        for item in raw_results:
            author = item.get('author', 'unknown')
            authors[author] = authors.get(author, 0) + 1
        print("\n    按作者统计:")
        for author, count in sorted(authors.items(), key=lambda x: -x[1])[:5]:
            print(f"      {author}: {count} 篇")

    total = wiki_total + raw_count
    print(f"\n  === 总计 ===")
    print(f"  导入: {total} 个Knowledge条目")
    print(f"    - wiki层: {wiki_total} 个(结构化知识)")
    print(f"    - raw层: {raw_count} 个(原始素材)")
    print(f"  跳过: {wiki_skipped + raw_skipped} 个低价值文件")

    print("\n[INFO] 迁移完成!接下来需要:")
    print("  1. 用真实WeKnora SDK替换MOCK客户端")
    print("  2. 配置API_KEY")
    print("  3. 运行迁移: python scripts/migrate_obsidian_to_weknora.py")
    print("  4. 在WeKnora中验证检索效果")
    print("  5. 构建知识图谱(基于wiki_links)")
    print("  6. 配置混合检索(向量+全文)+Rerank")

if __name__ == '__main__':
    main()
