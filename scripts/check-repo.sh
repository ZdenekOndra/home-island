#!/usr/bin/env bash
# Scan tracked files for things that must never be committed to this public
# repository: private keys, tokens, real-looking credentials, personal e-mail
# addresses, non-example IP addresses and runtime data.
#
# Usage: scripts/check-repo.sh   (exit 1 if anything suspicious is found)

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

status=0
fail() { printf 'FAIL: %s\n' "$*"; status=1; }

files() { git ls-files --cached --others --exclude-standard | grep -v -e '^tests/' -e '^scripts/check-repo.sh$'; }
# Portable (GNU/BSD) xargs over the file list.
scan() { files | tr '\n' '\0' | xargs -0 "$@"; }

# 1. Private keys and common token formats.
patterns=(
    '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    'AKIA[0-9A-Z]{16}'
    'gh[pousr]_[A-Za-z0-9]{36,}'
    'xox[baprs]-[A-Za-z0-9-]{10,}'
    'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.'
    'sk-[A-Za-z0-9]{32,}'
)
for p in "${patterns[@]}"; do
    if hits="$(scan grep -nIE -e "$p" 2>/dev/null)"; then
        fail "possible secret matching '$p':"; printf '%s\n' "$hits"
    fi
done

# 2. Assigned passwords/secrets with literal values (placeholders and variables are fine).
if hits="$(scan grep -nIE '^(export )?[A-Z_]*(PASSWORD|SECRET|TOKEN|API_KEY)[A-Z_]*=[^$[:space:]]{6,}' 2>/dev/null)"; then
    fail "credential assignments:"; printf '%s\n' "$hits"
fi

# 3. E-mail addresses other than documentation placeholders.
if hits="$(scan grep -nIoE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' 2>/dev/null \
        | grep -vE '@(example\.(com|org|net)|users\.noreply\.github\.com)$|noreply@|security@example')"; then
    fail "e-mail addresses:"; printf '%s\n' "$hits"
fi

# 4. IPv4 addresses outside documentation/example ranges and well-known public resolvers.
allowed='^(192\.168\.1\.[0-9]+|192\.0\.2\.[0-9]+|198\.51\.100\.[0-9]+|203\.0\.113\.[0-9]+|127\.0\.0\.[0-9]+|0\.0\.0\.0|255\.255\.255\.[0-9]+|10\.0\.0\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+|1\.1\.1\.1|1\.0\.0\.1|8\.8\.8\.8|8\.8\.4\.4|9\.9\.9\.9|149\.112\.112\.112|208\.67\.222\.222|208\.67\.220\.220)$'
while IFS= read -r line; do
    ip="${line##*:}"
    if ! printf '%s' "$ip" | grep -qE "$allowed"; then
        fail "unexpected IP address: $line"
    fi
done < <(files | grep -vE '\.(svg|png|jpg)$' | tr '\n' '\0' | xargs -0 grep -noIE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' 2>/dev/null || true)

# 5. Runtime data, backups and datasets.
if hits="$(files | grep -E '(^|/)(secrets\.env|homeisland\.env|modules\.enabled|\.env)$|\.(zim|pmtiles|mbtiles|sqlite|db)$|homeisland-backup-.*\.tar\.gz|teleporter.*\.zip$|pihole\.toml$')"; then
    fail "runtime data or datasets are tracked:"; printf '%s\n' "$hits"
fi

[ "$status" -eq 0 ] && echo "repository check passed"
exit "$status"
