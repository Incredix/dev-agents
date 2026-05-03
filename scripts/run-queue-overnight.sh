#!/usr/bin/env bash
# Run the queue headless under nohup (append logs, survive SSH disconnect).
# Usage:
#   cd dev-agents && ./scripts/run-queue-overnight.sh my-tasks.txt
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi
if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  source .env
  set +a
fi
QUEUE="${1:?path to queue file required}"
shift || true
LOG="${ROOT}/overnight-queue.log"
NOHUP_OUT="${ROOT}/overnight-nohup.out"
nohup dev-agents queue "$QUEUE" --log "$LOG" "$@" >>"$NOHUP_OUT" 2>&1 &
echo $! >"${ROOT}/overnight-queue.pid"
echo "Started PID $(cat "${ROOT}/overnight-queue.pid") — tail -f ${NOHUP_OUT}"
