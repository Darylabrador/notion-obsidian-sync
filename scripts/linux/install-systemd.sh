#!/usr/bin/env bash
# Install the systemd --user service + timer so sync runs automatically.
# Adjust SYNC_INTERVAL_MINUTES in .env, then re-run this script to update
# the timer's OnUnitActiveSec.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$UNIT_DIR"

INTERVAL_MINUTES=10
if [ -f "$PROJECT_DIR/.env" ]; then
    configured="$(grep -E '^SYNC_INTERVAL_MINUTES=' "$PROJECT_DIR/.env" | tail -n1 | cut -d= -f2- || true)"
    if [ -n "${configured:-}" ]; then
        INTERVAL_MINUTES="$configured"
    fi
fi

sed "s#__PROJECT_DIR__#$PROJECT_DIR#g" "$SCRIPT_DIR/notion-obsidian-sync.service" \
    > "$UNIT_DIR/notion-obsidian-sync.service"

sed "s/OnUnitActiveSec=10min/OnUnitActiveSec=${INTERVAL_MINUTES}min/" \
    "$SCRIPT_DIR/notion-obsidian-sync.timer" > "$UNIT_DIR/notion-obsidian-sync.timer"

systemctl --user daemon-reload

echo "Installed systemd user units to $UNIT_DIR"
echo
echo "Enable and start the timer with:"
echo "  systemctl --user enable --now notion-obsidian-sync.timer"
echo
echo "Check status with:"
echo "  systemctl --user status notion-obsidian-sync.timer"
echo "  journalctl --user -u notion-obsidian-sync.service"
