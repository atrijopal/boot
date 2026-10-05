#!/usr/bin/env bash
# Meeting AI - one command for everything.
#
#   bash run.sh                 first run: installs everything, then starts the web app
#   bash run.sh --setup         install only
#   bash run.sh --check         verify your API keys
#   bash run.sh --test          run the test suite (no keys needed)
#   bash run.sh --cli FILE      process one audio file from the terminal (add --provider groq|deepgram)
#
# Works on Linux, macOS and Windows via WSL / Git Bash. Needs: bash, curl (or pip), internet.
# Optional env vars: PORT (default 8000), VENV_DIR (where to put the Python environment).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PORT="${PORT:-8000}"
PY_VERSION="3.12"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }

ensure_uv() {
  command -v uv >/dev/null 2>&1 && return
  say "Installing uv (fast Python/package manager)"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || true
  fi
  if ! command -v uv >/dev/null 2>&1; then
    python3 -m pip install --user --quiet uv 2>/dev/null \
      || python3 -m pip install --user --quiet --break-system-packages uv 2>/dev/null || true
  fi
  command -v uv >/dev/null 2>&1 || die "Could not install uv. Install it from https://docs.astral.sh/uv/ and re-run."
}

venv_python() {
  if [ -f "$1/Scripts/python.exe" ]; then
    printf '%s\n' "$1/Scripts/python.exe"
  else
    printf '%s\n' "$1/bin/python"
  fi
}

pick_venv() {
  # 1) explicit VENV_DIR  2) remembered fallback  3) ./.venv
  if [ -n "${VENV_DIR:-}" ]; then echo "$VENV_DIR"; return; fi
  if [ -f .venv_dir ]; then cat .venv_dir; return; fi
  echo "$PWD/.venv"
}

make_venv() {
  local dir; dir="$(pick_venv)"
  if [ -x "$(venv_python "$dir")" ] && "$(venv_python "$dir")" -c "import sys" 2>/dev/null; then VENV="$dir"; return; fi
  say "Creating Python $PY_VERSION environment in $dir"
  if ! { uv venv --quiet --python "$PY_VERSION" "$dir" && "$(venv_python "$dir")" -c "import sys"; } 2>/dev/null; then
    # Some drives (e.g. NTFS/exFAT mounts) cannot hold a virtualenv: fall back to the home folder.
    local alt="$HOME/.venvs/meeting-ai"
    warn "Could not create the environment in the project folder; using $alt instead."
    rm -rf "$dir" "$alt"
    uv venv --quiet --python "$PY_VERSION" "$alt" || die "Could not create a Python $PY_VERSION environment."
    echo "$alt" > .venv_dir
    dir="$alt"
  fi
  VENV="$dir"
}

install_deps() {
  local py stamp want
  py="$(venv_python "$VENV")"; stamp="$VENV/.requirements.cksum"; want="$(cksum < requirements.txt)"
  if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$want" ] && "$py" -c "import fastapi, spacy, av, en_core_web_md" 2>/dev/null; then
    return
  fi
  say "Installing dependencies (first time takes a few minutes)"
  uv pip install --quiet --python "$py" -r requirements.txt || die "Dependency installation failed."
  echo "$want" > "$stamp"
}

ensure_env_file() {
  if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env 2>/dev/null || true
    say "Created .env from .env.example"
  fi
}

keys_status() {
  local groq
  groq="$(grep -E '^GROQ_API_KEY=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '[:space:]"' || true)"
  if [ -z "$groq" ]; then
    warn "GROQ_API_KEY is empty. Open .env and paste your key (see README.md, 'Get your API keys')."
    warn "The app will start, but transcription will fail until the key is set."
  fi
}

setup() {
  ensure_uv
  make_venv
  install_deps
  ensure_env_file
}

cmd="${1:-start}"
case "$cmd" in
  -h|--help|help)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  --setup)
    setup; keys_status; say "Setup complete. Start the app with:  bash run.sh"
    ;;
  --check)
    setup; "$(venv_python "$VENV")" scripts/check_keys.py
    ;;
  --test)
    setup; exec "$(venv_python "$VENV")" -m pytest -q
    ;;
  --cli)
    shift; [ $# -ge 1 ] || die "Usage: bash run.sh --cli FILE [--provider groq|deepgram]"
    setup; exec "$(venv_python "$VENV")" scripts/run_phase1.py "$@"
    ;;
  start|--start)
    setup; keys_status
    say "Starting Meeting AI at http://localhost:$PORT   (Ctrl+C to stop)"
    exec "$(venv_python "$VENV")" -m uvicorn app.api.main:app --host 127.0.0.1 --port "$PORT"
    ;;
  *)
    die "Unknown option '$cmd'. Try: bash run.sh --help"
    ;;
esac
