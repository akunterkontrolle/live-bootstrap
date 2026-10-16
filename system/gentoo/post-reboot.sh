#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -Eeuo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run in the bootstrap guest as root.' >&2; exit 1; }
[ "$(uname -r)" = 6.12.111 ] && [ "$(uname -m)" = x86_64 ] || {
    echo 'Cold-boot the saved x86_64 Linux 6.12.111 kernel before resuming.' >&2; exit 1;
}
mkdir -p /proc /sys /dev/pts /dev/shm
mountpoint -q /proc || mount -t proc proc /proc
mountpoint -q /sys || mount -t sysfs sysfs /sys
mountpoint -q /dev/pts || mount -t devpts devpts /dev/pts
mountpoint -q /dev/shm || mount -t tmpfs tmpfs /dev/shm
# This script is copied into handoff/INPUT_DIGEST, independent of current-inputs.
HERE=$(CDPATH= cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
WORK_ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
inputs=$(basename "$HERE")
[[ "$inputs" =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid durable handoff path.' >&2; exit 1; }
exec bash "$WORK_ROOT/inputs/$inputs/gentoo.sh" build
