#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Formatting and inspection must use the same generation of filesystem tools.
gentoo_use_native_filesystem_tools() {
    local name
    for name in blkid dumpe2fs; do
        gentoo_native_tool_path "$name" >/dev/null || fail "missing native filesystem inspector: $name"
    done
    blkid() { gentoo_bootstrap_tool blkid "$@"; }
    dumpe2fs() { gentoo_bootstrap_tool dumpe2fs "$@"; }
}

clean_ext4() {
    local type header
    type=$(blkid -p -s TYPE -o value "$1") || return 1
    [ "$type" = ext4 ] || return 1
    header=$(dumpe2fs -h "$1" 2>/dev/null) || return 1
    # Consume the complete captured header; grep -q on a live producer can
    # turn a successful check into SIGPIPE when pipefail is enabled.
    printf '%s\n' "$header" | grep -E '^Filesystem state:[[:space:]]+clean$' >/dev/null
}

probe_filesystem_type() {
    local status=0
    current=$(blkid -p -s TYPE -o value "$1") || status=$?
    case "$status" in
        0) [ -n "$current" ] || fail 'filesystem probe returned an empty successful result' ;;
        2) [ -z "$current" ] || fail 'filesystem probe returned inconsistent absence' ;;
        *) fail "filesystem probe failed with status $status; refusing to infer an empty LV" ;;
    esac
}

begin_filesystem_format() {
    local fs=$1 device=$2 uuid bytes approval
    [ ! -e "$STATE/fs-$fs.started" ] || fail "$fs format was interrupted; inspect before retrying"
    uuid=$(lvm_run lvs --noheadings -o lv_uuid "$VG/$fs" | tr -d '[:space:]')
    bytes=$(lvm_run lvs --noheadings --units b --nosuffix -o lv_size "$VG/$fs" | tr -d '[:space:]')
    [ -n "$uuid" ] && [[ "$bytes" =~ ^[0-9]+$ ]] || fail 'cannot establish filesystem LV identity'
    if [ "$MODE" != --initialize-storage ] &&
        ! grep -Fxq 'filesystem preparation=not started' "$STATE/lv-$fs.done"; then
        approval="$STATE/fs-$fs.first-format-approved"
        [ -f "$approval" ] && [ ! -L "$approval" ] &&
            grep -Fxq 'policy=verified first format after legacy root-verifier failure' "$approval" &&
            grep -Fxq "target identity sha256=$(sha256 "$STATE/target.identity")" "$approval" &&
            grep -Fxq "LUKS UUID=$luks_uuid" "$approval" &&
            grep -Fxq "LV=$VG/$fs" "$approval" &&
            grep -Fxq "LV UUID=$uuid" "$approval" && grep -Fxq "LV bytes=$bytes" "$approval" &&
            grep -Fxq "verified root UUID=$(blkid -p -s UUID -o value "/dev/$VG/root")" "$approval" ||
            fail "$fs has no verified first-format intent; inspect legacy storage state"
    fi
    phase "fs-$fs.started" "LV=$VG/$fs" "LV UUID=$uuid" "LV bytes=$bytes" "device=$device"
}
