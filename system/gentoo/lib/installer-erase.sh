#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Fresh-root erasure is authorized only by the installer's exact ERASE prompt.
# Resume never applies this policy after any installer-owned LUKS format intent.
installer_require_unused_root() {
    local children table child number holders swap canonical
    children=$(lsblk -nrpo NAME "$ROOT_REAL") || fail 'cannot inspect root descendants'
    [[ ${children%%$'\n'*} == "$ROOT_REAL" ]] || fail 'invalid root descendant inventory'
    table=$(findmnt -rn -o MAJ:MIN,TARGET) || fail 'cannot inspect mount inventory'
    [[ -n $table ]] || fail 'empty mount inventory'
    for child in $children; do
        [[ -b $child ]] || fail 'root descendant is not a block device'
        number=$(lsblk -dnro MAJ:MIN "$child") || fail 'cannot inspect root descendant identity'
        [[ $number =~ ^[0-9]+:[0-9]+$ ]] || fail 'invalid root descendant identity'
        if printf '%s\n' "$table" | awk -v n="$number" '$1==n {found=1} END {exit !found}'; then
            fail "root target is mounted: $child"
        fi
        # Plan/install/resume may inspect the already recorded open mapper.
        # Every erasure caller omits this argument and rejects all holders.
        if [[ ${1:-} != allow-mappings ]]; then
            [[ -d /sys/class/block/${child##*/}/holders ]] || fail 'root holder inventory unavailable'
            holders=$(find "/sys/class/block/${child##*/}/holders" -mindepth 1 -maxdepth 1 -print) || fail 'cannot inspect root holders'
            [[ -z $holders ]] || fail "root target has active holders: $child"
        fi
        while read -r swap _; do
            [[ $swap == Filename ]] && continue
            canonical=$(readlink -f "$swap") || fail 'cannot resolve active swap'
            [[ $canonical != "$child" ]] || fail "root target is active swap: $child"
        done < /proc/swaps
    done
}

installer_signature_inventory() {
    local children child signatures
    children=$(lsblk -nrpo NAME "$ROOT_REAL") || fail 'cannot enumerate signatures'
    for child in $children; do
        signatures=$(wipefs --no-act --noheadings --output TYPE,OFFSET "$child") || fail 'cannot inspect existing signatures'
        [[ -z $signatures ]] || printf '%s: %s\n' "$child" "$signatures"
    done
}

installer_clear_authorized_partition() {
    local partition_uuid identity signatures marker
    for marker in luks.started luks.done pv.done vg.done storage.done; do
        [[ ! -e $STATE/$marker && ! -L $STATE/$marker ]] || fail 'refusing signature erasure after LUKS/volume preparation started'
    done
    identity=$(sha256 "$STATE/target.identity")
    grep -Fxq 'policy=erase previous signatures before first LUKS format' "$STATE/erase-approved.done" &&
        grep -Fxq "target identity sha256=$identity" "$STATE/erase-approved.done" || fail 'root erase approval differs from target identity'
    partition_uuid=$(blkid -p -s PART_ENTRY_UUID -o value "$ROOT_PART") || fail 'cannot inspect partition UUID before erasure'
    [[ $partition_uuid =~ ^[0-9a-fA-F-]{36}$ ]] || fail 'invalid partition UUID before erasure'
    grep -Fxq "partition UUID=$partition_uuid" "$STATE/partition.done" || fail 'partition differs from its creation receipt'
    if [[ -e $STATE/partition-clear.done ]]; then
        grep -Fxq "partition UUID=$partition_uuid" "$STATE/partition-clear.done" &&
            grep -Fxq "target identity sha256=$identity" "$STATE/partition-clear.done" || fail 'partition-clear receipt differs'
        return
    fi
    installer_require_unused_root
    if [[ -e $STATE/partition-clear.started ]]; then
        grep -Fxq "partition UUID=$partition_uuid" "$STATE/partition-clear.started" &&
            grep -Fxq "target identity sha256=$identity" "$STATE/partition-clear.started" || fail 'partition-clear intent differs'
    else phase partition-clear.started "partition UUID=$partition_uuid" "target identity sha256=$identity"; fi
    signatures=$(wipefs --no-act --noheadings --output TYPE "$ROOT_PART") || fail 'cannot inspect previous partition signatures'
    [[ -z $signatures ]] || printf 'Clearing previous signatures on the confirmed root partition: %s\n' "$signatures"
    mkdir -p "$STATE/signature-backups"; chmod 0700 "$STATE/signature-backups"
    # --force is needed for a nested old partition-table signature. The mounted,
    # holder, swap, physical identity and journal gates above remain mandatory.
    wipefs --all --force --backup="$STATE/signature-backups" "$ROOT_PART"
    signatures=$(wipefs --no-act --noheadings --output TYPE "$ROOT_PART") || fail 'cannot verify signature erasure'
    [[ -z $signatures ]] || fail 'previous partition signatures remain after erasure'
    phase partition-clear.done "partition UUID=$partition_uuid" "target identity sha256=$identity"
}
