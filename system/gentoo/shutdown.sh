#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Explicit operator command only. Never call this from a build or error trap.
set -Eeuo pipefail
[ "$#" = 0 ] && [ "$(id -u)" = 0 ] || { echo 'Usage: bash shutdown.sh (guest root only)' >&2; exit 1; }
# Resolve the pinned helper before relocating this script away from /build.
if [ "$(readlink -f "$0")" != /run/gentoo-clean-shutdown.sh ]; then
    here=$(CDPATH= cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
    input_digest=$(basename "$here")
    work_root=$(CDPATH= cd -- "$here/../.." && pwd)
    export GENTOO_LOCK_COMMON="$work_root/inputs/$input_digest/lib/common.sh"
fi
: "${GENTOO_LOCK_COMMON:?cannot locate the durable lock helper}"
. "$GENTOO_LOCK_COMMON"
# Bash keeps its script open; relocate off /build before trying to unmount it.
if [ "$(readlink -f "$0")" != /run/gentoo-clean-shutdown.sh ]; then
    mkdir -p /run
    temporary=$(mktemp /run/.gentoo-shutdown.XXXXXX)
    cp "$(readlink -f "$0")" "$temporary"
    chmod 0700 "$temporary"
    mv "$temporary" /run/gentoo-clean-shutdown.sh
    cd /
    exec bash /run/gentoo-clean-shutdown.sh
fi
[ -w /proc/sysrq-trigger ] || { echo 'Kernel SysRq poweroff is unavailable.' >&2; exit 1; }
mkdir /run/gentoo-shutdown-in-progress || { echo 'Shutdown is already in progress.' >&2; exit 1; }
trap 'rmdir /run/gentoo-shutdown-in-progress 2>/dev/null || true' EXIT
# The sentinel blocks new drivers; the lock rejects an existing builder.
WORK_ROOT=/build/gentoo-bootstrap
export WORK_ROOT
gentoo_lock
sync
exec 9>&-
while IFS= read -r point; do umount "$point"; done < <(
    awk '$2!="/" && $3 ~ /^ext[234]$/ {print $2}' /proc/mounts | LC_ALL=C sort -r)
sync
mount -o remount,ro /
# No writable disk filesystem may remain. Virtual filesystems can stay mounted.
awk '$3 ~ /^(ext[234]|xfs|btrfs|vfat)$/ && $4 ~ /(^|,)rw(,|$)/ {bad=1} END {exit bad}' /proc/mounts
printf 'Filesystems clean; powering off.\n'
printf o > /proc/sysrq-trigger
# Reaching here means poweroff was not accepted. Preserve the sentinel and fail.
trap - EXIT
echo 'Poweroff did not complete; inspect the VM/hardware console.' >&2
exit 1
