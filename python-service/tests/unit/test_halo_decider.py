"""定性维度判分层（halo/decider.py）测试。

锁住的都是**真实后果**，不是实现细节：

* **空实现必须返回 7 个 SlotScore 而不是空列表** —— 影子记录的结构要每次都
  完整，否则「今天没跑判分」和「跑了但判分器挂了」在数据上无法区分。
* **score=None 与 score=0 必须可区分** —— 前者是「没判出来」，后者是
  「判了极低分」。统计时混为一谈会把均值往下拉。
* **判分器挂掉不许冒泡** —— 它是旁路，抛异常会连带 422/500，而
  ``/halo/verify`` 的返回值此刻已经算好了，白丢。
* **影子绝不进返回体** —— 一旦 agent 看得到 ``decider_scores``，它就可能开始
  「参考」判分器给分，影子期就失去了意义（这是自我实现的因果污染）。
* **半截文件不许留下** —— 进程被 kill 后，``os.replace`` 之前的窗口里磁盘上
  是 ``.tmp``；读到半截 JSON 的下游脚本会静默丢样本。
"""

import asyncio
import json
import os

import pytest

import main
from halo import decider
from halo.decider import (
    NullQualitativeScorer,
    ShadowRecorder,
    SlotScore,
    collect_shadow,
    get_scorer,
)

# 7 个定性维度，键与 halo/analyze.py:AI_DIMENSIONS 对齐
DIMS = ["moat", "stag", "esg", "management", "shareholder", "valuation", "risk"]


def _slots() -> list[dict]:
    """形状与 build_ai_slots() 的真实返回一致（含 percent→小数 的归一结果、
    missing_anchors 分离、以及 risk 维度特有的 hard_risk_facts）。"""
    return [
        {
            "dimension": "moat",
            "label": "护城河",
            "anchors": {"gross_margin": 0.9153, "roe": 0.3121, "net_margin": 0.5213},
            "missing_anchors": [],
            "has_anchor": True,
            "score": None,
        },
        {
            "dimension": "stag",
            "label": "滞胀防御",
            "anchors": {"tangible_pct": 0.074, "assets_debt_ratio": 0.0812,
                        "current_ratio": 5.09, "ocf": 61_522_000_000.0},
            "missing_anchors": [],
            "has_anchor": True,
            "score": None,
        },
        {
            "dimension": "esg",
            "label": "ESG",
            "anchors": {"employees_total": 34992, "revenue_per_employee": 4_824_112.0},
            "missing_anchors": ["emissions"],
            "has_anchor": False,
            "score": None,
        },
        {
            "dimension": "management",
            "label": "管理层",
            "anchors": {"roe": 0.3121, "assets_debt_ratio": 0.0812},
            "missing_anchors": ["dividend_payout"],
            "has_anchor": False,
            "score": None,
        },
        {
            "dimension": "shareholder",
            "label": "股东资金面",
            "anchors": {},
            "missing_anchors": ["main_fund_flow", "holder_count"],
            "has_anchor": False,
            "score": None,
        },
        {
            "dimension": "valuation",
            "label": "估值",
            "anchors": {"pe_ttm": 21.4, "pb": 7.9, "ps": 8.1, "pcf": 18.2},
            "missing_anchors": [],
            "has_anchor": True,
            "score": None,
        },
        {
            "dimension": "risk",
            "label": "风险",
            "anchors": {"assets_debt_ratio": 0.0812, "current_ratio": 5.09,
                        "hard_risk_facts": {"internal_control_nonstandard": "标准无保留"}},
            "missing_anchors": ["ocf_to_profit", "pe_percentile"],
            "has_anchor": False,
            "score": None,
        },
    ]


def _llm_scores() -> dict:
    return {"moat": 8, "stag": 7, "esg": 6, "management": 7,
            "shareholder": 5, "valuation": 6, "risk": 4}


@pytest.fixture
def rec(tmp_path) -> ShadowRecorder:
    return ShadowRecorder(directory=tmp_path / "shadow")


