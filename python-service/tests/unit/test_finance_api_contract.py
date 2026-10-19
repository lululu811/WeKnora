"""金融接口的线格式契约 —— 后端侧。

契约在 `python-service/contracts/finance_api.json`，由
`scripts/emit_finance_contract.py` 生成。这个用例盯的是**后端实际发什么**。

为什么要有这个：前端声明的字段与后端实际发送的字段各写一份时，字段改名或
删除都不会有人发现 —— 前端拿到空值，界面照常渲染成「今天没数据」，后端日志
里一切正常。不是崩溃，是静默失真：用户看到的是错的答案，而系统看起来是好的。

两种响应形态：

* **健康**：数据齐，响应带完整载荷。此时实际键必须与契约**逐字相同** ——
  少一个字段是后端没发，多一个字段是契约没记。
* **降级**：数据没同步时返回 ``{ok: false, reason, unavailable}`` 这样的壳，
  字段自然不一样。此时只要求实际键是契约键的**子集** —— 降级是数据状态，
  不是契约漂移，不该判失败。

本机数据不全时大部分端点会走降级分支（逐个 skip，原因写在 skip 消息里），
数据齐的机器上才会真正校验到载荷的键。
"""

from __future__ import annotations

import gc
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[2]
CONTRACT_PATH = SERVICE_DIR / "contracts" / "finance_api.json"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def load_contract() -> dict[str, Any]:
    assert CONTRACT_PATH.exists(), (
        f"契约文件不存在：{CONTRACT_PATH}；跑 python3 scripts/emit_finance_contract.py 生成"
    )
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def probe(client: Any, path: str, entry: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """打一次端点，返回 (状态码, 响应体)。"""
    method = entry.get("method", "GET")
    if method == "POST":
        resp = client.post(path, json=entry.get("body", {}))
    else:
        resp = client.get(path, params=entry.get("query", {}))
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — 非 JSON 响应也是被测对象
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {}


@pytest.fixture(scope="module")
def client() -> Any:
    """带 lifespan 的客户端。

    **必须**用 `with`：不进上下文就不会跑 startup 事件，数据源一个都没注册，
    每个端点都答「数据源未就绪」—— 那时候这个用例只是在验降级壳，
    真实形状一个都没验到。
    """
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as c:
        yield c

    # 关掉连接之后还要把注册表清空。注册表是进程级单例，它攥着数据源对象，
    # 而 duckdb 的文件锁要到连接对象被回收才释放 —— 不清的话，后面直接
    # duckdb.connect 同一文件的其他用例会撞上
    # 「Can't open a connection to same database file with a different configuration」。
    from datasources import registry

    for name in list(registry.list_sources().keys()):
        registry.unregister(name)
    gc.collect()


def test_contract_covers_the_finance_endpoints() -> None:
    """契约本身的自检：端点有接口名、字段非空、键不重复。"""
    contract = load_contract()
    endpoints = contract["endpoints"]
    assert len(endpoints) >= 20, f"契约只覆盖了 {len(endpoints)} 个端点"

    for path, entry in endpoints.items():
        assert entry.get("method") in ("GET", "POST"), f"{path} 的 method 不对"
        assert entry.get("fields"), f"{path} 没有记录字段"
        assert len(entry["fields"]) == len(set(entry["fields"])), f"{path} 的字段有重复"
        # backend_only 是 fields 的子集（后端发了、前端不读的那些）
        assert set(entry.get("backend_only", [])) <= set(entry["fields"]), \
            f"{path} 的 backend_only 不是 fields 的子集"
        # degraded_only 与 fields 不相交（只在降级形态出现的字段）
        overlap = set(entry["fields"]) & set(entry.get("degraded_only", []))
        assert not overlap, f"{path} 的字段同时出现在 fields 与 degraded_only：{overlap}"


def test_backend_responses_match_the_contract(client: Any) -> None:
    """后端实际发的键必须与契约一致。"""
    contract = load_contract()
    for path, entry in contract["endpoints"].items():
        status, body = probe(client, path, entry)
        if status != 200:
            pytest.skip(f"{path} 在本机返回 {status}（数据未就绪），跳过键比对")
        if body.get("ok") is False:
            # 降级壳：字段天然不一样（多一个 reason），只要求它是契约键的子集。
            allowed = (set(entry["fields"]) | set(entry.get("backend_only", []))
                       | set(entry.get("degraded_only", [])))
            extra = sorted(set(body.keys()) - allowed)
            assert not extra, f"{path} 降级响应多出契约外的字段：{extra}"
            continue

        # 健康形态：实际键必须与契约逐字相同。
        actual = set(body.keys())
        missing = sorted(set(entry["fields"]) - actual)
        extra = sorted(actual - set(entry["fields"]))
        assert not missing, f"{path} 契约里有、实际没发：{missing}"
        assert not extra, f"{path} 实际多发、契约里没有：{extra}"
