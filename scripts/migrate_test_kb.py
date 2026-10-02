#!/usr/bin/env python3
"""
试点迁移:只迁移zettaranc-knowledge到WeKnora
用于验证迁移效果和检索质量
"""
import os
import re
import yaml
import json
import requests
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime

# ========== 配置 ==========
# 全部走环境变量，不要把本机路径和账号密码写进代码里。
KNOWLEDGE_BASE_ROOT = Path(os.environ.get("KNOWLEDGE_BASE_ROOT", "./knowledge_base"))
WEKNORA_BASE_URL = os.environ.get("WEKNORA_BASE_URL", "http://localhost:8080")
WEKNORA_EMAIL = os.environ.get("WEKNORA_EMAIL", "admin@example.com")
WEKNORA_PASSWORD = os.environ.get("WEKNORA_PASSWORD", "")

# Embedding模型配置
EMBEDDING_MODEL_ID = "bc764613-9bc0-4af6-aadf-8fc5dc105985"  # milkey/wemm-embedding-2b:Q4_K_M

# 只迁移这个知识库做测试
TEST_KB_NAME = "zettaranc-knowledge"

# ========== WeKnora真实客户端 ==========
class WeKnoraClient:
    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url
        self.email = email
        self.password = password
        self.token = None
        self.headers = {}

    def login(self) -> bool:
        """登录获取JWT token"""
        url = f"{self.base_url}/api/v1/auth/login"
        data = {"email": self.email, "password": self.password}
        try:
            response = requests.post(url, json=data, timeout=30)
            response.raise_for_status()
            self.token = response.json()['token']
            self.headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json'
            }
            print(f"[OK] 登录成功: {self.email}")
            return True
        except Exception as e:
            print(f"[ERROR] 登录失败: {e}")
            return False

    def test_connection(self) -> bool:
        """测试API连接"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except Exception as e:
            print(f"[ERROR] 连接失败: {e}")
            return False

    def create_knowledge_base(self, name: str, description: str) -> str:
        """创建知识库"""
        url = f"{self.base_url}/api/v1/knowledge-bases"
        data = {
            "name": name,
            "description": description,
            "type": "document",
            "embedding_model_id": EMBEDDING_MODEL_ID
        }
        try:
            response = requests.post(url, json=data, headers=self.headers, timeout=30)
            response.raise_for_status()
            kb_id = response.json()['data']['id']
            print(f"[OK] 创建知识库: {name} (ID: {kb_id})")
            return kb_id
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 409:
                print(f"[WARN] 知识库已存在,尝试获取ID...")
                # 尝试查询已存在的知识库
                return self._find_knowledge_base(name)
            raise

    def _find_knowledge_base(self, name: str) -> str:
        """查询已存在的知识库"""
        url = f"{self.base_url}/api/v1/knowledge-bases"
        response = requests.get(url, headers=self.headers, timeout=30)
        response.raise_for_status()
        kbs = response.json()['data']
        for kb in kbs:
            if kb['name'] == name:
                return kb['id']
        raise Exception(f"找不到知识库: {name}")

    def create_knowledge(self, kb_id: str, title: str, content: str,
                        metadata: dict, tags: list, source_file: str) -> str:
        """上传文件创建Knowledge"""
        url = f"{self.base_url}/api/v1/knowledge-bases/{kb_id}/knowledge/file"

        # 将content写入临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write(content)
            temp_path = f.name

        try:
            # 准备multipart form data
            with open(temp_path, 'rb') as f:
                files = {
                    'file': (os.path.basename(source_file), f, 'text/markdown')
                }
                data = {
                    'metadata': json.dumps(metadata, ensure_ascii=False)
                }

                # 使用Authorization header,不设置Content-Type(让requests自动处理)
                headers = {'Authorization': self.headers['Authorization']}

                response = requests.post(
                    url,
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=60
                )

            if response.status_code != 200:
                print(f"    [DEBUG] 错误响应: {response.text}")

            response.raise_for_status()
            return response.json()['data']['id']
        finally:
            os.unlink(temp_path)

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
        print(f"  [ERROR] 读取失败: {e}")
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
        'created': str(frontmatter.get('created', '')),  # 转换为字符串
        'last_updated': str(frontmatter.get('last_updated', '')),  # 转换为字符串
        'aliases': ','.join(frontmatter.get('aliases', [])),  # 列表转字符串
        'wiki_links': ','.join(wiki_links[:20]),  # 只保留前20个链接,转字符串
        'complexity': frontmatter.get('complexity', ''),
        'entity_type': frontmatter.get('entity_type', ''),
    }

    if 'sources' in frontmatter:
        sources = frontmatter['sources']
        if isinstance(sources, list):
            metadata['original_sources'] = ','.join(sources[:5])  # 只保留前5个
        else:
            metadata['original_sources'] = str(sources)

    description = extract_first_paragraph(body)

    try:
        knowledge_id = client.create_knowledge(
            kb_id=kb_id, title=title, content=body,
            metadata=metadata, tags=tags, source_file=str(file_path)
        )
        return {'knowledge_id': knowledge_id, 'title': title, 'type': wiki_type}
    except Exception as e:
        print(f"  [ERROR] 上传失败 {title}: {e}")
        return None

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

    try:
        knowledge_id = client.create_knowledge(
            kb_id=kb_id, title=title[:100], content=body,
            metadata=metadata, tags=tags, source_file=str(file_path)
        )
        return {'knowledge_id': knowledge_id, 'title': title, 'type': 'raw-source'}
    except Exception as e:
        print(f"  [ERROR] 上传失败 {title}: {e}")
        return None

# ========== 主流程 ==========
def main():
    print("=" * 70)
    print(f"试点迁移: {TEST_KB_NAME}")
    print("=" * 70)

    if not WEKNORA_PASSWORD:
        print("[ERROR] 未设置 WEKNORA_PASSWORD 环境变量，脚本无法登录。")
        print("  用法: WEKNORA_EMAIL=you@example.com WEKNORA_PASSWORD=xxx python3 migrate_test_kb.py")
        return 1

    # 初始化客户端
    client = WeKnoraClient(WEKNORA_BASE_URL, WEKNORA_EMAIL, WEKNORA_PASSWORD)

    # 测试连接
    print("\n[1/4] 测试API连接...")
    if not client.test_connection():
        print("[ERROR] 无法连接到WeKnora API")
        print(f"  请确认:")
        print(f"  1. WeKnora正在运行: docker ps | grep WeKnora")
        print(f"  2. API地址正确: {WEKNORA_BASE_URL}")
        return

    print("[OK] API连接成功")

    # 登录
    print("\n[2/4] 登录...")
    if not client.login():
        print("[ERROR] 登录失败")
        return

    # 创建知识库
    print("\n[3/4] 创建知识库...")
    kb_path = KNOWLEDGE_BASE_ROOT / TEST_KB_NAME
    if not kb_path.exists():
        print(f"[ERROR] 知识库目录不存在: {kb_path}")
        return

    try:
        kb_id = client.create_knowledge_base(
            TEST_KB_NAME,
            f"从Obsidian迁移的{TEST_KB_NAME}知识库(试点测试)"
        )
    except Exception as e:
        print(f"[ERROR] 创建知识库失败: {e}")
        return

    # 迁移wiki/
    print("\n[4/4] 迁移文件...")
    wiki_stats = {'imported': 0, 'skipped': 0}
    wiki_dir = kb_path / "wiki"

    if wiki_dir.exists():
        wiki_types = ['concepts', 'entities', 'sources', 'syntheses']
        for wiki_type in wiki_types:
            type_dirs = list(wiki_dir.rglob(wiki_type))
            type_dirs = [d for d in type_dirs if d.is_dir()]

            if not type_dirs:
                continue

            for type_dir in type_dirs:
                md_files = list(type_dir.rglob("*.md"))
                print(f"  处理 wiki/**/{wiki_type}: {len(md_files)} 个文件")

                for i, md_file in enumerate(md_files, 1):
                    if i % 10 == 0:
                        print(f"    进度: {i}/{len(md_files)}")

                    result = migrate_wiki_file(md_file, wiki_type, TEST_KB_NAME, client, kb_id)
                    if result:
                        wiki_stats['imported'] += 1
                    else:
                        wiki_stats['skipped'] += 1

    # 迁移raw/
    raw_stats = {'imported': 0, 'skipped': 0}
    raw_dir = kb_path / "raw"

    if raw_dir.exists():
        md_files = list(raw_dir.rglob("*.md"))
        md_files = [f for f in md_files if f.name != 'INDEX.md']
        print(f"  处理 raw/: {len(md_files)} 个文件")

        for i, md_file in enumerate(md_files, 1):
            if i % 50 == 0:
                print(f"    进度: {i}/{len(md_files)}")

            result = migrate_raw_file(md_file, TEST_KB_NAME, client, kb_id)
            if result:
                raw_stats['imported'] += 1
            else:
                raw_stats['skipped'] += 1

    # 统计
    total = wiki_stats['imported'] + raw_stats['imported']
    print("\n" + "=" * 70)
    print("迁移完成!")
    print("=" * 70)
    print(f"\n  wiki层: {wiki_stats['imported']} 导入, {wiki_stats['skipped']} 跳过")
    print(f"  raw层: {raw_stats['imported']} 导入, {raw_stats['skipped']} 跳过")
    print(f"  总计: {total} 个Knowledge条目")

    print("\n下一步:")
    print(f"  1. 访问前端: http://localhost:8081")
    print(f"  2. 查看知识库: {TEST_KB_NAME}")
    print(f"  3. 测试检索: 搜索几个关键概念")
    print(f"  4. 验证效果: 检查召回率和检索质量")

if __name__ == '__main__':
    main()
