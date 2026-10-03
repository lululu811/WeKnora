#!/usr/bin/env python3
"""
对 vault 来源的文章重跑入库，让同级图片被登记进 image_info、进而进入多模态处理。

为什么需要它：707 篇当初走 /knowledge/manual 入库，而那条路径只解析 data-URI
和远程 URL，不认同级相对路径。结果 9639 个 chunk 里只有 2 个带非空 image_info，
多模态引擎没有可处理的对象。修复后（knowledge_vault.go 的 buildVaultImageResult）
重跑一次即可补上。

筛选口径是「大图数」而不是「图片总数」：公众号资讯稿里大量 <10KB 的分隔线、
加载占位符和空白 gif，按图数筛会选出一堆"图多但没数据"的稿子。≥100KB 的位图
才更可能是数据图。阈值越高越干净（但仍会误收 400KB 量级的公众号 logo）。

代价：每张图会触发一次 OCR + 一次 caption 的远端调用。这是真实花费，所以默认
dry-run，先看清楚名单和图片数再决定 --yes。

用法：
  export WEKNORA_EMAIL=… WEKNORA_PASSWORD=…
  python3 scripts/reparse_vlm_targets.py --kb-id <id>                    # dry-run
  python3 scripts/reparse_vlm_targets.py --kb-id <id> --min-big 15      # 更严的阈值
  python3 scripts/reparse_vlm_targets.py --kb-id <id> --yes --concurrency 2
  python3 scripts/reparse_vlm_targets.py --kb-id <id> --yes --limit 3   # 试水
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
from pathlib import Path

DEFAULT_BASE_URL = os.environ.get("WEKNORA_BASE_URL", "http://localhost:8080")
DEFAULT_VAULT_ROOT = Path(
    os.environ.get("WEKNORA_VAULT_HOST_DIR", "/Users/chenlei/003_knowledge/knowledge_base")
)
DEFAULT_SUBTREE = os.environ.get(
    "WECHAT_ARTICLE_SUBTREE", "wechat-jinrong-lianyaoshi/raw/01-articles"
)

# ≥100KB 的位图才计入「大图」。公众号 logo 常在 400KB 量级，所以这个口径仍有
# 误收；阈值抬高到 ≥15 张时被误收的比例会明显下降。
BIG_IMAGE_BYTES = 100 * 1024

MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def find_targets(vault_root: Path, subtree: str, min_big: int) -> list[dict]:
    """返回 [{big, vault_path}]，按大图数倒序。"""
    base = vault_root / subtree
    if not base.is_dir():
        sys.exit(f"vault 子目录不存在: {base}")
    out: list[dict] = []
    for md in base.rglob("*.md"):
        if md.name.upper() == "README.MD":
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        refs = {r.strip("<> ") for r in MD_IMAGE_RE.findall(text)}
        refs = {r for r in refs if not r.startswith("http")}
        big = 0
        for r in refs:
            try:
                if (md.parent / r).stat().st_size >= BIG_IMAGE_BYTES:
                    big += 1
            except OSError:
                pass
        if big >= min_big:
            out.append({"big": big, "vault_path": str(md.relative_to(vault_root))})
    out.sort(key=lambda d: d["big"], reverse=True)
    return out


class WeKnora:
    def __init__(self, base_url: str, email: str, password: str):
        self.base = base_url.rstrip("/")
        body = json.dumps({"email": email, "password": password}).encode()
        req = urllib.request.Request(
            f"{self.base}/api/v1/auth/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.loads(resp.read())
        self.token = d.get("token")
        if not self.token:
            sys.exit("登录失败：响应里没有 token")
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}

    def _req(self, method: str, path: str, body=None, timeout: int = 120):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=self.headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except Exception:
                return e.code, {}

    def map_vault_paths(self, kb_id: str) -> dict[str, str]:
        """vault_path -> knowledge_id，覆盖该库全部手工条目。"""
        out: dict[str, str] = {}
        for page in range(1, 20):
            st, d = self._req(
                "GET",
                f"/api/v1/knowledge-bases/{kb_id}/knowledge"
                f"?page={page}&page_size=100&file_type=manual",
            )
            items = d.get("data") or []
            if not items:
                break
            for k in items:
                _, kd = self._req("GET", f"/api/v1/knowledge/{k['id']}")
                meta = (kd.get("data") or kd).get("metadata") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                vp = meta.get("vault_path")
                if vp:
                    out[vp] = k["id"]
            if len(items) < 100:
                break
        return out

    def reparse(self, knowledge_id: str):
        return self._req("POST", f"/api/v1/knowledge/{knowledge_id}/reparse")


def main() -> int:
    ap = argparse.ArgumentParser(description="重跑入库，让 vault 同级图片进入多模态处理")
    ap.add_argument("--kb-id", required=True)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    ap.add_argument("--subtree", default=DEFAULT_SUBTREE)
    ap.add_argument(
        "--min-big",
        type=int,
        default=8,
        help=f"每篇至少几张 ≥{BIG_IMAGE_BYTES//1024}KB 的图（默认 8）",
    )
    ap.add_argument("--limit", type=int, default=None, help="只处理大图最多的前 N 篇")
    ap.add_argument("--concurrency", type=int, default=2, help="并发重跑数（默认 2，别开太高）")
    ap.add_argument("--yes", action="store_true", help="真的执行（默认 dry-run）")
    args = ap.parse_args()

    targets = find_targets(args.vault_root, args.subtree, args.min_big)
    if args.limit:
        targets = targets[: args.limit]
    total_big = sum(t["big"] for t in targets)
    print(f"命中 ≥{args.min_big} 张大图: {len(targets)} 篇 / {total_big} 张")
    if not targets:
        return 1

    print("\n大图最多的 10 篇：")
    for t in targets[:10]:
        print(f"  {t['big']:3d} 张  {t['vault_path'].split('/')[-1][:46]}")
    if len(targets) > 10:
        print(f"  … 另 {len(targets) - 10} 篇")

    if not args.yes:
        print(f"\n--dry-run：未执行。确认后加 --yes"
              f"（这会对约 {total_big} 张图产生远端 OCR/caption 调用）。")
        return 0

    email = os.environ.get("WEKNORA_EMAIL", "")
    password = os.environ.get("WEKNORA_PASSWORD", "")
    if not email or not password:
        sys.exit("请设置 WEKNORA_EMAIL / WEKNORA_PASSWORD 环境变量")

    client = WeKnora(args.base_url, email, password)
    print("\n登录成功，建立 vault_path → knowledge_id 映射…")
    mapping = client.map_vault_paths(args.kb_id)
    todo = [t for t in targets if t["vault_path"] in mapping]
    missing = len(targets) - len(todo)
    print(f"  匹配 {len(todo)} 篇" + (f"，{missing} 篇未入库（跳过）" if missing else ""))
    if not todo:
        print("没有可处理的目标。")
        return 1

    done = 0
    failed = 0
    errors: list[str] = []
    lock = threading.Lock()
    started = time.time()

    def work(t: dict):
        nonlocal done, failed
        kid = mapping[t["vault_path"]]
        st, payload = client.reparse(kid)
        with lock:
            done += 1
            if st == 200:
                print(f"  [{done}/{len(todo)}] ✓ {t['vault_path'].split('/')[-1][:44]}（{t['big']} 张）")
            else:
                failed += 1
                errors.append(f"{t['vault_path']}: HTTP {st} {json.dumps(payload)[:160]}")
                print(f"  [{done}/{len(todo)}] ✗ {t['vault_path'].split('/')[-1][:40]} HTTP {st}")

    with futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        list(pool.map(work, todo))

    elapsed = time.time() - started
    print(f"\n已触发 {len(todo)} 篇重跑，{failed} 篇失败（耗时 {elapsed:.0f}s）")
    print("注意：这只表示**已入队**。分块、嵌入与图片 OCR/caption 是异步的，")
    print("用这个查进度：")
    print(f"  psql -c \"SELECT chunk_type, count(*) FROM chunks c JOIN knowledges k"
          f" ON k.id=c.knowledge_id WHERE k.knowledge_base_id='{args.kb_id}'"
          f" AND c.deleted_at IS NULL GROUP BY 1;\"")
    if errors:
        print("\n错误：")
        for e in errors[:10]:
            print(f"  {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
