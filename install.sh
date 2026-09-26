#!/usr/bin/env bash
# HomeIsland installer.
#
#   sudo ./install.sh                     interactive installation
#   sudo ./install.sh --yes --modules kiwix,library,maps
#   sudo ./install.sh --restore /data/homeisland/backups/<file>.tar.gz
#
# Safe to re-run: existing configuration and secrets are kept.
#
# This script never formats, partitions or mounts disks, never changes router
# settings and never opens ports to the Internet.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${HOMEISLAND_CONFIG_DIR:-/etc/homeisland}"
BIN_LINK="/usr/local/bin/homeisland"

ASSUME_YES=0
MODULES=""
HOST_IP=""
DOMAIN=""
DATA_DIR=""
STATE_DIR=""
TIMEZONE=""
RESTORE=""
INSTALL_DOCKER=1
ALLOW_SYSTEM_DISK=0

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./install.sh [options]

Options:
  -y, --yes                 non-interactive; accept detected defaults
  --modules LIST            optional modules to enable, comma separated
                            (kiwix,library,maps,samba,homeassistant,jellyfin,bme280,ups,fan)
  --host-ip IP              LAN address of this server (default: detected)
  --domain NAME             local DNS domain (default: home.arpa)
  --data-dir DIR            data directory on the large disk (default: /data/homeisland)
  --state-dir DIR           state directory on the system disk (default: /var/lib/homeisland)
  --timezone TZ             time zone, e.g. Europe/Prague (default: system setting)
  --restore FILE            restore configuration from a HomeIsland backup
  --allow-system-disk       allow the data directory on the system disk without asking
  --no-docker-install       fail instead of installing Docker when it is missing
  -h, --help                show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes) ASSUME_YES=1 ;;
        --modules) MODULES="${2:?}"; shift ;;
        --host-ip) HOST_IP="${2:?}"; shift ;;
        --domain) DOMAIN="${2:?}"; shift ;;
        --data-dir) DATA_DIR="${2:?}"; shift ;;
        --state-dir) STATE_DIR="${2:?}"; shift ;;
        --timezone) TIMEZONE="${2:?}"; shift ;;
        --restore) RESTORE="${2:?}"; shift ;;
        --allow-system-disk) ALLOW_SYSTEM_DISK=1 ;;
        --no-docker-install) INSTALL_DOCKER=0 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

interactive() { [ "$ASSUME_YES" -eq 0 ] && [ -t 0 ]; }

ask() {
    # ask "Question" "default" -> prints the answer
    local question="$1" default="$2" answer
    if interactive; then
        read -r -p "$question [$default]: " answer </dev/tty || true
        printf '%s' "${answer:-$default}"
    else
        printf '%s' "$default"
    fi
}

