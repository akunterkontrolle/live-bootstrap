#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -Eeuo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run in the bootstrap guest as root.' >&2; exit 1; }
[ "$(uname -r)" = 6.12.111 ] && [ "$(uname -m)" = x86_64 ] || {
    echo 'Cold-boot the saved x86_64 Linux 6.12.111 kernel before resuming.' >&2; exit 1;
}
# Resolve and load the durable helper before probing mountpoints. The
# live-bootstrap util-linux may not provide the mountpoint executable; common.sh
# supplies an equivalent /proc/mounts-based shell function in that environment.
HERE=$(CDPATH= cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
WORK_ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
inputs=$(basename "$HERE")
[[ "$inputs" =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid durable handoff path.' >&2; exit 1; }
. "$WORK_ROOT/inputs/$inputs/lib/common.sh"
. "$WORK_ROOT/inputs/$inputs/lib/mounts.sh"
gentoo_prepare_virtual_filesystems
# init=/bin/bash does not run the original bootstrap's get_network step.
# Reuse its source-built DHCP client and installed resolver hooks, waiting for
# an IPv4 lease before continuation stages can attempt verified downloads.
export PATH="${PATH:-/usr/bin:/bin}:/usr/sbin:/sbin"
command -v dhcpcd >/dev/null 2>&1 || gentoo_die 'missing bootstrap prerequisite: dhcpcd'
mkdir -p /var/run/dhcpcd /var/db/dhcpcd
printf 'Restoring bootstrap networking with dhcpcd (IPv4, up to 60 seconds).\n'
dhcpcd --waitip=4 --timeout 60 ||
    gentoo_die 'DHCP did not obtain an IPv4 lease; check the network link and retry this resume script'
# This script is copied into handoff/INPUT_DIGEST, independent of current-inputs.
exec bash "$WORK_ROOT/inputs/$inputs/gentoo.sh" build
