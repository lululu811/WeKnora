#!/usr/bin/env bash
# 安装/刷新 launchd 定时任务：按准入规则把 vault 公众号文章批量导入 WeKnora。
#
# 背景：chatlog（:5030 biz）每 120 分钟把公众号文章导出到 vault（
# wechat-jinrong-lianyaoshi/raw/01-articles/<号>/<标题>/<标题>.md），
# 但 vault → WeKnora 这一跳没有调度。本任务补上这一跳，默认 6 小时一轮
# （与数据源同步默认节奏一致），脚本按 knowledge.source 幂等，重跑零代价。
#
# 用法：
#   WEKNORA_EMAIL=you@example.com WEKNORA_PASSWORD='…' \
#     scripts/install_wechat_import_agent.sh --kb-id 307ea0c1-… --dry-run
#   WEKNORA_EMAIL=… WEKNORA_PASSWORD='…' \
#     scripts/install_wechat_import_agent.sh --kb-id 307ea0c1-…
#
# 可选参数：
#   --interval <秒>   同步间隔，默认 21600（6h）
#   --python <路径>   解释器，默认自动探测 ≥3.10（脚本用 3.10+ 类型语法）
#   --repo <路径>     仓库根，默认 git rev-parse --show-toplevel
#   --label <标签>    launchd label，默认 com.chenlei.weknora-wechat-import
#   --dry-run         只打印 plist，不安装
#
# 安装后：
#   launchctl kickstart gui/$(id -u)/com.chenlei.weknora-wechat-import  # 立即跑一轮
#   tail -f ~/Library/Logs/weknora-wechat-import.{out,err}.log          # 看日志
#   launchctl bootout gui/$(id -u)/com.chenlei.weknora-wechat-import    # 卸载

set -euo pipefail

LABEL=com.chenlei.weknora-wechat-import
INTERVAL=21600
PYTHON_BIN=""
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
DRY_RUN=0
KB_ID=""

while [ $# -gt 0 ]; do
  case "$1" in
    --kb-id)    KB_ID="${2:?--kb-id 需要参数}"; shift 2 ;;
    --interval) INTERVAL="${2:?--interval 需要参数}"; shift 2 ;;
    --python)   PYTHON_BIN="${2:?--python 需要参数}"; shift 2 ;;
    --repo)     REPO_ROOT="${2:?--repo 需要参数}"; shift 2 ;;
    --label)    LABEL="${2:?--label 需要参数}"; shift 2 ;;
    --dry-run)  DRY_RUN=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

die() { echo "错误: $*" >&2; exit 1; }

[ -n "$KB_ID" ] || die "必须指定 --kb-id（目标知识库 id）"
[ -n "${WEKNORA_EMAIL:-}" ] || die "必须设置 WEKNORA_EMAIL"
[ -n "${WEKNORA_PASSWORD:-}" ] || die "必须设置 WEKNORA_PASSWORD"

# 脚本用了 `str | None` 类型标注和 dataclass，需要 ≥3.10；本机实测 3.14 OK。
if [ -z "$PYTHON_BIN" ]; then
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      v="$("$c" -c 'import sys; print("%d%02d" % sys.version_info[:2])')"
      if [ "$v" -ge 310 ]; then PYTHON_BIN="$(command -v "$c")"; break; fi
    fi
  done
fi
[ -n "$PYTHON_BIN" ] || die "找不到 ≥3.10 的 python3；用 --python 显式指定"

SCRIPT="$REPO_ROOT/scripts/import_wechat_vault.py"
[ -f "$SCRIPT" ] || die "脚本不存在: $SCRIPT"

PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs"

# 密码会写进 plist（chmod 600）。若介意，可改用 Keychain/环境文件后自行改 plist。
PLIST_XML="$(cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>$SCRIPT</string>
    <string>--kb-id</string>
    <string>$KB_ID</string>
    <string>--filter</string>
    <string>rule</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>WEKNORA_BASE_URL</key>
    <string>${WEKNORA_BASE_URL:-http://localhost:8080}</string>
    <key>WEKNORA_EMAIL</key>
    <string>$WEKNORA_EMAIL</string>
    <key>WEKNORA_PASSWORD</key>
    <string>$WEKNORA_PASSWORD</string>
    <key>WEKNORA_TENANT_ID</key>
    <string>${WEKNORA_TENANT_ID:-}</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>$REPO_ROOT</string>
  <key>StartInterval</key>
  <integer>$INTERVAL</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/weknora-wechat-import.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/weknora-wechat-import.err.log</string>
</dict>
</plist>
EOF
)"

if [ "$DRY_RUN" = "1" ]; then
  echo "$PLIST_XML"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
# bootout 旧实例（忽略"不存在"错误），再 bootstrap 新的；幂等，可重复安装。
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
printf '%s\n' "$PLIST_XML" > "$PLIST"
chmod 600 "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已安装 $LABEL"
echo "  间隔: ${INTERVAL}s  python: $PYTHON_BIN  kb: $KB_ID"
echo "  立即跑一轮: launchctl kickstart gui/$(id -u)/$LABEL"
echo "  日志: $LOG_DIR/weknora-wechat-import.{out,err}.log"
