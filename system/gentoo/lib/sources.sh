#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Later batches supply reviewed HTTPS URLs and SHA256 digests, not live versions.
gentoo_verify_source() {
    local file=$1 digest=$2 actual
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || return 1
    [ -f "$file" ] || return 1
    actual=$(sha256sum < "$file" | cut -d' ' -f1)
    [ "$actual" = "$digest" ]
}

gentoo_fetch_source() {
    local url=$1 digest=$2 destination=$3 temporary
    [[ "$url" == https://* && "$digest" =~ ^[0-9a-f]{64}$ ]] || gentoo_die 'source requires HTTPS and a pinned SHA256'
    if [ -e "$destination" ]; then
        gentoo_verify_source "$destination" "$digest" || gentoo_die "cached source checksum mismatch: $destination"
        return
    fi
    mkdir -p "$(dirname "$destination")"
    temporary=$(mktemp "${destination}.partial.XXXXXX")
    # The driver lock serializes cache writes. Failed partials are never reused.
    if ! wget -O "$temporary" -- "$url"; then
        rm -f "$temporary"
        gentoo_die "source download failed: $url"
    fi
    if ! gentoo_verify_source "$temporary" "$digest"; then
        rm -f "$temporary"
        gentoo_die "downloaded source checksum mismatch: $url"
    fi
    sync
    mv "$temporary" "$destination"
    sync
}
