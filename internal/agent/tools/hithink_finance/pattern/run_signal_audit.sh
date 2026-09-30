#!/usr/bin/env bash
# Run the signal trigger-frequency audit end to end.
#
#   ./internal/agent/tools/hithink_finance/pattern/run_signal_audit.sh
#
# Why a script and not a bare `go test`: the audit sweeps a stride sample of
# every stock in the indicators DuckDB and takes real wall-clock time against a
# live python-service container. The script does the health check, the bounded
# run and the container-memory watch in one place, so nobody has to memorise
# the env-var incantation.
#
# Why not chunk + container-restart any more: earlier revisions of this audit
# sharded the universe and restarted python-service between shards, because
# DuckDB sized its buffer pool against the host's RAM and the container was
# OOM-killed three times. That is fixed at the source now — duckdb_source.py
# passes memory_limit=2048MB on every connect (env DUCKDB_MEMORY_LIMIT_MB) and
# docker-compose.yml caps the service with mem_limit 3g (env
# PY_SERVICE_MEM_LIMIT). The workaround was removed with the bug it worked
# around; re-adding it would restate a bug that no longer exists.
#
# Env:
#   PYTHON_SERVICE_URL       default http://localhost:50052
#   WEKNORA_AUDIT_STOCKS     stocks in the stride sample, default 1500
#   WEKNORA_AUDIT_BARS       bars per stock, default 1000 (the endpoint's cap)
#   WEKNORA_AUDIT_WORKERS    concurrent fetches, default 3
#   WEKNORA_AUDIT_OUT        report path, default <pkg>/signal_frequency_audit.md

set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# pattern -> hithink_finance -> tools -> agent -> internal -> repo root: five
# levels, not four. One short resolves to <repo>/internal and every `go test`
# path below silently doubles up as internal/internal/... and fails at setup.
REPO_ROOT="$(cd "${PKG_DIR}/../../../../.." && pwd)"
export PYTHON_SERVICE_URL="${PYTHON_SERVICE_URL:-http://localhost:50052}"
STOCKS="${WEKNORA_AUDIT_STOCKS:-1500}"
BARS="${WEKNORA_AUDIT_BARS:-1000}"
WORKERS="${WEKNORA_AUDIT_WORKERS:-3}"
OUT="${WEKNORA_AUDIT_OUT:-${PKG_DIR}/signal_frequency_audit.md}"
SERVICE="${WEKNORA_AUDIT_SERVICE:-WeKnora-python-service}"
COMPOSE_SERVICE="${WEKNORA_AUDIT_COMPOSE_SERVICE:-python-service}"
# 资源门禁要向这两个容器问数字。定义在这里而不是内联默认值，是为了让人一眼看出
# 门禁依赖它们——取不到值时下面的检查会 fail-closed（拒绝），不会 fail-open。
APP_SERVICE="${WEKNORA_AUDIT_APP_SERVICE:-WeKnora-app}"
REDIS_SERVICE="${WEKNORA_AUDIT_REDIS_SERVICE:-WeKnora-redis}"

# Health-check-guided restart.
#
# python-service holds a ~17 GB read-only DuckDB and takes a while to come up.
# The audit is the only thing in this repo that sweeps the whole universe, so it
# is also the only thing that reliably finds the container sitting dead after an
# OOM. Restart it here rather than making the operator remember.
#
# Restart is only ever reached when the health endpoint does not answer, i.e.
# the container is down or wedged. If it is up but unhealthy for an unrelated
# reason the health check below still fails afterwards and the script refuses to
# start, so a restart can never be mistaken for a fix.
healthy() { curl -sf -m 10 "${PYTHON_SERVICE_URL}/health" >/dev/null 2>&1; }

if ! healthy; then
  echo "== python-service not answering at ${PYTHON_SERVICE_URL}; attempting restart"
  docker compose up -d "${COMPOSE_SERVICE}" >/dev/null 2>&1 || true
  for i in $(seq 1 30); do
    sleep 10
    if healthy; then break; fi
    echo "   waiting for health… $((i * 10))s"
  done
fi

