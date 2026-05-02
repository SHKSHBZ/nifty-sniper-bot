#!/usr/bin/env bash
# Start the dashboard: FastAPI backend (port 8000) + Next.js frontend
# (port 3000). Both run in the foreground with prefixed log output.
# Press Ctrl+C once to stop both cleanly.
#
# Usage:
#   ./start_dashboard.sh                   # default ports
#   BACKEND_PORT=8001 FRONTEND_PORT=3001 ./start_dashboard.sh

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# --- Pre-flight checks ---
if ! python3 -c "import fastapi" 2>/dev/null; then
  echo "ERROR: fastapi not installed. Run ./setup_dashboard.sh first."
  exit 1
fi
if [ ! -d "$ROOT/dashboard/node_modules" ]; then
  echo "ERROR: dashboard/node_modules missing. Run ./setup_dashboard.sh first."
  exit 1
fi

if [ ! -f "$ROOT/.env" ]; then
  echo
  echo "==============================================================="
  echo "  WARNING: No .env file found."
  echo "==============================================================="
  echo "  The Upstox LOGIN button will not work until you create a .env"
  echo "  file with UPSTOX_API_KEY, UPSTOX_API_SECRET, and"
  echo "  UPSTOX_REDIRECT_URI. Copy .env.example to .env and fill in."
  echo "==============================================================="
  echo
fi

# --- Free the ports if anything's hogging them ---
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if lsof -ti tcp:"$port" >/dev/null 2>&1; then
    echo "warn: port $port already in use; killing the holder"
    lsof -ti tcp:"$port" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
done

# --- Cleanup on Ctrl+C ---
BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
  echo
  echo "Stopping..."
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null
  echo "Stopped."
}
trap cleanup EXIT INT TERM

# --- Start backend ---
echo "==> Starting backend on http://0.0.0.0:$BACKEND_PORT ..."
(
  python3 -m uvicorn backend.server:app \
    --host 0.0.0.0 --port "$BACKEND_PORT" --log-level warning 2>&1 \
    | sed -u 's/^/[backend] /'
) &
BACKEND_PID=$!

# --- Wait for backend to come up ---
for i in $(seq 1 20); do
  if curl -s "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then
    echo "    backend healthy."
    break
  fi
  if [ "$i" -eq 20 ]; then
    echo "ERROR: backend did not respond on port $BACKEND_PORT after 10s."
    exit 1
  fi
  sleep 0.5
done

# --- Start frontend ---
echo "==> Starting frontend on http://localhost:$FRONTEND_PORT ..."
(
  cd "$ROOT/dashboard" && \
    npx next dev -p "$FRONTEND_PORT" 2>&1 \
    | sed -u 's/^/[frontend] /'
) &
FRONTEND_PID=$!

echo
echo "==============================================================="
echo "  Dashboard:  http://localhost:$FRONTEND_PORT"
echo "  Backend:    http://localhost:$BACKEND_PORT"
echo "  Press Ctrl+C to stop both."
echo "==============================================================="
echo

# --- Wait for either to die ---
wait -n
