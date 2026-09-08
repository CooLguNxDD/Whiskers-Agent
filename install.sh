#!/bin/sh
# Bootstrap Whiskers Agent: Docker, Python 3.11 venv, requirements, then setup.py all.
# Real setup logic lives in terminal/script/setup.py — this script stays thin.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

NO_UV=0
SKIP_DEPS=0
FORWARD=""

usage() {
  cat <<'EOF'
Usage: ./install.sh [installer flags] [setup.py flags]

Installer:
  --no-uv       skip uv; use python3 -m venv + pip
  --skip-deps   skip venv and pip; run setup.py with current interpreter

Forwarded to setup.py all:
  --yes / -y    non-interactive
  --dev         docker-compose.dev.yml
  --provider    openai | anthropic | gemini | gemini-vertex
  --force --from STEP --dry-run --reset-plugins
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --no-uv)
      NO_UV=1
      shift
      ;;
    --skip-deps)
      SKIP_DEPS=1
      shift
      ;;
    --yes|-y|--dev|--force|--dry-run|--reset-plugins)
      FORWARD="$FORWARD $1"
      shift
      ;;
    --provider|--from)
      if [ $# -lt 2 ]; then
        echo "error: $1 requires a value" >&2
        exit 1
      fi
      FORWARD="$FORWARD $1 $2"
      shift 2
      ;;
    *)
      FORWARD="$FORWARD $1"
      shift
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker is not on PATH. Install Docker Desktop / Engine, then retry." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "error: Docker daemon is not running." >&2
  echo "  Start Docker Desktop (or: sudo systemctl start docker) and retry." >&2
  exit 1
fi

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  if [ -x "$HOME/.local/bin/uv" ]; then
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    return 0
  fi
  echo "Installing uv (https://astral.sh/uv)..."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    echo "error: cannot install uv (need curl or wget). Re-run with --no-uv." >&2
    return 1
  fi
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  command -v uv >/dev/null 2>&1
}

PYTHON=""
if [ "$SKIP_DEPS" -eq 0 ]; then
  USED_UV=0
  if [ "$NO_UV" -eq 0 ]; then
    if ensure_uv; then
      USED_UV=1
    else
      echo "warning: uv unavailable; falling back to python3 -m venv + pip"
    fi
  fi

  if [ "$USED_UV" -eq 1 ]; then
    uv venv --allow-existing .venv --python 3.11
    uv pip install -r requirements.txt
    PYTHON="$ROOT/.venv/bin/python"
    if [ ! -x "$PYTHON" ]; then
      PYTHON="$ROOT/.venv/Scripts/python.exe"
    fi
  else
    PY=""
    if command -v python3.11 >/dev/null 2>&1; then
      PY=python3.11
    elif command -v python3 >/dev/null 2>&1; then
      PY=python3
    else
      echo "error: Python 3.11+ not found. Install it or uv (omit --no-uv)." >&2
      exit 1
    fi
    "$PY" -m venv .venv
    PYTHON="$ROOT/.venv/bin/python"
    if [ ! -x "$PYTHON" ]; then
      PYTHON="$ROOT/.venv/Scripts/python.exe"
    fi
    "$PYTHON" -m pip install -U pip
    "$PYTHON" -m pip install -r requirements.txt
  fi
else
  if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
  elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
    PYTHON="$ROOT/.venv/Scripts/python.exe"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
  else
    PYTHON=python
  fi
fi

# shellcheck disable=SC2086
exec "$PYTHON" "$ROOT/terminal/script/setup.py" all $FORWARD