if ! healthy; then
  echo "python-service is still not reachable at ${PYTHON_SERVICE_URL} after a restart attempt." >&2
  echo "It is unhealthy for a reason the restart did not fix — do not proceed; the sweep" >&2
  echo "would report every stock as a fetch failure and produce a meaningless table." >&2
  echo "Inspect it with: docker logs --tail 100 ${SERVICE}" >&2
  exit 1
fi

# ── 写入方门禁 ──────────────────────────────────────────────────────────────
# indicators.duckdb 由宿主机的每日 19:00 cron 增量更新（a-stock/scripts/
# indicators_sync.py），一次要跑两三个小时。DuckDB 是单写者模型：写入方持有
# 独占锁期间，任何只读连接**看不到未提交的 WAL**，而且读到半写完的块时校验和
# 必然对不上，报出来长这样：
#
#   IO Error: Corrupt database file: computed checksum ... does not match ...
#   INTERNAL Error: Bitpacking offset is out of range at block "2812" ...
#
# 看着像文件损坏，其实只是"有人在写"。真去 repair 或 --rebuild 才是灾难：那会在
# 同步写到一半时再开一个写者，两三个小时的成果全丢，运气不好还真的弄坏文件。
#
# 上一轮体检就是这么撞上的：跑了两轮、每轮几分钟的全部返回 400，最后报告
# "数据库损坏"——而库从头到尾是好的。**这道门存在的意义是让那种误判不可能发生**：
# 有写入方就直接拒绝跑，并说清楚该等什么。
#
# 检测方式：拿一个独立进程去开一次**读写**连接。拿不到锁 = 有人在写。必须是独立
# 进程——同进程内再开一次连接可能被当成复用。
writer_busy() {
  local db="${WEKNORA_AUDIT_DB_DIR:-$HOME/.hithink-finance}/indicators.duckdb"
  [ -f "$db" ] || return 1   # 文件不存在不算"被占用"
  python3 - "$db" <<'PY' >/dev/null 2>&1
import sys, duckdb
duckdb.connect(sys.argv[1], read_only=False).close()
PY
  # 退出码 0 = 拿到了锁 = 空闲；非 0 = 拿不到 = 有人写
}

if ! writer_busy; then
  cat >&2 <<'MSG'
== 拒绝执行：indicators.duckdb 正在被写入

  每日 19:00 的 cron（a-stock/scripts/indicators_sync.py）会增量更新这张表，
  一次跑 2-3 小时。DuckDB 单写者：写锁被持有时只读连接看不到未提交的 WAL，
  查询会报 "Corrupt database file" / "Bitpacking offset is out of range"——
  那是"有人在写"，不是"文件坏了"。

  现在重跑只会得到一张全 0 的表：每个查询都失败，而失败会被计成"该信号从未触发"。

  查看写入进度：
    ps aux | grep indicators_sync | grep -v grep

  等它结束（进程消失 + .wal 归零）再跑本脚本。
MSG
  exit 1
fi
echo "== 写入方检查通过：indicators.duckdb 空闲"

# ── 资源门禁 ──────────────────────────────────────────────────────────────
# 上面那道门挡的是"文件被写"，这一道挡的是"**机器被吃光**"。
#
# 2026-09-30 晚上出事那次的链条：体检把 python-service 的 DuckDB 缓冲池顶到
# 2.5 GB，而 redis 当时积压了 3.1 GB（asynq 的任务状态；app 容器当天被反复重启，
# retention 清理来不及跑，队列状态就堆起来了）。两者相加超过 Docker VM 的
# 7.75 GiB，内核按占用挑了最大的那个——redis——连杀 24 次；redis 一挂，asynq
# 全部 i/o timeout，SSE 存着的 running turn 读不到，用户侧直接 503。
#
# 也就是说：**本脚本之前只防了一种资源事故（文件锁），没防另一种（机器内存），
# 而后者伤的是线上服务，不是体检自己。** 体检失败只是白跑一次，redis 被杀是
# 别人的对话断了。
#
# 所以跑之前先量，量不过就别跑。下面三个值任一不达标就退出，并把当前值打出来，
# 让人能判断该等什么、或者该先清什么。
MEM_AVAIL_MIN_MB="${WEKNORA_AUDIT_MEM_AVAIL_MIN_MB:-3072}"   # 3 GiB
REDIS_MAX_MB="${WEKNORA_AUDIT_REDIS_MAX_MB:-512}"            # 512 MB
PYSVC_MAX_MB="${WEKNORA_AUDIT_PYSVC_MAX_MB:-2048}"           # 2 GiB

