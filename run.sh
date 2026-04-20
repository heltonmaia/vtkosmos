#!/usr/bin/env bash
# ------------------------------------------------------------------
# Vision Terminal Kosmos - launcher
# ------------------------------------------------------------------
# Loads the local config (config.sh) and runs the CLI with the chosen
# backend (uv or venv). With no arguments, opens the interactive menu.
#
# Usage:
#   ./run.sh                  # interactive menu
#   ./run.sh cut --help       # subcommand directly
#   ./run.sh wa-fix video.mp4
# ------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Load config.sh (fall back to defaults with a warning) -------
if [[ -f "$SCRIPT_DIR/config.sh" ]]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/config.sh"
else
    echo "[Vision Terminal Kosmos] config.sh not found. Using defaults (backend=uv, local paths)."
    echo "  Tip: cp config.sh.example config.sh  and tune it for your machine."
    export VTERM_BACKEND="${VTERM_BACKEND:-uv}"
    export VTERM_VENV_PATH="${VTERM_VENV_PATH:-./.venv}"
    export VTERM_PYTHON="${VTERM_PYTHON:-python3}"
fi

BACKEND="${VTERM_BACKEND:-uv}"

case "$BACKEND" in
    uv)
        if ! command -v uv >/dev/null 2>&1; then
            echo "[Vision Terminal Kosmos] 'uv' is not installed. Install it with:"
            echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
            exit 1
        fi
        # uv honors UV_PROJECT_ENVIRONMENT and UV_CACHE_DIR from the env.
        # `uv run` automatically syncs dependencies before executing.
        exec uv run vterm "$@"
        ;;
    venv)
        VENV_PATH="${VTERM_VENV_PATH:-./.venv}"
        PYTHON_BIN="${VTERM_PYTHON:-python3}"
        if [[ ! -d "$VENV_PATH" ]]; then
            echo "[Vision Terminal Kosmos] Creating venv at $VENV_PATH ..."
            "$PYTHON_BIN" -m venv "$VENV_PATH"
            # shellcheck disable=SC1091
            source "$VENV_PATH/bin/activate"
            pip install --upgrade pip
            pip install -e .
        else
            # shellcheck disable=SC1091
            source "$VENV_PATH/bin/activate"
        fi
        exec vterm "$@"
        ;;
    *)
        echo "[Vision Terminal Kosmos] Invalid VTERM_BACKEND: '$BACKEND' (use 'uv' or 'venv')."
        exit 1
        ;;
esac
