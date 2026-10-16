#!/bin/sh

# This is sourced by dracut before the real root mount and before the 10-resume
# hook. /home is a separate persistent LV, so rollback root snapshots all read
# the same marker. A mismatch halts in dracut emergency handling before swap
# image restoration or root filesystem writes can occur.
. /lib/dracut-lib.sh

marker_mount=/run/ms1504-hibernate-check
marker_file=$marker_mount/.ms1504-hibernate/hibernation-pending

home_count=$(awk '{for (i=1; i<=NF; i++) if ($i ~ /^rd[.]lvm[.]lv=[A-Za-z0-9+_.-]+\/home$/) n++} END {print n+0}' /proc/cmdline)
swap_count=$(awk '{for (i=1; i<=NF; i++) if ($i ~ /^rd[.]lvm[.]lv=[A-Za-z0-9+_.-]+\/swap$/) n++} END {print n+0}' /proc/cmdline)
home_lv=$(awk '{for (i=1; i<=NF; i++) if ($i ~ /^rd[.]lvm[.]lv=[A-Za-z0-9+_.-]+\/home$/) {sub(/^rd[.]lvm[.]lv=/, "", $i); print $i}}' /proc/cmdline)
swap_lv=$(awk '{for (i=1; i<=NF; i++) if ($i ~ /^rd[.]lvm[.]lv=[A-Za-z0-9+_.-]+\/swap$/) {sub(/^rd[.]lvm[.]lv=/, "", $i); print $i}}' /proc/cmdline)
[ "$home_count" -eq 1 ] && [ "$swap_count" -eq 1 ] ||
    die 'Missing or duplicate home/swap LVM activation arguments; refusing resume.'
marker_device=/dev/$home_lv
swap_device=/dev/$swap_lv

generation_count=$(awk '{for (i=1; i<=NF; i++) if ($i ~ /^ms1504[.]generation=/) n++} END {print n+0}' /proc/cmdline)
boot_generation=$(awk '{for (i=1; i<=NF; i++) if ($i ~ /^ms1504[.]generation=/) print substr($i, length("ms1504.generation=")+1)}' /proc/cmdline)
[ "$generation_count" -eq 1 ] && [ -n "$boot_generation" ] ||
    die 'Missing or duplicate ms1504.generation= boot identity; refusing resume.'
case "$boot_generation" in *[!A-Za-z0-9._-]*)
    die 'Invalid ms1504.generation= boot identity; refusing resume.' ;;
esac

[ -b "$marker_device" ] || die 'Persistent /home LV is unavailable before hibernation-resume validation.'
[ -b "$swap_device" ] || die 'Encrypted swap LV is unavailable before hibernation-resume validation.'
swap_type=$(blkid -s TYPE -o value "$swap_device") || die 'Cannot inspect the encrypted swap signature.'
case "$swap_type" in
    swap)
        # No hibernation image is present. Boot normally without asking the
        # resume hook to probe the swap device.
        rm -f /.resume /lib/dracut/hooks/pre-mount/10-resume.sh
        return 0
        ;;
    suspend|swsuspend|swsupend) ;;
    *) die "Unexpected encrypted swap signature '$swap_type'; refusing resume." ;;
esac

mkdir -p "$marker_mount"
mount -t ext4 -o ro,noload "$marker_device" "$marker_mount" ||
    die 'Cannot read the persistent hibernation-generation marker safely.'

if [ ! -e "$marker_file" ]; then
    umount "$marker_mount" || die 'Cannot unmount the marker filesystem.'
    die 'Untracked hibernation image found in encrypted swap; refusing resume.'
fi
[ -f "$marker_file" ] && [ ! -L "$marker_file" ] || {
    umount "$marker_mount" || :
    die 'Hibernation marker must be a regular, non-symlink file.'
}

saved_generation=$(awk -F= '$1 == "generation" {n++; value=$2} END {if(n != 1) exit 1; print value}' "$marker_file") || {
    umount "$marker_mount" || :
    die 'Hibernation marker has no unique generation; refusing resume.'
}
case "$saved_generation" in *[!A-Za-z0-9._-]*|'')
    umount "$marker_mount" || :
    die 'Hibernation marker generation is malformed; refusing resume.' ;;
esac
umount "$marker_mount" || die 'Cannot unmount the marker filesystem.'

[ "$saved_generation" = "$boot_generation" ] ||
    die "Refusing cross-generation hibernation resume (image=$saved_generation boot=$boot_generation). Select its matching boot entry."
