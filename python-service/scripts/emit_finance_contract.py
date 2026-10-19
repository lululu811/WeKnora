#!/usr/bin/env python3
"""生成金融接口的线格式契约。

契约回答一个问题：**前端以为后端会发什么，后端真的发什么？**

两边各写一份的��候，字段改名/删掉不会有人发现 —— 前端拿到空值，
界面照常渲染成「今天没数据」，而后端日志里一切正常。这不是崩溃，
是静默失真：用户看到的是错的答案，而系统看起来是好的。

所以把线格式**写成一份、两边都对着它验**：

  · 前端侧（TS 用例）：声明的接口字段必须与契约一致 ——
    前端多要一个字段，就是后端不会发它（危险方向）；
    后端多发一个字段，前端没声明，记进 backend_only 并写清原因
    （无害方向，但要看得见）。
  · 后端侧（pytest 用例）：响应 healthy 时的实际键必须与契约一致。

「healthy」是这里的关键字：不少端点在数据没同步时返回
``{ok: false, reason, unavailable}`` 这种降级壳，字段自然不一样。
那种情况下**跳过**而不是判失败 —— 降级是数据状态，不是契约漂移。

用法（从 python-service 目录）::

    python3 scripts/emit_finance_contract.py            # 生成
    python3 scripts/emit_finance_contract.py --check    # 只比对，不写
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = SERVICE_DIR.parent
FRONTEND_DIR = REPO_DIR / "frontend" / "src" / "finance"
CONTRACT_PATH = SERVICE_DIR / "contracts" / "finance_api.json"

# 端点 → （方法, 查询参数, 请求体, TypeScript 接口名）
# 映射从 frontend/src/finance/api/*.ts 的 get<Type>('/api/market/xxx') 调用里读出来，
# 这里只补上「要用哪些参数才能拿到有数据的那份响应」。
CASES: list[dict[str, Any]] = [
    {"path": "/api/market/snapshot", "method": "GET", "query": {"days": 60}, "ts_interface": "MarketSnapshot"},
    {"path": "/api/market/dragon-tiger", "method": "GET", "query": {"limit": 5}, "ts_interface": "DragonTigerResponse"},
    {"path": "/api/market/sectors", "method": "GET", "query": {"limit": 5}, "ts_interface": "SectorResponse"},
    {"path": "/api/market/limit-up-ladder", "method": "GET", "query": {"days": 30}, "ts_interface": "LadderResponse"},
    {"path": "/api/market/basis", "method": "GET", "query": {}, "ts_interface": "BasisResponse"},
    {"path": "/api/market/commodities", "method": "GET", "query": {}, "ts_interface": "CommoditiesResponse"},
    {"path": "/api/market/fund-holdings", "method": "GET", "query": {"thscode": "600519.SH", "limit": 20},
     "ts_interface": "FundHoldingsResponse"},
    {"path": "/api/market/ladder-detail", "method": "GET", "query": {"limit": 20}, "ts_interface": "LadderDetailResponse"},
    {"path": "/api/market/anomaly-reasons", "method": "GET", "query": {"thscode": "600519.SH", "limit": 10},
     "ts_interface": "AnomalyReasonsResponse"},
    {"path": "/api/market/limit-break-down", "method": "GET", "query": {"limit": 20}, "ts_interface": "LimitBreakDownResponse"},
    {"path": "/api/market/dividend-calendar", "method": "GET", "query": {}, "ts_interface": "DividendCalendarResponse"},
    {"path": "/api/market/hot-stocks", "method": "GET", "query": {"mode": "rank", "limit": 50},
     "ts_interface": "HotStocksResponse"},
    {"path": "/api/market/hot-stock-trend", "method": "GET", "query": {"thscode": "600519.SH", "limit": 30},
     "ts_interface": "HotStockTrendResponse"},
    {"path": "/api/market/futures-warehouse", "method": "GET", "query": {"limit": 20}, "ts_interface": "FuturesWarehouseResponse"},
    {"path": "/api/market/futures-basis-rate", "method": "GET", "query": {"limit": 20}, "ts_interface": "FuturesBasisRateResponse"},
    {"path": "/api/market/etf-holders", "method": "GET", "query": {"thscode": "510300.SH", "limit": 10},
     "ts_interface": "EtfHoldersResponse"},
    {"path": "/api/market/etf-institutional-ranking", "method": "GET", "query": {"limit": 20},
     "ts_interface": "EtfInstitutionalRankingResponse"},
    {"path": "/api/market/squeeze-status", "method": "GET", "query": {"thscode": "600519.SH", "limit": 30},
     "ts_interface": "SqueezeStatusResponse"},
    {"path": "/api/market/auction", "method": "GET", "query": {}, "ts_interface": "AuctionResponse"},
    {"path": "/api/market/technicals", "method": "POST", "body": {"thscodes": ["600519.SH"]},
     "ts_interface": "TechnicalsResponse"},
    # industry-map 的返回类型是内联的（{ code, data: Record<string, IndustryInfo> }），
    # 没有专门的 interface —— 字段在这里手写，仍然两边都对着契约验。
    {"path": "/api/market/industry-map", "method": "POST", "body": {"thscodes": ["600519.SH"]},
     "ts_interface": None, "fields": ["code", "data"]},
]

# 组件里的类型常常不是 export 的（`interface AuctionResponse {`），
# 所以两种写法都认。
INTERFACE_RE = re.compile(r"^(?:export\s+)?interface\s+(\w+)\s*\{", re.M)
FIELD_RE = re.compile(r"^\s{2,6}([A-Za-z_]\w*)\??\s*:", re.M)


def read_interfaces() -> dict[str, list[str]]:
    """从 TS 源里抽出每个 interface 的字段名。

    只认顶层字段（缩进 2~6 空格）；嵌套对象类型的字段不进表 ——
    契约盯的是「顶层少了哪个字段」，那才是会让整块面板空掉的失真。
    """
    out: dict[str, list[str]] = {}
    # 接口类型不一定住在 api 目录 —— 比如 /api/market/auction 的类型定义在
    # 组件里（AuctionResponse）。所以扫整个 finance 目录。
    candidates = sorted(FRONTEND_DIR.rglob("*.ts")) + sorted(FRONTEND_DIR.rglob("*.vue"))
    for path in candidates:
        if path.name.endswith(".test.ts"):
            continue
        src = path.read_text(encoding="utf-8")
        for m in INTERFACE_RE.finditer(src):
            name = m.group(1)
            start = m.end()
            depth = 1
            i = start
            while i < len(src) and depth > 0:
                if src[i] == "{":
                    depth += 1
                elif src[i] == "}":
                    depth -= 1
                i += 1
            body = src[start:i]
            fields = []
            for line in body.split("\n"):
                fm = FIELD_RE.match(line)
                if fm:
                    fields.append(fm.group(1))
            out[name] = fields
    return out


def probe_all(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """打一遍所有端点，返回 {路径: healthy 时的顶层键}；降级或失败是 None。

    **必须**用 with：TestClient 不进上下文就不会跑 lifespan，数据源一个都没注册
    —— 每个端点都会答「数据源未就绪」，探到的就不是真实形状而是一层降级壳。

    也**只开一次**客户端：lifespan 每跑一次就往注册表里再注册一遍数据源，
    而注册表是进程级单例，第二次注册会报「数据源 'index' 已存在」。
    """
    from fastapi.testclient import TestClient

    # 脚本住在 scripts/ 下，服务根目录不在 sys.path 里，import main 会失败。
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))

    import main  # noqa: PLC0415 — 放在函数里：import main 会把整个服务拉起来

    out: dict[str, dict[str, Any] | None] = {}
    with TestClient(main.app) as client:
        for case in cases:
            method = case["method"]
            if method == "POST":
                resp = client.post(case["path"], json=case.get("body", {}))
            else:
                resp = client.get(case["path"], params=case.get("query", {}))
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001 — 非 JSON 响应也是被测对象
                body = {}
            if not isinstance(body, dict) or body.get("ok") is False:
                out[case["path"]] = None
                continue
            out[case["path"]] = {"keys": sorted(body.keys()), "body": body}
    return out


def build_contract(probed: dict[str, dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """拼出契约。

    **字段以��端实际发的为准**（探针结果），前端声明只用来分三类：

      fields         健康形态下后端发的字段（真相）
      backend_only   健康形态下后端发了、但前端没声明的（前端不读，但要知道它存在）
      degraded_only  前端声明了、但只在降级形态下才出现的
                     （降级壳是 {ok:false, reason, unavailable}，reason 就属于这一类）

    为什么要分三处：接口类型写的是**两种形态的并集** —— 同一个
    SectorResponse，健康时没有 reason，降级时才有 reason。笼统地比一套
    字段集，要么误报（健康形态少一个字段），要么漏报（降级形态多一个）。
    """
    interfaces = read_interfaces()
    endpoints: dict[str, Any] = {}
    for case in CASES:
        path = case["path"]
        ts_iface = case.get("ts_interface")
        entry: dict[str, Any] = {
            "method": case["method"],
            "ts_interface": ts_iface,
        }
        if case.get("query"):
            entry["query"] = case["query"]
        if case.get("body"):
            entry["body"] = case["body"]

        declared = interfaces.get(ts_iface or "", [])
        result = (probed or {}).get(path)
        if result is not None:
            actual = set(result["keys"])
        elif case.get("fields"):
            actual = set(case["fields"])       # 手写覆盖（内联类型）
        else:
            actual = set(declared)

        entry["fields"] = sorted(actual)
        backend_only = sorted(actual - set(declared))
        degraded_only = sorted(set(declared) - actual)
        if backend_only:
            entry["backend_only"] = backend_only
        if degraded_only:
            entry["degraded_only"] = degraded_only
        endpoints[path] = entry
    return {
        "version": 1,
        "generated_by": "python-service/scripts/emit_finance_contract.py",
        "note": (
            "fields 是前端声明并依赖的顶层字段；backend_only 是后端多发、"
            "前端当前不用的字段。两边对着这份契约验：前端多要一个字段 = "
            "后端不会发它（危险）；后端多发一个字段 = 记进 backend_only 并写明原因。"
        ),
        "endpoints": endpoints,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args()

    probed: dict[str, dict[str, Any] | None] = {}
    try:
        probed = probe_all(CASES)
    except Exception as exc:  # noqa: BLE001 — 探不到就退回用前端声明生成
        print(f"（探针失败，契约退回前端声明为准：{type(exc).__name__}: {exc}）", file=sys.stderr)

    contract = build_contract(probed)
    text = json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=False) + "\n"

    if args.check:
        if not CONTRACT_PATH.exists():
            print(f"契约文件不存在：{CONTRACT_PATH}", file=sys.stderr)
            return 1
        current = CONTRACT_PATH.read_text(encoding="utf-8")
        if current != text:
            print("契约与生成器输出不一致 —— 跑一次 emit_finance_contract.py", file=sys.stderr)
            return 1
        print("契约与生成器输出一致 ✓")
        return 0

    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existed = CONTRACT_PATH.exists()
    CONTRACT_PATH.write_text(text, encoding="utf-8")
    print(f"{'已更新' if existed else '已生成'}：{CONTRACT_PATH}（{len(contract['endpoints'])} 个端点）")

    # 顺带报一次漂移：后端多发的字段（前端没声明）。
    try:
        probed = probe_all(CASES)
        for path, result in probed.items():
            if result is None:
                print(f"  - {path} 本机探不到（数据未就绪），未参与漂移比对")
                continue
            entry = contract["endpoints"][path]
            extra = sorted(set(result["keys"]) - set(entry.get("fields", [])))
            if extra:
                print(f"  ! {path} 后端多发、契约里没有：{extra}")
    except Exception as exc:  # noqa: BLE001 — 探针失败不该让生成失败
        print(f"（漂移探测跳过：{type(exc).__name__}: {exc}）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
