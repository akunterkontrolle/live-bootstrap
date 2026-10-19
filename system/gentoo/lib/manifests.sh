#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
. "$SCRIPT_DIR/lib/repository-policy.sh"
GENTOO_REPO_LOCK=sources/gentoo-repository.lock
GENTOO_REPO_RECORD=$(gentoo_source_record "$GENTOO_REPO_LOCK") || gentoo_die 'invalid Gentoo repository lock'
GENTOO_REPO_ARCHIVE=${GENTOO_REPO_RECORD%%|*}
GENTOO_REPO_COMMIT=$(gentoo_repository_commit "$GENTOO_REPO_LOCK") || gentoo_die 'invalid pinned Gentoo commit'
GENTOO_REPO_TREE="$SOURCES/gentoo-$GENTOO_REPO_COMMIT"
GENTOO_MANIFEST_RECEIPT="$WORK_ROOT/state/gentoo-manifests-$GENTOO_REPO_COMMIT.sha256"
GENTOO_REPOSITORY_RECEIPT="$WORK_ROOT/state/gentoo-tree-$GENTOO_REPO_COMMIT.sha256"
PORTAGE_SOURCE="$SOURCES/portage-3.0.82.2"
HOST_PREFIX="$WORK_ROOT/host-tools"
PORTAGE_CONFIG="$WORK_ROOT/portage-host-config"

portage_manifest_snapshot() {
    local tree=$1 destination=$2
    [ -d "$tree" ] || return 1
    if ! (
        set -o pipefail
        cd "$tree" || exit 1
        find . -type f -name Manifest -print0 |
            LC_ALL=C sort -z | xargs -0 -r sha256sum
    ) > "$destination"; then
        return 1
    fi
    [ -s "$destination" ]
}

portage_manifest_guard_init() {
    local temporary
    if [ -e "$GENTOO_MANIFEST_RECEIPT" ]; then
        portage_manifest_guard_check
        return
    fi
    temporary=$(mktemp "$WORK_ROOT/state/.manifests.XXXXXX")
    portage_manifest_snapshot "$GENTOO_REPO_TREE" "$temporary" || {
        rm -f "$temporary"
        gentoo_die 'pinned Gentoo tree contains no readable Manifests'
    }
    sync
    mv "$temporary" "$GENTOO_MANIFEST_RECEIPT"
    sync
    printf 'Pinned %s Gentoo Manifests in %s\n' \
        "$(wc -l < "$GENTOO_MANIFEST_RECEIPT")" "$GENTOO_MANIFEST_RECEIPT"
}

portage_manifest_guard_check() {
    local current
    [ -s "$GENTOO_MANIFEST_RECEIPT" ] || gentoo_die 'pinned Manifest baseline is missing'
    current=$(mktemp "$WORK_ROOT/state/.manifest-check.XXXXXX")
    if ! portage_manifest_snapshot "$GENTOO_REPO_TREE" "$current"; then
        rm -f "$current"
        return 1
    fi
    if ! cmp -s "$GENTOO_MANIFEST_RECEIPT" "$current"; then
        mv "$current" "$current.changed"
        printf 'Pinned Gentoo Manifests changed; diagnostic: %s\n' "$current.changed" >&2
        return 1
    fi
    rm "$current"
}



# The archive checksum pins the download. This extracted-tree receipt also
# catches local source edits before they can influence Portage dependency data.
portage_repository_snapshot() {
    local tree=$1 destination=$2
    [ -d "$tree" ] || return 1
    if ! (
        set -o pipefail
        cd "$tree" || exit 1
        {
            find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum || exit 1
            find . -type f -printf 'F %m %p\n' | LC_ALL=C sort || exit 1
            find . -type d -printf 'D %m %p\n' | LC_ALL=C sort || exit 1
            find . -type l -printf 'L %m %p -> %l\n' | LC_ALL=C sort || exit 1
        } | sha256sum | cut -d' ' -f1
    ) > "$destination"; then
        return 1
    fi
    [ -s "$destination" ]
}

portage_repository_guard_init() {
    local temporary
    if [ -e "$GENTOO_REPOSITORY_RECEIPT" ]; then
        portage_repository_guard_check
        return
    fi
    temporary=$(mktemp "$WORK_ROOT/state/.gentoo-tree.XXXXXX")
    portage_repository_snapshot "$GENTOO_REPO_TREE" "$temporary" || {
        rm -f "$temporary"
        gentoo_die 'could not fingerprint the pinned Gentoo repository tree'
    }
    sync
    mv "$temporary" "$GENTOO_REPOSITORY_RECEIPT"
    sync
}

portage_repository_guard_check() {
    local current
    [ -s "$GENTOO_REPOSITORY_RECEIPT" ] || gentoo_die 'pinned Gentoo repository receipt is missing'
    current=$(mktemp "$WORK_ROOT/state/.gentoo-tree-check.XXXXXX")
    if ! portage_repository_snapshot "$GENTOO_REPO_TREE" "$current"; then
        rm -f "$current"
        return 1
    fi
    if ! cmp -s "$GENTOO_REPOSITORY_RECEIPT" "$current"; then
        mv "$current" "$current.changed"
        printf 'Pinned Gentoo repository contents changed; diagnostic: %s\n' "$current.changed" >&2
        return 1
    fi
    rm "$current"
}