# ---------------------------------------------------------------------------
# 1. 空实现
# ---------------------------------------------------------------------------


def test_null_scorer_returns_all_none():
    """7 个 slot 进，7 个 SlotScore 出，全部 score=None、source='null'。

    刻意不返回空列表：空列表会让「没配判分器」和「判分器全挂了」在影子数据里
    长得一样。
    """
    slots = _slots()
    out = asyncio.run(NullQualitativeScorer().score_slots("600519", "2025-12-31", slots))

    assert [s.dimension for s in out] == DIMS
    assert all(s.score is None for s in out), "空实现不得凭空给分"
    assert all(s.source == "null" for s in out)
    # score=None 与 score=0 必须可区分
    assert all(s.score != 0 for s in out)


def test_get_scorer_defaults_to_null(monkeypatch):
    monkeypatch.delenv("HALO_DECIDER", raising=False)
    assert isinstance(get_scorer(), NullQualitativeScorer)

    monkeypatch.setenv("HALO_DECIDER", "null")
    assert isinstance(get_scorer(), NullQualitativeScorer)

    # 未知实现也必须降级成 Null，而不是抛异常把 /halo/verify 带崩
    monkeypatch.setenv("HALO_DECIDER", "typesafe/jev-1.13")
    assert isinstance(get_scorer(), NullQualitativeScorer)


# ---------------------------------------------------------------------------
# 2/3. 记录完整性与隔离
# ---------------------------------------------------------------------------


def test_record_writes_complete_envelope(rec):
    """四个必备块齐全，标识字段非空。"""
    path = asyncio.run(collect_shadow(
        thscode="600519",
        period="2025-12-31",
        report_type="annual",
        llm_scores=_llm_scores(),
        slots=_slots(),
        recheck={"ok": True, "total": 7.21, "rating": "强"},
        recorder=rec,
    ))

    assert path is not None and path.exists()
    env = json.loads(path.read_text(encoding="utf-8"))

    for key in ("llm_scores", "slots", "decider_scores", "recheck"):
        assert key in env, f"影子记录缺 {key}"
    assert env["thscode"] == "600519"
    assert env["period"] == "2025-12-31"
    assert env["run_id"] and env["ts"]
    assert env["error"] is None

    # 基线：7 维标量原样落盘
    assert env["llm_scores"] == _llm_scores()
    # 证据：7 个槽位，锚点与 has_anchor 区分都保住了
    assert [s["dimension"] for s in env["slots"]] == DIMS
    by_dim = {s["dimension"]: s for s in env["slots"]}
    assert by_dim["moat"]["has_anchor"] is True
    assert by_dim["esg"]["has_anchor"] is False
    assert by_dim["esg"]["missing_anchors"] == ["emissions"]
    # 本轮判分器是空实现：结构在，分数全空
    assert [d["dimension"] for d in env["decider_scores"]] == DIMS
    assert all(d["score"] is None for d in env["decider_scores"])


def test_record_isolated_per_run(rec):
    """同参数连写两次 → 两个文件，互不覆盖。

    影子记录是 append-only 的一次性产物：重跑 /halo/verify 产生新 run_id，
    旧文件保留供对比。共享一个文件会要求文件锁，不值得。
    """
    kwargs = dict(thscode="600519", period="2025-12-31", report_type="annual",
                  llm_scores=_llm_scores(), slots=_slots(), recheck={"ok": True},
                  recorder=rec)
    p1 = asyncio.run(collect_shadow(**kwargs))
    p2 = asyncio.run(collect_shadow(**kwargs))

    assert p1 != p2, "两次 run 必须落到不同文件"
    assert p1.exists() and p2.exists()
    assert len(list(rec.directory.glob("*.json"))) == 2


# ---------------------------------------------------------------------------
# 4. 判分器故障不许冒泡
# ---------------------------------------------------------------------------


