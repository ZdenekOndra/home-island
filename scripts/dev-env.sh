#!/usr/bin/env bash
# Create a throwaway development configuration that runs HomeIsland as a normal
# user on high ports (HTTP 8088, DNS 15353) bound to 127.0.0.1.
#
#   scripts/dev-env.sh /tmp/homeisland-dev
#   export HOMEISLAND_CONFIG_DIR=/tmp/homeisland-dev/etc HOMEISLAND_DEV=1
#   ./homeisland apply && ./homeisland collector --once && ./homeisland test offline
#
# Used by CI for the smoke test. Not for production use.

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV="${1:?usage: scripts/dev-env.sh <directory>}"
mkdir -p "$DEV/etc" "$DEV/state" "$DEV/data"
DEV="$(cd "$DEV" && pwd)"
touch "$DEV/data/.homeisland-data"

sed -e "s#^HOMEISLAND_HOST_IP=.*#HOMEISLAND_HOST_IP=127.0.0.1#" \
    -e "s#^HOMEISLAND_HTTP_PORT=.*#HOMEISLAND_HTTP_PORT=8088#" \
    -e "s#^HOMEISLAND_DNS_PORT=.*#HOMEISLAND_DNS_PORT=15353#" \
    -e "s#^HOMEISLAND_STATE_DIR=.*#HOMEISLAND_STATE_DIR=$DEV/state#" \
    -e "s#^HOMEISLAND_DATA_DIR=.*#HOMEISLAND_DATA_DIR=$DEV/data#" \
    -e "s#^HOMEISLAND_DATA_UID=.*#HOMEISLAND_DATA_UID=$(id -u)#" \
    -e "s#^HOMEISLAND_DATA_GID=.*#HOMEISLAND_DATA_GID=$(id -g)#" \
    "$REPO_DIR/.env.example" > "$DEV/etc/homeisland.env"

if [ ! -f "$DEV/etc/secrets.env" ]; then
    python3 -c 'import secrets
for k in ("PIHOLE_WEB_PASSWORD", "SAMBA_PASSWORD"):
    print("%s=%s" % (k, secrets.token_urlsafe(18)))' > "$DEV/etc/secrets.env"
    chmod 600 "$DEV/etc/secrets.env"
fi
[ -f "$DEV/etc/modules.enabled" ] || printf '%s\n' "${HOMEISLAND_DEV_MODULES:-kiwix library maps}" | tr ' ' '\n' > "$DEV/etc/modules.enabled"

echo "export HOMEISLAND_CONFIG_DIR=$DEV/etc HOMEISLAND_DEV=1"
