#!/usr/bin/env bash
# Remove HomeIsland services from this machine.
#
#   sudo ./uninstall.sh            stop and remove containers, systemd units and the CLI link;
#                                  keep configuration, state, datasets and backups
#   sudo ./uninstall.sh --purge    additionally delete configuration and state
#                                  (/etc/homeisland and the state directory) after confirmation
#
# The data directory (datasets, documents, media, backups) is never deleted.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${HOMEISLAND_CONFIG_DIR:-/etc/homeisland}"
PURGE=0

case "${1:-}" in
    "") ;;
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo ./uninstall.sh" >&2; exit 1; }

STATE_DIR="$(sed -n 's/^HOMEISLAND_STATE_DIR=//p' "$CONFIG_DIR/homeisland.env" 2>/dev/null | tail -n 1)"
STATE_DIR="${STATE_DIR:-/var/lib/homeisland}"
DATA_DIR="$(sed -n 's/^HOMEISLAND_DATA_DIR=//p' "$CONFIG_DIR/homeisland.env" 2>/dev/null | tail -n 1)"

echo "==> Stopping and removing HomeIsland containers"
if command -v docker >/dev/null 2>&1; then
    ids="$(docker ps -aq --filter label=com.docker.compose.project=homeisland)"
    if [ -n "$ids" ]; then
        # shellcheck disable=SC2086
        docker rm -f $ids >/dev/null
    fi
    docker network rm homeisland >/dev/null 2>&1 || true
fi

echo "==> Removing systemd units"
for unit in homeisland-collector.service homeisland-boot.service homeisland-backup.timer homeisland-backup.service; do
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$unit"
done
systemctl daemon-reload || true

if [ "$(readlink /usr/local/bin/homeisland 2>/dev/null)" = "$REPO_DIR/homeisland" ]; then
    rm -f /usr/local/bin/homeisland
fi

if [ "$PURGE" -eq 1 ]; then
    echo
    echo "This deletes $CONFIG_DIR and $STATE_DIR (Pi-hole settings, Home Assistant configuration,"
    echo "search indexes, generated passwords). Backups in the data directory are kept."
    read -r -p "Type 'delete' to continue: " answer </dev/tty
    if [ "$answer" = "delete" ]; then
        case "$STATE_DIR" in /|"") echo "refusing to delete '$STATE_DIR'" >&2; exit 1 ;; esac
        rm -rf -- "$CONFIG_DIR" "$STATE_DIR"
        echo "==> Configuration and state deleted"
    else
        echo "Not deleted."
    fi
fi

echo
echo "HomeIsland services were removed."
[ "$PURGE" -eq 1 ] || echo "Configuration kept in $CONFIG_DIR and $STATE_DIR."
[ -z "$DATA_DIR" ] || echo "Data directory $DATA_DIR was not touched."
echo "Container images can be removed with: docker image prune -a"
echo "Remember to point your router's DNS setting away from this server."
