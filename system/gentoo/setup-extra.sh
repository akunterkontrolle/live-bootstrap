#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Migrate the bootstrap workspace to encrypted /home, then encrypt and restore
# it to the identity-checked extra disk. No action occurs without a subcommand.
set -Eeuo pipefail
umask 077

MODE=${1:---plan}; shift || true
case "$MODE" in --plan|plan) MODE=plan ;; --migrate|migrate) MODE=migrate ;;
    --initialize|initialize) MODE=initialize ;; --resume|resume) MODE=resume ;;
    --restore|restore) MODE=restore ;; --verify|verify) MODE=verify ;;
    --discard-migration|discard-migration) MODE=discard-migration ;;
    *) echo 'Usage: ms1504-extra {plan|migrate --source MOUNT|initialize --source MOUNT|resume|restore|verify|discard-migration}' >&2; exit 2 ;; esac

. "$(dirname -- "$(readlink -f "$0")")/lib/devices.sh"
gentoo_die() { fail "$@"; }

fail() { printf 'ms1504-extra: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"; }
for command in awk blkid blockdev chmod chown cryptsetup date dd df du find findmnt flock grep id install lsblk lvs mkdir mkfs.ext4 mount mountpoint mv rc-service rc-update readlink rm rsync sed sha256sum sgdisk sort stat sync tail tr udevadm umount wc xargs; do need "$command"; done
[ "$(id -u)" = 0 ] || fail 'run as root.'
case "$-" in *x*) fail 'disable shell tracing before using the extra-disk helper.' ;; esac

IDENTITY=/etc/ms1504-extra-identity
ROOT_IDENTITY=/etc/ms1504-root-identity
STATE=/var/lib/ms1504-extra
MIGRATION=/home/.ms1504-workspace-migration
DATA=$MIGRATION/data
KEY=/root/.keys/ms1504-extra.key
MAPPER=ms1504-extra
MOUNT=/extra
CONFIG=/etc/conf.d/ms1504-extra
SERVICE=/etc/init.d/ms1504-extra

read_identity() {
    local file=$1 line key value
    declare -gA ID=()
    [[ -f $file && ! -L $file && $(stat -c '%u:%g:%a' "$file") == 0:0:644 ]] ||
        fail "identity receipt must be a root-owned, mode-0644 regular file: $file"
    while IFS= read -r line || [[ -n $line ]]; do
        [[ $line == *=* ]] || fail "malformed identity line in $file"
        key=${line%%=*}; value=${line#*=}
        if [[ $file == "$ROOT_IDENTITY" ]]; then
            case "$key" in profile|volume_group|root_lv|home_lv|swap_lv|reserve_gib|by_id|serial|wwn|model|size) ;; *) fail "unknown root identity field: $key" ;; esac
        else
            case "$key" in profile|volume_group|root_lv|home_lv|by_id|serial|wwn|model|size|workspace_uuid|workspace_label) ;; *) fail "unknown extra identity field: $key" ;; esac
        fi
        [[ -z ${ID[$key]+x} ]] || fail "duplicate identity field $key in $file"
        ID[$key]=$value
    done < "$file"
}