def test_scorer_failure_does_not_raise(rec):
    """判分器抛异常 → decider_scores 空 + error 非空，主流程照常拿到路径。"""

    class Boom:
        async def score_slots(self, thscode, period, slots):
            raise RuntimeError("connection refused")

    path = asyncio.run(collect_shadow(
        thscode="600519", period="2025-12-31", report_type="annual",
        llm_scores=_llm_scores(), slots=_slots(), recheck={"ok": True},
        scorer=Boom(), recorder=rec,
    ))

    assert path is not None, "判分失败不该丢掉整条影子记录"
    env = json.loads(path.read_text(encoding="utf-8"))
    assert env["decider_scores"] == []
    assert "connection refused" in env["error"]
    # 基线照样在 —— 这条记录的价值恰恰是「LLM 给了什么分」，
    # 判分器缺席只是少了一半信息
    assert env["llm_scores"] == _llm_scores()


def test_scorer_timeout_is_captured(rec, monkeypatch):
    """超时按失败记录，不冒泡（SCORER_TIMEOUT_SECONDS 由本模块定义）。"""

    class Slow:
        async def score_slots(self, thscode, period, slots):
            await asyncio.sleep(5)

    monkeypatch.setattr(decider, "SCORER_TIMEOUT_SECONDS", 0.01)
    path = asyncio.run(collect_shadow(
        thscode="600519", period="2025-12-31", report_type="annual",
        llm_scores=_llm_scores(), slots=_slots(), recheck={},
        scorer=Slow(), recorder=rec,
    ))

    env = json.loads(path.read_text(encoding="utf-8"))
    assert env["decider_scores"] == []
    assert "TimeoutError" in env["error"] or "timeout" in env["error"].lower()


def test_unserializable_anchor_does_not_break_record(rec):
    """锚点里出现不可序列化的值时，影子仍要落盘（default=str 兜住）。

    锚点来自事实库，类型不保证全是标量；影子是旁路，不能因为一个怪值
    就把已经算好的 /halo/verify 带崩。
    """

    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    slots = _slots()
    slots[0]["anchors"]["gross_margin"] = Weird()

    path = asyncio.run(collect_shadow(
        thscode="600519", period="2025-12-31", report_type="annual",
        llm_scores=_llm_scores(), slots=slots, recheck={}, recorder=rec,
    ))
    assert path is not None
    assert json.loads(path.read_text(encoding="utf-8"))["thscode"] == "600519"


# ---------------------------------------------------------------------------
# 5. 半截文件
# ---------------------------------------------------------------------------


def test_no_partial_file_on_crash(rec, monkeypatch):
    """os.replace 失败时不得留下可被读到的 .json。

    读到半截 JSON 的下游脚本会静默丢样本，而影子样本本来就少，
    再悄悄丢一批就攒不出影子期要的 ≥30 个成对样本了。
    """
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        rec.write({"thscode": "600519", "period": "2025-12-31", "run_id": "x"})

    assert list(rec.directory.glob("*.json")) == [], "崩溃时不得留下 .json"


