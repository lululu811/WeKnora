#!/usr/bin/env python3
"""
全量迁移脚本:将所有Obsidian知识库迁移到WeKnora
支持19个目录,每个目录都有wiki/和raw/结构
"""
import os
import re
import yaml
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from collections import defaultdict

# ========== 配置 ==========
KNOWLEDGE_BASE_ROOT = Path("/Users/chenlei/003_knowledge/knowledge_base")
WEKNORA_BASE_URL = "http://localhost:18080"
WEKNORA_API_KEY = "your_api_key_here"

# 所有知识库目录(18个,排除course-knowledge)
KB_DIRECTORIES = [
    "research-reports",
    "qishui_knowledege",
    "chenliitaz_knowledge",
    "mo_knowledge",
    "fundamental-research",
    "fupeng-knowledge",
    "tutufang-knowledge",
    "jianfang-knowledge",
    "kongshanlieren-knowledge",
    "fuzong-knowledge",
    "changsishan-knowledge",
    "sanxian_knowledege",
    "wechat-jinrong-lianyaoshi",
    "init-knowledge-base",
    # "course-knowledge",  # 排除
    "zettaranc-knowledge",
    "docs",
    "memory",
]

# 排除的目录(备份、临时文件等)
EXCLUDED_DIRS = [
    "qishui_knowledge_bak",  # 备份
]

# ========== WeKnora客户端(MOCK) ==========
class WeKnoraClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def create_knowledge_base(self, name: str, description: str) -> str:
        print(f"[MOCK] 创建知识库: {name}")
        return f"mock_kb_{hash(name)}"

    def create_knowledge(self, kb_id: str, title: str, content: str,
                        metadata: Dict, tags: List[str], source_file: str) -> str:
        # 静默模式,不打印每个文件
        return f"mock_k_{hash(title)}"

# ========== 核心函数 ==========
def parse_frontmatter(content: str) -> Tuple[Dict, str]:
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        frontmatter = yaml.safe_load(parts[1])
        body = parts[2].strip()
        return frontmatter or {}, body
    except yaml.YAMLError:
        return {}, content

def extract_wiki_links(content: str) -> List[str]:
    pattern = r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
    return re.findall(pattern, content)

def should_skip_file(frontmatter: Dict, body: str) -> Tuple[bool, str]:
    status = frontmatter.get('status', '')
    if status and status != 'finished':
        return True, f"status={status}"
    if '待补充' in body and len(body.strip()) < 100:
        return True, "placeholder"
    if len(body.strip()) < 50:
        return True, "empty"
    return False, ""

def extract_first_paragraph(body: str) -> str:
    for line in body.split('\n'):
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('>'):
            return line[:200]
    return ""

def migrate_wiki_file(file_path: Path, wiki_type: str, kb_name: str,
                     client: WeKnoraClient, kb_id: str) -> Optional[Dict]:
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        return None

    frontmatter, body = parse_frontmatter(content)
    should_skip, reason = should_skip_file(frontmatter, body)
    if should_skip:
        return None

    title = frontmatter.get('title', file_path.stem)
    tags = frontmatter.get('tags', [])
    if isinstance(tags, str):
        tags = [tags]
    tags.extend([f"wiki/{wiki_type}", f"kb/{kb_name}", "status/finished"])

    wiki_links = extract_wiki_links(body)

    metadata = {
        'source': f'{kb_name}/wiki/{wiki_type}',
        'source_file': str(file_path),
        'knowledge_base': kb_name,
        'type': frontmatter.get('type', wiki_type),
        'status': 'finished',
        'created': frontmatter.get('created', ''),
        'last_updated': frontmatter.get('last_updated', ''),
        'aliases': frontmatter.get('aliases', []),
        'wiki_links': wiki_links,
        'complexity': frontmatter.get('complexity', ''),
        'entity_type': frontmatter.get('entity_type', ''),
    }

    if 'sources' in frontmatter:
        metadata['original_sources'] = frontmatter['sources']

    description = extract_first_paragraph(body)

    knowledge_id = client.create_knowledge(
        kb_id=kb_id, title=title, content=body,
        metadata=metadata, tags=tags, source_file=str(file_path)
    )

    return {
        'knowledge_id': knowledge_id,
        'title': title,
        'type': wiki_type,
        'knowledge_base': kb_name,
        'wiki_links_count': len(wiki_links),
    }

