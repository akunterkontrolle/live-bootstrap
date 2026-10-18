#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Bare SHA256 remains supported for callers from batch 1. Source locks retain
# the original sha512= digest from the reviewed reference, without conversion.
gentoo_verify_source() {
    local file=$1 digest=$2 actual algorithm
    case "$digest" in
        sha512=*) algorithm=sha512sum; digest=${digest#sha512=} ;;
        sha256=*) algorithm=sha256sum; digest=${digest#sha256=} ;;
        *) algorithm=sha256sum ;;
    esac
    if [ "$algorithm" = sha512sum ]; then
        [[ "$digest" =~ ^[0-9a-f]{128}$ ]] || return 1
    else
        [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || return 1
    fi
    [ -f "$file" ] || return 1
    actual=$("$algorithm" < "$file" | cut -d' ' -f1)
    [ "$actual" = "$digest" ]
}

gentoo_fetch_source() {
    local url=$1 digest=$2 destination=$3 temporary
    [[ "$url" == https://* ]] || gentoo_die 'source requires HTTPS'
    # Validate the pin before invoking a downloader, including on a cold cache.
    [[ "$digest" =~ ^(sha256=)?[0-9a-f]{64}$ || "$digest" =~ ^sha512=[0-9a-f]{128}$ ]] ||
        gentoo_die 'source requires a pinned SHA256 or SHA512'
    if [ -e "$destination" ]; then
        gentoo_verify_source "$destination" "$digest" || gentoo_die "cached source checksum mismatch: $destination"
        return
    fi
    mkdir -p "$(dirname "$destination")"
    temporary=$(mktemp "${destination}.partial.XXXXXX")
    # The driver lock serializes cache writes. Failed partials are never reused.
    # The completed live-bootstrap host already provides curl. Wget is built
    # later inside the native root, so it cannot be a host-side prerequisite.
    if ! curl --fail --location --retry 3 --output "$temporary" -- "$url"; then
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

gentoo_source_record() {
    # A lock is deliberately scoped to one source. Later source additions do
    # not alter the fingerprints of compilers already built from other locks.
    awk -F '|' '!/^#/ && NF {count++; record=$0; if (NF != 3) bad=1}
        END {if (count != 1 || bad) exit 1; print record}' "$1"
}

gentoo_prepare_source() {
    local lock=$1 record name url digest tree destination temporary
    record=$(gentoo_source_record "$lock") || gentoo_die "invalid source lock: $lock"
    IFS='|' read -r name url digest <<< "$record"
    [[ "$name" =~ ^[A-Za-z0-9.+_-]+\.tar\.(xz|gz|bz2)$ ]] || gentoo_die 'invalid archive name'
    tree=${name%.tar.*}
    destination="$WORK_ROOT/sources/$tree"
    gentoo_fetch_source "$url" "$digest" "$WORK_ROOT/distfiles/$name"
    if [ -d "$destination" ]; then
        [ -f "$destination/.source-pin" ] && [ "$(cat "$destination/.source-pin")" = "$record" ] ||
            gentoo_die "unrecognized source tree: $destination"
        return
    fi
    mkdir -p "$WORK_ROOT/sources"
    temporary=$(mktemp -d "$WORK_ROOT/sources/.unpack.XXXXXX")
    tar -xf "$WORK_ROOT/distfiles/$name" --strip-components=1 -C "$temporary"
    printf '%s\n' "$record" > "$temporary/.source-pin"
    sync
    mv "$temporary" "$destination"
    sync
}