mem_available_mb() {
  docker exec "${APP_SERVICE}" sh -lc 'awk "/MemAvailable/{printf \"%d\", \$2/1024}" /proc/meminfo' 2>/dev/null
}
redis_used_mb() {
  local pw; pw=$(docker exec "${APP_SERVICE}" sh -lc 'printenv REDIS_PASSWORD' 2>/dev/null | tr -d '\r')
  [ -z "$pw" ] && return 1
  # used_memory_human 形如 "2.76M" / "512.00K" / "1.20G"，单位后缀本身就是量级，
  # 换算成整数 MB 供下面比较。别用 gsub 剥单位再 +0——那会留下小数点，
  # 整数校验会把正常读数当成"量不到"。
  docker exec -e PW="$pw" "${REDIS_SERVICE}" sh -lc \
    'redis-cli -a "$PW" --no-raw INFO memory 2>/dev/null \
     | awk -F: "/^used_memory_human/{v=\$2; u=substr(v,length(v),1); n=v+0;
        if (u==\"G\") n=n*1024; else if (u==\"K\") n=n/1024;
        printf \"%d\", (n<0?0:n)}"' 2>/dev/null
}
pysvc_used_mb() {
  # docker stats 要的是**容器名**（SERVICE），不是 compose 服务名（COMPOSE_SERVICE）。
  # 两者混用会静默返回空——然后被当成"占用 0"而放行。
  #
  # docker stats 本身也会偶发返回空值或 "0B"，那同样是"没量到"而不是 0。
  # 任何一次拿到数字就返回；三次都拿不到返回 1，交给调用方按 fail-closed 处理。
  #
  # 注意最后那步：失败时必须 `return 1` 而不是让 `$(...)` 带着非零状态出去——
  # 脚本开了 set -e，赋值语句的退出码就是函数最后一条命令的退出码，
  # 非零会当场把整个脚本带走，而且不打印任何东西。
  local n
  for _ in 1 2 3; do
    n=$(docker stats --no-stream --format '{{.MemUsage}}' "${SERVICE}" 2>/dev/null \
      | awk '{v=$1; u=substr(v,length(v),1); if (u!="B") next; x=v+0; printf "%d", x}')
    case "$n" in ''|*[!0-9]*) sleep 2 ;; *) echo "$n"; return 0 ;; esac
  done
  echo ""   # 失败也要有输出
  return 1
}

AVAIL_MB=$(mem_available_mb)
REDIS_MB=$(redis_used_mb)
PYSVC_MB=$(pysvc_used_mb)

# 取不到数字 = 不知道 = 不许跑。
# 这里必须 fail-closed：把取不到的 0 当成"资源充足"而放行，等于把门关死了，
# 而门存在的全部意义就是在这个时刻拦住重活。宁可让人手动确认一次。
unreadable() {
  echo "" >&2
  echo "== 拒绝执行：量不到 $1（容器名可用 WEKNORA_AUDIT_APP_SERVICE /" >&2
  echo "   WEKNORA_AUDIT_REDIS_SERVICE / WEKNORA_AUDIT_COMPOSE_SERVICE 覆盖）" >&2
  echo "   量不到就当合格放行，等于没有这道门。" >&2
  exit 1
}
case "$AVAIL_MB" in ''|*[!0-9]*) unreadable "VM 可用内存" ;; esac
case "$REDIS_MB" in ''|*[!0-9]*) unreadable "redis 占用" ;; esac
case "$PYSVC_MB" in ''|*[!0-9]*) unreadable "${COMPOSE_SERVICE} 占用" ;; esac
# 阈值本身也要是整数。少了这一步，`[ 5868 -lt "" ]` 只会让 bash 报一句
# "integer expression expected" 然后**返回假**——门禁就此静默放行，而且是所有
# 检查里最危险的那种放行：看起来跑过了，其实一条都没比。
for t in MEM_AVAIL_MIN_MB REDIS_MAX_MB PYSVC_MAX_MB; do
  case "${!t}" in ''|*[!0-9]*)
    echo "== 拒绝执行：阈值 ${t} 不是整数（当前值 [${!t}]）" >&2
    exit 1 ;;
  esac