read_identity "$ROOT_IDENTITY"
ROOT_PROFILE=${ID[profile]:-}; ROOT_VG=${ID[volume_group]:-}; ROOT_LV=${ID[root_lv]:-}; HOME_LV=${ID[home_lv]:-}
ROOT_BY_ID=${ID[by_id]:-}; ROOT_SERIAL=${ID[serial]:-}; ROOT_WWN=${ID[wwn]:-}; ROOT_MODEL=${ID[model]:-}; ROOT_BYTES=${ID[size]:-}
[[ $ROOT_PROFILE == hardware || $ROOT_PROFILE == vm ]] || fail 'invalid installed profile.'
[[ $ROOT_VG =~ ^[A-Za-z0-9+_.-]+$ && $ROOT_LV =~ ^[A-Za-z0-9+_.-]+$ && $HOME_LV =~ ^[A-Za-z0-9+_.-]+$ ]] || fail 'invalid installed LVM identity.'
[[ $ROOT_SERIAL =~ ^[A-Za-z0-9._+-]{1,128}$ && $ROOT_BYTES =~ ^[1-9][0-9]*$ ]] || fail 'invalid installed root disk identity.'
if [[ $ROOT_PROFILE == hardware ]]; then [[ $ROOT_BY_ID == /dev/disk/by-id/* ]] || fail 'hardware root by-id path is missing.'; else [[ -z $ROOT_BY_ID ]] || fail 'VM root identity must not contain a hardware by-id path.'; fi

read_identity "$IDENTITY"
PROFILE=${ID[profile]:-}; VG=${ID[volume_group]:-}; ROOT_LV_EXTRA=${ID[root_lv]:-}; HOME_LV_EXTRA=${ID[home_lv]:-}
EXTRA_BY_ID=${ID[by_id]:-}; EXTRA_SERIAL=${ID[serial]:-}; EXTRA_WWN=${ID[wwn]:-}
EXTRA_MODEL=${ID[model]:-}; EXTRA_BYTES=${ID[size]:-}; WORKSPACE_UUID=${ID[workspace_uuid]:-}
WORKSPACE_LABEL=${ID[workspace_label]:-}
[[ $PROFILE == "$ROOT_PROFILE" && $VG == "$ROOT_VG" && $ROOT_LV_EXTRA == "$ROOT_LV" && $HOME_LV_EXTRA == "$HOME_LV" ]] || fail 'extra identity does not belong to the installed root.'
[[ $EXTRA_SERIAL =~ ^[A-Za-z0-9._+-]{1,128}$ && $EXTRA_BYTES =~ ^[1-9][0-9]*$ ]] || fail 'invalid extra-disk identity.'
[[ $WORKSPACE_UUID =~ ^[A-Fa-f0-9-]{36}$ && $WORKSPACE_LABEL =~ ^[A-Za-z0-9._+-]{1,16}$ ]] || fail 'invalid workspace filesystem identity.'
if [[ $PROFILE == hardware ]]; then
    [[ $EXTRA_BY_ID == /dev/disk/by-id/* && -n $EXTRA_WWN && -n $EXTRA_MODEL ]] || fail 'hardware extra-disk by-id, WWN and model are required.'
else
    [[ -z $EXTRA_BY_ID ]] || fail 'VM extra identity must not contain a hardware by-id path.'
fi

[[ ! -L $STATE ]] || fail 'extra state directory must not be a symlink.'
if [[ $MODE != plan ]]; then
    mkdir -p "$STATE"; chmod 0700 "$STATE"
    [[ $(stat -c '%u:%g:%a' "$STATE") == 0:0:700 ]] || fail 'state directory ownership or mode is unsafe.'
    exec 9>"$STATE/lock"
    flock -n 9 || fail 'another extra-disk operation is running.'
fi
[[ ! -L $MOUNT ]] || fail '/extra mountpoint must not be a symlink.'

disk_serial() {
    local disk=${1} value
    value=$(lsblk -dnro SERIAL "$disk" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [[ -n $value ]] || fail "cannot read disk serial: $disk"
    printf '%s\n' "$value"
}

find_by_serial() {
    local path type count=0 found=
    while read -r path type; do
        [[ $type == disk ]] || continue
        [[ $(disk_serial "$path" 2>/dev/null || true) == "$EXTRA_SERIAL" ]] || continue
        found=$path; count=$((count + 1))
    done < <(lsblk -dnpo NAME,TYPE)
    [[ $count -eq 1 ]] || fail "expected exactly one extra disk with serial $EXTRA_SERIAL; found $count."
    printf '%s\n' "$found"
}

resolve_devices() {
    local root_devs root_disks actual size type
    ROOT_DEVICE=$(readlink -f "/dev/$VG/$ROOT_LV")
    HOME_DEVICE=$(readlink -f "/dev/$VG/$HOME_LV")
    [[ -b $ROOT_DEVICE && -b $HOME_DEVICE ]] || fail 'installed root/home LVs are unavailable.'
    mountpoint -q /home || fail '/home is not mounted; migration must be on encrypted home.'
    [[ $(readlink -f "$(findmnt -rn -M /home -o SOURCE)") == "$HOME_DEVICE" ]] || fail '/home is not the installed home LV.'
    root_devs=$(lvs --noheadings --options devices "$ROOT_DEVICE" | tr -d '[:space:]')
    [[ -n $root_devs ]] || fail 'cannot identify the physical root backing device.'
    root_disks=$(lsblk -s -lnpo NAME,TYPE "$ROOT_DEVICE" | awk '$2 == "disk" {print $1}')
    [[ $(printf '%s\n' "$root_disks" | wc -l) -eq 1 ]] || fail 'root LV does not resolve to exactly one physical disk.'
    ROOT_DISK=$(readlink -f "$root_disks")
    [[ $(disk_serial "$ROOT_DISK") == "$ROOT_SERIAL" && $(lsblk -bndro SIZE "$ROOT_DISK" | tr -d '[:space:]') == "$ROOT_BYTES" ]] || fail 'running root disk differs from its installed identity receipt.'
    if [[ $ROOT_PROFILE == hardware ]]; then
        [[ $(gentoo_resolve_hardware_disk "$ROOT_BY_ID" "$ROOT_SERIAL" "$ROOT_BYTES" "$ROOT_BYTES" "$ROOT_WWN" "$ROOT_MODEL") == "$ROOT_DISK" ]] || fail 'running root disk no longer resolves to its installed by-id path.'
        [[ $(gentoo_disk_attribute WWN "$ROOT_DISK" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//') == "$ROOT_WWN" ]] || fail 'running root WWN differs from its installed identity receipt.'
        [[ $(gentoo_disk_attribute MODEL "$ROOT_DISK" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//') == "$ROOT_MODEL" ]] || fail 'running root model differs from its installed identity receipt.'
    fi
    if [[ $PROFILE == hardware ]]; then EXTRA_DISK=$(gentoo_resolve_hardware_disk "$EXTRA_BY_ID" "$EXTRA_SERIAL" "$EXTRA_BYTES" "$EXTRA_BYTES" "$EXTRA_WWN" "$EXTRA_MODEL"); else EXTRA_DISK=$(find_by_serial); fi
    [[ -b $EXTRA_DISK && $(lsblk -dnro TYPE "$EXTRA_DISK" | tr -d '[:space:]') == disk ]] || fail 'extra identity is not a whole block disk.'
    EXTRA_REAL=$(readlink -f "$EXTRA_DISK")
    [[ $EXTRA_REAL != "$ROOT_DISK" ]] || fail 'extra disk is the encrypted root disk.'
    [[ $(disk_serial "$EXTRA_REAL") == "$EXTRA_SERIAL" ]] || fail 'extra disk serial mismatch.'
    size=$(lsblk -bndro SIZE "$EXTRA_REAL" | tr -d '[:space:]')
    [[ $size == "$EXTRA_BYTES" ]] || fail 'extra disk capacity differs from its installed identity receipt.'
    if [[ $PROFILE == hardware ]]; then
        [[ $(gentoo_disk_attribute WWN "$EXTRA_REAL" | tr '[:upper:]' '[:lower:]' | sed 's/^0x//') == "$EXTRA_WWN" ]] || fail 'extra disk WWN mismatch.'
        [[ $(gentoo_disk_attribute MODEL "$EXTRA_REAL" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//') == "$EXTRA_MODEL" ]] || fail 'extra disk model mismatch.'
        [[ $(gentoo_resolve_hardware_disk "$EXTRA_BY_ID" "$EXTRA_SERIAL" "$EXTRA_BYTES" "$EXTRA_BYTES" "$EXTRA_WWN" "$EXTRA_MODEL") == "$EXTRA_REAL" ]] || fail 'configured extra by-id path changed.'
    fi
    EXTRA_PART=${EXTRA_REAL}1
    case "$EXTRA_REAL" in *[0-9]) EXTRA_PART=${EXTRA_REAL}p1 ;; esac
    EXTRA_MAJMIN=$(lsblk -dnro MAJ:MIN "$EXTRA_REAL" | tr -d '[:space:]')
}

tree_manifest() {
    local directory=$1 output=$2
    (cd "$directory" && find . -xdev -type f -print0 | sort -z | xargs -0 -r sha256sum) > "$output"
}

validate_home_migration() {
    [[ ! -L $MIGRATION && ! -L $DATA && -d $MIGRATION && -d $DATA ]] || fail 'migration staging tree is missing or unsafe.'
    [[ $(stat -c '%u:%g:%a' "$MIGRATION") == 0:0:700 && $(stat -c '%u:%g:%a' "$DATA") == 0:0:700 ]] || fail 'migration staging must be root-owned mode 0700.'
    [[ $(findmnt -rn -T "$DATA" -o TARGET) == /home ]] || fail 'migration data is not stored on /home.'
    [[ -s $STATE/migration.done && -s $STATE/workspace.sha256 ]] || fail 'workspace migration is incomplete.'
    grep -Fxq "extra serial=$EXTRA_SERIAL" "$STATE/migration.done" || fail 'migration belongs to another extra disk.'
    (cd "$DATA" && sha256sum --check --status "$STATE/workspace.sha256") || fail 'preserved migration data failed its file hash manifest.'
}

check_workspace_source() {
    local source=$1 device parent actual_uuid actual_label
    source=$(readlink -f "$source")
    mountpoint -q "$source" || fail "workspace source is not a mountpoint: $source"
    [[ $source != / && $source != /home && $source != "$MIGRATION"* && $source != "$MOUNT"* ]] || fail 'unsafe workspace source mountpoint.'
    device=$(findmnt -rn -M "$source" -o SOURCE)
    [[ -b $device ]] || fail 'workspace source is not a block device.'
    [[ $(blkid -s TYPE -o value "$device") == ext4 ]] || fail 'workspace source is not ext4.'
    actual_uuid=$(blkid -s UUID -o value "$device"); actual_label=$(blkid -s LABEL -o value "$device")
    [[ ${actual_uuid,,} == ${WORKSPACE_UUID,,} && $actual_label == "$WORKSPACE_LABEL" ]] || fail 'workspace source UUID/label differs from the installed identity.'
    if [[ $PROFILE == hardware ]]; then
        parent=$(lsblk -dnro PKNAME "$device" | tr -d '[:space:]')
        [[ -n $parent && $(readlink -f "/dev/$parent") == "$EXTRA_REAL" ]] || fail 'hardware workspace source is not on the identified extra disk.'
    else
        [[ $(readlink -f "$device") != "$ROOT_DEVICE" && $(readlink -f "$device") != "$HOME_DEVICE" && $(readlink -f "$device") != "$EXTRA_REAL" ]] || fail 'VM workspace source aliases root, home, or extra.'
    fi
    local nested
    nested=$(findmnt -rn -o TARGET | awk -v prefix="$source/" 'index($0,prefix)==1 {print}')
    [[ -z $nested ]] || fail "workspace has nested mounts that are not included: $nested"
    SOURCE_MOUNT=$source; SOURCE_DEVICE=$(readlink -f "$device")
}

copy_and_verify_migration() {
    local source_bytes available staged_bytes required
    [[ ! -e $STATE/initialize.started && ! -e $STATE/restore.done ]] || fail 'extra initialization has started; migration must remain unchanged.'
    [[ ! -L $MIGRATION && ! -L $DATA ]] || fail 'migration path and data directory must not be symlinks.'
    if [[ -e $MIGRATION ]]; then
        [[ -d $MIGRATION && $(stat -c '%u' "$MIGRATION") == 0 ]] || fail 'existing migration directory is unsafe.'
        [[ ! -e $DATA || ( -d $DATA && $(stat -c '%u' "$DATA") == 0 ) ]] || fail 'existing migration data path is unsafe.'
        [[ $(findmnt -rn -T "$MIGRATION" -o TARGET) == /home ]] || fail 'existing migration tree is no longer on encrypted /home.'
        chmod 0700 "$MIGRATION"
        [[ ! -d $DATA ]] || chmod 0700 "$DATA"
    fi
    source_bytes=$(du -sx --block-size=1 "$SOURCE_MOUNT" | awk 'NR==1 {print $1}')
    available=$(df -B1 --output=avail /home | awk 'NR==2 {print $1}')
    staged_bytes=0
    [[ ! -d $DATA ]] || staged_bytes=$(du -sx --block-size=1 "$DATA" | awk 'NR==1 {print $1}')
    [[ $source_bytes =~ ^[0-9]+$ && $available =~ ^[0-9]+$ && $staged_bytes =~ ^[0-9]+$ ]] || fail 'cannot measure workspace or encrypted /home space.'
    available=$((available + staged_bytes)); required=$((source_bytes + source_bytes / 20 + 536870912))
    [[ $available -ge $required ]] || fail "encrypted /home needs $required free bytes (5% plus 512 MiB reserve); available $available."
    [[ $required -lt $EXTRA_BYTES ]] || fail "workspace plus reserve exceeds extra disk capacity ($required required, $EXTRA_BYTES available)."
    printf 'workspace allocated=%s bytes; /home available=%s bytes; extra capacity=%s bytes\n' "$source_bytes" "$available" "$EXTRA_BYTES"
    mkdir -p "$DATA"; chown 0:0 "$MIGRATION" "$DATA"; chmod 0700 "$MIGRATION" "$DATA"
    printf 'started %s\nsource device=%s\nsource UUID=%s\nextra serial=%s\n' \
        "$(date -u +%FT%TZ)" "$SOURCE_DEVICE" "$WORKSPACE_UUID" "$EXTRA_SERIAL" > "$STATE/migration.started.tmp"
    sync "$STATE/migration.started.tmp"; mv "$STATE/migration.started.tmp" "$STATE/migration.started"; sync "$STATE"
    rsync -aHAX --numeric-ids --sparse --one-file-system --delete "$SOURCE_MOUNT/" "$DATA/"
    chown 0:0 "$MIGRATION" "$DATA"; chmod 0700 "$MIGRATION" "$DATA"
    tree_manifest "$DATA" "$STATE/workspace.sha256.tmp"
    sync "$STATE/workspace.sha256.tmp"; mv "$STATE/workspace.sha256.tmp" "$STATE/workspace.sha256"
    (cd "$DATA" && sha256sum --check "$STATE/workspace.sha256")
    local differences
    differences=$(rsync -aHAXnci --out-format='%i%f' --numeric-ids --one-file-system --delete "$SOURCE_MOUNT/" "$DATA/" | awk '$NF !~ /(^|\/)\.$/ {print}')
    [[ -z $differences ]] || fail "staging copy differs from the source: $differences"
    printf 'complete %s\nsource device=%s\nsource UUID=%s\nextra serial=%s\nworkspace allocated bytes=%s\nfiles=%s\n' \
        "$(date -u +%FT%TZ)" "$SOURCE_DEVICE" "$WORKSPACE_UUID" "$EXTRA_SERIAL" "$source_bytes" "$(wc -l < "$STATE/workspace.sha256")" > "$STATE/migration.done.tmp"
    sync "$STATE/migration.done.tmp"; mv "$STATE/migration.done.tmp" "$STATE/migration.done"; sync "$STATE"
}

verify_target_mount() {
    local source luks_uuid filesystem_uuid
    cryptsetup status "$MAPPER" >/dev/null 2>&1 || fail 'extra LUKS mapping is not open.'
    source=$(cryptsetup status "$MAPPER" | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
    [[ -b $source && $(readlink -f "$source") == "$(readlink -f "$EXTRA_PART")" ]] || fail 'extra mapper is backed by another partition.'
    mountpoint -q "$MOUNT" || fail '/extra is not mounted.'
    [[ $(readlink -f "$(findmnt -rn -M "$MOUNT" -o SOURCE)") == "$(readlink -f "/dev/mapper/$MAPPER")" ]] || fail '/extra is mounted from an unexpected device.'
    [[ $(blkid -s LABEL -o value "/dev/mapper/$MAPPER") == "$WORKSPACE_LABEL" ]] || fail '/extra filesystem lost the workspace label.'
    luks_uuid=$(cryptsetup luksUUID "$EXTRA_PART")
    filesystem_uuid=$(blkid -s UUID -o value "/dev/mapper/$MAPPER")
    [[ ! -s $STATE/luks.done ]] || grep -Fxq "LUKS UUID=$luks_uuid" "$STATE/luks.done" || fail 'LUKS UUID differs from its receipt.'
    [[ ! -s $STATE/filesystem.done ]] || grep -Fxq "UUID=$filesystem_uuid" "$STATE/filesystem.done" || fail 'filesystem UUID differs from its receipt.'
}

configure_openrc() {
    local uuid=$1 temp
    [[ $uuid =~ ^[A-Fa-f0-9-]{36}$ ]] || fail 'invalid extra LUKS UUID.'
    [[ ! -L /root/.keys && -f $KEY && ! -L $KEY ]] || fail 'extra key path must be a regular non-symlink file.'
    install -d -m 0700 /root/.keys /etc/conf.d /etc/init.d
    [[ $(stat -c '%u:%g:%a' "$KEY") == 0:0:400 ]] || fail 'extra key must remain root-owned mode 0400.'
    temp=$(mktemp /etc/conf.d/.ms1504-extra.XXXXXX)
    printf 'LUKS_UUID=%q\nMAPPER=%q\nKEY=%q\nMOUNT=%q\nLABEL=%q\n' "$uuid" "$MAPPER" "$KEY" "$MOUNT" "$WORKSPACE_LABEL" > "$temp"
    chmod 0600 "$temp"; sync "$temp"; mv "$temp" "$CONFIG"
    temp=$(mktemp /etc/init.d/.ms1504-extra.XXXXXX)
    cat > "$temp" <<'OPENRC'
#!/sbin/openrc-run
description="Unlock and mount the encrypted MS1504 workspace"

depend() { need localmount; }

load_config() {
    [ -f /etc/conf.d/ms1504-extra ] && [ ! -L /etc/conf.d/ms1504-extra ] || return 1
    [ "$(stat -c '%u:%g:%a' /etc/conf.d/ms1504-extra)" = 0:0:600 ] || return 1
    . /etc/conf.d/ms1504-extra
    [ "${#LUKS_UUID}" -eq 36 ] || return 1
    case "$LUKS_UUID" in *[!A-Fa-f0-9-]*) return 1 ;; esac
    case "$LABEL" in ''|*[!A-Za-z0-9._+-]*) return 1 ;; esac
    [ "${#LABEL}" -le 16 ] && [ "$MAPPER" = ms1504-extra ] && [ "$KEY" = /root/.keys/ms1504-extra.key ] && [ "$MOUNT" = /extra ]
}

start() {
    ebegin "Unlocking and mounting ${MOUNT:-/extra}"
    load_config || { eend 1 "unsafe extra configuration"; return 1; }
    [ "$(stat -c '%u:%g:%a' "$KEY")" = 0:0:400 ] || { eend 1 "unsafe extra key permissions"; return 1; }
    device=/dev/disk/by-uuid/$LUKS_UUID
    [ -b "$device" ] || { eend 1 "extra LUKS UUID is unavailable"; return 1; }
    if ! cryptsetup status "$MAPPER" >/dev/null 2>&1; then
        cryptsetup open --key-file "$KEY" "$device" "$MAPPER" || { eend 1; return 1; }
    fi
    source=$(cryptsetup status "$MAPPER" | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
    [ -n "$source" ] && [ "$(readlink -f "$source")" = "$(readlink -f "$device")" ] || { eend 1 "unexpected mapper source"; return 1; }
    [ "$(blkid -s TYPE -o value "/dev/mapper/$MAPPER")" = ext4 ] &&
        [ "$(blkid -s LABEL -o value "/dev/mapper/$MAPPER")" = "$LABEL" ] || { eend 1 "unexpected extra filesystem"; return 1; }
    mkdir -p "$MOUNT"
    if mountpoint -q "$MOUNT"; then
        mounted=$(findmnt -rn -M "$MOUNT" -o SOURCE)
        [ "$(readlink -f "$mounted")" = "$(readlink -f /dev/mapper/$MAPPER)" ] || { eend 1 "mountpoint occupied"; return 1; }
    else
        mount "/dev/mapper/$MAPPER" "$MOUNT" || { cryptsetup close "$MAPPER"; eend 1; return 1; }
    fi
    eend 0
}

stop() {
    ebegin "Unmounting ${MOUNT:-/extra}"
    load_config || { eend 1 "unsafe extra configuration"; return 1; }
    if mountpoint -q /build; then
        build_source=$(findmnt -rn -M /build -o SOURCE)
        if [ "$(readlink -f "$build_source")" = "$(readlink -f /dev/mapper/$MAPPER)" ]; then
            umount /build || { eend 1 "workspace mount is busy"; return 1; }
        fi
    fi
    if mountpoint -q "$MOUNT"; then
        mounted=$(findmnt -rn -M "$MOUNT" -o SOURCE)
        [ "$(readlink -f "$mounted")" = "$(readlink -f /dev/mapper/$MAPPER)" ] || { eend 1 "unexpected mount source"; return 1; }
        umount "$MOUNT" || { eend 1; return 1; }
    fi
    cryptsetup status "$MAPPER" >/dev/null 2>&1 && cryptsetup close "$MAPPER"
    eend 0
}
OPENRC
    chmod 0755 "$temp"; sync "$temp"; mv "$temp" "$SERVICE"
    rc-update add ms1504-extra default
    rc-service ms1504-extra start
}

resolve_devices
case "$MODE" in
    plan)
        [[ $# -eq 0 ]] || fail 'plan takes no additional arguments.'
        home_free=$(df -B1 --output=avail /home | awk 'NR==2 {print $1}')
        printf 'Profile: %s\nRoot disk: %s (%s)\nExtra disk: %s (%s; %s bytes)\nWorkspace: UUID=%s label=%s\n' \
            "$PROFILE" "$ROOT_DISK" "$ROOT_SERIAL" "$EXTRA_REAL" "$EXTRA_SERIAL" "$EXTRA_BYTES" "$WORKSPACE_UUID" "$WORKSPACE_LABEL"
        printf 'Migration destination: %s on encrypted /home (%s bytes currently free)\nEncrypted restore: %s; key: %s\nPlan only; no disk changes made.\n' \
            "$MIGRATION" "$home_free" "$MOUNT" "$KEY"
        ;;
    migrate)
        [[ $# -eq 2 && $1 == --source ]] || fail 'migrate requires --source MOUNT.'
        check_workspace_source "$2"
        copy_and_verify_migration
        echo 'Workspace copy and file manifest verified; source disk is unchanged.'
        ;;
    initialize)
        [[ $# -eq 2 && $1 == --source ]] || fail 'initialize requires --source MOUNT for a final content comparison.'
        validate_home_migration
        check_workspace_source "$2"
        differences=$(rsync -aHAXnci --out-format='%i%f' --numeric-ids --one-file-system --delete "$SOURCE_MOUNT/" "$DATA/" | awk '$NF !~ /(^|\/)\.$/ {print}')
        [[ -z $differences ]] || fail 'workspace changed after migration; rerun migrate and inspect its manifest.'
        [[ ! -e $STATE/initialize.started && ! -e $STATE/initialize.done ]] || fail 'initialization has already started; inspect state and use resume.'
        mountpoint -q "$MOUNT" && fail '/extra is mounted; stop its service and unmount before initialization.'
        cryptsetup status "$MAPPER" >/dev/null 2>&1 && fail 'extra mapper is already open; stop its service and inspect it before initialization.'
        [[ ! -e $KEY && ! -L $KEY ]] || fail 'extra key path already exists; inspect it rather than replacing it.'
        [[ ! -L /root/.keys ]] || fail 'root key directory must not be a symlink.'
        while read -r child; do
            [[ -n $child ]] || continue
            child_mount=$(findmnt -rn -S "$child" -o TARGET 2>/dev/null || true)
            [[ -z $child_mount || ( $child_mount == "$SOURCE_MOUNT" && $(readlink -f "$child") == "$SOURCE_DEVICE" ) ]] ||
                fail "extra disk child is mounted outside the verified workspace source: $child at $child_mount"
        done < <(lsblk -lnrpo NAME "$EXTRA_REAL")
        expected="ERASE-EXTRA $EXTRA_SERIAL $EXTRA_MAJMIN"
        partitions=$(lsblk -lnrpo NAME,TYPE "$EXTRA_REAL" | awk '$2 == "part" {print $1}')
        unexpected=$(lsblk -lnrpo TYPE "$EXTRA_REAL" | tail -n +2 | grep -Ev '^part$' || true)
        [[ -z $unexpected ]] || fail "extra disk has unexpected mapped descendants: $unexpected"
        if [[ $PROFILE == hardware ]]; then
            [[ $(printf '%s\n' "$partitions" | wc -l) -eq 1 && $(readlink -f "$partitions") == "$SOURCE_DEVICE" ]] ||
                fail 'hardware extra disk must contain only the verified mounted workspace partition.'
        else
            [[ -z $partitions ]] || fail 'VM extra target already has partitions; inspect it instead of reformatting.'
        fi
        printf 'This erases the identified extra disk after the workspace copy has been verified.\nType exactly: %s\n' "$expected" > /dev/tty
        IFS= read -r confirmation < /dev/tty
        [[ $confirmation == "$expected" ]] || fail 'confirmation mismatch; no disk changes requested.'
        printf 'started %s\nserial=%s\nsize=%s\nmajor:minor=%s\n' "$(date -u +%FT%TZ)" "$EXTRA_SERIAL" "$EXTRA_BYTES" "$EXTRA_MAJMIN" > "$STATE/initialize.started.tmp"
        sync "$STATE/initialize.started.tmp"; mv "$STATE/initialize.started.tmp" "$STATE/initialize.started"; sync "$STATE"
        install -d -m 0700 /root/.keys
        key_tmp="$KEY.tmp.$$"; dd if=/dev/urandom of="$key_tmp" bs=64 count=1 iflag=fullblock status=none
        chmod 0400 "$key_tmp"; sync "$key_tmp"; mv "$key_tmp" "$KEY"
        if [[ $PROFILE == hardware ]]; then umount "$SOURCE_MOUNT"; fi
        while read -r child; do [[ -z $child ]] || [[ -z $(findmnt -rn -S "$child" -o TARGET 2>/dev/null || true) ]] || fail "extra disk child remains mounted after workspace unmount: $child"; done < <(lsblk -lnrpo NAME "$EXTRA_REAL")
        sgdisk --zap-all --clear --new=1:0:0 --typecode=1:8309 --change-name=1:MS1504-EXTRA "$EXTRA_REAL"
        sync; blockdev --rereadpt "$EXTRA_REAL"; udevadm settle --timeout=30
        [[ -b $EXTRA_PART ]] || fail 'partition did not appear; leave the disk unchanged and inspect it.'
        printf 'partition UUID=%s\n' "$(blkid -s PARTUUID -o value "$EXTRA_PART")" > "$STATE/partition.done.tmp"
        sync "$STATE/partition.done.tmp"; mv "$STATE/partition.done.tmp" "$STATE/partition.done"; sync "$STATE"
        set --
        ;&
    resume)
        [[ $# -eq 0 ]] || fail 'resume takes no additional arguments.'
        [[ -s $STATE/initialize.started && -s $STATE/partition.done ]] || fail 'resume requires durable initialization and partition receipts; inspect before proceeding.'
        grep -Fxq "serial=$EXTRA_SERIAL" "$STATE/initialize.started" && grep -Fxq "size=$EXTRA_BYTES" "$STATE/initialize.started" &&
            grep -Fxq "major:minor=$EXTRA_MAJMIN" "$STATE/initialize.started" &&
            grep -Fxq "partition UUID=$(blkid -s PARTUUID -o value "$EXTRA_PART")" "$STATE/partition.done" || fail 'extra partition identity changed.'
        [[ -f $KEY && ! -L $KEY && $(stat -c '%u:%g:%a' "$KEY" 2>/dev/null || true) == 0:0:400 ]] || fail 'original root-only extra key is missing or unsafe.'
        if cryptsetup isLuks "$EXTRA_PART"; then
            cryptsetup open --test-passphrase --key-file "$KEY" "$EXTRA_PART" || fail 'LUKS header does not match the recorded key.'
            luks_uuid=$(cryptsetup luksUUID "$EXTRA_PART")
            if [[ -s $STATE/luks.done ]]; then
                grep -Fxq "LUKS UUID=$luks_uuid" "$STATE/luks.done" || fail 'LUKS UUID differs from its durable receipt.'
            else
                printf 'LUKS UUID=%s\n' "$luks_uuid" > "$STATE/luks.done.tmp"
                sync "$STATE/luks.done.tmp"; mv "$STATE/luks.done.tmp" "$STATE/luks.done"; sync "$STATE"
            fi
        else
            [[ ! -e $STATE/luks.done ]] || fail 'LUKS header is missing despite a completion receipt; inspect manually.'
            [[ ! -e $STATE/luks.started ]] || fail 'LUKS formatting was interrupted without a readable header; inspect manually before any reformat.'
            printf 'started %s\n' "$(date -u +%FT%TZ)" > "$STATE/luks.started"; sync "$STATE/luks.started"
            cryptsetup luksFormat --batch-mode --type luks2 --pbkdf argon2id --label MS1504-EXTRA --key-file "$KEY" "$EXTRA_PART"
            cryptsetup isLuks "$EXTRA_PART" || fail 'LUKS header verification failed.'
            printf 'LUKS UUID=%s\n' "$(cryptsetup luksUUID "$EXTRA_PART")" > "$STATE/luks.done.tmp"
            sync "$STATE/luks.done.tmp"; mv "$STATE/luks.done.tmp" "$STATE/luks.done"; sync "$STATE"
        fi
        if ! cryptsetup status "$MAPPER" >/dev/null 2>&1; then cryptsetup open --key-file "$KEY" "$EXTRA_PART" "$MAPPER"; fi
        mapper_source=$(cryptsetup status "$MAPPER" | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
        [[ -n $mapper_source && $(readlink -f "$mapper_source") == "$(readlink -f "$EXTRA_PART")" ]] || fail 'existing extra mapper belongs to another partition.'
        mkdir -p "$MOUNT"
        filesystem=$(blkid -s TYPE -o value "/dev/mapper/$MAPPER" 2>/dev/null || true)
        if [[ $filesystem == ext4 ]]; then
            [[ $(blkid -s LABEL -o value "/dev/mapper/$MAPPER") == "$WORKSPACE_LABEL" ]] || fail 'existing extra filesystem has an unexpected label.'
        elif [[ -z $filesystem ]]; then
            [[ ! -e $STATE/filesystem.done ]] || fail 'filesystem is missing despite a completion receipt; inspect manually.'
            [[ ! -e $STATE/filesystem.started ]] || fail 'filesystem formatting was interrupted; inspect the mapper manually.'
            printf 'started %s\n' "$(date -u +%FT%TZ)" > "$STATE/filesystem.started"; sync "$STATE/filesystem.started"
            mkfs.ext4 -F -m 0 -L "$WORKSPACE_LABEL" "/dev/mapper/$MAPPER"
        else fail "unexpected filesystem on the extra mapper: $filesystem"; fi
        blkid -s TYPE -o value "/dev/mapper/$MAPPER" | grep -Fxq ext4 || fail 'extra filesystem verification failed.'
        filesystem_uuid=$(blkid -s UUID -o value "/dev/mapper/$MAPPER")
        if [[ -s $STATE/filesystem.done ]]; then
            grep -Fxq "label=$WORKSPACE_LABEL" "$STATE/filesystem.done" && grep -Fxq "UUID=$filesystem_uuid" "$STATE/filesystem.done" || fail 'filesystem identity differs from its durable receipt.'
        else
            printf 'label=%s\nUUID=%s\n' "$WORKSPACE_LABEL" "$filesystem_uuid" > "$STATE/filesystem.done.tmp"
            sync "$STATE/filesystem.done.tmp"; mv "$STATE/filesystem.done.tmp" "$STATE/filesystem.done"; sync "$STATE"
        fi
        echo 'Encrypted extra filesystem is initialized; use restore to copy and verify the workspace.'
        ;;
    restore)
        [[ $# -eq 0 ]] || fail 'restore takes no additional arguments.'
        validate_home_migration
        [[ -s $STATE/filesystem.done && -f $KEY && ! -L $KEY ]] || fail 'encrypted extra filesystem is not initialized; use resume.'
        if ! cryptsetup status "$MAPPER" >/dev/null 2>&1; then cryptsetup open --key-file "$KEY" "$EXTRA_PART" "$MAPPER"; fi
        [[ -b /dev/mapper/$MAPPER ]] || fail 'extra mapper is unavailable.'
        mapper_source=$(cryptsetup status "$MAPPER" | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
        [[ -n $mapper_source && $(readlink -f "$mapper_source") == "$(readlink -f "$EXTRA_PART")" ]] || fail 'existing extra mapper belongs to another partition.'
        [[ $(blkid -s TYPE -o value "/dev/mapper/$MAPPER") == ext4 && $(blkid -s LABEL -o value "/dev/mapper/$MAPPER") == "$WORKSPACE_LABEL" ]] || fail 'extra mapper does not contain the expected workspace filesystem.'
        if mountpoint -q "$MOUNT"; then verify_target_mount; else mount "/dev/mapper/$MAPPER" "$MOUNT"; fi
        verify_target_mount
        required=$(sed -n 's/^workspace allocated bytes=//p' "$STATE/migration.done")
        [[ $required =~ ^[0-9]+$ ]] || fail 'migration receipt has no valid workspace size.'
        required=$((required + required / 20 + 536870912))
        available=$(df -B1 --output=avail "$MOUNT" | awk 'NR==2 {print $1}')
        [[ $available =~ ^[0-9]+$ && $available -ge $required ]] || fail "actual /extra filesystem free space is insufficient: need $required bytes including reserve, have ${available:-unknown}."
        rsync -aHAX --numeric-ids --sparse --one-file-system --delete --exclude=/lost+found "$DATA/" "$MOUNT/"
        (cd "$MOUNT" && sha256sum --check "$STATE/workspace.sha256")
        differences=$(rsync -aHAXnci --out-format='%i%f' --exclude=/lost+found --numeric-ids --one-file-system --delete "$DATA/" "$MOUNT/" | awk '$NF !~ /(^|\/)\.$/ {print}')
        [[ -z $differences ]] || fail "restored /extra differs from verified migration: $differences"
        verify_target_mount
        configure_openrc "$(cryptsetup luksUUID "$EXTRA_PART")"
        verify_target_mount
        printf 'complete %s\nLUKS UUID=%s\nextra serial=%s\nmanifest sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$(cryptsetup luksUUID "$EXTRA_PART")" "$EXTRA_SERIAL" "$(sha256sum "$STATE/workspace.sha256" | awk '{print $1}')" > "$STATE/restore.done.tmp"
        sync "$STATE/restore.done.tmp"; mv "$STATE/restore.done.tmp" "$STATE/restore.done"; sync "$STATE"
        echo '/extra restored, verified and configured for OpenRC unlock/mount.'
        ;;
    verify)
        [[ $# -eq 0 ]] || fail 'verify takes no additional arguments.'
        [[ -s $STATE/restore.done && -s $STATE/workspace.sha256 && -f $KEY && ! -L $KEY && -f $CONFIG && -f $SERVICE ]] || fail 'restore has not completed or its manifest is missing.'
        grep -Fxq "extra serial=$EXTRA_SERIAL" "$STATE/restore.done" &&
            grep -Fxq "LUKS UUID=$(cryptsetup luksUUID "$EXTRA_PART")" "$STATE/restore.done" &&
            grep -Fxq "manifest sha256=$(sha256sum "$STATE/workspace.sha256" | awk '{print $1}')" "$STATE/restore.done" || fail 'restore receipt does not match current extra identity or manifest.'
        if ! cryptsetup status "$MAPPER" >/dev/null 2>&1 || ! mountpoint -q "$MOUNT"; then rc-service ms1504-extra start; fi
        verify_target_mount
        (cd "$MOUNT" && sha256sum --check --status "$STATE/workspace.sha256") || fail '/extra contents differ from the verified workspace manifest.'
        if [[ -d $DATA ]]; then
            differences=$(rsync -aHAXnci --out-format='%i%f' --exclude=/lost+found --numeric-ids --one-file-system --delete "$DATA/" "$MOUNT/" | awk '$NF !~ /(^|\/)\.$/ {print}')
            [[ -z $differences ]] || fail "/extra tree differs from retained migration data: $differences"
        fi
        printf 'complete %s\nfile manifest and rsync tree comparison=passed\n' "$(date -u +%FT%TZ)" > "$STATE/verify.done.tmp"
        sync "$STATE/verify.done.tmp"; mv "$STATE/verify.done.tmp" "$STATE/verify.done"; sync "$STATE"
        echo 'Encrypted /extra identity, mount, key permissions, file hashes and tree metadata verify.'
        ;;
    discard-migration)
        [[ $# -eq 0 ]] || fail 'discard-migration takes no additional arguments.'
        if [[ -s $STATE/migration-discarded.started && ! -e $MIGRATION && ! -L $MIGRATION ]]; then
            grep -Fxq "extra serial=$EXTRA_SERIAL" "$STATE/migration-discarded.started" || fail 'cleanup receipt belongs to another extra disk.'
            verify_target_mount
            (cd "$MOUNT" && sha256sum --check --status "$STATE/workspace.sha256") || fail 'restored /extra changed after migration cleanup began.'
            printf 'complete %s\nremoved after verified encrypted /extra restore\n' "$(date -u +%FT%TZ)" > "$STATE/migration-discarded.done.tmp"
            sync "$STATE/migration-discarded.done.tmp"; mv "$STATE/migration-discarded.done.tmp" "$STATE/migration-discarded.done"; sync "$STATE"
            echo 'Migration cleanup receipt completed; verified data remain on encrypted /extra.'
            exit 0
        fi
        [[ -s $STATE/verify.done && -s $STATE/restore.done ]] || fail 'verify the restored /extra before deleting migration data.'
        grep -Fxq "extra serial=$EXTRA_SERIAL" "$STATE/restore.done" &&
            grep -Fxq "LUKS UUID=$(cryptsetup luksUUID "$EXTRA_PART")" "$STATE/restore.done" || fail 'restore receipt belongs to another extra device.'
        validate_home_migration; verify_target_mount
        (cd "$MOUNT" && sha256sum --check --status "$STATE/workspace.sha256") || fail 'restored /extra no longer matches the migration manifest.'
        differences=$(rsync -aHAXnci --out-format='%i%f' --exclude=/lost+found --numeric-ids --one-file-system --delete "$DATA/" "$MOUNT/" | awk '$NF !~ /(^|\/)\.$/ {print}')
        [[ -z $differences ]] || fail "restored /extra changed since verification: $differences"
        expected="DELETE-MIGRATION $EXTRA_SERIAL"
        printf 'This removes the temporary workspace copy from encrypted /home.\nType exactly: %s\n' "$expected" > /dev/tty
        IFS= read -r confirmation < /dev/tty
        [[ $confirmation == "$expected" ]] || fail 'confirmation mismatch; migration retained.'
        printf 'started %s\nextra serial=%s\n' "$(date -u +%FT%TZ)" "$EXTRA_SERIAL" > "$STATE/migration-discarded.started.tmp"
        sync "$STATE/migration-discarded.started.tmp"; mv "$STATE/migration-discarded.started.tmp" "$STATE/migration-discarded.started"; sync "$STATE"
        rm -rf --one-file-system -- "$MIGRATION"
        printf 'complete %s\nremoved after verified encrypted /extra restore\n' "$(date -u +%FT%TZ)" > "$STATE/migration-discarded.done.tmp"
        sync "$STATE/migration-discarded.done.tmp"; mv "$STATE/migration-discarded.done.tmp" "$STATE/migration-discarded.done"; sync "$STATE"
        echo 'Temporary migration copy removed; verified data remain on encrypted /extra.'
        ;;
esac