def migrate_raw_file(file_path: Path, kb_name: str,
                    client: WeKnoraClient, kb_id: str) -> Optional[Dict]:
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception:
        return None

    frontmatter, body = parse_frontmatter(content)

    if len(body.strip()) < 100:
        return None

    filename = file_path.stem
    date_match = re.match(r'(\d{2})-(\d{2})_(.+)', filename)
    metadata = {'source': f'{kb_name}/raw', 'knowledge_base': kb_name, 'layer': 'raw'}

    if date_match:
        month, day, title = date_match.groups()
        parent_path = str(file_path.parent)
        year_match = re.search(r'/(20\d{2})/', parent_path)
        if year_match:
            metadata['date'] = f"{year_match.group(1)}-{month}-{day}"
        metadata['raw_title'] = title
    else:
        title = filename

    if '**作者**:' in body:
        author_match = re.search(r'\*\*作者\*\*:\s*(.+?)(?:\n|$)', body)
        if author_match:
            metadata['author'] = author_match.group(1).strip()

    tags = ["raw-source", f"kb/{kb_name}"]
    if 'author' in metadata:
        tags.append(f"author/{metadata['author']}")

    knowledge_id = client.create_knowledge(
        kb_id=kb_id, title=title[:100], content=body,
        metadata=metadata, tags=tags, source_file=str(file_path)
    )

    return {'knowledge_id': knowledge_id, 'title': title, 'type': 'raw-source', 'knowledge_base': kb_name}

# ========== 主流程 ==========
def main():
    print("=" * 70)
    print("全量迁移:所有Obsidian知识库 → WeKnora")
    print("=" * 70)

    client = WeKnoraClient(WEKNORA_BASE_URL, WEKNORA_API_KEY)

    # 为每个知识库创建独立的WeKnora知识库
    kb_ids = {}
    print("\n[1/3] 创建知识库...")
    for kb_name in KB_DIRECTORIES:
        kb_path = KNOWLEDGE_BASE_ROOT / kb_name
        if not kb_path.exists():
            print(f"  [SKIP] {kb_name} 目录不存在")
            continue
        kb_id = client.create_knowledge_base(
            f"{kb_name}",
            f"从Obsidian迁移的{kb_name}知识库"
        )
        kb_ids[kb_name] = kb_id

    # 迁移每个知识库
    print("\n[2/3] 迁移知识库...")
    stats = defaultdict(lambda: {'wiki': 0, 'raw': 0, 'skipped': 0})

    for kb_name, kb_id in kb_ids.items():
        kb_path = KNOWLEDGE_BASE_ROOT / kb_name
        print(f"\n  === {kb_name} ===")

        # 迁移wiki/ - 支持嵌套结构(如wiki/zettaranc/concepts/)
        wiki_dir = kb_path / "wiki"
        if wiki_dir.exists():
            wiki_types = ['concepts', 'entities', 'sources', 'syntheses']
            for wiki_type in wiki_types:
                # 查找所有匹配的目录(支持嵌套)
                type_dirs = list(wiki_dir.rglob(wiki_type))
                type_dirs = [d for d in type_dirs if d.is_dir()]

                if not type_dirs:
                    continue

                imported = 0
                skipped = 0
                for type_dir in type_dirs:
                    # 递归查找所有.md文件(支持子目录)
                    md_files = list(type_dir.rglob("*.md"))
                    for md_file in md_files:
                        result = migrate_wiki_file(md_file, wiki_type, kb_name, client, kb_id)
                        if result:
                            imported += 1
                        else:
                            skipped += 1

                if imported > 0 or skipped > 0:
                    print(f"    wiki/**/{wiki_type}: {imported} 导入, {skipped} 跳过")
                    stats[kb_name]['wiki'] += imported
                    stats[kb_name]['skipped'] += skipped

        # 迁移raw/
        raw_dir = kb_path / "raw"
        if raw_dir.exists():
            md_files = list(raw_dir.rglob("*.md"))
            md_files = [f for f in md_files if f.name != 'INDEX.md']
            imported = 0
            for md_file in md_files:
                result = migrate_raw_file(md_file, kb_name, client, kb_id)
                if result:
                    imported += 1
            stats[kb_name]['raw'] = imported
            if imported > 0:
                print(f"    raw/: {imported} 导入")

    # 统计
    print("\n[3/3] 迁移统计:")
    total_wiki = 0
    total_raw = 0
    total_skipped = 0

    print("\n  知识库名称                    | wiki | raw  | 跳过")
    print("  " + "-" * 60)
    for kb_name in sorted(stats.keys()):
        s = stats[kb_name]
        print(f"  {kb_name:30s} | {s['wiki']:4d} | {s['raw']:4d} | {s['skipped']:4d}")
        total_wiki += s['wiki']
        total_raw += s['raw']
        total_skipped += s['skipped']

    total = total_wiki + total_raw
    print("  " + "-" * 60)
    print(f"  {'总计':30s} | {total_wiki:4d} | {total_raw:4d} | {total_skipped:4d}")
    print(f"\n  总Knowledge条目: {total:,}")
    print(f"    - wiki层: {total_wiki:,} (结构化知识)")
    print(f"    - raw层: {total_raw:,} (原始素材)")
    print(f"    - 跳过: {total_skipped:,} (低价值内容)")

    print("\n[INFO] 迁移完成!")
    print("  下一步:")
    print("  1. 用真实WeKnora SDK替换MOCK客户端")
    print("  2. 配置API_KEY")
    print("  3. 运行: python3 scripts/migrate_all_to_weknora.py")
    print("  4. 部署Qdrant/Milvus + Neo4j")
    print("  5. 验证检索效果")

if __name__ == '__main__':
    main()
