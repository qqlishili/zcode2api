#!/bin/bash
# 两个发版脚本共用的参数校验与同步选项；真实参数由操作者显式提供
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=deploy
RSYNC_ARGS=(-az --from0 --files-from=-)
if (( $# > 1 )); then
  echo "用法: bash scripts/deploy-{backend,frontend}.sh [--dry-run|--sync-only]" >&2
  exit 2
fi
case "${1:-}" in
  "") ;;
  --dry-run) MODE=dry-run; RSYNC_ARGS+=(--dry-run --itemize-changes) ;;
  --sync-only) MODE=sync-only ;;
  *) echo "未知参数；支持 --dry-run 或 --sync-only" >&2; exit 2 ;;
esac

: "${DEPLOY_HOST:?请设置 DEPLOY_HOST（SSH 别名或 user@host）}"
: "${DEPLOY_DIR:?请设置 DEPLOY_DIR（既有项目绝对目录）}"
if [[ ! "$DEPLOY_HOST" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.@-]*$ ]]; then
  echo "DEPLOY_HOST 须为 SSH 别名或 user@host" >&2
  exit 2
fi

check_deploy_dir() {
  if [[ ! "$1" =~ ^/[a-zA-Z0-9_-][a-zA-Z0-9_./-]*$ ]] ||
     [[ "/${1#/}/" == */../* || "/${1#/}/" == */./* ]]; then
    echo "部署目录须为非根绝对路径，不含空格、特殊字符或 . / .. 路径段" >&2
    exit 2
  fi
}
check_deploy_dir "$DEPLOY_DIR"
git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null
