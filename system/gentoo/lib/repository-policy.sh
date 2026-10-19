#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
GENTOO_BASE_PROFILE=default/linux/amd64/23.0/split-usr
GENTOO_DESKTOP_PROFILE=$GENTOO_BASE_PROFILE/desktop
gentoo_repository_commit() {
    local record
    record=$(awk -F '|' '!/^#/ && NF {n++; if(NF!=3) bad=1; print $1}
        END {if(n!=1 || bad) exit 1}' "$1") || return 1
    [[ "$record" =~ ^gentoo-([0-9a-f]{40})\.tar\.gz$ ]] || return 1
    printf '%s\n' "${BASH_REMATCH[1]}"
}
