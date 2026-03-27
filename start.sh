#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ -x "$ROOT_DIR/backend/.venv/bin/python" ]; then
  VENV_PY="$ROOT_DIR/backend/.venv/bin/python"
elif [ -x "$ROOT_DIR/backend/.venv/Scripts/python.exe" ]; then
  VENV_PY="$ROOT_DIR/backend/.venv/Scripts/python.exe"
else
  echo "ERROR: backend virtualenv python not found at backend/.venv"
  echo "Please create it first (Python 3.11):"
  echo "  py -3.11 -m venv backend/.venv"
  exit 1
fi

echo "[1/4] Installing backend dependencies..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r requirements.txt >/dev/null

echo "[2/4] Installing frontend dependencies..."
npm --prefix frontend install >/dev/null

echo "[3/4] Starting backend on :8000 ..."
"$VENV_PY" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

cleanup() {
  echo "Shutting down..."
  kill "$BACKEND_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[4/4] Starting frontend on :5173 ..."
npm --prefix frontend run dev -- --host 0.0.0.0 --port 5173
