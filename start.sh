#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

APP_MODULE="${APP_MODULE:-backend.main:app}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
INSTALL_DEPS="${INSTALL_DEPS:-0}"
BUILD_FRONTEND="${BUILD_FRONTEND:-0}"
NPM_BIN="${NPM_BIN:-npm}"

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

if [ "$INSTALL_DEPS" = "1" ]; then
  echo "[setup] Installing backend dependencies..."
  "$VENV_PY" -m pip install --upgrade pip
  "$VENV_PY" -m pip install -r requirements.txt

  if [ -d "$ROOT_DIR/frontend" ]; then
    echo "[setup] Installing frontend dependencies..."
    "$NPM_BIN" --prefix "$ROOT_DIR/frontend" ci
  fi
fi

if [ "$BUILD_FRONTEND" = "1" ]; then
  if [ ! -d "$ROOT_DIR/frontend" ]; then
    echo "ERROR: frontend directory not found"
    exit 1
  fi
  echo "[build] Building frontend..."
  "$NPM_BIN" --prefix "$ROOT_DIR/frontend" run build
fi

UVICORN_ARGS=( -m uvicorn "$APP_MODULE" --host "$BACKEND_HOST" --port "$BACKEND_PORT" )
if [ -f "$ROOT_DIR/.env" ]; then
  UVICORN_ARGS+=( --env-file "$ROOT_DIR/.env" )
fi

if [ -n "${UVICORN_EXTRA_ARGS:-}" ]; then
  # shellcheck disable=SC2206
  EXTRA_ARGS=( ${UVICORN_EXTRA_ARGS} )
  UVICORN_ARGS+=( "${EXTRA_ARGS[@]}" )
fi

echo "[start] Working directory: $ROOT_DIR"
echo "[start] Python: $VENV_PY"
echo "[start] Uvicorn: $APP_MODULE on ${BACKEND_HOST}:${BACKEND_PORT}"
if [ -f "$ROOT_DIR/.env" ]; then
  echo "[start] Env file: $ROOT_DIR/.env"
else
  echo "[start] Env file: not found, using current environment"
fi

exec "$VENV_PY" "${UVICORN_ARGS[@]}"
