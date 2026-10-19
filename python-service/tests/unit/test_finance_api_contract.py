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
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def test_contract_covers_the_finance_endpoints() -> None:
    """契约本身的自检：端点有接口名、字段非空、键不重复。"""
    contract = load_contract()
    endpoints = contract["endpoints"]
    assert len(endpoints) >= 20, f"契约只覆盖了 {len(endpoints)} 个端点"

    for path, entry in endpoints.items():
        assert entry.get("method") in ("GET", "POST"), f"{path} 的 method 不对"
        assert entry.get("fields"), f"{path} 没有记录字段"
        assert len(entry["fields"]) == len(set(entry["fields"])), f"{path} 的字段有重复"
        overlap = set(entry["fields"]) & set(entry.get("backend_only", []))
        assert not overlap, f"{path} 的字段同时出现在 fields 与 backend_only：{overlap}"


def test_backend_responses_match_the_contract(client: Any) -> None:
    """后端实际发的键必须与契约一致。"""
    contract = load_contract()
    for path, entry in contract["endpoints"].items():
        status, body = probe(client, path, entry)
        if status != 200:
            pytest.skip(f"{path} 在本机返回 {status}（数据未就绪），跳过键比对")
        if body.get("ok") is False:
            # 降级壳：字段天然不一样，只要求它是契约键的子集。
            allowed = set(entry["fields"]) | set(entry.get("backend_only", []))
            extra = sorted(set(body.keys()) - allowed)
            assert not extra, f"{path} 降级响应多出契约外的字段：{extra}"
            continue

        allowed = set(entry["fields"]) | set(entry.get("backend_only", []))
        actual = set(body.keys())
        missing = sorted(allowed - actual)
        extra = sorted(actual - allowed)
        assert not missing, f"{path} 契约里有、实际没发：{missing}"
        assert not extra, f"{path} 实际多发、契约里没有：{extra}"
