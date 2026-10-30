#!/bin/sh
# Stock dracut resume is omitted: this is the only image-restoration path.
ms1504_cmdline=/proc/cmdline
ms1504_power=/sys/power
ms1504_blocks=/sys/class/block
ms1504_mount=/run/ms1504-hibernate-check
ms1504_ready=/etc/ms1504-hibernate-ready
ms1504_stock_rules=/etc/udev/rules.d/99-resume.rules
ms1504_stock_resume=/lib/dracut/resume.sh
ms1504_stock_parse=/var/lib/dracut/hooks/cmdline/10-parse-resume.sh

ms1504_argument() {
    awk -v key="$1" '{for(i=1;i<=NF;i++) if(index($i,key "=")==1) {n++; v=substr($i,length(key)+2)}} END {if(n!=1 || v=="") exit 1; print v}' "$ms1504_cmdline"
}
ms1504_field() {
    awk -F= -v key="$1" '$1==key {n++;v=$2; if(NF!=2) bad=1} END {if(bad || n!=1 || v=="") exit 1; print v}' "$ms1504_mount/.ms1504-hibernate/hibernation-pending"
}
ms1504_validate_marker() {
    file=$ms1504_mount/.ms1504-hibernate/hibernation-pending
    [ -d "$ms1504_mount/.ms1504-hibernate" ] && [ ! -L "$ms1504_mount/.ms1504-hibernate" ] || return 1
    [ -f "$file" ] && [ ! -L "$file" ] || return 1
    [ "$(stat -c '%u:%a' "$ms1504_mount/.ms1504-hibernate")" = 0:700 ] &&
        [ "$(stat -c '%u:%a' "$file")" = 0:600 ] || return 1
    [ "$(ms1504_field format)" = 1 ] &&
        [ "$(ms1504_field generation)" = "$generation" ] &&
        [ "$(ms1504_field kernel_release)" = "$(uname -r)" ] &&
        [ "$(ms1504_field root_uuid)" = "$expected_root_uuid" ] &&
        [ "$(ms1504_field swap_uuid)" = "$expected_swap_uuid" ] &&
        [ "$(ms1504_field home_uuid)" = "$expected_home_uuid" ]
}
ms1504_resume() {
    [ ! -e "$ms1504_stock_rules" ] &&
        [ ! -e "$ms1504_stock_resume" ] &&
        [ ! -e "$ms1504_stock_parse" ] || return 1
    generation=$(ms1504_argument ms1504.generation) || return 1
    case "$generation" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
    home_lv=$(awk '{for(i=1;i<=NF;i++) if($i ~ /^rd[.]lvm[.]lv=[A-Za-z0-9+_.-]+\/home$/) {n++;v=substr($i,11)}} END {if(n!=1) exit 1; print v}' "$ms1504_cmdline") || return 1
    swap_lv=$(awk '{for(i=1;i<=NF;i++) if($i ~ /^rd[.]lvm[.]lv=[A-Za-z0-9+_.-]+\/swap$/) {n++;v=substr($i,11)}} END {if(n!=1) exit 1; print v}' "$ms1504_cmdline") || return 1
    [ "${home_lv%/*}" = "${swap_lv%/*}" ] || return 1
    home_device=/dev/$home_lv; swap_device=/dev/$swap_lv
    resume=$(ms1504_argument resume) || return 1
    [ "$(readlink -f "$resume")" = "$(readlink -f "$swap_device")" ] || return 1
    [ -b "$home_device" ] && [ -b "$swap_device" ] || return 1
    root_device=$(ms1504_argument root) || return 1
    [ -b "$root_device" ] || return 1
    swap_type=$(blkid -p -s TYPE -o value "$swap_device") || return 1
    disabled=$(awk '{for(i=1;i<=NF;i++) if($i ~ /^(noresume|nohibernate)(=|$)/ || $i ~ /^hibernate=(no|noresume)$/) n=1} END {print n+0}' "$ms1504_cmdline") || return 1
    case "$swap_type" in
        swap)
            # Recovery entries never configure or restore shared hibernation.
            [ "$disabled" = 0 ] || return 0
            [ -f "$ms1504_ready" ] || return 0 ;;
        suspend|swsuspend|swsupend)
            [ "$disabled" = 0 ] || return 1
            expected_root_uuid=$(blkid -p -s UUID -o value "$root_device") || return 1
            expected_swap_uuid=$(blkid -p -s UUID -o value "$swap_device") || return 1
            expected_home_uuid=$(blkid -p -s UUID -o value "$home_device") || return 1
            [ -n "$expected_root_uuid" ] && [ -n "$expected_swap_uuid" ] && [ -n "$expected_home_uuid" ] || return 1
            # Read persistent identity without journal replay or disk writes.
            mkdir -p "$ms1504_mount" || return 1
            mount -t ext4 -o ro,noload "$home_device" "$ms1504_mount" || return 1
            valid=0
            ms1504_validate_marker && valid=1
            umount "$ms1504_mount" || return 1
            [ "$valid" = 1 ] || return 1
            ;;
        *) return 1 ;;
    esac
    real=$(readlink -f "$swap_device") || return 1
    devno=$(cat "$ms1504_blocks/${real##*/}/dev") || return 1
    case "$devno" in ''|*[!0-9:]*|:*|*:) return 1 ;; esac
    # This write probes for an image. Only perform it here before root mounts.
    printf '0\n' > "$ms1504_power/resume_offset" || return 1
    printf '%s\n' "$devno" > "$ms1504_power/resume" || return 1
    [ "$(cat "$ms1504_power/resume")" = "$devno" ] || return 1
    # Successful image restoration never returns into this initramfs.
    [ "$swap_type" = swap ]
}
