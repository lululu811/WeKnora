#!/usr/bin/env python3
"""定性维度判分层（封闭判断通道 + 影子记录）。

背景
----
``/halo/score`` 返回 7 个定性维度的待判槽位（:func:`halo.analyze.build_ai_slots`），
由驱动 agent 的 LLM 自由给出 0–10 分，再经 ``/halo/verify`` 复算校验。
这条链路有一个结构性缺口：分数是**自由生成的标量**，没有任何机制保证它可复现
或可校准 —— 同一家公司、同一份事实库、同一组锚点，两次调用可以给出不同的分。
``RECHECK_TOLERANCE`` 只校验「声明 vs 复算」的自洽性，不校验分数本身是否稳定。

本模块引入可插拔的封闭判断通道 :class:`QualitativeScorer` 来填这个缺口。
**当前轮次只做接口 + 空实现 + 影子记录**：判分层只旁路记录，不参与生产判分，
``/halo/verify`` 的返回体一个字节都不变。

为什么是「封闭判断」
------------------
封闭判断模型（System One 三原语 Choice / Score / Noul：只输出**预定义取值**与
概率，不生成文本）正好匹配这 7 个维度的形状 —— 有序档位上的标量，输入是量化
锚点。本机 ``~/002_tools/openjev-multimodal`` 已跑通同契约的 ``/v1/systemone``
端点，所以 :class:`QualitativeScorer` 按该契约设计，实现方将来是 drop-in。

刻意**不在本模块里实现具体模型**：python-service 是零依赖服务，引入推理 SDK
会破坏这个性质（而且模型/重试/超时的策略应该由调用方按自己的部署决定，不该被
一个判分器绑死）。这一取舍与 :mod:`halo.extractor` 的 ``LLMExtractor`` 一致，
不发明新范式。

失败语义
--------
判分层是旁路，失败只应该让影子记录少一条，绝不能影响 ``/halo/verify`` 的返回。
:func:`collect_shadow` 不抛异常（内部兜住），调用方 ``main.py`` 那侧再包一层
try/except 兜底 —— 与 ``NullLLMExtractor`` 的既有语义同源：可选通道的不可用是
一个**部署状态**，不是一次运行失败。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

#: 影子记录默认目录。与 ``halo/store.py`` 的 ``HALO_DB_PATH``
#: （默认 ``~/.hithink-finance/halo.sqlite``）同处一个数据根：判分数据是运行
#: 产物不是源码，不该进仓库，也就不需要动 ``.gitignore``。
DEFAULT_SHADOW_DIR = "~/.hithink-finance/halo-shadow"

#: 判分器的超时预算。影子不设重试 —— 判分失败的最优动作是「这一条没有判分
#: 结果」，重试只会让 ``/halo/verify`` 的响应变慢，而它是被 agent 同步调用的。
SCORER_TIMEOUT_SECONDS = 30.0

#: 判分层标识。写进影子记录的 ``source`` 字段，是将来区分不同实现与不同模型的
#: 唯一依据。空实现用 ``null``，与 ``extractor.py`` 的 ``NullLLMExtractor`` 同义。
SOURCE_NULL = "null"


def _env(name: str, default: str = "") -> str:
    """环境变量优先，空串视为未设置（与项目其他模块一致）。"""
    v = os.getenv(name)
    return v if v else default


def shadow_dir() -> Path:
    return Path(_env("HALO_SHADOW_DIR", DEFAULT_SHADOW_DIR)).expanduser()


# ---------------------------------------------------------------------------
# 判分结果
# ---------------------------------------------------------------------------


@dataclass
class SlotScore:
    """单个定性维度的判分结果。

    ``score`` 为 ``None`` 表示「这一维没有判分结果」—— 这与「判了 0 分」是两
    件完全不同的事，影子记录必须能区分，否则将来统计会把「没判分」当成
    「判了极低分」而污染均值。

    刻意带 ``probabilities`` 而不是只留标量：影子的目的是攒**带置信度的成对
    样本**。只存一个数，将来无法判断分歧是判分器错了，还是两套判分都对只是
    粒度不同；有分布才能算 ECE（期望校准误差），而校准正是封闭判断模型唯一
    值得认真对待的卖点。
    """

    dimension: str
    score: Optional[float] = None
    confidence: Optional[float] = None
    probabilities: Optional[Dict[str, float]] = None
    source: str = SOURCE_NULL
    note: str = ""


class QualitativeScorer(Protocol):
    """定性维度判分通道的注入接口。

    刻意**不在本模块里实现**：见模块 docstring 的取舍说明。

    实现方只需对传入的每个 slot 返回一个 :class:`SlotScore`，``dimension``
    与输入 slot 对齐即可。返回 ``score=None`` 表示「这一维没判出来」，实现方
    应当逐维降级而不是整条放弃 —— 一维失败不该拖垮另外六维。

    约定（对齐 ``~/002_tools/openjev-multimodal`` 的 ``/v1/systemone``）：
    一次调用承载全部 7 问，共享同一份 state，摊薄预填充开销。
    """

    async def score_slots(  # pragma: no cover - 协议
        self,
        thscode: str,
        period: str,
        slots: Sequence[Dict[str, Any]],
    ) -> List[SlotScore]:
        ...


class NullQualitativeScorer:
    """未接入判分器时的降级实现。

    对每个 slot 原样返回一个 ``score=None`` 的 :class:`SlotScore`，而不是抛
    异常或返回空列表：影子记录的结构必须每次都完整，否则「今天没跑判分」和
    「跑了但判分器挂了」在数据上无法区分。
    """

    def __init__(self, reason: str = "未注入定性维度判分器") -> None:
        self.reason = reason

    async def score_slots(
        self,
        thscode: str,
        period: str,
        slots: Sequence[Dict[str, Any]],
    ) -> List[SlotScore]:
        logger.debug("判分通道不可用（%s），本轮不记判分结果", self.reason)
        return [
            SlotScore(dimension=str(s.get("dimension") or ""), source=SOURCE_NULL)
            for s in slots
        ]


def get_scorer() -> QualitativeScorer:
    """按环境变量选实现。``HALO_DECIDER`` 为空或 ``null`` -> 空实现。

    本轮不注册任何真实实现；这个工厂存在的意义是让「接入判分器」将来只是
    往这里加一个分支，而不必改动 :func:`collect_shadow` 的调用方。
    """
    name = _env("HALO_DECIDER", SOURCE_NULL).strip().lower()
    if name in ("", SOURCE_NULL):
        return NullQualitativeScorer()
    logger.warning(
        "HALO_DECIDER=%r 无可用实现，本轮只支持空判分器；已降级为 Null", name
    )
    return NullQualitativeScorer(f"HALO_DECIDER={name!r} 无可用实现")


# ---------------------------------------------------------------------------
# 影子记录
# ---------------------------------------------------------------------------


@dataclass
class ShadowRecorder:
    """把一次判分现场写成 JSON 文件。

    **每次 run 一个独立文件，不 append 到共享 JSONL**。``/halo/verify`` 可能被
    并发调用（agent 侧无串行保证），append 共享文件需要文件锁；独立文件名彻底
    绕开竞态，而且单个文件很小（锚点只有几十个数字，估算 <1KB），不值得为此
    引入锁的复杂度与死锁面。
    """

    directory: Path = field(default_factory=shadow_dir)

    def write(self, envelope: Dict[str, Any]) -> Path:
        """落盘。**先写临时文件再 :func:`os.replace`**，同目录 rename 在 POSIX
        上是原子的 —— 读者永远看不到半截 JSON，进程被 kill 最多留一个 ``.tmp``
        残留（下次清理即可）。
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        run_id = str(envelope.get("run_id") or _new_run_id())
        thscode = str(envelope.get("thscode") or "unknown")
        period = str(envelope.get("period") or "unknown")
        final = self.directory / f"{thscode}-{period}-{run_id}.json"
        tmp = final.with_suffix(".tmp")

        payload = dict(envelope)
        payload["run_id"] = run_id
        # default=str：slots 里的锚点来自事实库，类型不保证全是标量。影子是
        # 旁路，绝不能因为一个不可序列化的值就把 /halo/verify 带崩。
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, final)
        return final


