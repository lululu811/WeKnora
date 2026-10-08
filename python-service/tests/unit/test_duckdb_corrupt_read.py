"""
连接读到"写到一半的 DuckDB 文件"时的自愈回归测试。

要防的场景（2026-10-08 实测）：宿主机 `indicators_sync.py` 正在写
`indicators.duckdb`，容器里的长驻 read_only 连接在某次 checkpoint 中间被
`maybe_reopen` 换掉，新连接把写到一半/已被复用的元数据块当成块指针 —— 之后
**每一次**表查询都是

    Serialization Error: Failed to deserialize: field id mismatch,
    expected: 102, got: 65535

而 `os.stat` 在 Docker Desktop 的 bind mount 上陈旧（实测滞后 30+ 分钟），签名
探测不会再来换连接，坏连接一直坏到进程重启。

所以这里断言两件事：

  1. 读到不一致元数据 → 换连接重试，查询正常返回（自愈）；
  2. 这一类以外的错误（SQL 写错、锁冲突）**不**重试，原样抛出（不丢好连接）。

跨命名空间的"宿主机写 + 容器读"没法在单测里搭，真实形态只能在跑起来的容器上
观察（见 tests/unit/test_data_probe.py 末尾的说明）。
"""
import asyncio
import sys
from pathlib import Path

import duckdb
import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from datasources import duckdb_source
from datasources.duckdb_source import (
    CORRUPTION_RETRIES,
    CORRUPTION_RETRY_DELAY,
    DuckDBCorruptReadError,
    DuckDBSource,
)

CORRUPT_TEXT = (
    "Serialization Error: Failed to deserialize: "
    "field id mismatch, expected: 102, got: 65535"
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "probe.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t AS SELECT 42 AS v")
    con.close()
    return path


class StubConnection:
    """模拟"读到不一致元数据"的连接：execute 直接抛，且不该再被使用。"""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0
        self.closed = False

    def execute(self, *args, **kwargs):
        self.calls += 1
        raise self.error

    def close(self) -> None:
        self.closed = True


def poison(src: DuckDBSource, error: Exception) -> StubConnection:
    """把数据源的长驻连接换成一条"坏"连接。"""
    stub = StubConnection(error)
    old, src._conn = src._conn, stub
    if old is not None:
        old.close()
    return stub


def test_corrupt_read_reopens_and_retries(db: Path) -> None:
    src = DuckDBSource(name="probe", db_path=str(db))
    asyncio.run(src.initialize())
    stub = poison(src, duckdb.SerializationException(CORRUPT_TEXT))

    rows = asyncio.run(src.execute("SELECT v FROM t"))

    assert rows == [{"v": 42}]
    assert stub.calls == 1, "坏连接上只该碰一次"
    assert stub.closed, "坏连接必须被关掉"
    assert src.generation == 1, "换连接要计入代次（缓存键含代次）"
    assert src.reopen_failures == 0


def test_execute_scalar_also_heals(db: Path) -> None:
    src = DuckDBSource(name="probe", db_path=str(db))
    asyncio.run(src.initialize())
    stub = poison(src, duckdb.SerializationException(CORRUPT_TEXT))

    assert asyncio.run(src.execute_scalar("SELECT max(v) FROM t")) == 42
    assert stub.closed


def test_unrecoverable_corruption_names_the_cause(db: Path, monkeypatch) -> None:
    """重开也失败（文件被别的进程独占）→ 抛带原因的 5xx 类错误，而不是原始报错。"""
    src = DuckDBSource(name="indicators", db_path=str(db))
    asyncio.run(src.initialize())
    stub = poison(src, duckdb.SerializationException(CORRUPT_TEXT))

    def locked(*args, **kwargs):
        raise duckdb.IOException("IO Error: Could not set lock on file")

    monkeypatch.setattr(duckdb_source.duckdb, "connect", locked)
    with pytest.raises(DuckDBCorruptReadError) as exc_info:
        asyncio.run(src.execute("SELECT v FROM t"))

    msg = str(exc_info.value)
    assert "indicators" in msg and db.name in msg
    assert "不是 SQL 的问题" in msg, "要告诉调用方别去改 SQL"
    assert CORRUPT_TEXT in msg, "底层错误要留在消息里，便于对日志"
    assert stub.closed, "坏实例必须被关掉，否则新连接会接进同一份坏实例"
    assert src._conn is None, "重开失败时没有可用连接，而不是留着坏连接"
    assert src.reopen_failures == 1
    assert src.generation == 0