done

echo "== 资源快照: VM 可用 ${AVAIL_MB}MB · redis ${REDIS_MB}MB · ${COMPOSE_SERVICE} ${PYSVC_MB}MB"

refuse() {
  echo "" >&2
  echo "== 拒绝执行：$1" >&2
  echo "   VM 可用 ${AVAIL_MB}MB (需 ≥${MEM_AVAIL_MIN_MB}MB)" >&2
  echo "   redis  ${REDIS_MB}MB (需 <${REDIS_MAX_MB}MB)" >&2
  echo "   ${COMPOSE_SERVICE} ${PYSVC_MB}MB (需 <${PYSVC_MAX_MB}MB)" >&2
  echo "" >&2
  echo "体检是这台机器上最重的任务：它会把 python-service 顶到 2 GB 以上。" >&2
  echo "扛不住时被杀的是**别的容器**（上一轮是 redis），线上服务会跟着断。" >&2
  echo "先腾出资源再跑，或用 WEKNORA_AUDIT_MEM_AVAIL_MIN_MB 调低门槛（不建议）。" >&2
  exit 1
}

[ "$AVAIL_MB" -lt "$MEM_AVAIL_MIN_MB" ] && refuse "VM 可用内存不足"
[ "$REDIS_MB" -ge "$REDIS_MAX_MB" ]    && refuse "redis 占用过高（多半是 asynq 任务状态积压）"
[ "$PYSVC_MB" -ge "$PYSVC_MAX_MB" ]    && refuse "${COMPOSE_SERVICE} 内存占用过高（DuckDB 缓冲池未释放）"
echo "== 资源检查通过"
echo "== health check ${PYTHON_SERVICE_URL} ok"

# Refuse to start if the previous run died by OOM: the container comes back up
# looking healthy, and the next sweep walks straight back into the same ceiling.
# DuckDB's buffer pool is not the only allocation a wide scan makes, so a
# container that OOM'd at 3g will OOM again at 3g. Lower the worker count until
# the run completes.
LAST_STATE="$(docker inspect -f '{{.State.OOMKilled}}' "${SERVICE}" 2>/dev/null || echo unknown)"
if [ "${LAST_STATE}" = "true" ]; then
  echo "WARNING: ${SERVICE} was OOM-killed on its last run (before this restart)."
  echo "         3 workers will hit the same ceiling. Running with 1 worker."
  echo "         Raise WEKNORA_AUDIT_WORKERS only once a run completes cleanly."
  WORKERS=1
fi

# Watch memory alongside the sweep rather than after it. The audit is bounded,
# so a runaway here means the bound is wrong, and the operator should see it
# while there is still time to react rather than in the OOM post-mortem.
( while true; do
    docker stats --no-stream --format '{{.MemUsage}}' "${SERVICE}" 2>/dev/null || true
    sleep 30
  done ) &
WATCH_PID=$!
# shellcheck disable=SC2064
trap "kill ${WATCH_PID} 2>/dev/null || true" EXIT

cd "${REPO_ROOT}"
PYTHON_SERVICE_URL="${PYTHON_SERVICE_URL}" \
WEKNORA_SIGNAL_AUDIT=1 \
WEKNORA_SIGNAL_AUDIT_STOCKS="${STOCKS}" \
WEKNORA_SIGNAL_AUDIT_BARS="${BARS}" \
WEKNORA_SIGNAL_AUDIT_WORKERS="${WORKERS}" \
WEKNORA_SIGNAL_AUDIT_OUT="${OUT}" \
  go test -tags signal_audit -run TestSignalFrequencyAudit -v -timeout 60m \
    ./internal/agent/tools/hithink_finance/pattern/

echo "== report: ${OUT}"
