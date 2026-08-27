#!/usr/bin/env bash
# Run one sync pass. Intended for manual use or as the command invoked by
# the systemd service. Any extra arguments are forwarded to `sync`
# (e.g. `./sync.sh --verbose`).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -x ".venv/bin/notion-obsidian-sync" ]; then
    echo "ERROR: virtual environment not found. Run scripts/linux/install.sh first." >&2
    exit 1
fi

exec "./.venv/bin/notion-obsidian-sync" sync "$@"
