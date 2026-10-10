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

准入规则（默认开启，--filter off 关闭，2026-10-11 起）：
  默认拒绝。账号分两层放行：ADMIT_ACCOUNTS（权威金融源）无条件收；
  CONDITION_ACCOUNTS（研报/产业/个人复盘混合源）要求标题命中
  MARKET_TITLE_RE 的市场语义。两张表之外的账号一律不收——新号先出现、
  由人决定升格，好过默认全收把噪音喂给 retrieval 和 agent。

用法：
  export WEKNORA_EMAIL=… WEKNORA_PASSWORD=…
  python3 scripts/import_wechat_vault.py --kb-id <id> --dry-run
  python3 scripts/import_wechat_vault.py --kb-id <id> --limit 20
  python3 scripts/import_wechat_vault.py --kb-id <id> --concurrency 4
  python3 scripts/import_wechat_vault.py --kb-id <id> --filter off   # 旧行为：不过滤

定时批量（launchd）：
  scripts/install_wechat_import_agent.sh --kb-id <id>
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
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


# ---- 入库准入规则：默认拒绝，显式放行 ----
#
# 前提（2026-10-11 定）：收错一篇的代价是 agent 引用一篇骗人的文档
# （internal/handler/halo.go:178-182 同款教义），漏收一篇还能手动补，
# 所以新账号默认不收。两层放行：
#   ADMIT_ACCOUNTS     权威金融源，无条件收；
#   CONDITION_ACCOUNTS  研报/产业/个人复盘混合源，标题命中市场语义才收。
# 表外账号一律拒——包括未来新出现的号，由人决定何时升格。

ADMIT_ACCOUNTS: frozenset[str] = frozenset(
    {
        # 监管媒体 / 持牌机构研究
        "中国证券报", "中国基金报", "券商中国", "上海证券报", "证券时报",
        "第一财经公司与行业", "产联社CLS", "华尔街见闻", "财联社",
        "图解金融", "国家数据局",
        # 券商 / 期货研究
        "中信证券", "中信建投证券", "中金点睛", "华泰睿思", "广发证券",
        "中国银河策略", "广发金融工程研究", "国泰君安期货投研", "正信期货",
        "财信证券",
    }
)

CONDITION_ACCOUNTS: frozenset[str] = frozenset(
    {
        # 研报 / 产业 / 策略（标题需命中市场语义）
        "电眼看研报", "口罩哥研报60秒", "Quant攻城小队", "ETF进化论",
        "TMT研究院", "产业投研院", "九思行研", "Kevin策略研究", "TOP行业报告",
        "远川投资评论", "付鹏的财经世界", "饭统戴老板", "厉害财经", "盘前纪要",
        "优享智库", "看懂产业链", "半导体投研笔记", "沧海一土狗",
        "大宗商品价值投资俱乐部",
        # 个人复盘 / 游资情绪（同上：标题说话，账号不判决）
        "越甲策市", "养心解盘", "只做主线核心的柚子", "大奇自修", "冷眼局中人",
        "知行小菜鸟", "小陈无所事事的一天", "常言万语",
    }
)

# 市场语义命题词。宁缺毋滥：只放得进行情/策略/交易语境的词，
# 不放"数据""观察""解读"这类放之四海而皆准的灌水词。
MARKET_TITLE_RE = re.compile(
    r"涨停|跌停|大涨|大跌|暴涨|暴跌|反弹|反转|突破|新高|新低|牛市|熊市|震荡市|"
    r"普涨|普跌|缩量|放量|赚钱效应|亏钱效应|打板|龙头|补涨|卡位|接力|分歧|"
    r"情绪周期|地天板|"
    r"大盘|指数|沪指|深成指|创业板|科创板|北交所|北向资金|南向资金|外资|"
    r"主力资金|游资|机构调研|散户|基金|券商|ETF|转债|可转债|融资融券|"
    r"股指期货|期权|期指|"
    r"板块|题材|概念股|主线|轮动|高低切|产业链|景气度|供需|涨价|库存|排产|"
    r"招标|中标|订单|产能|扩产|投产|减产|国产替代|"
    r"持仓|仓位|加仓|减仓|抄底|逃顶|割肉|解套|买点|卖点|止损|止盈|低吸|"
    r"高抛|埋伏|扫货|出货|建仓|清仓|空仓|满仓|"
    r"估值|PE|PB|股息率|分红|财报|业绩|预告|快报|一季报|中报|三季报|年报|"
    r"季报|回购|增持|减持|举牌|定增|IPO|新股|打新|破发|退市|停牌|复牌|"
    r"解禁|股权质押|强赎|下修|套利|"
    r"政策|央行|降准|降息|LPR|MLF|逆回购|国常会|政治局|美联储|加息|关税|"
    r"汇率|人民币|黄金|原油|铜|锂|稀土|大宗商品|PMI|CPI|PPI|社融|M2|GDP|"
    r"社零|固定资产投资|"
    r"A股|港股|美股|外盘|隔夜|收盘|开盘|盘中|盘前|盘后|盘面|复盘|"
    r"战法|策略|研报|点评|前瞻|纵览|解析|梳理|速评|看多|看空|做多|做空|"
    # 交易行话：P 层个人复盘号的标题全是这些，缺了它们等于把实盘情报全拒了；
    # 行话本身够专，不会放进"周末爬山"这种生活标题。
    r"图形|信号|节奏|兑现|高位|滞涨|筹码|回撤|年化|夏普|Alpha|暴露|"
    r"退潮|冰点|高潮|主升|反包|洗盘|诱多|诱空|弱转强|强转弱|承接|压制|"
    r"箱体|区间|均线|趋势线|连板|首板|换手率|封单|龙虎榜|短线|超短|中线|"
    r"长线|蓝筹|白马|高标|补跌|右侧|左侧|仓位管理|风险偏好|波动率|因子|"
    r"多因子|选股|量化|回测|实盘|模拟盘|交易笔记|交易计划|看盘|盯盘|"
    r"集合竞价|成交额|成交量|天量|地量|"
    # 行业板块名词：产业/研报类 CONDITION 账号的标题主语
    r"新能源|光伏|储能|半导体|芯片|算力|机器人|军工|医药|白酒|银行|地产|"
    r"煤炭|钢铁|有色|化工|养殖|游戏|影视|旅游|零售|电商|物流|建筑|"
    r"电力|电网|运营商|汽车|手机|消费电子|"
    r"美债|国债|收益率|利率|债市|信用债|第一股|个股|妖股|跑赢|跑输|重估|"
    # 日报/夜报类：标题形如「每日夜报·金融炼药师｜2026-09-24」，命题词只在
    # 正文里；这类是稳定日更的金融Digest，漏收代价恒定，直接放行。
    r"夜报|早报|晨报|晚报|日报|午评|收评|盘前必读"
)


