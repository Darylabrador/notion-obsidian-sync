#!/usr/bin/env bash
# Uninstall the notion-obsidian-sync CLI, regardless of how it was installed
# (project .venv, pipx, or a plain `pip install` into some other
# interpreter/venv). Also disables and removes the systemd --user timer if
# one was installed.
#
# This script NEVER touches your Obsidian vault or the notes it synced —
# only the tool's own installation and local project state are candidates
# for removal, and even those require your confirmation (or --yes).
#
# Usage:
#   ./scripts/linux/uninstall.sh                # interactive
#   ./scripts/linux/uninstall.sh --yes           # no prompts
#   ./scripts/linux/uninstall.sh --yes --purge   # also remove local state/logs (.sync-state.sqlite*, logs/)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

ASSUME_YES=0
PURGE=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y) ASSUME_YES=1 ;;
        --purge) PURGE=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

confirm() {
    local prompt="$1"
    if [ "$ASSUME_YES" -eq 1 ]; then
        return 0
    fi
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

did_something=0

# --- 1. Automation: systemd --user timer/service -----------------------------

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
if [ -f "$UNIT_DIR/notion-obsidian-sync.timer" ] || [ -f "$UNIT_DIR/notion-obsidian-sync.service" ]; then
    if confirm "Found a systemd --user timer/service. Disable and remove it?"; then
        systemctl --user disable --now notion-obsidian-sync.timer >/dev/null 2>&1 || true
        rm -f "$UNIT_DIR/notion-obsidian-sync.timer" "$UNIT_DIR/notion-obsidian-sync.service"
        systemctl --user daemon-reload >/dev/null 2>&1 || true
        echo "Removed systemd --user timer/service."
        did_something=1
    fi
fi

# --- 2. pipx install -----------------------------------------------------------

if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q '^notion-obsidian-sync '; then
    if confirm "Found a pipx installation of notion-obsidian-sync. Uninstall it?"; then
        pipx uninstall notion-obsidian-sync
        echo "Removed pipx installation."
        did_something=1
    fi
fi

# --- 3. Project virtual environment (.venv) -------------------------------------

if [ -d ".venv" ]; then
    if confirm "Found the project virtual environment at $PROJECT_DIR/.venv. Remove it?"; then
        rm -rf ".venv"
        echo "Removed $PROJECT_DIR/.venv."
        did_something=1
    fi
fi

# --- 4. Any other pip install (system/user Python, outside .venv/pipx) --------

for PY in python3 python; do
    if command -v "$PY" >/dev/null 2>&1; then
        if "$PY" -m pip show notion-obsidian-sync >/dev/null 2>&1; then
            if confirm "Found notion-obsidian-sync installed for '$($PY -c 'import sys; print(sys.executable)')'. Uninstall it?"; then
                "$PY" -m pip uninstall -y notion-obsidian-sync
                echo "Removed pip installation for $PY."
                did_something=1
            fi
        fi
        break
    fi
done

# --- 5. Optional: local state/logs (never the vault) ---------------------------

if [ "$PURGE" -eq 1 ]; then
    rm -f .sync-state.sqlite .sync-state.sqlite-wal .sync-state.sqlite-shm
    rm -rf logs
    echo "Purged local state database and logs (your synced notes in the Obsidian vault were not touched)."
    did_something=1
fi

echo
if [ "$did_something" -eq 1 ]; then
    echo "Uninstall complete."
else
    echo "Nothing found to uninstall."
fi
echo "Note: your .env, and everything already synced into your Obsidian vault, were left untouched."
[ "$PURGE" -eq 0 ] && echo "Local state (.sync-state.sqlite*, logs/) was kept — re-run with --purge to remove it too."