def test_tmp_file_is_not_readable_as_record(rec, monkeypatch):
    """正常路径：临时文件已被 rename 掉，目录里只剩一个 .json。"""
    rec.write({"thscode": "600519", "period": "2025-12-31", "run_id": "abc"})
    assert list(rec.directory.glob("*.tmp")) == []
    assert len(list(rec.directory.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# 6. 影子不外泄（第一铁律）
# ---------------------------------------------------------------------------


def test_shadow_absent_from_response(monkeypatch, tmp_path):
    """打真实的 /halo/verify handler，断言返回体不含任何影子字段。

    影子一旦出现在 agent 可见的返回体里，它就可能被 agent 当成判分依据 ——
    影子期随之失效（自我实现的因果污染）。这是必须由测试锁死的不变量。
    """
    import halo.analyze as analyze_mod
    import halo.scoring as scoring_mod
    import halo.store as store_mod

    async def fake_analyze(thscode, store=None, period=None, report_type=None,
                           scope=None, include_external=None):
        return {
            "thscode": thscode,
            "period": period or "2025-12-31",
            "halo": {"ok": True, "score": 24.5, "rating": "强"},
            "growth": {"ok": True, "score": 8.0, "total": 8.0, "rating": "强"},
            "ai_slots": _slots(),
        }

    monkeypatch.setattr(analyze_mod, "analyze", fake_analyze)
    monkeypatch.setattr(scoring_mod, "verify_comprehensive",
                        lambda declared_total, scores, declared_rating=None: {
                            "ok": True, "total": 7.21, "rating": "强"})
    monkeypatch.setattr(store_mod, "FactStore", lambda *a, **k: object())
    monkeypatch.setenv("HALO_SHADOW_DIR", str(tmp_path / "shadow"))

    request = main.HaloVerifyRequest(
        thscode="600519",
        report_type="annual",
        scores=_llm_scores(),
        declared_total=7.2,
        declared_rating="强",
    )
    body = asyncio.run(main.halo_verify(request))

    # 返回体的键必须与改动前完全一致
    assert set(body) == {
        "thscode", "period", "server_recomputed",
        "accepted_ai_scores", "verdict", "ok",
    }
    for leaked in ("decider_scores", "llm_scores", "slots", "shadow", "confidence"):
        assert leaked not in body, f"影子字段 {leaked} 泄漏到返回体"

    # 同一调用确实落了影子（证明测的不是「影子压根没跑」的空断言）
    files = list((tmp_path / "shadow").glob("*.json"))
    assert len(files) == 1
    env = json.loads(files[0].read_text(encoding="utf-8"))
    assert env["llm_scores"] == _llm_scores()
    assert len(env["slots"]) == 7
    assert env["recheck"]["total"] == 7.21


def test_shadow_failure_does_not_change_response(monkeypatch, tmp_path):
    """影子写盘炸了，/halo/verify 的返回体仍然一模一样。"""
    import halo.analyze as analyze_mod
    import halo.decider as decider_mod
    import halo.scoring as scoring_mod
    import halo.store as store_mod

    async def fake_analyze(thscode, store=None, period=None, report_type=None,
                           scope=None, include_external=None):
        return {
            "thscode": thscode, "period": period or "2025-12-31",
            "halo": {"ok": True, "score": 24.5, "rating": "强"},
            "growth": {"ok": True, "score": 8.0, "total": 8.0, "rating": "强"},
            "ai_slots": _slots(),
        }

    async def boom(**kwargs):
        raise RuntimeError("shadow recorder down")

    monkeypatch.setattr(analyze_mod, "analyze", fake_analyze)
    monkeypatch.setattr(scoring_mod, "verify_comprehensive",
                        lambda declared_total, scores, declared_rating=None: {
                            "ok": True, "total": 7.21, "rating": "强"})
    monkeypatch.setattr(store_mod, "FactStore", lambda *a, **k: object())
    monkeypatch.setattr(decider_mod, "collect_shadow", boom)

    request = main.HaloVerifyRequest(
        thscode="600519", report_type="annual", scores=_llm_scores(),
        declared_total=7.2, declared_rating="强",
    )
    body = asyncio.run(main.halo_verify(request))
    assert body["ok"] is True
    assert body["verdict"]["total"] == 7.21


# ---------------------------------------------------------------------------
# SlotScore 数据形状
# ---------------------------------------------------------------------------


def test_slot_score_roundtrip():
    """SlotScore 带概率分布落盘后形状不丢 —— 没有分布就算不出 ECE。"""
    from dataclasses import asdict

    s = SlotScore(
        dimension="moat", score=8.0, confidence=0.97,
        probabilities={"0": 0.01, "8": 0.83, "10": 0.16},
        source="typesafe/jev-1.13", note="锚点齐全",
    )
    d = asdict(s)
    assert d["dimension"] == "moat"
    assert d["confidence"] == 0.97
    assert d["probabilities"] == {"0": 0.01, "8": 0.83, "10": 0.16}
    assert d["source"] == "typesafe/jev-1.13"
