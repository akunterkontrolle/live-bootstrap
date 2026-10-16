#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -Eeuo pipefail
umask 077
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
. "$SCRIPT_DIR/lib/common.sh"
case "${1:-build}" in build|status) command=${1:-build} ;; *) gentoo_die 'usage: bash gentoo.sh [build|status]' ;; esac
[ "$#" -le 1 ] || gentoo_die 'unexpected arguments'
[ "$(id -u)" = 0 ] || gentoo_die 'run in the bootstrap guest as root'
gentoo_verify_bundle "$SCRIPT_DIR"
. "$SCRIPT_DIR/config.sh"
. "$SCRIPT_DIR/lib/devices.sh"
gentoo_mount_workspace
# Only write build data after proving the mount. Never format/mount other disks.
WORK_ROOT=/build/gentoo-bootstrap
export WORK_ROOT
mkdir -p "$WORK_ROOT"
gentoo_lock
mkdir -p "$WORK_ROOT/logs" "$WORK_ROOT/state"
if [ "$command" = status ]; then
    find "$WORK_ROOT/state" -maxdepth 1 -name '*.done' -print | LC_ALL=C sort
    exit 0
fi
# Keep errors and progress in an append-only-per-invocation log. A FIFO allows
# waiting for tee so the command does not finish before its log is flushed.
log=$(mktemp "$WORK_ROOT/logs/run-$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")
fifo="$log.pipe"
mkfifo "$fifo"
tee -a "$log" < "$fifo" 9>&- &
logger_pid=$!
exec 3>&1 4>&2
exec > "$fifo" 2>&1
rm "$fifo"
finish() {
    result=$?
    trap - EXIT
    printf 'Continuation exited %s at %s\n' "$result" "$(date -u +%FT%TZ)"
    exec 1>&3 2>&4 3>&- 4>&-
    logger_result=0
    wait "$logger_pid" || logger_result=$?
    if [ "$logger_result" -ne 0 ]; then
        printf 'gentoo: durable logger failed (exit %s)\n' "$logger_result" >&2
        [ "$result" -ne 0 ] || result=$logger_result
    fi
    exit "$result"
}
trap finish EXIT
printf 'Starting Gentoo inputs milestone: mode=%s workspace=%s\n' "$GENTOO_MODE" "$GENTOO_DISCOVERED_WORKSPACE_UUID"
gentoo_persist_bundle "$SCRIPT_DIR"
# Execute all stages from the verified durable snapshot, including first use.
. "$SCRIPT_DIR/config.sh"
. "$SCRIPT_DIR/lib/common.sh"
. "$SCRIPT_DIR/lib/state.sh"
. "$SCRIPT_DIR/lib/sources.sh"
cd "$SCRIPT_DIR"
. "$SCRIPT_DIR/stages/00-inputs.sh"
# Batch 2 adds the next explicit numbered stage here.
printf 'Inputs and logs are durable. Batch 1 stops before building sources.\n'
printf 'Resume: bash %s/gentoo.sh build\n' "$SCRIPT_DIR"
