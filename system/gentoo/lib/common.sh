#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Sourced by Bash entry points with set -Eeuo pipefail.
gentoo_die() { printf 'gentoo: %s\n' "$*" >&2; exit 1; }

gentoo_verify_bundle() {
    local directory=$1
    [ -s "$directory/SHA256SUMS" ] || gentoo_die 'missing input manifest'
    (cd "$directory" && sha256sum -c SHA256SUMS) || gentoo_die 'input verification failed'
}

gentoo_lock() {
    command -v flock >/dev/null || gentoo_die 'util-linux flock is required'
    # Never unlink the file: other processes may already have opened its inode.
    exec 9>"$WORK_ROOT/driver.lock"
    flock -n 9 || gentoo_die 'another continuation process owns this workspace'
}

gentoo_persist_bundle() {
    local source=$1 digest destination temporary
    gentoo_verify_bundle "$source"
    digest=$(sha256sum < "$source/SHA256SUMS" | cut -d' ' -f1)
    destination="$WORK_ROOT/inputs/$digest"
    mkdir -p "$WORK_ROOT/inputs"
    if [ ! -d "$destination" ]; then
        temporary=$(mktemp -d "$WORK_ROOT/inputs/.pending.XXXXXX")
        # Copy only manifest-listed files. The generated list has fixed safe names.
        while read -r checksum name; do
            [[ "$checksum" =~ ^[0-9a-f]{64}$ && "$name" =~ ^[A-Za-z0-9_./-]+$ && "$name" != /* && "$name" != *..* ]] ||
                gentoo_die 'invalid bundle manifest entry'
            mkdir -p "$temporary/$(dirname "$name")"
            cp "$source/$name" "$temporary/$name"
        done < "$source/SHA256SUMS"
        cp "$source/SHA256SUMS" "$temporary/SHA256SUMS"
        gentoo_verify_bundle "$temporary"
        sync
        mv "$temporary" "$destination"
        sync
    fi
    gentoo_verify_bundle "$destination"
    SCRIPT_DIR=$destination
    # Record the selected version atomically, while retaining all older inputs.
    temporary=$(mktemp "$WORK_ROOT/.current.XXXXXX")
    printf '%s\n' "$digest" > "$temporary"
    sync
    mv "$temporary" "$WORK_ROOT/current-inputs"
    sync
}