def test_reopen_closes_instance_before_opening(db: Path, monkeypatch) -> None:
    """先关旧、再开新 —— 反过来的话新连接会复用同一份 DatabaseInstance。

    `duckdb.connect(path)` 在同一进程里对同一路径返回同一份实例，只要该路径上
    还有连接开着。所以"先开新的、成功后再关旧的"永远救不回坏连接。
    """
    src = DuckDBSource(name="probe", db_path=str(db))
    asyncio.run(src.initialize())
    poison(src, duckdb.SerializationException(CORRUPT_TEXT))

    seen = []
    real_connect = duckdb_source.duckdb.connect

    def spy(*args, **kwargs):
        seen.append(src._conn)          # 开新连接的那一刻，旧连接是否已经关掉
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb_source.duckdb, "connect", spy)
    assert asyncio.run(src.execute("SELECT v FROM t")) == [{"v": 42}]

    assert seen == [None], f"开新连接时旧连接还开着：{seen}"
    assert src._conn is not None


def test_retries_are_bounded(db: Path, monkeypatch) -> None:
    """换连接"成功"但每次都还是读到不一致内容 → 重试必须有上限，且逐步退避。"""
    src = DuckDBSource(name="probe", db_path=str(db))
    asyncio.run(src.initialize())
    stub = poison(src, duckdb.SerializationException(CORRUPT_TEXT))

    reopens = {"n": 0}

    def fake_reopen() -> bool:
        reopens["n"] += 1
        return True          # 连接换掉了，但坏的是文件内容，下次照样失败

    sleeps: list = []

    class Recorder:
        """记录退避时长，不真的睡 —— 否则这个测试要跑 3 秒。"""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        async def sleep(self, seconds):
            sleeps.append(seconds)

    monkeypatch.setattr(duckdb_source, "asyncio", Recorder(asyncio))
    monkeypatch.setattr(src, "_reopen_broken", fake_reopen)
    with pytest.raises(DuckDBCorruptReadError):
        asyncio.run(src.execute("SELECT v FROM t"))

    assert stub.calls == CORRUPTION_RETRIES + 1
    assert reopens["n"] == CORRUPTION_RETRIES
    # 立刻重开会再次读到中间态（实测三次全部落在同一个 checkpoint 窗口里），
    # 所以重试之间必须等待，且越等越长。
    assert sleeps == [
        CORRUPTION_RETRY_DELAY * (n + 1) for n in range(CORRUPTION_RETRIES)
    ]


def test_sql_error_is_not_retried(db: Path) -> None:
    """笔误不能触发换连接：重试只会白丢一条好连接，还得重读库的元数据。"""
    src = DuckDBSource(name="probe", db_path=str(db))
    asyncio.run(src.initialize())
    conn = src._conn

    with pytest.raises(duckdb.BinderException):
        asyncio.run(src.execute("SELECT nonexistent FROM t"))

    assert src._conn is conn and src.generation == 0
    assert asyncio.run(src.execute("SELECT v FROM t")) == [{"v": 42}]


def test_lock_conflict_is_not_retried(db: Path) -> None:
    """锁冲突重开也开不了，重试没有意义（宿主机跑服务时就是这条）。"""
    src = DuckDBSource(name="probe", db_path=str(db))
    asyncio.run(src.initialize())
    stub = poison(src, duckdb.IOException("IO Error: Could not set lock on file"))

    with pytest.raises(duckdb.IOException):
        asyncio.run(src.execute("SELECT v FROM t"))

    assert stub.calls == 1
    assert src.generation == 0 and src.reopen_failures == 0
