#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Device helpers inspect/mount existing filesystems only. No format operations.
. "$(dirname -- "${BASH_SOURCE[0]}")/hardware-discovery.sh"
gentoo_disk_serial() {
    local disk=$1 path sysnode serial tool
    serial=$(lsblk -dnro SERIAL "$disk" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//') || serial=
    if [ -n "$serial" ]; then printf '%s\n' "$serial"; return; fi
    sysnode=$(readlink -f "/sys/class/block/${disk##*/}/device")
    path=$sysnode
    while [[ "$path" == /sys/* ]]; do
        if [ -r "$path/serial" ]; then
            serial=$(sed 's/^[[:space:]]*//;s/[[:space:]]*$//' "$path/serial") || serial=
            if [ -n "$serial" ]; then printf '%s\n' "$serial"; return; fi
        fi
        path=${path%/*}
    done
    if serial=$(gentoo_sysfs_vpd_serial "$sysnode/vpd_pg80"); then
        printf '%s\n' "$serial"; return
    fi
    # Post-Gentoo installation runs from the musl host without a udev DB.
    # The native helper can read an IDE disk's ATA identity directly.
    if declare -F gentoo_native_ata_serial >/dev/null &&
        serial=$(gentoo_native_ata_serial "$disk"); then
        [ -n "$serial" ] && { printf '%s\n' "$serial"; return; }
    fi
    for tool in /usr/lib/udev/ata_id /lib/udev/ata_id; do
        [ -x "$tool" ] || continue
        serial=$("$tool" --export "$disk" | sed -n 's/^ID_SERIAL_SHORT=//p')
        if [ -n "$serial" ]; then printf '%s\n' "$serial"; return; fi
    done
    return 1
}

gentoo_disk_attribute() {
    local field=$1 disk=$2 sysnode value
    # Prefer the kernel's canonical model/WWN representation so discovery is
    # stable before and after udev starts (its model strings can be longer).
    sysnode="/sys/class/block/${disk##*/}"
    case "$field" in
        MODEL) value=$(cat "$sysnode/device/model" 2>/dev/null) || value= ;;
        WWN)
            value=$(cat "$sysnode/wwid" 2>/dev/null) ||
                value=$(cat "$sysnode/device/wwid" 2>/dev/null) || value=
            value=$(printf '%s' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^[Nn][Aa][Aa]\.//;s/^[Ee][Uu][Ii]\.//')
            case "$value" in 0x*|0X*) value=${value:2} ;; esac
            [ -z "$value" ] || value="0x$value"
            ;;
        *) return 1 ;;
    esac
    if [ -z "$value" ] && command -v lsblk >/dev/null 2>&1; then
        value=$(lsblk -dnro "$field" "$disk" 2>/dev/null) || return 1
    fi
    [ -n "$value" ] || return 1
    printf '%s\n' "$value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

gentoo_check_hardware_workspace() {
    local device=$1 parent disk expected size actual field variable
    parent=$(readlink -f "/sys/class/block/${device##*/}")
    [ ! -f "$parent/partition" ] || parent=${parent%/*}
    disk="/dev/${parent##*/}"
    expected=$(gentoo_resolve_hardware_disk "$GENTOO_HARDWARE_DISKS_EXTRA_BY_ID" \
        "$GENTOO_HARDWARE_DISKS_EXTRA_SERIAL" "$GENTOO_HARDWARE_DISKS_EXTRA_MIN_BYTES" \
        "$GENTOO_HARDWARE_DISKS_EXTRA_MAX_BYTES" "$GENTOO_HARDWARE_DISKS_EXTRA_WWN" \
        "$GENTOO_HARDWARE_DISKS_EXTRA_MODEL")
    [ -b "$expected" ] && [ "$disk" = "$expected" ] || gentoo_die 'workspace is not on the configured extra disk'
    [ "$(gentoo_disk_serial "$disk")" = "$GENTOO_HARDWARE_DISKS_EXTRA_SERIAL" ] || gentoo_die 'workspace disk serial mismatch'
    size=$(blockdev --getsize64 "$disk")
    [ "$size" -ge "$GENTOO_HARDWARE_DISKS_EXTRA_MIN_BYTES" ] &&
        [ "$size" -le "$GENTOO_HARDWARE_DISKS_EXTRA_MAX_BYTES" ] || gentoo_die 'workspace disk capacity mismatch'
    for field in MODEL WWN; do
        variable=GENTOO_HARDWARE_DISKS_EXTRA_$field
        expected=${!variable}
        [ -n "$expected" ] || continue
        actual=$(gentoo_disk_attribute "$field" "$disk") || gentoo_die "cannot read workspace disk $field"
        if [ "$field" = WWN ]; then
            actual=$(printf '%s' "$actual" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//')
            expected=$(printf '%s' "$expected" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//')
        fi
        [ "$actual" = "$expected" ] || gentoo_die "workspace disk $field mismatch"
    done
}

gentoo_mount_workspace() {
    local selector device mounted candidates
    selector="LABEL=$GENTOO_WORKSPACE_LABEL"
    [ -z "$GENTOO_WORKSPACE_UUID" ] || selector="UUID=$GENTOO_WORKSPACE_UUID"
    # blkid -c /dev/null avoids a stale on-disk cache. Never guess /dev/sdX.
    candidates=$(blkid -c /dev/null -t "$selector" -o device) || gentoo_die 'workspace filesystem not found'
    [ "$(printf '%s\n' "$candidates" | wc -l)" = 1 ] || gentoo_die 'workspace identity is ambiguous'
    device=$(readlink -f "$candidates")
    [ -b "$device" ] || gentoo_die 'workspace is not a block device'
    [ "$(blkid -s TYPE -o value "$device")" = ext4 ] || gentoo_die 'workspace must already be ext4'
    [ "$(blkid -s LABEL -o value "$device")" = "$GENTOO_WORKSPACE_LABEL" ] || gentoo_die 'workspace label mismatch'
    if [ "$GENTOO_MODE" = hardware ]; then gentoo_check_hardware_workspace "$device"; fi
    mkdir -p /build
    mounted=$(awk '$2=="/build" {print $1}' /proc/mounts)
    if [ -n "$mounted" ]; then
        [ "$(readlink -f "$mounted")" = "$device" ] || gentoo_die '/build is mounted from another device'
    else
        mount -t ext4 -o rw "$device" /build
    fi
    awk '$2=="/build" && $3=="ext4" && $4 ~ /(^|,)rw(,|$)/ {found=1} END {exit !found}' /proc/mounts ||
        gentoo_die 'workspace mount must be writable ext4'
    export GENTOO_WORKSPACE_DEVICE=$device
    export GENTOO_DISCOVERED_WORKSPACE_UUID=$(blkid -s UUID -o value "$device")
    [ -n "$GENTOO_DISCOVERED_WORKSPACE_UUID" ] || gentoo_die 'workspace has no UUID'
}
