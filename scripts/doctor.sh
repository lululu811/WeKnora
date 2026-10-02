#!/bin/bash
# WeKnora 部署自检 —— 在跑 `docker compose up -d` 之前先跑这个。
#
# 它回答三个问题：
#   1. 我这台机器能不能跑？
#   2. 我该用默认部署还是 finance profile？
#   3. 如果跑不起来，缺的是什么、怎么补？
#
# 用法：
#   ./scripts/doctor.sh              # 完整自检
#   ./scripts/doctor.sh --finance    # 额外检查投研栈数据目录
#
# 退出码：0 = 可以直接启动；1 = 有阻塞项。

set -uo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
DIM='\033[2m'
NC='\033[0m'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT" || exit 1

CHECK_FINANCE=0
[ "${1:-}" = "--finance" ] && CHECK_FINANCE=1

BLOCKERS=0
WARNINGS=0

ok()   { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
warn() { printf "  ${YELLOW}!${NC} %s\n" "$1"; WARNINGS=$((WARNINGS+1)); }
bad()  { printf "  ${RED}✗${NC} %s\n" "$1"; BLOCKERS=$((BLOCKERS+1)); }
info() { printf "  ${DIM}%s${NC} %s\n" "$1" "$2"; }
head2(){ printf "\n${BLUE}▸ %s${NC}\n" "${1}"; }
hint() { printf "    ${DIM}→ %s${NC}\n" "$1"; }

printf "${BLUE}WeKnora 部署自检${NC}\n"
printf "${DIM}%s${NC}\n" "$PROJECT_ROOT"

# ---------------------------------------------------------------- 1. 环境
head2 "环境依赖"

if command -v docker > /dev/null 2>&1; then
    ok "Docker 已安装"
else
    bad "未找到 Docker"
    hint "安装：https://docs.docker.com/get-docker/"
fi

if docker compose version > /dev/null 2>&1; then
    ok "Docker Compose $(docker compose version --short 2>/dev/null | head -1)"
elif command -v docker-compose > /dev/null 2>&1; then
    warn "只有旧版 docker-compose（v1），建议升级到 v2：docker compose"
else
    bad "未找到 Docker Compose"
fi

# Docker 守护进程是否真的在跑（装了 ≠ 起着）
if docker info > /dev/null 2>&1; then
    ok "Docker 守护进程正常"
elif command -v docker > /dev/null 2>&1; then
    bad "Docker 守护进程未运行"
    hint "启动 Docker Desktop，或 systemctl start docker"
fi

# ---------------------------------------------------------------- 2. 资源
head2 "机器资源"

# macOS / Linux 通用：优先用 sysctl，其次 /proc/meminfo
get_mem_mb() {
    if [ -r /proc/meminfo ]; then
        awk '/MemTotal/ {printf "%d", $2/1024; exit}' /proc/meminfo
    elif [ "$(uname -s)" = "Darwin" ]; then
        sysctl -n hw.memsize 2>/dev/null | awk '{printf "%d", $1/1024/1024}'
    fi
}

MEM_MB="$(get_mem_mb)"
if [ -n "${MEM_MB:-}" ]; then
    if [ "$MEM_MB" -ge 8192 ]; then
        ok "内存 $((MEM_MB / 1024)) GB（够用）"
    elif [ "$MEM_MB" -ge 4096 ]; then
        warn "内存 $((MEM_MB / 1024)) GB 偏紧"
        hint "关掉沙箱 WEKNORA_SANDBOX_MODE=disabled，图谱 ENABLE_GRAPH_RAG=false"
    else
        bad "内存 $((MEM_MB / 1024)) GB 不够（最低建议 8 GB）"
        hint "默认栈约需 3-4 GB；--profile full 需要更多"
    fi
fi

AVAIL_GB="$(df -Pk . 2>/dev/null | awk 'NR==2 {printf "%d", $4/1024/1024}')"
if [ -n "${AVAIL_GB:-}" ]; then
    if [ "$AVAIL_GB" -ge 10 ]; then
        ok "磁盘可用 $AVAIL_GB GB"
    else
        warn "磁盘可用 $AVAIL_GB GB 偏少（建议 ≥ 10 GB）"
    fi
fi

# ---------------------------------------------------------------- 3. 配置文件
head2 "配置文件"

if [ -f .env ]; then
    ok ".env 存在"
else
    warn ".env 不存在"
    hint "生成：cp .env.example .env，然后填 DASHSCOPE_API_KEY（见下一节）"
fi

# 模型 Key：唯一一项「不设就没有可用模型」的配置。
# config/builtin_models.yaml 里 4 个内置模型的 api_key 都读它，其中 3 个是默认模型；
# 未设置时它们会以**字面量** ${DASHSCOPE_API_KEY} 落库，默认模型不可用，
# 直到用户提问才报鉴权错误——所以必须在这里提前拦住。
if [ -f .env ]; then
    llm_key="$(grep -E "^DASHSCOPE_API_KEY=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ')"
    if [ -z "$llm_key" ]; then
        warn "DASHSCOPE_API_KEY 未设置 —— 内置模型不可用，提问会报鉴权错误"
        hint "去 https://bailian.console.aliyun.com/ 申请，然后在 .env 里写：DASHSCOPE_API_KEY=sk-..."
        hint "不想用百炼？也可以在 Web 界面「模型管理」里添加别的模型（启动之后）"
    else
        ok "DASHSCOPE_API_KEY 已设置（内置模型可用）"
    fi
fi

# 安全相关的关键项：只在 .env 存在时检查
if [ -f .env ]; then
    for key in JWT_SECRET SYSTEM_AES_KEY; do
        val="$(grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ')"
        if [ -z "$val" ]; then
            warn "${key} 未设置"
            hint "公网部署前必须设：${key}=\$(openssl rand -hex 16)"
        fi
    done
fi

# ---------------------------------------------------------------- 4. 端口
head2 "端口占用"

# 读 .env 里的端口设定，未设则用 compose 默认值
env_or_default() {
    local key="$1" default="$2" val=""
    [ -f .env ] && val="$(grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ')"
    printf "%s" "${val:-$default}"
}

check_port() {
    local port="$1" label="$2" owner=""
    if command -v lsof > /dev/null 2>&1; then
        owner="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1" (pid "$2")"; exit}')" || owner=""
    fi
    if [ -z "$owner" ]; then
        ok "端口 ${port} 空闲（${label}）"
    else
        warn "端口 ${port} 被占用：${owner}（${label}）"
        hint "停止占用进程，或在 .env 里改端口（见 DEPLOY.md 第 1 节）"
    fi
}

check_port "$(env_or_default FRONTEND_PORT 80)" "Web 界面"
check_port "$(env_or_default APP_PORT 8080)" "后端 API"

# ---------------------------------------------------------------- 5. Compose
head2 "Compose 配置"

if command -v docker > /dev/null 2>&1 && docker compose version > /dev/null 2>&1; then
    if docker compose config -q > /dev/null 2>&1; then
        ok "默认配置校验通过"
    else
        bad "compose 配置有语法错误"
        docker compose config -q 2>&1 | head -5 | while read -r line; do hint "$line"; done
    fi

    if docker compose --profile finance config -q > /dev/null 2>&1; then
        ok "finance profile 校验通过"
    else
        warn "finance profile 配置有问题"
    fi

    DEFAULT_SVCS="$(docker compose config --services 2>/dev/null | grep -vE '^(postgres-data|data-files|docreader-tmp|minio_data|neo4j-data|qdrant_data|milvus_data|weaviate_data|doris_|langfuse_|searxng_config)$' | tr '\n' ' ')"
    info "默认会启动：" "$DEFAULT_SVCS"
else
    info "跳过" "Docker Compose 不可用"
fi

# ---------------------------------------------------------------- 6. 投研栈
head2 "投研栈 finance profile (可选)"

HITHINK_DIR="$(env_or_default HITHINK_DB_DIR '')"

if [ -n "$HITHINK_DIR" ]; then
    if [ -d "$HITHINK_DIR" ]; then
        DUCKDB_COUNT="$(find "$HITHINK_DIR" -maxdepth 1 -name '*.duckdb' 2>/dev/null | wc -l | tr -d ' ')"
        if [ "$DUCKDB_COUNT" -gt 0 ]; then
            ok "HITHINK_DB_DIR=$HITHINK_DIR（$DUCKDB_COUNT 个 .duckdb）"
        else
            warn "HITHINK_DIR=$HITHINK_DIR 里没有 .duckdb 文件"
            hint "确认路径正确；投研工具会返回友好错误，主流程不受影响"
        fi
    else
        warn "HITHINK_DB_DIR=$HITHINK_DIR 不存在"
        hint "检查路径；不设这个变量也能正常部署，只是投研工具不可用"
    fi
else
    info "未配置" "HITHINK_DB_DIR"
    info "→ " "默认部署即可：docker compose up -d"
    if [ "$CHECK_FINANCE" -eq 1 ]; then
        warn "但你传了 --finance，没数据投研栈跑不起来"
    fi
fi

# ---------------------------------------------------------------- 结论
printf "\n${BLUE}▸ 结论${NC}\n"
if [ "$BLOCKERS" -eq 0 ]; then
    printf "  ${GREEN}可以启动${NC}\n\n"
    if [ -n "$HITHINK_DIR" ] && [ -d "$HITHINK_DIR" ]; then
        printf "  ${DIM}启动：${NC}docker compose --profile finance up -d\n"
    else
        printf "  ${DIM}启动：${NC}docker compose up -d\n"
        printf "  ${DIM}然后：${NC}open http://localhost:%s\n" "$(env_or_default FRONTEND_PORT 80)"
    fi
else
    printf "  ${RED}有 %d 项阻塞，先解决上面标 ✗ 的${NC}\n" "$BLOCKERS"
fi

if [ "$WARNINGS" -gt 0 ]; then
    printf "  ${YELLOW}%d 项提醒${NC} ${DIM}（不阻塞启动）${NC}\n" "$WARNINGS"
fi

printf "\n${DIM}排障详见 DEPLOY.md${NC}\n"
exit $([ "$BLOCKERS" -eq 0 ] && echo 0 || echo 1)
