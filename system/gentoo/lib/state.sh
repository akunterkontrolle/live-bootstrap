#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Call from SCRIPT_DIR with explicit relative recipe/source/config input names.
# Include prerequisite receipts, not whole trees, and omit concurrency config.
gentoo_fingerprint() {
    local input
    for input in "$@"; do
        [ -f "$input" ] || return 1
    done
    for input in "$@"; do
        printf '%s\n' "$input"
        sha256sum < "$input" || return 1
    done | sha256sum | cut -d' ' -f1
}

gentoo_run_stage() {
    local stage=$1 fingerprint=$2 action=$3 verify=$4 receipt temporary
    [[ "$stage" =~ ^[A-Za-z0-9_-]+$ && "$fingerprint" =~ ^[0-9a-f]{64}$ ]] || gentoo_die 'invalid stage identity'
    receipt="$WORK_ROOT/state/$stage.done"
    if [ -f "$receipt" ]; then
        [ "$(cat "$receipt")" = "$fingerprint" ] || gentoo_die "$stage inputs changed; inspect outputs and use a fresh build lineage"
        "$verify" || gentoo_die "$stage completed outputs failed verification; inspect before repair"
        printf 'Verified completed stage %s\n' "$stage"
        return
    fi
    printf 'Starting stage %s at %s\n' "$stage" "$(date -u +%FT%TZ)"
    # A subshell retains errexit for sequential commands; a failed action cannot
    # fall through to a receipt. Do not invoke run_stage in an if/|| condition.
    (set -Eeuo pipefail; "$action")
    "$verify" || gentoo_die "$stage output verification failed"
    temporary=$(mktemp "$WORK_ROOT/state/.$stage.XXXXXX")
    printf '%s\n' "$fingerprint" > "$temporary"
    sync
    mv "$temporary" "$receipt"
    sync
    printf 'Completed stage %s\n' "$stage"
}
