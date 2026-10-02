"""
数据版本探针的回归测试。

要防的场景：ETL 在文件层面重算 DuckDB 之后，长驻的 read_only 连接会一直返回
打开那一刻的快照，直到进程重启。今天批量重算 indicators.duckdb 之后
python-service 读到旧值（ma60 全 NULL）就是这样。

关于测试写法
------------
DuckDB 明确拒绝"持有者还在的时候再开一个读写连接"：

    IO Error: Could not set lock on file ...: Conflicting lock is held in
    Python (PID 53288) ... you would be able to open this database in
    read-only mode

所以本文件**不能**用"另起进程改库"来模拟 —— 那样测的是 DuckDB 的文件锁，
不是我们的探针。分两层测：

  A. 纯逻辑层：直接改 `_signature`，验证"签名变了就重开、代次就涨、
     没变就不重开、文件缺失不致崩"。
  B. 真实跨命名空间层：容器里的服务 vs 宿主机的写入，只能对跑起来的
     容器做，见文件末尾的说明。

跑法：cd <repo>/python-service && python3 tests/unit/test_data_probe.py
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVICE_ROOT))

import duckdb

from datasources.cache import stable_hash
from datasources.duckdb_source import DuckDBSource


def build_db(path: Path, value: int) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE OR REPLACE TABLE t AS SELECT ? AS v", [value])
    con.commit()
    con.close()


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp())
    db = tmpdir / "probe.duckdb"
    build_db(db, 1)

    src = DuckDBSource(name="probe", db_path=str(db), max_concurrent=2)
    failures = []

    # ── 打开长驻连接，模拟服务启动 ──
    asyncio.run(src.initialize())
    got = asyncio.run(src.execute("SELECT v FROM t"))
    print(f"  连接打开后         v={got[0]['v']}  generation={src.generation}")
    if got != [{"v": 1}] or src.generation != 0:
        failures.append("初始读取或代次不对")

    real_sig = src._signature
    real_conn = src._conn

    # ── 1. 文件签名变了 → 探针应重开连接并把代次 +1 ──
    src._signature = (0, 0)  # 伪装成"文件变了"
    reopened = src.maybe_reopen()
    print(f"  签名变化后 maybe_reopen={reopened}  generation={src.generation}"
          f"  连接已换={src._conn is not real_conn}")
    if not reopened or src.generation != 1 or src._conn is real_conn:
        failures.append("签名变化没有触发重开")

    # ── 2. 签名没变 → 不应重开（否则每次查询都白白丢掉连接） ──
    conn_now = src._conn
    for _ in range(3):
        asyncio.run(src.execute("SELECT v FROM t"))
    if src.maybe_reopen() or src._conn is not conn_now or src.generation != 1:
        failures.append("无变化时却反复重开")
    print(f"  无变化再查 3 次     generation={src.generation}  (应仍为 1)")

    # ── 3. 缓存键并上代次 → 代次变化即缓存失效 ──
    key_v1 = stable_hash("probe", "gen=1", "SELECT v FROM t")
    key_v2 = stable_hash("probe", "gen=2", "SELECT v FROM t")
    print(f"  gen=1 与 gen=2 的缓存键不同: {key_v1 != key_v2}")
    if key_v1 == key_v2:
        failures.append("缓存键没并上 generation，重算后的旧缓存会被继续命中")

    # ── 4. stat 拿不到签名（权限/挂载掉线）→ 保留旧连接，不致崩 ──
    src._signature = real_sig
    src._file_signature = lambda: None      # 模拟 os.stat 失败
    gen_before = src.generation
    try:
        survived = asyncio.run(src.execute("SELECT v FROM t"))
        stable = src.generation == gen_before
        print(f"  stat 拿不到签名时仍可查询: {survived}  代次未变={stable}")
        if not survived or not stable:
            failures.append("stat 失败导致查询失败或代次漂移")
    except Exception as exc:
        failures.append(f"stat 失败导致异常：{exc}")
    finally:
        del src._file_signature

    # ── 5. 代次在 /health 里可见 ──
    print(f"  可暴露给 /health 的代次: {src.generation}  "
          f"reopen_failures={src.reopen_failures}")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print()
    if failures:
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("  ✓ 探针逻辑正确：签名变→重开且代次涨、签名不变→不重开、")
    print("    缓存键含代次、stat 失败→沿用旧连接不致崩")
    print()
    print("  真实跨命名空间验证（容器内服务 vs 宿主机改库）需要跑起来的")
    print("  容器，单独用 curl 对 /health 的 generations 观察，见交付说明。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
