#!/usr/bin/env bash
# Expose the Streamlit UI to your tailnet via Tailscale Serve (HTTPS on *.ts.net).
#
# Prereq: streamlit already running on 127.0.0.1:8501 (default), e.g.
#   cd dev-agents && source .venv/bin/activate && pip install -e ".[ui]" && streamlit run ui/app.py
#
# Usage:
#   scripts/serve-ui-tailscale.sh           # tailscale serve --bg on STREAMLIT_PORT (default 8501)
#   STREAMLIT_PORT=8502 scripts/serve-ui-tailscale.sh
#   scripts/serve-ui-tailscale.sh off       # tailscale serve off (remove proxy)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${STREAMLIT_PORT:-8501}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale CLI not found. Install Tailscale on this host first." >&2
  exit 1
fi

if [[ "${1:-}" == "off" ]]; then
  exec tailscale serve off
fi

echo "Publishing http://127.0.0.1:${PORT} to your tailnet (HTTPS)." >&2
echo "Ensure Streamlit is listening: streamlit run ${ROOT}/ui/app.py --server.address 127.0.0.1 --server.port ${PORT}" >&2
echo >&2

# Proxy local Streamlit (loopback-only) onto the machine's MagicDNS HTTPS URL.
tailscale serve --bg "${PORT}"

echo >&2
echo "Serve status:" >&2
tailscale serve status || true
echo >&2
echo "Open the https://…ts.net URL shown above from another device on your tailnet." >&2
echo "Stop:  scripts/serve-ui-tailscale.sh off   (or: tailscale serve off)" >&2