def _new_run_id() -> str:
    """``<秒级时间戳>-<8 位随机>``。时间戳让文件名天然按时间排序，随机段防同秒
    内的并发碰撞。"""
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


async def collect_shadow(
    *,
    thscode: str,
    period: str,
    report_type: str,
    llm_scores: Dict[str, float],
    slots: Sequence[Dict[str, Any]],
    recheck: Dict[str, Any],
    scorer: Optional[QualitativeScorer] = None,
    recorder: Optional[ShadowRecorder] = None,
) -> Optional[Path]:
    """跑一次判分并落一条影子记录。**不抛异常。**

    三个降级点，任何一个失败都只影响本条影子记录：

    1. 判分器没注入 / 抛异常 / 超时 -> ``decider_scores`` 留空 + ``error`` 非空
    2. 目录不可写 -> 记日志，返回 ``None``
    3. 序列化遇到怪值 -> ``json.dumps(default=str)`` 兜住

    ``llm_scores`` 与 ``slots`` 在同一个函数调用点上同时可得：``/halo/verify``
    收到 LLM 交的 7 个分（``request.scores``），又已经跑过 ``analyze()`` 拿到了
    全部量化锚点（``result["ai_slots"]``）。基线与证据天然同处，这是影子记录
    能做到零 Go 改动、零额外取数的根本原因。
    """
    scorer = scorer or get_scorer()
    recorder = recorder or ShadowRecorder()

    error: Optional[str] = None
    decider_scores: List[SlotScore] = []
    try:
        decider_scores = await asyncio.wait_for(
            scorer.score_slots(thscode, period, slots),
            timeout=SCORER_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 —— 故意兜住所有异常，见 docstring
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("定性维度判分失败（%s %s）：%s", thscode, period, error)

    envelope: Dict[str, Any] = {
        "run_id": _new_run_id(),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "thscode": thscode,
        "period": period,
        "report_type": report_type,
        # 基线：LLM 自由生成的 7 个标量。这是要被评估的对象。
        "llm_scores": dict(llm_scores or {}),
        # 证据：量化锚点 + has_anchor / missing_anchors。
        "slots": [dict(s) for s in slots or []],
        # 实源：判分器输出。本轮恒为全 None（空实现）。
        "decider_scores": [asdict(s) for s in decider_scores],
        "recheck": dict(recheck or {}),
        "error": error,
    }

    try:
        return recorder.write(envelope)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "影子记录写入失败（%s %s）：%s", thscode, period, exc
        )
        return None
