#!/usr/bin/env bash
# Offline readiness test. Same as `homeisland test offline`.
# For a real island test, unplug the router's WAN cable first (docs/OFFLINE_MODE.md).
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/homeisland" test offline "$@"
