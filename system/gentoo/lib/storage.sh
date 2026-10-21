#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Small storage primitives shared by the encrypted-base installer and tests.

gentoo_partition_path() {
    local disk=$1 number=${2:-1}
    [[ "$disk" == /dev/* && "$number" =~ ^[1-9][0-9]*$ ]] || return 2
    case "$disk" in *[0-9]) printf '%sp%s\n' "$disk" "$number" ;; *) printf '%s%s\n' "$disk" "$number" ;; esac
}

gentoo_storage_receipt() {
    local directory=$1 name=$2 temporary
    [[ "$name" =~ ^[a-z][a-z0-9.-]*$ ]] || return 2
    mkdir -p "$directory"
    temporary=$(mktemp "$directory/.$name.XXXXXX") || return
    cat > "$temporary" || { rm -f "$temporary"; return 1; }
    sync "$temporary"
    mv "$temporary" "$directory/$name"
    sync "$directory"
}

gentoo_storage_resume_gate() {
    local state=$1
    [ -s "$state/storage.started" ] && [ -s "$state/target.identity" ] &&
        [ -s "$state/partition.done" ] && [ -s "$state/luks.done" ] || {
        printf 'Storage recovery requires durable start, target, partition, and LUKS receipts; inspect the target before proceeding.\n' >&2
        return 1
    }
}

# A partition checkpoint exists before any LUKS formatting is attempted.
gentoo_storage_partition_gate() {
    local state=$1
    [ -s "$state/storage.started" ] && [ -s "$state/target.identity" ] &&
        [ -s "$state/partition.done" ] || {
        printf 'Storage recovery requires durable start, target, and partition receipts.\n' >&2
        return 1
    }
}
