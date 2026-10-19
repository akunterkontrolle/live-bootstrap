#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Enumerate kernel whole disks, independently of udev's optional by-id links.
gentoo_whole_disks() {
    local node
    for node in /sys/class/block/*; do
        [ ! -f "$node/partition" ] && [ -b "/dev/${node##*/}" ] || continue
        printf '/dev/%s\n' "${node##*/}"
    done
}
gentoo_normalize_wwn() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//;s/^naa\.//'
}
gentoo_by_id_target() {
    if [ -L "$1" ]; then
        readlink -f "$1" || return 2
    elif [ -e "$1" ]; then
        return 2
    else
        return 1
    fi
}
gentoo_hardware_disk_matches() {
    local disk=$1 serial=$2 minimum=$3 maximum=$4 wwn=$5 model=$6 size value
    [ "$(gentoo_disk_serial "$disk")" = "$serial" ] || return 1
    size=$(blockdev --getsize64 "$disk") || return 1
    [[ "$size" =~ ^[0-9]+$ ]] && [ "$size" -ge "$minimum" ] && [ "$size" -le "$maximum" ] || return 1
    if [ -n "$wwn" ]; then
        value=$(gentoo_disk_attribute WWN "$disk") || return 1
        [ "$(gentoo_normalize_wwn "$value")" = "$(gentoo_normalize_wwn "$wwn")" ] || return 1
    fi
    if [ -n "$model" ]; then
        value=$(gentoo_disk_attribute MODEL "$disk") || return 1
        [ "$value" = "$model" ] || return 1
    fi
}
gentoo_resolve_hardware_disk() {
    local by_id=$1 serial=$2 minimum=$3 maximum=$4 wwn=$5 model=$6 disk matched= count=0 linked status
    [[ "$by_id" =~ ^/dev/disk/by-id/[A-Za-z0-9_.:+-]+$ && ! "$by_id" =~ -part[0-9]+$ ]] ||
        gentoo_die 'configured hardware identity must name a whole-disk by-id path'
    [ -n "$serial" ] && [[ "$minimum" =~ ^[1-9][0-9]*$ && "$maximum" =~ ^[1-9][0-9]*$ ]] &&
        [ "$minimum" -le "$maximum" ] || gentoo_die 'hardware serial/capacity constraints are missing'
    while IFS= read -r disk; do
        if gentoo_hardware_disk_matches "$disk" "$serial" "$minimum" "$maximum" "$wwn" "$model"; then
            matched=$disk; count=$((count+1))
        fi
    done < <(gentoo_whole_disks)
    [ "$count" = 1 ] || gentoo_die "hardware identity is missing or ambiguous: $by_id"
    # Absence is permitted; an existing link, dangling link or conflicting
    # path must agree with the uniquely verified kernel disk. Never recreate it.
    if linked=$(gentoo_by_id_target "$by_id"); then
        [ "$linked" = "$matched" ] || gentoo_die 'configured by-id link conflicts with verified disk identity'
    else
        status=$?
        [ "$status" = 1 ] || gentoo_die "broken or unexpected configured by-id entry: $by_id"
    fi
    printf '%s\n' "$matched"
}

# Linux SCSI exports the read-only unit-serial VPD page even when there is no
# udev database and no device/serial text attribute (notably SATA disks).
gentoo_sysfs_vpd_serial() {
    python3 - "$1" <<'VPD_SERIAL'
from pathlib import Path
import sys
try:
    data = Path(sys.argv[1]).read_bytes()
    if len(data) < 5 or data[0] & 0x1f or data[1] != 0x80:
        raise ValueError('not a disk unit-serial VPD page')
    length = int.from_bytes(data[2:4], 'big')
    if length != len(data) - 4 or any(byte < 32 or byte > 126 for byte in data[4:]):
        raise ValueError('invalid unit-serial VPD payload')
    serial = data[4:].decode('ascii').strip()
    if not serial:
        raise ValueError('empty unit serial')
    print(serial)
except (OSError, ValueError):
    sys.exit(1)
VPD_SERIAL
}