def admit(art: "Article") -> tuple[bool, str]:
    """准入判决。返回 (是否放行, 拒绝原因)。

    规则见模块 docstring：账号分层 × 标题命题。未知账号默认拒绝。
    """
    if art.account in ADMIT_ACCOUNTS:
        return True, ""
    if art.account in CONDITION_ACCOUNTS:
        if MARKET_TITLE_RE.search(art.title):
            return True, ""
        return False, "标题无市场语义"
    return False, "账号未放行"


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


def collect(
    vault_root: Path,
    subtree: str,
    limit: int | None = None,
    admit_fn: "Callable[[Article], tuple[bool, str]] | None" = None,
) -> tuple[list[Article], list[tuple[str, str]]]:
    """扫描 vault 子树，返回可导入的文章列表（按发布时间倒序，最新的先导）。

    admit_fn 非空时逐篇过准入规则，返回的第二项是被拒文章的
    (account, 拒绝原因) 清单，供 main() 打印统计。
    """
    base = vault_root / subtree
    if not base.is_dir():
        sys.exit(f"vault 子目录不存在: {base}")

    articles: list[Article] = []
    rejects: list[tuple[str, str]] = []
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

        art = Article(
            path=md,
            vault_path=str(md.relative_to(vault_root)),
            title=title,
            source=url_m.group(1),
            account=(acc_m.group(1) if acc_m else ""),
            published=(pub_m.group(1) if pub_m else ""),
            size=len(text.encode("utf-8")),
            images=text.count("!["),
        )
        if admit_fn is not None:
            ok, reason = admit_fn(art)
            if not ok:
                rejects.append((art.account, reason))
                continue
        articles.append(art)

    articles.sort(key=lambda a: a.published, reverse=True)
    return articles[:limit] if limit else articles, rejects


def _pagination_done(items_count: int, fetched: int, total: int, page_size: int = 100) -> bool:
    """列表分页的停止条件：末页不满页，或已取条数达到 total。

    曾经在这里写错过一次：用 `fetched + page_size >= total` 提前 break，
    total=830 时第 9 页（30 条）被跳过，最老的条目永远进不了去重集合，
    每轮导入都把它们当新文章重复入库。停止条件必须拿"已取条数"和 total 比。
    """
    return items_count < page_size or fetched >= total


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
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                d = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # launchd 里跑的时候日志只会留 traceback；坏凭据要一句人话。
            if e.code in (401, 403):
                sys.exit(f"登录失败（HTTP {e.code}）：WEKNORA_EMAIL/PASSWORD 不对，"
                         f"或该用户无权访问 --kb-id 指定的知识库")
            raise
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
            fetched = min(page * 100, total)
            page += 1
            if _pagination_done(len(items), fetched, total):
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
    ap.add_argument(
        "--filter",
        choices=["rule", "off"],
        default="rule",
        help="入库准入规则：rule=账号分层+标题命题（默认）；off=不过滤（旧行为）",
    )
    args = ap.parse_args()

    admit_fn = admit if args.filter == "rule" else None
    articles, rejects = collect(args.vault_root, args.subtree, args.limit, admit_fn)
    scanned = len(articles) + len(rejects)
    print(f"扫描到 {scanned} 篇（{args.vault_root / args.subtree}）")
    if rejects:
        by_reason = Counter(f"{account or '未知账号'}/{reason}" for account, reason in rejects)
        print(f"准入过滤：放行 {len(articles)} / 拒绝 {len(rejects)}（--filter off 关闭）")
        for key, cnt in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"    {key}: {cnt}")
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
        if rejects:
            print("\n被拒样本（前 10 条，确认规则无误再导）：")
            for account, reason in rejects[:10]:
                print(f"  ✗ [{account or '?'}] {reason}")
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
