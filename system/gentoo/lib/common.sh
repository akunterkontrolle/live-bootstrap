#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Sourced by Bash entry points with set -Eeuo pipefail.
gentoo_die() { printf 'gentoo: %s\n' "$*" >&2; exit 1; }

# live-bootstrap's pinned util-linux 2.19.1 intentionally omits both flock and
# mountpoint. Keep the same kernel-backed lock semantics without requiring the
# flock executable, and provide the small mountpoint query used by our scripts.
if ! command -v mountpoint >/dev/null 2>&1; then
    mountpoint() {
        [ "$#" -eq 2 ] && [ "$1" = -q ] || return 2
        awk -v path="$2" '$2 == path { found=1 } END { exit !found }' /proc/mounts
    }
fi

gentoo_verify_bundle() {
    local directory=$1
    [ -s "$directory/SHA256SUMS" ] || gentoo_die 'missing input manifest'
    (cd "$directory" && sha256sum -c SHA256SUMS) || gentoo_die 'input verification failed'
}

gentoo_lock() {
    # Never unlink the file: other processes may already have opened its inode.
    if command -v flock >/dev/null 2>&1; then
        exec 9>"$WORK_ROOT/driver.lock"
        flock -n 9 || gentoo_die 'another continuation process owns this workspace'
        return
    fi

    # Perl is part of the live-bootstrap tool floor and exposes the same kernel
    # flock(2). A coprocess holds it while this shell (and its children) retains
    # FD 9; process death closes the pipe and releases the lock automatically.
    command -v perl >/dev/null 2>&1 || gentoo_die 'neither flock nor Perl is available for process locking'
    coproc GENTOO_LOCK_HELPER {
        perl -MFcntl=:flock -e '
            open my $lock, ">>", $ARGV[0] or die "open lock: $!";
            unless (flock($lock, LOCK_EX | LOCK_NB)) { print "busy\n"; exit 3; }
            $| = 1;
            print "locked\n";
            while (defined(my $line = <STDIN>)) { }
        ' "$WORK_ROOT/driver.lock"
    }
    local read_fd=${GENTOO_LOCK_HELPER[0]} write_fd=${GENTOO_LOCK_HELPER[1]} result
    if ! IFS= read -r -t 10 result <&"$read_fd" || [ "$result" != locked ]; then
        eval "exec ${read_fd}<&-" 2>/dev/null || true
        eval "exec ${write_fd}>&-" 2>/dev/null || true
        gentoo_die 'another continuation process owns this workspace (or the Perl lock helper failed)'
    fi
    exec 9>&"$write_fd"
    eval "exec ${read_fd}<&-"
    eval "exec ${write_fd}>&-"
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
