#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -Eeuo pipefail
umask 077
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
# init=/bin/bash may have serial file descriptors but no controlling terminal.
# Run the driver in a separate foreground session so Ctrl+C and /dev/tty work.
if [ "${GENTOO_CONSOLE_SESSION:-0}" != 1 ]; then
    if [ "$$" -eq 1 ] || { [ -t 0 ] && ! ( : < /dev/tty ) 2>/dev/null; }; then
        exec python3 "$SCRIPT_DIR/console-session.py" bash "$SCRIPT_DIR/gentoo.sh" "$@"
    fi
fi
. "$SCRIPT_DIR/lib/common.sh"
case "${1:-build}" in
    build|status) command=${1:-build} ;;
    plan) command=install-plan ;;
    storage-init|storage-resume|install-root) command=$1 ;;
    *) gentoo_die 'usage: bash gentoo.sh [build|status|plan|storage-init|storage-resume|install-root]' ;;
esac
[ "$#" -le 1 ] || gentoo_die 'unexpected arguments'
[ "$(id -u)" = 0 ] || gentoo_die 'run in the bootstrap guest as root'
# Early util-linux reads fstab even for explicit mount operations. Preserve
# existing policy; the bootstrap root itself has no fstab entries to install.
if [ ! -e /etc/fstab ] && [ ! -L /etc/fstab ]; then
    printf '# Bootstrap mounts are managed explicitly by the continuation.\n' > /etc/fstab
    chmod 0644 /etc/fstab
fi
[ ! -d /run/gentoo-shutdown-in-progress ] || gentoo_die 'shutdown is in progress'
gentoo_verify_bundle "$SCRIPT_DIR"
. "$SCRIPT_DIR/lib/mounts.sh"
gentoo_prepare_virtual_filesystems
. "$SCRIPT_DIR/config.sh"
. "$SCRIPT_DIR/lib/devices.sh"
gentoo_mount_workspace
# Only write build data after proving the mount. Never format/mount other disks.
WORK_ROOT=/build/gentoo-bootstrap
export WORK_ROOT
mkdir -p "$WORK_ROOT"
gentoo_lock
[ ! -d /run/gentoo-shutdown-in-progress ] || gentoo_die 'shutdown is in progress'
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
# Run stages in a child shell. If a stage exits inside a redirected function
# or block, Bash retains saved copies of the log pipe until that shell exits.
# The parent must wait for that shell to close its descriptors before asking
# tee to finish; otherwise the EXIT trap can deadlock waiting for pipe EOF.
(
trap - EXIT
printf 'Starting Gentoo inputs milestone: mode=%s workspace=%s\n' "$GENTOO_MODE" "$GENTOO_DISCOVERED_WORKSPACE_UUID"
gentoo_persist_bundle "$SCRIPT_DIR"
# Execute all stages from the verified durable snapshot, including first use.
. "$SCRIPT_DIR/config.sh"
. "$SCRIPT_DIR/lib/common.sh"
. "$SCRIPT_DIR/lib/state.sh"
. "$SCRIPT_DIR/lib/sources.sh"
cd "$SCRIPT_DIR"
. "$SCRIPT_DIR/stages/00-inputs.sh"
if [ "$command" != build ]; then
    GENTOO_DRIVER_LOCK_HELD=1
    export GENTOO_DRIVER_LOCK_HELD
    case "$command" in
        install-plan) installer_mode=--plan ;;
        storage-init) installer_mode=--initialize-storage ;;
        storage-resume) installer_mode=--resume-storage ;;
        install-root) installer_mode=--install-root ;;
        *) gentoo_die 'invalid installer command' ;;
    esac
    bash "$SCRIPT_DIR/install-base.sh" "$installer_mode"
    exit 0
fi
# Runtime directories and installed public files must be readable by Portage
# after it drops privileges. Workspace inputs and logs above stay private.
umask 022
. "$SCRIPT_DIR/lib/toolchain.sh"
. "$SCRIPT_DIR/stages/10-host-preflight.sh"
. "$SCRIPT_DIR/stages/11-binutils-elf.sh"
. "$SCRIPT_DIR/stages/12-gcc-elf.sh"
. "$SCRIPT_DIR/stages/13-host-tools.sh"
. "$SCRIPT_DIR/stages/14-linux-headers.sh"
. "$SCRIPT_DIR/stages/15-binutils-linux.sh"
. "$SCRIPT_DIR/stages/16-gcc-linux-stage1.sh"
. "$SCRIPT_DIR/stages/17-glibc-bootstrap.sh"
. "$SCRIPT_DIR/stages/18-gcc-linux-runtime.sh"
. "$SCRIPT_DIR/stages/19-glibc.sh"
. "$SCRIPT_DIR/stages/20-verify-sysroot.sh"
. "$SCRIPT_DIR/stages/30-kernel.sh"
. "$SCRIPT_DIR/stages/31-kernel-modules.sh"
. "$SCRIPT_DIR/stages/32-handoff.sh"
if [ "$GENTOO_MODERN_KERNEL_READY" = 1 ]; then
    . "$SCRIPT_DIR/lib/cxx.sh"
    . "$SCRIPT_DIR/lib/runtime.sh"
    . "$SCRIPT_DIR/stages/40-gcc-cxx.sh"
    . "$SCRIPT_DIR/stages/41-cxx-libgcc.sh"
    . "$SCRIPT_DIR/stages/42-libstdcxx.sh"
    . "$SCRIPT_DIR/stages/43-runtime-acceptance.sh"
    . "$SCRIPT_DIR/lib/portage.sh"
    . "$SCRIPT_DIR/stages/50-gentoo-sources.sh"
    . "$SCRIPT_DIR/lib/python.sh"
    . "$SCRIPT_DIR/stages/51-host-python.sh"
    . "$SCRIPT_DIR/stages/52-portage-host.sh"
    . "$SCRIPT_DIR/lib/native.sh"
    . "$SCRIPT_DIR/lib/native-resume.sh"
    if gentoo_native_resume_handoff; then
        : # Stage 80 now owns verification of the changing native root.
    else
        . "$SCRIPT_DIR/stages/60-native-root.sh"
        . "$SCRIPT_DIR/stages/61-native-shell-tools.sh"
        . "$SCRIPT_DIR/stages/62-native-compiler.sh"
        . "$SCRIPT_DIR/lib/native-tools.sh"
        . "$SCRIPT_DIR/stages/63-native-tool-floor.sh"
        . "$SCRIPT_DIR/stages/70-native-runtimes.sh"
        . "$SCRIPT_DIR/stages/71-native-portage.sh"
    fi
    # This account directory stays private despite the public build umask.
    chmod 0700 "$NATIVE_ROOT/root"
    python3 "$SCRIPT_DIR/probes/native-permissions.py" --root "$NATIVE_ROOT"
    . "$SCRIPT_DIR/stages/80-native-gentoo-system.sh"
    . "$SCRIPT_DIR/stages/90-native-base-runtime.sh"
fi
printf 'Resume: bash %s/gentoo.sh build\n' "$SCRIPT_DIR"
)
