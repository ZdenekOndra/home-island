#!/usr/bin/env bash
# Update HomeIsland. Equivalent to `sudo homeisland update`.
#
#   sudo ./update.sh                 update to the latest version of the current branch
#   sudo ./update.sh --ref v0.2.0    update (or downgrade) to a specific tag
#   sudo ./update.sh --rollback      return to the version before the last update
#
# A configuration backup is created first. Configuration, datasets, user files
# and backups are never modified by an update.

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo ./update.sh" >&2; exit 1; }
exec "$REPO_DIR/homeisland" update "$@"
