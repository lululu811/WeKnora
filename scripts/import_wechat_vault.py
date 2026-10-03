#!/usr/bin/env python3
"""
把 vault 里的公众号 markdown 导入 WeKnora。

与 scripts/migrate_obsidian_to_weknora.py 的区别：那个是骨架
（WeKnoraClient 里留着 TODO，打印 [MOCK]，从未真正发过请求），本脚本是可用的。

为什么走 /knowledge/manual 而不是 docreader：
  输入已经是 wechat-article-to-markdown 产出的干净 markdown，让 docreader 再
  解析一遍没有意义，而且它的 MarkdownTableFormatter 会重写表格，导致
  attachStructureBlocks 的逐字相等检查失败、含表格文章的引用定位退化。

为什么正文不改写、图片也不动：
  vault 是只读事实源。图片的相对引用交给渲染层在读取时改写成
  /knowledge/{id}/vault-asset?ref=…（见 internal/application/service/knowledge_vault.go），
  代理只在条目自己目录内解析。这样 vault 重新导出后路径变了也不会失效，
  也不会产生 819MB 的第二份副本。

用法：
  export WEKNORA_EMAIL=… WEKNORA_PASSWORD=…
  python3 scripts/import_wechat_vault.py --kb-id <id> --dry-run
  python3 scripts/import_wechat_vault.py --kb-id <id> --limit 20
  python3 scripts/import_wechat_vault.py --kb-id <id> --concurrency 4
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = os.environ.get("WEKNORA_BASE_URL", "http://localhost:8080")
# vault 根目录必须与 app 容器里的 WEKNORA_VAULT_ROOT 指向同一棵树；这里填的是
# 宿主机路径，用于读文件，vault_path 传给服务端的是相对于该根的路径。
DEFAULT_VAULT_ROOT = Path(
    os.environ.get(
        "WEKNORA_VAULT_HOST_DIR", "/Users/chenlei/003_knowledge/knowledge_base"
    )
)
# vault 根下的哪棵子树是公众号文章导出目录（相对 vault 根）
DEFAULT_ARTICLE_SUBTREE = os.environ.get(
    "WECHAT_ARTICLE_SUBTREE",
    "wechat-jinrong-lianyaoshi/raw/01-articles",
)

H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
ACCOUNT_RE = re.compile(r"^>\s*公众号[：:]\s*(.+?)\s*$", re.M)
PUBLISHED_RE = re.compile(r"^>\s*发布时间[：:]\s*(.+?)\s*$", re.M)
ORIGINAL_URL_RE = re.compile(r"^>\s*原文链接[：:]\s*(\S+)\s*$", re.M)


@dataclass
class Article:
    """一篇待导入的文章。"""

    path: Path
    vault_path: str  # 相对 vault 根，服务端据此定位目录
    title: str
    source: str  # 公众号原文链接
    account: str
    published: str
    size: int = 0
    images: int = 0


@dataclass
class Summary:
    total: int = 0
    created: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def merge(self, other: "Summary") -> None:
        with self.lock:
            self.total += other.total
            self.created += other.created
            self.skipped += other.skipped
            self.failed += other.failed
            self.errors.extend(other.errors)


def collect(vault_root: Path, subtree: str, limit: int | None = None) -> list[Article]:
    """扫描 vault 子树，返回可导入的文章列表（按发布时间倒序，最新的先导）。"""
    base = vault_root / subtree
    if not base.is_dir():
        sys.exit(f"vault 子目录不存在: {base}")

    articles: list[Article] = []
    # 导出器产出的形状是 <公众号>/<标题>/<标题>.md + 同级 images/
    for md in base.rglob("*.md"):
        if md.name.upper() == "README.MD":
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        h1 = H1_RE.search(text)
        title = (h1.group(1) if h1 else md.stem).strip()
        url_m = ORIGINAL_URL_RE.search(text)
        if not url_m:
            # 没有原文链接 = 不是导出器产物，跳过；硬导会把 source 写成
            # "manual"，之后既不能点回原文也不能按 URL 去重。
            continue
        acc_m = ACCOUNT_RE.search(text)
        pub_m = PUBLISHED_RE.search(text)

        articles.append(
            Article(
                path=md,
                vault_path=str(md.relative_to(vault_root)),
                title=title,
                source=url_m.group(1),
                account=(acc_m.group(1) if acc_m else ""),
                published=(pub_m.group(1) if pub_m else ""),
                size=len(text.encode("utf-8")),
                images=text.count("!["),
            )
        )

    articles.sort(key=lambda a: a.published, reverse=True)
    return articles[:limit] if limit else articles


class WeKnora:
    def __init__(self, base_url: str, email: str, password: str, tenant_id: str = ""):
        self.base = base_url.rstrip("/")
        self.tenant_id = tenant_id
        token = self._login(email, password)
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if tenant_id:
            self.headers["X-Tenant-ID"] = tenant_id
        self._lock = threading.Lock()

    def _request(self, method: str, path: str, body=None, timeout: int = 120):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=self.headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw[:400].decode("utf-8", "replace")}

    def _login(self, email: str, password: str) -> str:
        req = urllib.request.Request(
            f"{self.base}/api/v1/auth/login",
            data=json.dumps({"email": email, "password": password}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.loads(resp.read())
        token = d.get("token")
        if not token:
            sys.exit("登录失败：响应里没有 token")
        return token

    def existing_sources(self, kb_id: str) -> set[str]:
        """已入库条目的 source 集合，用于断点续跑与幂等。

        列表响应是 { data: [...], page, page_size, success, total } —— data 是
        数组、total 在顶层，不是 { data: { items, total } }。
        """
        out: set[str] = set()
        page = 1
        while True:
            q = urllib.parse.urlencode(
                {"page": page, "page_size": 100, "file_type": "manual"}
            )
            st, d = self._request("GET", f"/api/v1/knowledge-bases/{kb_id}/knowledge?{q}")
            if st != 200:
                break
            items = d.get("data") or []
            if not isinstance(items, list) or not items:
                break
            for k in items:
                s = k.get("source")
                if s and s != "manual":
                    out.add(s)
            total = d.get("total") or 0
            page += 1
            if page * 100 >= total:
                break
        return out

    def create(self, kb_id: str, art: Article) -> tuple[int, str]:
        body = {
            "title": art.title,
            "content": art.path.read_text(encoding="utf-8", errors="replace"),
            "status": "publish",
            "channel": "wechat",
            "source": art.source,
            "vault_path": art.vault_path,
        }
        st, d = self._request(
            "POST", f"/api/v1/knowledge-bases/{kb_id}/knowledge/manual", body, timeout=180
        )
        if st == 200:
            kid = (d.get("data") or d).get("id", "")
            return 200, kid
        msg = json.dumps(d, ensure_ascii=False)[:300]
        return st, msg


def main() -> int:
    ap = argparse.ArgumentParser(description="把 vault 里的公众号文章导入 WeKnora")
    ap.add_argument("--kb-id", required=True, help="目标知识库 id")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    ap.add_argument("--subtree", default=DEFAULT_ARTICLE_SUBTREE)
    ap.add_argument("--limit", type=int, default=None, help="只导入最新的 N 篇（试跑用）")
    ap.add_argument("--concurrency", type=int, default=4, help="并发导入数")
    ap.add_argument("--dry-run", action="store_true", help="只扫描并打印，不入库")
    args = ap.parse_args()

    articles = collect(args.vault_root, args.subtree, args.limit)
    print(f"扫描到 {len(articles)} 篇（{args.vault_root / args.subtree}）")
    if not articles:
        return 1

    total_bytes = sum(a.size for a in articles)
    total_images = sum(a.images for a in articles)
    accounts = {a.account for a in articles if a.account}
    newest = max((a.published for a in articles if a.published), default="-")
    oldest = min((a.published for a in articles if a.published), default="-")
    print(f"  {len(accounts)} 个公众号 · {total_bytes/1e6:.1f} MB · {total_images} 张图")
    print(f"  时间跨度 {oldest} → {newest}")

    if args.dry_run:
        print("\n前 5 篇：")
        for a in articles[:5]:
            print(f"  · [{a.account}] {a.title[:44]}  ({a.published}, {a.images} 图)")
            print(f"    vault_path={a.vault_path}")
        print(f"\n--dry-run：未入库。用 --kb-id <id> --limit N 试跑真实导入。")
        return 0

    email = os.environ.get("WEKNORA_EMAIL", "")
    password = os.environ.get("WEKNORA_PASSWORD", "")
    if not email or not password:
        sys.exit("请设置 WEKNORA_EMAIL / WEKNORA_PASSWORD 环境变量")

    client = WeKnora(args.base_url, email, password, os.environ.get("WEKNORA_TENANT_ID", ""))
    print("\n登录成功，拉取已入库清单用于去重…")
    seen = client.existing_sources(args.kb_id)
    todo = [a for a in articles if a.source not in seen]
    print(f"  已存在 {len(seen)} 篇，本次待导入 {len(todo)} 篇")

    if not todo:
        print("没有新文章，无需导入。")
        return 0

    summary = Summary(total=len(todo))
    done = 0
    started = time.time()

    def work(art: Article) -> Summary:
        # done 在闭包里递增，`nonlocal` 必须在函数体顶部声明 —— 放进 with 块里
        # 虽然语法合法，但读代码的人会以为它只在那条路径上生效。
        nonlocal done
        s = Summary(total=1)
        try:
            st, payload = client.create(args.kb_id, art)
            with summary.lock:
                done += 1
                if st == 200:
                    s.created = 1
                    print(f"  [{done}/{len(todo)}] ✓ {art.title[:46]}")
                else:
                    s.failed = 1
                    s.errors.append(f"{art.title}: HTTP {st} {payload}")
                    print(f"  [{done}/{len(todo)}] ✗ {art.title[:40]} — HTTP {st} {payload[:120]}")
        except Exception as e:  # 单篇失败不该中断整批
            with summary.lock:
                done += 1
            s.failed = 1
            s.errors.append(f"{art.title}: {e}")
            print(f"  [{done}/{len(todo)}] ✗ {art.title[:40]} — {e}")
        return s

    with futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        for s in pool.map(work, todo):
            summary.merge(s)

    elapsed = time.time() - started
    print(f"\n完成：{summary.created} 成功 / {summary.skipped} 跳过 / {summary.failed} 失败"
          f"（耗时 {elapsed:.0f}s，平均 {elapsed/max(1,len(todo)):.2f}s/篇）")
    if summary.errors:
        print("\n前 10 条错误：")
        for e in summary.errors[:10]:
            print(f"  {e}")
    if summary.failed:
        # 重跑本脚本会按 source 去重，成功的不会重复入库。
        print("\n重跑本脚本即可续跑：已成功的条目会按原文链接跳过。")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
