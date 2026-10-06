#!/bin/bash
# 后端发版：同步已跟踪的运行代码，再重启既有 systemd 服务
# 用法: bash scripts/deploy-backend.sh [--dry-run|--sync-only]
set -euo pipefail
source "$(dirname "$0")/deploy-common.sh"

: "${DEPLOY_SERVICE:?请设置 DEPLOY_SERVICE（既有 systemd 服务名）}"
: "${DEPLOY_PORT:?请设置 DEPLOY_PORT（既有服务端口）}"
if [[ ! "$DEPLOY_SERVICE" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.@-]*\.service$ ]] ||
   [[ ! "$DEPLOY_PORT" =~ ^[0-9]{1,5}$ ]] ||
   (( 10#$DEPLOY_PORT < 1 || 10#$DEPLOY_PORT > 65535 )); then
  echo "服务名须以 .service 结尾，端口须在 1–65535 之间" >&2
  exit 2
fi

# 只发布 Git 已跟踪的运行文件；不删除远端文件，不同步前端与私有配置
git -C "$ROOT" ls-files -z -- app/ cli.py requirements.txt \
  captcha_node/solver.js captcha_node/package.json captcha_node/package-lock.json |
  rsync "${RSYNC_ARGS[@]}" --exclude 'app/statics/' "$ROOT/" "$DEPLOY_HOST:$DEPLOY_DIR/"

if [[ "$MODE" != deploy ]]; then
  if [[ "$MODE" == dry-run ]]; then
    echo "✓ backend → 预演完成，未写入或重启服务"
  else
    echo "✓ backend → 同步完成，未重启服务"
  fi
  exit 0
fi

ssh "$DEPLOY_HOST" "systemctl restart -- '$DEPLOY_SERVICE' && \
  systemctl is-active --quiet -- '$DEPLOY_SERVICE' && \
  curl --fail --silent --show-error --noproxy '*' --retry 5 --retry-connrefused \
    --retry-delay 1 --max-time 5 'http://127.0.0.1:$((10#$DEPLOY_PORT))/meta'"
echo
echo "✓ backend → 已重启；请继续验证前端与实际请求"
