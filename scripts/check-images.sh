#!/usr/bin/env bash
# Verify that every pinned container image is available for linux/arm64 and
# linux/amd64. Needs network access and `docker manifest` (or `docker buildx imagetools`).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

images="$(grep -hE '^\s+image:' compose.yml modules/*/compose.yml | awk '{print $2}' | grep -v '^homeisland/' | sort -u)"
# Base image of the locally built library module and the maps extraction tool.
images="$images
$(awk '/^FROM/ {print $2}' modules/library/Dockerfile)
$(sed -n 's/^PMTILES_IMAGE = "\(.*\)"/\1/p' lib/homeisland/maps.py)"

status=0
for image in $images; do
    manifest="$(docker manifest inspect "$image" 2>/dev/null || true)"
    if [ -z "$manifest" ]; then
        printf 'FAIL  %-70s manifest not found\n' "$image"; status=1; continue
    fi
    for arch in arm64 amd64; do
        if ! printf '%s' "$manifest" | grep -q "\"architecture\": *\"$arch\""; then
            printf 'FAIL  %-70s no linux/%s image\n' "$image" "$arch"; status=1
        fi
    done
    [ "$status" -ne 0 ] || printf 'ok    %-70s arm64 amd64\n' "$image"
done
exit "$status"
