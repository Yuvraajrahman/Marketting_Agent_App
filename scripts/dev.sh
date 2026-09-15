#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Only install when requirements change (or first run). Avoids a slow
# silent reinstall every launch — and the pip cache warnings that go with it.
REQ_HASH_FILE=".venv/.requirements.sha256"
REQ_HASH="$(shasum -a 256 backend/requirements.txt | awk '{print $1}')"
NEED_INSTALL=0
if [[ ! -f "$REQ_HASH_FILE" ]] || [[ "$(cat "$REQ_HASH_FILE")" != "$REQ_HASH" ]]; then
  NEED_INSTALL=1
fi
if ! python -c "import fastapi, uvicorn, httpx, cv2, psutil" 2>/dev/null; then
  NEED_INSTALL=1
fi

if [[ "$NEED_INSTALL" -eq 1 ]]; then
  echo "Installing / updating Python deps (first run or requirements changed)…"
  pip install --disable-pip-version-check -r backend/requirements.txt
  echo "$REQ_HASH" > "$REQ_HASH_FILE"
else
  echo "Python deps already installed — skipping pip."
fi

if [[ ! -d frontend/node_modules ]]; then
  echo "Installing frontend deps…"
  (cd frontend && npm install)
fi

cleanup() {
  kill "$BACK_PID" "$FRONT_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "Starting backend on :8000…"
PYTHONPATH="$ROOT" uvicorn backend.gateway:app --host 0.0.0.0 --port 8000 --reload &
BACK_PID=$!

echo "Starting frontend on :5173…"
(cd frontend && npm run dev -- --host 127.0.0.1 --port 5173) &
FRONT_PID=$!

echo "Live Presence: http://127.0.0.1:5173  (API/WS :8000)"
wait
