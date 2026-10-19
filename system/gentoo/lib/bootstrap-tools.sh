#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Run native storage inspectors against the host's /dev, /proc and /sys, without
# mounting the native root or copying GNU libraries into the musl bootstrap.
gentoo_native_tool_path() {
    local name=$1 candidate target count
    local -a candidates
    [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]] || return 1
    candidates=("$NATIVE_ROOT/usr/bin/$name" "$NATIVE_ROOT/usr/sbin/$name"
        "$NATIVE_ROOT/bin/$name" "$NATIVE_ROOT/sbin/$name")
    if [ "$name" = ata_id ]; then
        candidates+=("$NATIVE_ROOT/usr/lib/udev/ata_id" "$NATIVE_ROOT/lib/udev/ata_id")
    fi
    for candidate in "${candidates[@]}"; do
        for ((count=0; count<32; count++)); do
            [ -L "$candidate" ] || break
            target=$(readlink "$candidate") || return 1
            case "$target" in /*) candidate="$NATIVE_ROOT$target" ;; *) candidate="$(dirname "$candidate")/$target" ;; esac
        done
        [ "$count" -lt 32 ] || return 1
        candidate=$(readlink -f "$candidate") || continue
        case "$candidate" in "$NATIVE_ROOT/"*) ;; *) return 1 ;; esac
        if [ -f "$candidate" ] && [ -x "$candidate" ]; then printf '%s\n' "$candidate"; return; fi
    done
    return 1
}

gentoo_bootstrap_tool() {
    local binary
    binary=$(gentoo_native_tool_path "$1") || return 1
    shift
    env -u LD_LIBRARY_PATH -u LD_PRELOAD "$NATIVE_ROOT/lib64/ld-linux-x86-64.so.2" \
        --inhibit-cache --library-path "$NATIVE_ROOT/lib64:$NATIVE_ROOT/usr/lib64:$NATIVE_ROOT/usr/lib:$NATIVE_ROOT/usr/lib64/systemd:$NATIVE_ROOT/usr/lib/systemd:$NATIVE_ROOT/lib64/systemd:$NATIVE_ROOT/lib/systemd" \
        "$binary" "$@"
}

gentoo_native_ata_serial() {
    local exported serial
    # ata_id reads ATA IDENTIFY via the existing block device. Run the native
    # helper with its GNU loader; the bootstrap host has neither udev nor its DB.
    exported=$(gentoo_bootstrap_tool ata_id --export "$1") || return 1
    serial=$(printf '%s\n' "$exported" | awk '
        /^ID_SERIAL_SHORT=/ {n++; value=substr($0, 17)}
        END {if(n!=1 || value=="") exit 1; print value}') || return 1
    printf '%s\n' "$serial"
}

gentoo_use_native_storage_tools() {
    local name
    [ -x "$NATIVE_ROOT/lib64/ld-linux-x86-64.so.2" ] || gentoo_die 'native GNU loader is unavailable'
    for name in lsblk findmnt wipefs rsync; do
        gentoo_native_tool_path "$name" >/dev/null || gentoo_die "missing native storage inspector: $name"
    done
    lsblk() { gentoo_bootstrap_tool lsblk "$@"; }
    findmnt() { gentoo_bootstrap_tool findmnt "$@"; }
    wipefs() { gentoo_bootstrap_tool wipefs "$@"; }
    rsync() { gentoo_bootstrap_tool rsync "$@"; }
}
