#!/bin/bash
# 前端独立发版：只同步已跟踪的 frontend/ 文件，无需重启后端
# 用法: bash scripts/deploy-frontend.sh [--dry-run]（先 bump frontend/version）
set -euo pipefail
source "$(dirname "$0")/deploy-common.sh"

DEPLOY_FRONTEND_DIR="${DEPLOY_FRONTEND_DIR:-$DEPLOY_DIR/frontend}"
check_deploy_dir "$DEPLOY_FRONTEND_DIR"

git -C "$ROOT/frontend" ls-files -z -- . |
  rsync "${RSYNC_ARGS[@]}" "$ROOT/frontend/" "$DEPLOY_HOST:$DEPLOY_FRONTEND_DIR/"

if [[ "$MODE" == dry-run ]]; then
  echo "✓ frontend → 预演完成，未写入或重启服务"
else
  echo "✓ frontend → 同步完成，无需重启；请验证页面与 frontend/version"
fi
