#!/usr/bin/env python3
"""
导入前处理：剥离 markdown frontmatter 里的 sources: 字段。

为什么：WeKnora 的 docreader 不解析 frontmatter（markdown_parser.py 只做表格规范化），
frontmatter 会原样进 chunk 内容。chunk_size 只有 512，而 sources: 里往往是多条长文件路径，
纯噪音却吃掉近一半预算，还可能因路径片段被误召回。

保留 title / type / tags —— 它们对检索是正向的。
`[[双链]]` 保持原样（当普通文本，不影响检索）。
"""
import os
import re
import shutil
import sys

SRC = sys.argv[1]
DST = sys.argv[2]
LABEL = sys.argv[3] if len(sys.argv) > 3 else ""
# 匹配 frontmatter 块
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)

stats = {"total": 0, "with_fm": 0, "stripped": 0, "no_sources": 0, "errors": 0}


def strip_sources(fm: str) -> tuple[str, bool]:
    """从 frontmatter 正文里删掉 sources: 键。返回 (新正文, 是否删过)。"""
    lines = fm.split("\n")
    out, i, removed = [], 0, False
    while i < len(lines):
        line = lines[i]
        # 顶层键（无缩进）以 key: 开头
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:", line)
        if m and m.group(1) == "sources":
            removed = True
            i += 1
            # 单行数组: sources: [a, b]  —— 不消费后续行
            if line.rstrip().endswith("]"):
                continue
            # 多行数组/块: 消费所有缩进续行
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() == "":
                    # 空行后若仍是缩进行则继续，否则结束
                    if i + 1 < len(lines) and re.match(r"^\s+\S", lines[i + 1]):
                        i += 1
                        continue
                    break
                if re.match(r"^\s+\S", nxt):
                    i += 1
                    continue
                break
            continue
        out.append(line)
        i += 1
    return "\n".join(out), removed


def process(src: str, dst: str) -> None:
    for root, _dirs, files in os.walk(src):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            sp = os.path.join(root, fn)
            rel = os.path.relpath(sp, src)
            # 避免不同子目录同名文件在导入后无法区分
            flat = rel.replace(os.sep, "__")
            if LABEL:
                flat = f"{LABEL}__{flat}"
            dp = os.path.join(dst, flat)
            stats["total"] += 1
            try:
                with open(sp, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                m = FM_RE.match(text)
                if m:
                    stats["with_fm"] += 1
                    new_fm, removed = strip_sources(m.group(1))
                    if removed:
                        stats["stripped"] += 1
                    else:
                        stats["no_sources"] += 1
                    text = f"---\n{new_fm}\n---\n" + text[m.end():]
                with open(dp, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                print(f"  !! {rel}: {exc}", file=sys.stderr)


if os.path.exists(DST):
    shutil.rmtree(DST)
os.makedirs(DST, exist_ok=True)
process(SRC, DST)

print(f"  总文件     : {stats['total']}")
print(f"  有frontmatter: {stats['with_fm']}")
print(f"  剥离sources : {stats['stripped']}")
print(f"  本就无sources: {stats['no_sources']}")
print(f"  错误       : {stats['errors']}")
