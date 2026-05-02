#!/usr/bin/env bash
# One-time setup for the dashboard. Run this from the repo root before
# the first start. Idempotent — safe to re-run if a dep is missing.
#
# Usage:
#   ./setup_dashboard.sh

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "==> 1/3  Checking Python..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.9+ and re-run."
  exit 1
fi
PY="$(command -v python3)"
echo "    Using $($PY --version) at $PY"

echo
echo "==> 2/3  Installing Python deps (backend + bot)..."
$PY -m pip install --upgrade pip --quiet
$PY -m pip install \
  fastapi \
  uvicorn \
  psutil \
  python-dotenv \
  pytz \
  requests \
  pandas \
  numpy \
  upstox-client \
  --quiet
echo "    Python deps OK."

echo
echo "==> 3/3  Installing Node deps for the dashboard..."
if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node not found. Install Node 18+ and re-run."
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not found. Install npm and re-run."
  exit 1
fi
echo "    Using $(node --version) / npm $(npm --version)"

cd "$ROOT/dashboard"
if [ ! -d node_modules ]; then
  npm install --silent
else
  npm install --silent --prefer-offline
fi
cd "$ROOT"
echo "    Node deps OK."

echo
echo "==============================================================="
echo "  Setup complete."
echo "  Next:  ./start_dashboard.sh"
echo "==============================================================="
