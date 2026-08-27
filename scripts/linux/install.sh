#!/usr/bin/env bash
# Install notion-obsidian-sync: create a virtualenv, install the package,
# and scaffold a .env file. Safe to re-run; never overwrites an existing .env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is not installed or not on PATH." >&2
    exit 1
fi

PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "ERROR: Python 3.11+ is required (found $PY_VERSION)." >&2
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

echo "Installing notion-obsidian-sync..."
"./.venv/bin/pip" install -q --upgrade pip
"./.venv/bin/pip" install -q -e .

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env from .env.example — edit it with your Notion token and vault path."
else
    echo ".env already exists, leaving it untouched."
fi

echo
echo "Install complete. Next steps:"
echo "  1. Edit .env with your Notion integration token and Obsidian vault path."
echo "  2. source .venv/bin/activate"
echo "  3. notion-obsidian-sync doctor"
echo "  4. notion-obsidian-sync dry-run"
echo "  5. notion-obsidian-sync sync"
echo
echo "To automate periodic syncs, see scripts/linux/notion-obsidian-sync.service"
echo "and scripts/linux/notion-obsidian-sync.timer."