confirm() {
    local question="$1" answer
    if ! interactive; then
        return 0
    fi
    read -r -p "$question [y/N]: " answer </dev/tty || true
    case "$answer" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

env_value() {
    # env_value FILE KEY -> value of KEY=value in FILE (quotes stripped)
    sed -n "s/^$2=//p" "$1" 2>/dev/null | tail -n 1 | sed -e "s/^['\"]//" -e "s/['\"]$//"
}

# -- preflight -----------------------------------------------------------------

[ "$(id -u)" -eq 0 ] || die "run as root: sudo ./install.sh"

case "$REPO_DIR" in
    /tmp/*|/var/tmp/*) die "the repository is in a temporary directory; clone it to a permanent place such as /opt/homeisland" ;;
esac

ARCH="$(uname -m)"
case "$ARCH" in
    aarch64|arm64) ARCH_NAME="ARM64" ;;
    x86_64|amd64) ARCH_NAME="x86-64" ;;
    armv7l|armv6l|armhf)
        die "32-bit ARM is not supported (several images are 64-bit only). Install a 64-bit OS such as Raspberry Pi OS Lite (64-bit)." ;;
    *) die "unsupported architecture: $ARCH" ;;
esac

OS_ID="unknown"; OS_LIKE=""; OS_CODENAME=""; OS_NAME="unknown"
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"; OS_LIKE="${ID_LIKE:-}"; OS_CODENAME="${VERSION_CODENAME:-}"; OS_NAME="${PRETTY_NAME:-$OS_ID}"
fi
IS_DEBIAN_LIKE=0
case " $OS_ID $OS_LIKE " in *" debian "*|*" ubuntu "*|*" raspbian "*) IS_DEBIAN_LIKE=1 ;; esac

log "HomeIsland installer"
echo "    repository:   $REPO_DIR"
echo "    system:       $OS_NAME ($ARCH_NAME)"
if [ "$IS_DEBIAN_LIKE" -ne 1 ]; then
    warn "$OS_NAME is not a tested distribution (tested: Raspberry Pi OS, Debian, Ubuntu)."
    warn "Install docker (with the compose plugin), python3, git, curl and smartmontools yourself."
fi
case "$REPO_DIR" in
    /opt/*) ;;
    *) warn "recommended location for the repository is /opt/homeisland (systemd units will point to $REPO_DIR)" ;;
esac

# -- packages ------------------------------------------------------------------

if [ "$IS_DEBIAN_LIKE" -eq 1 ]; then
    log "Installing required packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    apt-get install -y -q --no-install-recommends \
        ca-certificates curl git python3 iproute2 iputils-ping smartmontools gnupg
fi
for cmd in python3 git curl ip; do
    command -v "$cmd" >/dev/null 2>&1 || die "required command not found: $cmd"
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "Python 3.9 or newer is required"

# -- Docker --------------------------------------------------------------------

install_docker() {
    local dist="$OS_ID"
    case " $OS_ID $OS_LIKE " in
        *" ubuntu "*) dist="ubuntu" ;;
        *) dist="debian" ;;
    esac
    [ -n "$OS_CODENAME" ] || die "cannot determine the OS codename for the Docker repository"
    log "Installing Docker Engine from the official Docker repository (download.docker.com)"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$dist/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
        "$(dpkg --print-architecture)" "$dist" "$OS_CODENAME" > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
}

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    [ "$INSTALL_DOCKER" -eq 1 ] || die "Docker with the compose plugin is required"
    [ "$IS_DEBIAN_LIKE" -eq 1 ] || die "install Docker Engine with the compose plugin, then re-run this script"
    confirm "Docker is not installed. Install Docker Engine from download.docker.com?" || die "Docker is required"
    install_docker
fi
docker info >/dev/null 2>&1 || die "the Docker daemon is not running (systemctl status docker)"
log "Using $(docker --version | cut -d, -f1) with compose $(docker compose version --short)"

# -- restore mode ---------------------------------------------------------------

if [ -n "$RESTORE" ]; then
    [ -f "$RESTORE" ] || die "backup not found: $RESTORE"
    log "Restoring configuration from $RESTORE"
    HOMEISLAND_CONFIG_DIR="$CONFIG_DIR" "$REPO_DIR/homeisland" restore "$RESTORE" --yes --no-start
fi

# -- network ----------------------------------------------------------------------

detect_ip() {
    local ip
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')"
    if [ -z "$ip" ]; then
        ip="$(ip -4 -o addr show scope global 2>/dev/null | awk '{split($4, a, "/"); print a[1]; exit}')"
    fi
    printf '%s' "$ip"
}

if [ -f "$CONFIG_DIR/homeisland.env" ]; then
    log "Keeping existing configuration in $CONFIG_DIR/homeisland.env"
    HOST_IP="$(env_value "$CONFIG_DIR/homeisland.env" HOMEISLAND_HOST_IP)"
    DATA_DIR="$(env_value "$CONFIG_DIR/homeisland.env" HOMEISLAND_DATA_DIR)"
    DETECTED_IP="$(detect_ip)"
    if [ -n "$DETECTED_IP" ] && [ "$DETECTED_IP" != "$HOST_IP" ]; then
        warn "configured HOMEISLAND_HOST_IP=$HOST_IP but this machine uses $DETECTED_IP"
        warn "edit $CONFIG_DIR/homeisland.env if the address changed"
    fi
    NEW_CONFIG=0
else
    NEW_CONFIG=1
    [ -n "$HOST_IP" ] || HOST_IP="$(ask "LAN IP address of this server" "$(detect_ip)")"
    [ -n "$HOST_IP" ] || die "could not detect the LAN address; pass --host-ip"
    DOMAIN="${DOMAIN:-$(ask "Local DNS domain" "home.arpa")}"
    DATA_DIR="${DATA_DIR:-$(ask "Data directory (on the large disk)" "/data/homeisland")}"
    STATE_DIR="${STATE_DIR:-/var/lib/homeisland}"
    if [ -z "$TIMEZONE" ]; then
        TIMEZONE="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
        [ -n "$TIMEZONE" ] || TIMEZONE="$(cat /etc/timezone 2>/dev/null || echo Etc/UTC)"
    fi
fi

if ip -4 -o addr show 2>/dev/null | grep -q " $HOST_IP/.* dynamic"; then
    warn "$HOST_IP was assigned by DHCP. Reserve it for this server in your router so it never changes."
fi

# Port 53 must be free on the LAN address (a resolver stub on 127.0.0.53 is fine).
port53="$(ss -Hlnutp 'sport = :53' 2>/dev/null)" || true
conflict="$(printf '%s\n' "$port53" | awk -v ip="$HOST_IP" '$0 !~ /docker-proxy/ && ($5 ~ "^" ip ":53$" || $5 ~ /^(0\.0\.0\.0|\*|\[::\]):53$/)')"
if [ -n "$conflict" ]; then
    printf '%s\n' "$conflict" >&2
    die "another program already listens on port 53 (see above). Stop it (e.g. dnsmasq or another Pi-hole) and re-run."
fi

# -- data disk ----------------------------------------------------------------------

log "Disks (for information; HomeIsland never formats or mounts disks):"
{ lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL 2>/dev/null || lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null; } | sed 's/^/    /' || true

existing_parent() {
    local p="$1"
    while [ ! -e "$p" ]; do p="$(dirname "$p")"; done
    printf '%s' "$p"
}

DATA_PARENT="$(existing_parent "$DATA_DIR")"
DATA_MOUNT="$(findmnt -n -o TARGET --target "$DATA_PARENT" 2>/dev/null || echo /)"
FSTAB_TARGET=""
probe="$DATA_DIR"
while [ "$probe" != "/" ]; do
    if findmnt --fstab -n --mountpoint "$probe" >/dev/null 2>&1; then FSTAB_TARGET="$probe"; break; fi
    probe="$(dirname "$probe")"
done
if [ -n "$FSTAB_TARGET" ] && ! mountpoint -q "$FSTAB_TARGET"; then
    die "$FSTAB_TARGET is listed in /etc/fstab but not mounted. Mount it (sudo mount $FSTAB_TARGET) and re-run."
fi

if [ -e "$DATA_DIR/.homeisland-data" ]; then
    log "Found existing HomeIsland data directory at $DATA_DIR (mounted on $DATA_MOUNT)"
else
    if [ "$DATA_MOUNT" = "/" ]; then
        warn "$DATA_DIR is on the system disk. Large datasets (Wikipedia, maps) belong on a separate disk;"
        warn "see docs/STORAGE.md for mounting one. You can move the data later."
        if [ "$ALLOW_SYSTEM_DISK" -ne 1 ] && ! confirm "Use $DATA_DIR on the system disk anyway?"; then
            die "mount your data disk first, then re-run the installer"
        fi
    fi
    mkdir -p "$DATA_DIR"
    touch "$DATA_DIR/.homeisland-data"
    log "Initialised data directory $DATA_DIR"
fi

# -- configuration ---------------------------------------------------------------------

install -d -m 0755 "$CONFIG_DIR"
if [ "$NEW_CONFIG" -eq 1 ]; then
    DATA_UID="${SUDO_UID:-1000}"; DATA_GID="${SUDO_GID:-1000}"
    [ "$DATA_UID" != "0" ] || { DATA_UID=1000; DATA_GID=1000; }
    tmp="$(mktemp)"
    sed -e "s#^HOMEISLAND_HOST_IP=.*#HOMEISLAND_HOST_IP=$HOST_IP#" \
        -e "s#^HOMEISLAND_DOMAIN=.*#HOMEISLAND_DOMAIN=$DOMAIN#" \
        -e "s#^HOMEISLAND_DATA_DIR=.*#HOMEISLAND_DATA_DIR=$DATA_DIR#" \
        -e "s#^HOMEISLAND_STATE_DIR=.*#HOMEISLAND_STATE_DIR=$STATE_DIR#" \
        -e "s#^HOMEISLAND_TZ=.*#HOMEISLAND_TZ=$TIMEZONE#" \
        -e "s#^HOMEISLAND_DATA_UID=.*#HOMEISLAND_DATA_UID=$DATA_UID#" \
        -e "s#^HOMEISLAND_DATA_GID=.*#HOMEISLAND_DATA_GID=$DATA_GID#" \
        "$REPO_DIR/.env.example" > "$tmp"
    install -m 0644 "$tmp" "$CONFIG_DIR/homeisland.env"
    rm -f "$tmp"
    log "Wrote $CONFIG_DIR/homeisland.env"
fi

gen_secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(18))'; }
touch "$CONFIG_DIR/secrets.env"
chmod 0600 "$CONFIG_DIR/secrets.env"
for key in PIHOLE_WEB_PASSWORD SAMBA_PASSWORD; do
    if ! grep -q "^$key=." "$CONFIG_DIR/secrets.env"; then
        printf '%s=%s\n' "$key" "$(gen_secret)" >> "$CONFIG_DIR/secrets.env"
        log "Generated $key"
    fi
done

if [ ! -f "$CONFIG_DIR/dns-records.conf" ]; then
    install -m 0644 "$REPO_DIR/examples/dns-records.conf" "$CONFIG_DIR/dns-records.conf"
fi

if [ ! -f "$CONFIG_DIR/modules.enabled" ]; then
    if [ -z "$MODULES" ] && interactive; then
        echo
        echo "Optional modules (core DNS, dashboard, monitoring and backups are always installed):"
        echo "  kiwix          offline Wikipedia and other ZIM archives"
        echo "  library        document library with full-text search"
        echo "  maps           offline vector maps"
        echo "  samba          SMB file shares on the data disk"
        echo "  homeassistant  Home Assistant"
        echo "  jellyfin       media server"
        echo "  bme280 ups fan hardware integrations (see docs/HARDWARE.md)"
        MODULES="$(ask "Modules to enable (comma separated)" "kiwix,library,maps")"
    fi
    printf '# Optional HomeIsland modules, one per line.\n# Manage with: homeisland module enable|disable <name>\n' > "$CONFIG_DIR/modules.enabled"
    IFS=', ' read -r -a module_list <<< "$MODULES"
    for m in "${module_list[@]}"; do
        [ -n "$m" ] || continue
        [ -d "$REPO_DIR/modules/$m" ] || die "unknown module: $m"
        printf '%s\n' "$m" >> "$CONFIG_DIR/modules.enabled"
    done
elif [ -n "$MODULES" ]; then
    warn "$CONFIG_DIR/modules.enabled exists; ignoring --modules (use: homeisland module enable ...)"
fi

HOMEISLAND_CONFIG_DIR="$CONFIG_DIR" "$REPO_DIR/homeisland" config check

# Containers read the dashboard and map viewer straight from the repository.
chmod -R o+rX "$REPO_DIR/dashboard" "$REPO_DIR/modules"

# -- CLI, services, start --------------------------------------------------------------

ln -sfn "$REPO_DIR/homeisland" "$BIN_LINK"
log "Installed $BIN_LINK"

log "Installing systemd units"
HOMEISLAND_CONFIG_DIR="$CONFIG_DIR" "$REPO_DIR/homeisland" system install-units

log "Starting HomeIsland (downloading container images may take a while)"
set +e
HOMEISLAND_CONFIG_DIR="$CONFIG_DIR" "$REPO_DIR/homeisland" apply --pull
apply_status=$?
set -e
[ "$apply_status" -eq 0 ] || [ "$apply_status" -eq 2 ] || die "starting services failed; see the output above"

log "Waiting for services to become ready"
for _ in $(seq 1 30); do
    if HOMEISLAND_CONFIG_DIR="$CONFIG_DIR" "$REPO_DIR/homeisland" health >/dev/null 2>&1; then break; fi
    sleep 5
done
HOMEISLAND_CONFIG_DIR="$CONFIG_DIR" "$REPO_DIR/homeisland" health || warn "some checks failed; see: homeisland status"

log "Creating a first configuration backup"
HOMEISLAND_CONFIG_DIR="$CONFIG_DIR" "$REPO_DIR/homeisland" backup --quiet || warn "backup failed; see docs/BACKUP.md"

HOST_IP="$(env_value "$CONFIG_DIR/homeisland.env" HOMEISLAND_HOST_IP)"
DOMAIN="$(env_value "$CONFIG_DIR/homeisland.env" HOMEISLAND_DOMAIN)"
cat <<EOF

HomeIsland is installed.

  Dashboard:        http://$HOST_IP/   (http://home.${DOMAIN:-home.arpa}/ once DNS is configured)
  Pi-hole password: sudo homeisland secrets show pihole
  Status:           homeisland status
  Offline test:     homeisland test offline

Next steps:
  1. Point your router's DHCP DNS setting to $HOST_IP (docs/NETWORKING.md).
     Keep the router as the DHCP server.
  2. Download offline data while you are online (docs/KNOWLEDGE.md, docs/MAPS.md).
  3. Run 'homeisland audit' and fix any warnings.

EOF
