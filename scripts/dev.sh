#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"

cleanup() {
  kill "${API_PID:-}" "${WORKER_PID:-}" "${WEB_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$BACKEND_DIR"
"$BACKEND_DIR/.venv/bin/python" -m uvicorn app.main:app \
  --reload --port 8001 --host 127.0.0.1 &
API_PID=$!

"$BACKEND_DIR/.venv/bin/python" -m app.worker &
WORKER_PID=$!

cd "$FRONTEND_DIR"
./node_modules/.bin/next dev -p 3001 &
WEB_PID=$!

echo "Decision Council 已启动："
echo "  前端   http://127.0.0.1:3001"
echo "  API    http://127.0.0.1:8001/api/health"
echo "  Worker 在线，会议会自动执行"

wait
