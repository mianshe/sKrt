#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ ! -d "$ROOT_DIR/backend/.venv" ]; then
  echo "Creating virtual environment at backend/.venv ..."
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv backend/.venv
  else
    python -m venv backend/.venv
  fi
fi

if [ -x "$ROOT_DIR/backend/.venv/bin/python" ]; then
  VENV_PY="$ROOT_DIR/backend/.venv/bin/python"
elif [ -x "$ROOT_DIR/backend/.venv/Scripts/python.exe" ]; then
  VENV_PY="$ROOT_DIR/backend/.venv/Scripts/python.exe"
else
  echo "ERROR: Could not find python in backend/.venv"
  exit 1
fi

echo "Upgrading pip..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null

echo "Installing requirements from requirements.txt ..."
"$VENV_PY" -m pip install -r requirements.txt

echo "Done. Run: $VENV_PY -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
echo "Or use: ./start.sh"
