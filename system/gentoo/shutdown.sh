#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Explicit operator command only. Never call this from a build or error trap.
set -Eeuo pipefail
[ "$#" = 0 ] && [ "$(id -u)" = 0 ] || { echo 'Usage: bash shutdown.sh (guest root only)' >&2; exit 1; }
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
exec 9>/build/gentoo-bootstrap/driver.lock
flock -n 9 || { echo 'A build still owns the workspace; stop it before shutdown.' >&2; exit 1; }
sync
flock -u 9
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
