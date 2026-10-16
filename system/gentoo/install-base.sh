#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -Eeuo pipefail
umask 077
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
. "$SCRIPT_DIR/lib/common.sh"
. "$SCRIPT_DIR/config.sh"
. "$SCRIPT_DIR/lib/state.sh"
. "$SCRIPT_DIR/lib/devices.sh"
. "$SCRIPT_DIR/lib/storage.sh"
MODE=--plan; [ "$#" -eq 0 ] || MODE=$1
case "$MODE" in --plan|--initialize-storage|--resume-storage|--install-root) ;; *) gentoo_die 'usage: install-base.sh [--plan|--initialize-storage|--resume-storage|--install-root]' ;; esac
[ "$#" -le 1 ] || gentoo_die 'pass one installer mode'
[ "$(id -u)" = 0 ] || gentoo_die 'run as root'
case "$-" in *x*) gentoo_die 'disable shell tracing before using the installer' ;; esac
gentoo_mount_workspace
WORK_ROOT=/build/gentoo-bootstrap
NATIVE_ROOT="$WORK_ROOT/native-root-x86_64"
STATE="$WORK_ROOT/install-base-state"
VG=$GENTOO_SYSTEM_VOLUME_GROUP
ROOT_GIB=$GENTOO_LAYOUT_ROOT_GIB; SWAP_GIB=$GENTOO_LAYOUT_SWAP_GIB
RESERVE_GIB=$GENTOO_LAYOUT_RESERVE_GIB; HOME_MIN_GIB=$GENTOO_LAYOUT_HOME_MIN_GIB
RELEASE=6.12.111; TARGET_ROOT=/mnt/gentoo-install
BASE_GENERATION="base-$RELEASE"
mkdir -p "$WORK_ROOT"
if declare -p GENTOO_DRIVER_LOCK_HELD >/dev/null 2>&1 && [ "$GENTOO_DRIVER_LOCK_HELD" = 1 ]; then :; else gentoo_lock; fi
fail() { gentoo_die "install-base: $*"; }
sha256() { sha256sum < "$1" | awk '{print $1}'; }
tree_sha256() {
    ( cd "$1"; { find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum; \
        LC_ALL=C find . -type f -printf 'F %m %p\n' | LC_ALL=C sort; \
        LC_ALL=C find . -type d -printf 'D %m %p\n' | LC_ALL=C sort; \
        LC_ALL=C find . -type l -printf 'L %m %p -> %l\n' | LC_ALL=C sort; } | sha256sum | awk '{print $1}');
}
read_secret() { local prompt=$1 variable=$2; printf '%s' "$prompt" > /dev/tty; IFS= read -r -s "$variable" < /dev/tty; printf '\n' > /dev/tty; }
read_tty_line() { local prompt=$1 variable=$2; printf '%s' "$prompt" > /dev/tty; IFS= read -r "$variable" < /dev/tty; }
vm_identity() {
    local token value
    VM_ROOT_SERIAL= VM_ROOT_BYTES= VM_BOOT_SERIAL= VM_BOOT_BYTES= VM_EXTRA_SERIAL= VM_EXTRA_BYTES=
    for token in $(cat /proc/cmdline); do
        case "$token" in
            gentoo_root_serial=*) VM_ROOT_SERIAL=$(printf '%s' "$token" | cut -d= -f2-) ;;
            gentoo_root_bytes=*) VM_ROOT_BYTES=$(printf '%s' "$token" | cut -d= -f2-) ;;
            gentoo_boot_serial=*) VM_BOOT_SERIAL=$(printf '%s' "$token" | cut -d= -f2-) ;;
            gentoo_boot_bytes=*) VM_BOOT_BYTES=$(printf '%s' "$token" | cut -d= -f2-) ;;
            gentoo_extra_serial=*) VM_EXTRA_SERIAL=$(printf '%s' "$token" | cut -d= -f2-) ;;
            gentoo_extra_bytes=*) VM_EXTRA_BYTES=$(printf '%s' "$token" | cut -d= -f2-) ;;
        esac
    done
    for value in "$VM_ROOT_SERIAL" "$VM_BOOT_SERIAL" "$VM_EXTRA_SERIAL"; do [[ "$value" =~ ^[A-Za-z0-9]{1,20}$ ]] || fail 'VM launcher disk identity missing/unsafe.'; done
    for value in "$VM_ROOT_BYTES" "$VM_BOOT_BYTES" "$VM_EXTRA_BYTES"; do [[ "$value" =~ ^[1-9][0-9]*$ ]] || fail 'VM launcher capacity missing.'; done
    ROOT_SERIAL=$VM_ROOT_SERIAL; ROOT_MIN=$VM_ROOT_BYTES; ROOT_MAX=$VM_ROOT_BYTES
    BOOT_SERIAL=$VM_BOOT_SERIAL; BOOT_MIN=$VM_BOOT_BYTES; BOOT_MAX=$VM_BOOT_BYTES
    EXTRA_SERIAL=$VM_EXTRA_SERIAL; EXTRA_MIN=$VM_EXTRA_BYTES; EXTRA_MAX=$VM_EXTRA_BYTES
}
find_serial_disk() {
    local wanted=$1 path type serial count=0
    while read -r path type; do
        [ "$type" = disk ] || continue
        serial=$(gentoo_disk_serial "$path" 2>/dev/null || true)
        [ "$serial" = "$wanted" ] || continue
        FOUND_DISK=$path; count=$((count + 1))
    done < <(lsblk -dnpo NAME,TYPE)
    [ "$count" -eq 1 ] || fail "expected exactly one disk with serial $wanted."
}
check_disk() {
    local path=$1 expected=$2 minimum=$3 maximum=$4 expected_wwn=$5 expected_model=$6
    local real serial bytes value
    [ -b "$path" ] && [ "$(lsblk -dnro TYPE "$path" | tr -d '[:space:]')" = disk ] || fail "not a whole disk: $path"
    real=$(readlink -f "$path"); serial=$(gentoo_disk_serial "$real"); bytes=$(lsblk -bndro SIZE "$real" | tr -d '[:space:]')
    [ "$serial" = "$expected" ] && [ "$bytes" -ge "$minimum" ] && [ "$bytes" -le "$maximum" ] || fail "serial/capacity mismatch: $path"
    if [ -n "$expected_wwn" ]; then value=$(lsblk -dnro WWN "$real" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//'); [ "$value" = "$(printf '%s' "$expected_wwn" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//')" ] || fail 'disk WWN mismatch.'; fi
    if [ -n "$expected_model" ]; then value=$(lsblk -dnro MODEL "$real" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'); [ "$value" = "$expected_model" ] || fail 'disk model mismatch.'; fi
    printf '%s\n' "$real"
}
resolve_disks() {
    local source real item child
    if [ "$GENTOO_MODE" = vm ]; then
        vm_identity
        find_serial_disk "$ROOT_SERIAL"; ROOT_DISK=$FOUND_DISK
        find_serial_disk "$BOOT_SERIAL"; BOOT_DISK=$FOUND_DISK
        find_serial_disk "$EXTRA_SERIAL"; EXTRA_DISK=$FOUND_DISK
    else
        ROOT_DISK=$GENTOO_HARDWARE_DISKS_ROOT_BY_ID; BOOT_DISK=$GENTOO_HARDWARE_DISKS_BOOT_BY_ID; EXTRA_DISK=$GENTOO_HARDWARE_DISKS_EXTRA_BY_ID
        ROOT_SERIAL=$GENTOO_HARDWARE_DISKS_ROOT_SERIAL; BOOT_SERIAL=$GENTOO_HARDWARE_DISKS_BOOT_SERIAL; EXTRA_SERIAL=$GENTOO_HARDWARE_DISKS_EXTRA_SERIAL
        ROOT_BY_ID=$GENTOO_HARDWARE_DISKS_ROOT_BY_ID
        ROOT_MIN=$GENTOO_HARDWARE_DISKS_ROOT_MIN_BYTES; ROOT_MAX=$GENTOO_HARDWARE_DISKS_ROOT_MAX_BYTES
        BOOT_MIN=$GENTOO_HARDWARE_DISKS_BOOT_MIN_BYTES; BOOT_MAX=$GENTOO_HARDWARE_DISKS_BOOT_MAX_BYTES
        EXTRA_MIN=$GENTOO_HARDWARE_DISKS_EXTRA_MIN_BYTES; EXTRA_MAX=$GENTOO_HARDWARE_DISKS_EXTRA_MAX_BYTES
    fi
    ROOT_REAL=$(check_disk "$ROOT_DISK" "$ROOT_SERIAL" "$ROOT_MIN" "$ROOT_MAX" "$GENTOO_HARDWARE_DISKS_ROOT_WWN" "$GENTOO_HARDWARE_DISKS_ROOT_MODEL")
    BOOT_REAL=$(check_disk "$BOOT_DISK" "$BOOT_SERIAL" "$BOOT_MIN" "$BOOT_MAX" "" "")
    EXTRA_REAL=$(check_disk "$EXTRA_DISK" "$EXTRA_SERIAL" "$EXTRA_MIN" "$EXTRA_MAX" "$GENTOO_HARDWARE_DISKS_EXTRA_WWN" "$GENTOO_HARDWARE_DISKS_EXTRA_MODEL")
    EXTRA_BYTES=$(lsblk -bndro SIZE "$EXTRA_REAL" | tr -d '[:space:]')
    EXTRA_WWN_OBSERVED=$(lsblk -dnro WWN "$EXTRA_REAL" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//')
    EXTRA_MODEL_OBSERVED=$(lsblk -dnro MODEL "$EXTRA_REAL" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    if [ "$GENTOO_MODE" = hardware ]; then EXTRA_BY_ID=$GENTOO_HARDWARE_DISKS_EXTRA_BY_ID; else EXTRA_BY_ID=; fi
    if [ "$GENTOO_MODE" = hardware ]; then ROOT_BY_ID=$GENTOO_HARDWARE_DISKS_ROOT_BY_ID; else ROOT_BY_ID=; fi
    [ "$ROOT_REAL" != "$BOOT_REAL" ] && [ "$ROOT_REAL" != "$EXTRA_REAL" ] && [ "$BOOT_REAL" != "$EXTRA_REAL" ] || fail 'root, boot and extra disks must differ.'
    source=$(findmnt -rn -T /build -o SOURCE); real=$(readlink -f "$source"); [ -b "$real" ] || fail 'cannot identify workspace source.'
    while IFS= read -r child; do [ -z "$child" ] || [ "$(readlink -f "$child")" != "$ROOT_REAL" ] || fail 'root target backs workspace.'; done < <(lsblk -s -nrpo NAME "$real")
    ROOT_PART=$(gentoo_partition_path "$ROOT_REAL" 1); ROOT_BYTES=$(lsblk -bndro SIZE "$ROOT_REAL" | tr -d '[:space:]')
    ROOT_MAJMIN=$(lsblk -dnro MAJ:MIN "$ROOT_REAL" | tr -d '[:space:]')
    [ "$ROOT_BYTES" -ge "$(((ROOT_GIB + SWAP_GIB + RESERVE_GIB + HOME_MIN_GIB + 2) * 1073741824))" ] || fail 'root disk too small for layout.'
    ROOT_WWN_OBSERVED=$(lsblk -dnro WWN "$ROOT_REAL" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//')
    ROOT_MODEL_OBSERVED=$(lsblk -dnro MODEL "$ROOT_REAL" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    while IFS= read -r child; do [ -z "$child" ] || [ -z "$(findmnt -rn -S "$child" -o TARGET 2>/dev/null || true)" ] || fail "target is mounted: $child"; done < <(lsblk -nrpo NAME "$ROOT_REAL")
}
native_run() { chroot "$NATIVE_ROOT" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C LC_ALL=C "$@"; }
lvm_run() { local op=$1; shift; native_run lvm "$op" --config 'activation { udev_sync=0 udev_rules=0 }' "$@"; }
native_mounts() {
    mkdir -p "$NATIVE_ROOT/dev" "$NATIVE_ROOT/proc" "$NATIVE_ROOT/sys"
    for p in "$NATIVE_ROOT/dev" "$NATIVE_ROOT/proc" "$NATIVE_ROOT/sys"; do mountpoint -q "$p" && fail "unexpected native mount: $p"; done
    mount -t proc proc "$NATIVE_ROOT/proc"; mount --rbind /sys "$NATIVE_ROOT/sys"; mount --make-rslave "$NATIVE_ROOT/sys"
    mount --rbind /dev "$NATIVE_ROOT/dev"; mount --make-rslave "$NATIVE_ROOT/dev"
}
native_unmounts() {
    local p nested
    for p in "$NATIVE_ROOT/dev" "$NATIVE_ROOT/sys" "$NATIVE_ROOT/proc"; do
        while IFS= read -r nested; do [ -z "$nested" ] || umount "$nested" || true; done < <(awk -v p="$p/" 'index($2,p)==1 {print $2}' /proc/mounts | sort -r)
        mountpoint -q "$p" && umount "$p" || true
    done
}
phase() { local name=$1; shift; { printf 'complete %s\n' "$(date -u +%FT%TZ)"; printf '%s\n' "$@"; } | gentoo_storage_receipt "$STATE" "$name"; }
verify_target() {
    resolve_disks
    if [ -s "$STATE/target.identity" ]; then
        grep -Fxq "profile=$GENTOO_MODE" "$STATE/target.identity" && grep -Fxq "serial=$ROOT_SERIAL" "$STATE/target.identity" &&
            grep -Fxq "size=$ROOT_BYTES" "$STATE/target.identity" && grep -Fxq "major:minor=$ROOT_MAJMIN" "$STATE/target.identity" &&
            grep -Fxq "volume group=$VG" "$STATE/target.identity" && grep -Fxq "root GiB=$ROOT_GIB" "$STATE/target.identity" &&
            grep -Fxq "swap GiB=$SWAP_GIB" "$STATE/target.identity" && grep -Fxq "reserve GiB=$RESERVE_GIB" "$STATE/target.identity" &&
            grep -Fxq "home minimum GiB=$HOME_MIN_GIB" "$STATE/target.identity" || fail 'target identity or storage plan changed.'
        [ "$GENTOO_MODE" != hardware ] || grep -Fxq "wwn=$ROOT_WWN_OBSERVED" "$STATE/target.identity" || fail 'root WWN changed.'
    fi
}
clean_ext4() { [ "$(blkid -p -s TYPE -o value "$1" 2>/dev/null || true)" = ext4 ] && dumpe2fs -h "$1" 2>/dev/null | grep -Eq '^Filesystem state:[[:space:]]+clean$'; }
resume_storage() {
    local luks_uuid pv vg lv free needed home_extents device fs current home_bytes
    gentoo_storage_resume_gate "$STATE" || fail 'storage state ambiguous; inspect manually.'
    verify_target; grep -Fxq "partition=$ROOT_PART" "$STATE/partition.done" || fail 'partition receipt differs.'
    [ -b "$ROOT_PART" ] && [ "$(lsblk -dnro PARTTYPE "$ROOT_PART" | tr '[:lower:]' '[:upper:]')" = CA7D7CCB-63ED-4C53-861C-1742536059CC ] || fail 'GPT LUKS partition mismatch.'
    native_run cryptsetup isLuks "$ROOT_PART" || fail 'root partition is not LUKS.'
    luks_uuid=$(native_run cryptsetup luksUUID "$ROOT_PART"); grep -Fxq "LUKS UUID=$luks_uuid" "$STATE/luks.done" || fail 'LUKS receipt mismatch.'
    if ! native_run cryptsetup status gentoo-crypt >/dev/null 2>&1; then
        read_secret 'Existing LUKS passphrase: ' pass; [ -n "$pass" ] || fail 'empty passphrase.'
        printf '%s' "$pass" | native_run cryptsetup luksOpen --key-file=- "$ROOT_PART" gentoo-crypt; unset pass
    else
        mapper_source=$(native_run cryptsetup status gentoo-crypt | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
        [ "$(readlink -f "$mapper_source")" = "$(readlink -f "$ROOT_PART")" ] || fail 'existing mapper belongs to another disk.'
    fi
    if [ ! -s "$STATE/pv.done" ]; then
        native_run pvs --noheadings /dev/mapper/gentoo-crypt >/dev/null 2>&1 && fail 'PV without receipt; inspect metadata.'
        native_run pvcreate --yes /dev/mapper/gentoo-crypt; phase pv.done 'PV=/dev/mapper/gentoo-crypt'
    fi
    pv=$(lvm_run pvs --noheadings -o pv_name --select 'pv_name=/dev/mapper/gentoo-crypt' | awk '{$1=$1; if(NF) print}'); [ "$pv" = /dev/mapper/gentoo-crypt ] || fail 'PV identity mismatch.'
    if [ ! -s "$STATE/vg.done" ]; then
        vg=$(lvm_run pvs --noheadings -o vg_name /dev/mapper/gentoo-crypt | tr -d '[:space:]'); [ -z "$vg" ] || fail 'VG without receipt; inspect metadata.'
        native_run vgcreate --physicalextentsize 4M "$VG" /dev/mapper/gentoo-crypt; phase vg.done "VG=$VG"
    fi
    vg=$(lvm_run pvs --noheadings -o vg_name /dev/mapper/gentoo-crypt | tr -d '[:space:]'); [ "$vg" = "$VG" ] || fail 'VG identity mismatch.'
    [ "$(lvm_run vgs --noheadings --units b --nosuffix -o vg_extent_size "$VG" | tr -d '[:space:]')" = 4194304 ] || fail 'VG extent size differs from the storage plan.'
    for lv in root swap home; do
        device="/dev/$VG/$lv"
        if [ ! -s "$STATE/lv-$lv.done" ]; then
            [ ! -e "$device" ] && [ ! -e "/dev/mapper/$VG-$lv" ] || fail "$lv exists without receipt; inspect."
            case "$lv" in
                root) native_run lvcreate --yes --size "$ROOT_GIB"G --name root "$VG" ;;
                swap) native_run lvcreate --yes --size "$SWAP_GIB"G --name swap "$VG" ;;
                home)
                    free=$(lvm_run vgs --noheadings -o vg_free_count "$VG" | tr -d '[:space:]'); needed=$(((RESERVE_GIB + HOME_MIN_GIB) * 256))
                    [ "$free" -gt "$needed" ] || fail 'not enough extents for home and reserve.'
                    home_extents=$((free - RESERVE_GIB * 256)); native_run lvcreate --yes --extents "$home_extents" --name home "$VG" ;;
            esac
            phase "lv-$lv.done" "LV=$VG/$lv"
        fi
        [ -b "$device" ] || fail "$lv is missing."
    done
    [ "$(lvm_run lvs --noheadings --units b --nosuffix -o lv_size "$VG/root" | tr -d '[:space:]')" = "$((ROOT_GIB * 1073741824))" ] || fail 'root LV size differs from the plan.'
    [ "$(lvm_run lvs --noheadings --units b --nosuffix -o lv_size "$VG/swap" | tr -d '[:space:]')" = "$((SWAP_GIB * 1073741824))" ] || fail 'swap LV size differs from the plan.'
    home_bytes=$(lvm_run lvs --noheadings --units b --nosuffix -o lv_size "$VG/home" | tr -d '[:space:]')
    [[ "$home_bytes" =~ ^[0-9]+$ ]] && [ "$home_bytes" -ge "$((HOME_MIN_GIB * 1073741824))" ] || fail 'home LV is below its configured minimum or unreadable.'
    native_run vgmknodes "$VG"
    for fs in root home swap; do
        device="/dev/$VG/$fs"
        if [ ! -s "$STATE/fs-$fs.done" ]; then
            case "$fs" in
                root|home)
                    if clean_ext4 "$device"; then :
                    elif [ -z "$(blkid -p -s TYPE -o value "$device" 2>/dev/null || true)" ] && [ "$MODE" = --initialize-storage ]; then
                        native_run mkfs.ext4 -F -m 0 -L "GENTOO-$fs" "$device"; clean_ext4 "$device" || fail "$fs format failed."
                    else fail "$fs format incomplete/unreceipted; inspect manually."; fi ;;
                swap)
                    current=$(blkid -p -s TYPE -o value "$device" 2>/dev/null || true)
                    if [ "$current" != swap ]; then [ -z "$current" ] && [ "$MODE" = --initialize-storage ] || fail 'swap state requires inspection.'; native_run mkswap -L GENTOO-swap "$device"; fi ;;
            esac
            phase "fs-$fs.done" "LV=$VG/$fs" "type=$(blkid -s TYPE -o value "$device")"
        fi
    done
    [ "$(lvm_run lvs --noheadings -o lv_name "$VG" | awk '{$1=$1; if(NF) print}' | sort)" = "home
root
swap" ] || fail 'unexpected target LVs.'
    clean_ext4 "/dev/$VG/root" && clean_ext4 "/dev/$VG/home" || fail 'root/home ext4 verification failed.'
    [ "$(blkid -s TYPE -o value "/dev/$VG/swap")" = swap ] || fail 'swap verification failed.'
    phase storage.done "LUKS UUID=$luks_uuid" "root UUID=$(blkid -s UUID -o value "/dev/$VG/root")" "home UUID=$(blkid -s UUID -o value "/dev/$VG/home")" "swap UUID=$(blkid -s UUID -o value "/dev/$VG/swap")" "VG=$VG" "root GiB=$ROOT_GIB" "swap GiB=$SWAP_GIB" "reserve GiB=$RESERVE_GIB" "home minimum GiB=$HOME_MIN_GIB"
}


target_run() { chroot "$TARGET_ROOT" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C LC_ALL=C "$@"; }
target_mounts() {
    local p
    for p in "$TARGET_ROOT/dev" "$TARGET_ROOT/proc" "$TARGET_ROOT/sys" "$TARGET_ROOT/run"; do
        mkdir -p "$p"; mountpoint -q "$p" && fail "unexpected target chroot mount: $p"
    done
    mount --rbind /dev "$TARGET_ROOT/dev"; mount --make-rslave "$TARGET_ROOT/dev"
    mount -t proc proc "$TARGET_ROOT/proc"; mount -t sysfs sysfs "$TARGET_ROOT/sys"
    mount --rbind /run "$TARGET_ROOT/run"; mount --make-rslave "$TARGET_ROOT/run"
}
target_unmounts() {
    local p nested
    for p in "$TARGET_ROOT/run" "$TARGET_ROOT/sys" "$TARGET_ROOT/proc" "$TARGET_ROOT/dev" "$TARGET_ROOT/home" "$TARGET_ROOT"; do
        while IFS= read -r nested; do [ -z "$nested" ] || umount "$nested" || true; done < <(awk -v p="$p/" 'index($2,p)==1 {print $2}' /proc/mounts | sort -r)
        mountpoint -q "$p" && umount "$p" || true
    done
    if [ "${OPENED_MAPPER_BY_US:-0}" = 1 ]; then
        native_run vgchange --activate n "$VG" >/dev/null 2>&1 || true
        native_run cryptsetup close gentoo-crypt >/dev/null 2>&1 || true
        OPENED_MAPPER_BY_US=0
    fi
}
install_accounts() {
    local group policy
    if [ -s "$STATE/accounts.done" ]; then return; fi
    for group in users wheel audio video; do
        target_run getent group "$group" >/dev/null 2>&1 || target_run groupadd "$group"
    done
    if target_run getent passwd "$GENTOO_SYSTEM_USERNAME" >/dev/null 2>&1; then
        target_run usermod -a -G wheel,audio,video "$GENTOO_SYSTEM_USERNAME"
    else target_run useradd -m -g users -G wheel,audio,video -s /bin/bash "$GENTOO_SYSTEM_USERNAME"; fi
    policy="$TARGET_ROOT/etc/security/passwdqc.conf"
    [ -f "$policy" ] || fail 'passwdqc configuration is missing.'
    sed '/^[[:space:]]*enforce[[:space:]]*=/d' "$policy" > "$policy.tmp"
    printf 'enforce=%s\n' "$([ "$GENTOO_SYSTEM_ALLOW_WEAK_PASSWORDS" = yes ] && echo none || echo everyone)" >> "$policy.tmp"
    chmod --reference="$policy" "$policy.tmp"; mv "$policy.tmp" "$policy"
    printf 'Set root and %s passwords; values are not stored or logged.\n' "$GENTOO_SYSTEM_USERNAME" >&2
    read_secret 'Root password: ' root_pw
    read_secret 'Repeat root password: ' root_again
    read_secret "$GENTOO_SYSTEM_USERNAME password: " user_pw
    read_secret "Repeat $GENTOO_SYSTEM_USERNAME password: " user_again
    [ -n "$root_pw" ] && [ "$root_pw" = "$root_again" ] && [ -n "$user_pw" ] && [ "$user_pw" = "$user_again" ] || fail 'empty or mismatched passwords.'
    printf 'root:%s\n%s:%s\n' "$root_pw" "$GENTOO_SYSTEM_USERNAME" "$user_pw" | target_run chpasswd
    unset root_pw root_again user_pw user_again
    target_run passwd -S root | awk '$2=="P" {found=1} END {exit !found}' || fail 'root password check failed.'
    target_run passwd -S "$GENTOO_SYSTEM_USERNAME" | awk '$2=="P" {found=1} END {exit !found}' || fail 'user password check failed.'
    target_run visudo -c
    phase accounts.done "account=$GENTOO_SYSTEM_USERNAME" "weak passwords=$GENTOO_SYSTEM_ALLOW_WEAK_PASSWORDS"
}
copy_continuation_bundle() {
    local digest directory checksum name
    digest=$(basename "$SCRIPT_DIR")
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail 'use the durable content-addressed input snapshot.'
    directory="$TARGET_ROOT/usr/local/share/gentoo-continuation/inputs/$digest"
    mkdir -p "$directory"
    while read -r checksum name; do
        [[ "$checksum" =~ ^[0-9a-f]{64}$ && "$name" =~ ^[A-Za-z0-9_./+-]+$ && "$name" != /* && "$name" != *..* ]] || fail 'unsafe manifest entry.'
        mkdir -p "$directory/$(dirname "$name")"
        if [ -e "$directory/$name" ]; then
            [ -f "$directory/$name" ] && [ "$(sha256 "$directory/$name")" = "$checksum" ] || fail "installed bundle differs: $name"
        else cp -p "$SCRIPT_DIR/$name" "$directory/$name"; fi
    done < "$SCRIPT_DIR/SHA256SUMS"
    if [ -e "$directory/SHA256SUMS" ]; then
        cmp -s "$SCRIPT_DIR/SHA256SUMS" "$directory/SHA256SUMS" || fail 'installed bundle manifest differs.'
    else cp -p "$SCRIPT_DIR/SHA256SUMS" "$directory/SHA256SUMS"; fi
    (cd "$directory" && sha256sum -c SHA256SUMS)
    chmod 0755 "$directory/gentoo.sh" "$directory/install-base.sh"
    mkdir -p "$TARGET_ROOT/usr/local/sbin"
    ln -sfn "/usr/local/share/gentoo-continuation/inputs/$digest/gentoo.sh" "$TARGET_ROOT/usr/local/sbin/gentoo-continue"
    ln -sfn "/usr/local/share/gentoo-continuation/inputs/$digest/install-base.sh" "$TARGET_ROOT/usr/local/sbin/gentoo-install-base"
    install -d -m 0755 "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo" "$TARGET_ROOT/usr/local/sbin"
    for name in signed-generation.py signed-generation.example.toml SIGNED-GENERATIONS.md; do
        install -m 0644 "$SCRIPT_DIR/$name" "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/$name.tmp"
        mv "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/$name.tmp" "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/$name"
    done
    chmod 0755 "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/signed-generation.py"
    ln -sfn /usr/local/share/ms1504-bootstrap/gentoo/signed-generation.py "$TARGET_ROOT/usr/local/sbin/ms1504-signed-generation"
    install -m 0644 "$SCRIPT_DIR/signed-generation.example.toml" "$TARGET_ROOT/usr/share/ms1504-signed-generation.example.toml"
    install -m 0644 "$SCRIPT_DIR/setup-extra.sh" "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/setup-extra.sh"
    chmod 0755 "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/setup-extra.sh"
    ln -sfn /usr/local/share/ms1504-bootstrap/gentoo/setup-extra.sh "$TARGET_ROOT/usr/local/sbin/ms1504-extra"
    for name in complete-desktop.sh desktop-packages.list desktop-package.use desktop-package.accept_keywords desktop-package.mask desktop-rust-bootstrap.py desktop-lightdm.conf config.sh; do
        install -m 0644 "$SCRIPT_DIR/$name" "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/$name"
    done
    for name in "${GENTOO_PACKAGES_DESKTOP:-}" "${GENTOO_PACKAGES_DESKTOP_USE:-}" \
        "${GENTOO_PACKAGES_DESKTOP_KEYWORDS:-}" "${GENTOO_PACKAGES_DESKTOP_MASK:-}"; do
        [ -n "$name" ] || continue
        [[ "$name" =~ ^policy/[A-Za-z0-9_.+-]+$ ]] || fail "unsafe desktop policy input path: $name"
        install -D -m 0644 "$SCRIPT_DIR/$name" "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/$name"
    done
    chmod 0755 "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/complete-desktop.sh"
    chmod 0755 "$TARGET_ROOT/usr/local/share/ms1504-bootstrap/gentoo/desktop-rust-bootstrap.py"
    ln -sfn /usr/local/share/ms1504-bootstrap/gentoo/complete-desktop.sh "$TARGET_ROOT/usr/local/sbin/complete-desktop"
}
install_root() {
    local root_lv="/dev/$VG/root" home_lv="/dev/$VG/home" swap_lv="/dev/$VG/swap"
    local root_uuid home_uuid swap_uuid luks_uuid module_release cmdline temp
    OPENED_MAPPER_BY_US=0
    [ -s "$STATE/storage.done" ] || fail 'storage is incomplete; run --resume-storage.'
    verify_target; native_mounts
    trap 'target_unmounts; native_unmounts' EXIT
    native_run cryptsetup isLuks "$ROOT_PART" || fail 'target partition is not LUKS.'
    if ! native_run cryptsetup status gentoo-crypt >/dev/null 2>&1; then
        read_secret 'Root LUKS passphrase: ' pass
        [ -n "$pass" ] || fail 'empty passphrase.'
        printf '%s' "$pass" | native_run cryptsetup luksOpen --key-file=- "$ROOT_PART" gentoo-crypt; unset pass
        OPENED_MAPPER_BY_US=1
    else
        mapper_source=$(native_run cryptsetup status gentoo-crypt | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
        [ "$(readlink -f "$mapper_source")" = "$(readlink -f "$ROOT_PART")" ] || fail 'existing mapper belongs to another disk.'
    fi
    native_run vgchange --activate y "$VG"; native_run vgmknodes "$VG"
    [ -b "$root_lv" ] && [ -b "$home_lv" ] && [ -b "$swap_lv" ] || fail 'expected LVs are missing.'
    mkdir -p "$TARGET_ROOT"
    if mountpoint -q "$TARGET_ROOT"; then
        [ "$(readlink -f "$(findmnt -rn -M "$TARGET_ROOT" -o SOURCE)")" = "$(readlink -f "$root_lv")" ] || fail 'wrong root mount.'
    else
        [ -z "$(findmnt -rn -M "$TARGET_ROOT" -o SOURCE 2>/dev/null || true)" ] && [ -z "$(ls -A "$TARGET_ROOT")" ] || fail 'install mountpoint is occupied.'
        mount "$root_lv" "$TARGET_ROOT"
    fi
    mkdir -p "$TARGET_ROOT/home"
    if mountpoint -q "$TARGET_ROOT/home"; then
        [ "$(readlink -f "$(findmnt -rn -M "$TARGET_ROOT/home" -o SOURCE)")" = "$(readlink -f "$home_lv")" ] || fail 'wrong home mount.'
    else mount "$home_lv" "$TARGET_ROOT/home"; fi
    root_uuid=$(blkid -s UUID -o value "$root_lv"); home_uuid=$(blkid -s UUID -o value "$home_lv"); swap_uuid=$(blkid -s UUID -o value "$swap_lv")
    luks_uuid=$(native_run cryptsetup luksUUID "$ROOT_PART")
    grep -Fxq "root UUID=$root_uuid" "$STATE/storage.done" && grep -Fxq "home UUID=$home_uuid" "$STATE/storage.done" &&
        grep -Fxq "swap UUID=$swap_uuid" "$STATE/storage.done" && grep -Fxq "LUKS UUID=$luks_uuid" "$STATE/storage.done" || fail 'storage receipt mismatch.'
    if [ ! -s "$STATE/root-filesystem.done" ]; then
        [ -x "$WORK_ROOT/host-tools/bin/rsync" ] || fail 'source-built host rsync is missing.'
        "$WORK_ROOT/host-tools/bin/rsync" -aHAX --numeric-ids \
            --exclude=/dev/*** --exclude=/proc/*** --exclude=/sys/*** --exclude=/run/*** --exclude=/tmp/*** \
            --exclude=/mnt/*** --exclude=/media/*** --exclude=/build/*** --exclude=/gentoo-scratch/*** \
            --exclude=/host-build/*** --exclude=/var/tmp/portage/*** --exclude=/var/cache/distfiles/*** \
            "$NATIVE_ROOT/" "$TARGET_ROOT/"
        phase root-filesystem.done 'source=verified native Gentoo root' "root UUID=$root_uuid"
    fi
    install -d -m 1777 "$TARGET_ROOT/tmp" "$TARGET_ROOT/var/tmp"
    copy_continuation_bundle
    (cd "$WORK_ROOT/artifacts/kernel-$RELEASE" && sha256sum -c SHA256SUMS)
    module_release=$(cat "$WORK_ROOT/artifacts/kernel-$RELEASE/kernelrelease")
    [ "$module_release" = "$RELEASE" ] || fail 'kernel release mismatch.'
    mkdir -p "$TARGET_ROOT/boot" "$TARGET_ROOT/lib/modules/$module_release"
    install -m 0644 "$WORK_ROOT/artifacts/kernel-$RELEASE/bzImage" "$TARGET_ROOT/boot/vmlinuz-$module_release"
    install -m 0644 "$WORK_ROOT/artifacts/kernel-$RELEASE/config" "$TARGET_ROOT/boot/config-$module_release"
    "$WORK_ROOT/host-tools/bin/rsync" -aH --numeric-ids \
        "$WORK_ROOT/artifacts/kernel-$RELEASE/modules/lib/modules/$module_release/" "$TARGET_ROOT/lib/modules/$module_release/"
    printf 'UUID=%s\t/\text4\tdefaults,noatime\t0 1\nUUID=%s\t/home\text4\tdefaults,noatime\t0 2\nUUID=%s\tnone\tswap\tsw\t0 0\n' \
        "$root_uuid" "$home_uuid" "$swap_uuid" > "$TARGET_ROOT/etc/fstab.tmp"
    mv "$TARGET_ROOT/etc/fstab.tmp" "$TARGET_ROOT/etc/fstab"
    printf '%s\n' "$GENTOO_SYSTEM_HOSTNAME" > "$TARGET_ROOT/etc/hostname"
    printf 'hostname="%s"\n' "$GENTOO_SYSTEM_HOSTNAME" > "$TARGET_ROOT/etc/conf.d/hostname"
    printf '127.0.0.1 localhost\n127.0.1.1 %s %s\n::1 localhost\n' "$GENTOO_SYSTEM_FQDN" "$GENTOO_SYSTEM_HOSTNAME" > "$TARGET_ROOT/etc/hosts"
    printf '%s UTF-8\n' "$GENTOO_SYSTEM_LOCALE" > "$TARGET_ROOT/etc/locale.gen"
    printf 'LANG="%s"\n' "$GENTOO_SYSTEM_LOCALE" > "$TARGET_ROOT/etc/env.d/02locale"
    printf 'KEYMAP="%s"\n' "$GENTOO_SYSTEM_KEYMAP" > "$TARGET_ROOT/etc/conf.d/keymaps"
    ln -sfn "../usr/share/zoneinfo/$GENTOO_SYSTEM_TIMEZONE" "$TARGET_ROOT/etc/localtime"
    mkdir -p "$TARGET_ROOT/etc/kernel" "$TARGET_ROOT/etc/sudoers.d" "$TARGET_ROOT/home/.ms1504-hibernate"
    chown 0:0 "$TARGET_ROOT/home/.ms1504-hibernate"
    chmod 0700 "$TARGET_ROOT/home/.ms1504-hibernate"
    printf 'profile=%s\nvolume_group=%s\nroot_lv=root\nhome_lv=home\nswap_lv=swap\nreserve_gib=%s\nby_id=%s\nserial=%s\nwwn=%s\nmodel=%s\nsize=%s\n' \
        "$GENTOO_MODE" "$VG" "$RESERVE_GIB" "$ROOT_BY_ID" "$ROOT_SERIAL" "$ROOT_WWN_OBSERVED" "$ROOT_MODEL_OBSERVED" "$ROOT_BYTES" \
        > "$TARGET_ROOT/etc/ms1504-root-identity"
    chmod 0644 "$TARGET_ROOT/etc/ms1504-root-identity"
    printf 'profile=%s\nvolume_group=%s\nroot_lv=root\nhome_lv=home\nby_id=%s\nserial=%s\nwwn=%s\nmodel=%s\nsize=%s\nworkspace_uuid=%s\nworkspace_label=%s\n' \
        "$GENTOO_MODE" "$VG" "$EXTRA_BY_ID" "$EXTRA_SERIAL" "$EXTRA_WWN_OBSERVED" "$EXTRA_MODEL_OBSERVED" "$EXTRA_BYTES" \
        "$GENTOO_DISCOVERED_WORKSPACE_UUID" "$GENTOO_WORKSPACE_LABEL" > "$TARGET_ROOT/etc/ms1504-extra-identity"
    chmod 0644 "$TARGET_ROOT/etc/ms1504-extra-identity"
    printf '%s\n' "$BASE_GENERATION" > "$TARGET_ROOT/etc/ms1504-generation"
    chmod 0644 "$TARGET_ROOT/etc/ms1504-generation"
    repo_commit=5548522e2c2fa10a4c009348e9ba84c44783f47d
    [ -d "$TARGET_ROOT/usr/portage/profiles" ] || fail 'pinned Gentoo repository is missing from the installation root.'
    install -d -m 0755 "$TARGET_ROOT/usr/share/gentoo-continuation"
    printf 'repository commit=%s\nrepository tree sha256=%s\n' "$repo_commit" "$(tree_sha256 "$TARGET_ROOT/usr/portage")" \
        > "$TARGET_ROOT/usr/share/gentoo-continuation/gentoo-source-receipt"
    chmod 0644 "$TARGET_ROOT/usr/share/gentoo-continuation/gentoo-source-receipt"
    printf 'rd.luks.uuid=luks-%s rd.lvm.lv=%s/root rd.lvm.lv=%s/home rd.lvm.lv=%s/swap root=/dev/mapper/%s-root resume=/dev/mapper/%s-swap ms1504.generation=%s ro loglevel=4 %s\n' \
        "$luks_uuid" "$VG" "$VG" "$VG" "$VG" "$VG" "$BASE_GENERATION" "$([ "$GENTOO_MODE" = vm ] && echo 'console=tty0 console=ttyS0,115200n8' || echo 'console=tty0')" > "$TARGET_ROOT/etc/kernel/cmdline"
    [ -f "$TARGET_ROOT/etc/sudoers" ] || fail 'sudo configuration missing.'
    grep -Eq '^#includedir[[:space:]]+/etc/sudoers.d' "$TARGET_ROOT/etc/sudoers" || printf '\n#includedir /etc/sudoers.d\n' >> "$TARGET_ROOT/etc/sudoers"
    printf '%s ALL=(ALL:ALL) ALL\n' "$GENTOO_SYSTEM_USERNAME" > "$TARGET_ROOT/etc/sudoers.d/10-gentoo-user"
    chmod 0440 "$TARGET_ROOT/etc/sudoers.d/10-gentoo-user"
    if [ "$GENTOO_MODE" = vm ]; then
        [ -f "$TARGET_ROOT/etc/inittab" ] || fail 'VM serial login requires inittab.'
        grep -Eq '^[[:space:]]*[^#[:space:]].*ttyS0' "$TARGET_ROOT/etc/inittab" || printf 's0:12345:respawn:/sbin/agetty -L 115200 ttyS0 vt100\n' >> "$TARGET_ROOT/etc/inittab"
    fi
    install_accounts
    target_mounts
    trap 'target_unmounts; native_unmounts' EXIT
    resume_guard="$TARGET_ROOT/usr/lib/dracut/modules.d/05ms1504-resume-guard"
    install -d -m 0755 "$resume_guard"
    install -m 0644 "$SCRIPT_DIR/dracut-ms1504-resume-guard/module-setup.sh" "$resume_guard/module-setup.sh"
    install -m 0644 "$SCRIPT_DIR/dracut-ms1504-resume-guard/check-generation.sh" "$resume_guard/check-generation.sh"
    target_run depmod -a "$module_release"
    target_run dracut --force --no-hostonly --kver "$module_release" --add 'crypt lvm resume ms1504-resume-guard' "/boot/initramfs-$module_release.img" "$module_release"
    [ -s "$TARGET_ROOT/boot/initramfs-$module_release.img" ] || fail 'dracut did not create initramfs.'
    target_run locale-gen; target_run env-update
    for service in NetworkManager sysklogd; do
        [ -e "$TARGET_ROOT/etc/init.d/$service" ] || fail "missing OpenRC service: $service"
        target_run rc-update add "$service" default
    done
    [ -e "$TARGET_ROOT/etc/init.d/lvm" ] || fail 'LVM OpenRC service missing.'
    target_run rc-update add lvm boot
    [ -e "$TARGET_ROOT/etc/init.d/keymaps" ] || fail 'keymaps service missing.'
    target_run rc-update add keymaps boot; target_run visudo -c
    target_run lsinitrd "/boot/initramfs-$module_release.img" > "$STATE/initramfs-listing.txt"
    grep -Eiq 'cryptsetup|cryptroot' "$STATE/initramfs-listing.txt" && grep -Eiq 'lvm' "$STATE/initramfs-listing.txt" || fail 'initramfs lacks crypt/LVM.'
    grep -Fq 'check-generation.sh' "$STATE/initramfs-listing.txt" || fail 'initramfs lacks the hibernation-generation guard.'
    mkdir -p "$WORK_ROOT/artifacts/installed-base"
    install -m 0644 "$TARGET_ROOT/boot/vmlinuz-$module_release" "$WORK_ROOT/artifacts/installed-base/kernel.tmp"
    install -m 0644 "$TARGET_ROOT/boot/initramfs-$module_release.img" "$WORK_ROOT/artifacts/installed-base/initramfs.tmp"
    mv "$WORK_ROOT/artifacts/installed-base/kernel.tmp" "$WORK_ROOT/artifacts/installed-base/kernel"
    mv "$WORK_ROOT/artifacts/installed-base/initramfs.tmp" "$WORK_ROOT/artifacts/installed-base/initramfs"
    cmdline=$(cat "$TARGET_ROOT/etc/kernel/cmdline")
    temp=$(mktemp "$STATE/.installed-boot.XXXXXX")
    printf 'release=%s\nkernel sha256=%s\ninitramfs sha256=%s\nworkspace UUID=%s\ncmdline=%s\n' \
        "$module_release" "$(sha256 "$WORK_ROOT/artifacts/installed-base/kernel")" \
        "$(sha256 "$WORK_ROOT/artifacts/installed-base/initramfs")" "$GENTOO_DISCOVERED_WORKSPACE_UUID" "$cmdline" > "$temp"
    sync "$temp"; mv "$temp" "$STATE/installed-boot.receipt"; sync "$STATE"
    phase root-install.done "root UUID=$root_uuid" "kernel release=$module_release"
    printf 'Encrypted base installed. VM: python3 system/gentoo/vm.py boot-installed --run-dir RUN_DIRECTORY --apply\n'
}
for utility in awk blkid blockdev cat chroot cmp cp date dumpe2fs find findmnt grep install lsblk mkdir mount mountpoint mv readlink rsync sed sha256sum sort sync umount wipefs; do
    command -v "$utility" >/dev/null 2>&1 || fail "missing command: $utility"
done
[ -s "$WORK_ROOT/state/90-native-base-runtime.done" ] && [ -x "$NATIVE_ROOT/usr/bin/emerge" ] || fail 'batch 9 runtime receipt missing.'
[ -s "$WORK_ROOT/artifacts/kernel-$RELEASE/SHA256SUMS" ] || fail 'kernel artifacts missing.'
resolve_disks
printf 'Profile: %s\nRoot: %s (%s; %s bytes)\nProtected boot: %s (%s)\nProtected extra/workspace: %s (%s)\n' \
    "$GENTOO_MODE" "$ROOT_REAL" "$ROOT_SERIAL" "$ROOT_BYTES" "$BOOT_REAL" "$BOOT_SERIAL" "$EXTRA_REAL" "$EXTRA_SERIAL"
printf 'Planned GPT/LUKS2/LVM %s: root %s GiB, swap %s GiB, home remainder, reserve %s GiB.\n' \
    "$VG" "$ROOT_GIB" "$SWAP_GIB" "$RESERVE_GIB"
case "$MODE" in
    --plan)
        if [ -s "$STATE/storage.started" ]; then
            find "$STATE" -maxdepth 1 -type f \( -name '*.done' -o -name 'storage.started' -o -name 'target.identity' \) -print | sort
            native_mounts; native_run cryptsetup status gentoo-crypt || true
            lvm_run pvs -o pv_name,pv_uuid,vg_name,pv_size || true; lvm_run vgs -o vg_name,vg_uuid,vg_size,vg_free || true
            lvm_run lvs -a -o lv_name,vg_name,lv_size,lv_attr,devices || true; native_unmounts
        else printf 'Plan only; no storage writes or mounts performed.\n'; fi
        ;;
    --initialize-storage)
        [ -t 0 ] || fail 'interactive terminal required.'
        native_mounts; trap 'native_unmounts' EXIT
        verify_target
        signatures=$(wipefs --no-act --noheadings --output TYPE "$ROOT_REAL") || fail 'cannot inspect root signatures.'
        [ -z "$signatures" ] || fail 'target contains existing signatures; inspect before proceeding.'
        printf 'ERASE %s %s\n' "$ROOT_SERIAL" "$ROOT_MAJMIN" > /dev/tty
        read_tty_line 'Type the exact line above: ' confirmation
        [ "$confirmation" = "ERASE $ROOT_SERIAL $ROOT_MAJMIN" ] || fail 'confirmation mismatch.'
        mkdir -p "$STATE"; chmod 0700 "$STATE"
        [ ! -e "$STATE/storage.started" ] && [ ! -e "$STATE/storage.done" ] || fail 'storage already started; use plan/resume.'
        phase target.identity "profile=$GENTOO_MODE" "serial=$ROOT_SERIAL" "size=$ROOT_BYTES" "major:minor=$ROOT_MAJMIN" "wwn=$ROOT_WWN_OBSERVED" "model=$ROOT_MODEL_OBSERVED" "root device=$ROOT_REAL" "volume group=$VG" "root GiB=$ROOT_GIB" "swap GiB=$SWAP_GIB" "reserve GiB=$RESERVE_GIB" "home minimum GiB=$HOME_MIN_GIB"
        phase storage.started "target serial=$ROOT_SERIAL" "major:minor=$ROOT_MAJMIN"
        native_run sgdisk --zap-all --clear --new=1:0:0 --typecode=1:8309 --change-name=1:GENTOO-ROOT "$ROOT_REAL"
        sync; native_run blockdev --rereadpt "$ROOT_REAL"; native_run udevadm settle --timeout=30
        [ -b "$ROOT_PART" ] && [ "$(lsblk -dnro PARTTYPE "$ROOT_PART" | tr '[:lower:]' '[:upper:]')" = CA7D7CCB-63ED-4C53-861C-1742536059CC ] || fail 'GPT partition verification failed.'
        phase partition.done "partition=$ROOT_PART" 'type=CA7D7CCB-63ED-4C53-861C-1742536059CC'
        printf 'New LUKS passphrase twice; it will not be saved or logged.\n' >&2
        read_secret 'New passphrase: ' pass
        read_secret 'Repeat passphrase: ' pass_again
        [ -n "$pass" ] && [ "$pass" = "$pass_again" ] || fail 'empty or mismatched passphrases.'
        printf '%s' "$pass" | native_run cryptsetup luksFormat --batch-mode --type luks2 --pbkdf argon2id --label GENTOO-ROOT --key-file=- "$ROOT_PART"
        unset pass pass_again; native_run cryptsetup isLuks "$ROOT_PART" || fail 'LUKS format failed.'
        phase luks.done "LUKS UUID=$(native_run cryptsetup luksUUID "$ROOT_PART")"
        resume_storage; native_unmounts; trap - EXIT
        ;;
    --resume-storage)
        [ -t 0 ] || fail 'interactive terminal required to unlock the target.'
        native_mounts; trap 'native_unmounts' EXIT; resume_storage; native_unmounts; trap - EXIT
        ;;
    --install-root)
        [ -t 0 ] || fail 'interactive terminal required for LUKS and account passwords.'
        install_root
        ;;
esac
