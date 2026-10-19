#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Bootstrap runs without an init service manager. Stop only the DHCP daemon
# holding its pidfile on root; otherwise ext4 correctly refuses a read-only
# remount. Do not invoke dhcpcd's control client, which can wait for IPC.
gentoo_dhcpcd_holds_root_pidfile() {
    local process=$1 root_device=$2 fd target device
    for fd in "$process"/fd/*; do
        target=$(readlink "$fd" 2>/dev/null) || continue
        case "$target" in /var/run/dhcpcd/pid|/run/dhcpcd/pid) ;; *) continue ;; esac
        device=$(stat -Lc %d "$fd" 2>/dev/null) || continue
        [ "$device" != "$root_device" ] || return 0
    done
    return 1
}

gentoo_stop_bootstrap_dhcpcd() {
    local process name pid started current root_device attempt
    root_device=$(stat -Lc %d /) || return 1
    for process in /proc/[0-9]*; do
        IFS= read -r name < "$process/comm" 2>/dev/null || continue
        [ "$name" = dhcpcd ] || continue
        gentoo_dhcpcd_holds_root_pidfile "$process" "$root_device" || continue
        pid=${process##*/}
        [ "$pid" -gt 1 ] || { printf 'Refusing to signal PID 1.\n' >&2; return 1; }
        started=$(awk '{print $22}' "$process/stat") || return 1
        [ -n "$started" ] || return 1
        # Recheck identity and the pidfile immediately before sending SIGTERM.
        IFS= read -r name < "$process/comm" || return 1
        current=$(awk '{print $22}' "$process/stat") || return 1
        [ "$name" = dhcpcd ] && [ "$started" = "$current" ] || return 1
        gentoo_dhcpcd_holds_root_pidfile "$process" "$root_device" || continue
        printf 'Stopping bootstrap dhcpcd PID %s before the root remount.\n' "$pid"
        kill -TERM "$pid" || return 1
        for ((attempt=0; attempt<100; attempt++)); do
            if [ ! -r "$process/stat" ]; then break; fi
            current=$(awk '{print $22}' "$process/stat" 2>/dev/null) || break
            [ "$current" = "$started" ] || break
            gentoo_dhcpcd_holds_root_pidfile "$process" "$root_device" || break
            sleep 0.1
        done
        if [ -r "$process/stat" ]; then
            current=$(awk '{print $22}' "$process/stat" 2>/dev/null) || current=
            if [ "$current" = "$started" ] && gentoo_dhcpcd_holds_root_pidfile "$process" "$root_device"; then
                printf 'dhcpcd still holds a writable root pidfile after 10 seconds; shutdown stopped.\n' >&2
                return 1
            fi
        fi
    done
}
