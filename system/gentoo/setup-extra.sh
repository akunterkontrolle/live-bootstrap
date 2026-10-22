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
    --retention-plan|retention-plan) MODE=retention-plan ;;
    --discard-migration|discard-migration) MODE=discard-migration ;;
    *) echo 'Usage: ms1504-extra {plan|retention-plan --source MOUNT|migrate [--source MOUNT]|initialize --source MOUNT|resume|restore|verify|discard-migration}' >&2; exit 2 ;; esac

. "$(dirname -- "$(readlink -f "$0")")/lib/devices.sh"
gentoo_die() { fail "$@"; }

fail() { printf 'ms1504-extra: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"; }
for command in wipefs mktemp cmp python3 awk blkid blockdev chmod chown cryptsetup date dd df du find findmnt flock grep id install lsblk lvs mkdir mkfs.ext4 mount mountpoint mv rc-service rc-update readlink rm rsync sed sha256sum sgdisk sort stat sync tail tr udevadm umount wc xargs; do need "$command"; done
[ "$(id -u)" = 0 ] || fail 'run as root.'
case "$-" in *x*) fail 'disable shell tracing before using the extra-disk helper.' ;; esac

IDENTITY=/etc/ms1504-extra-identity
ROOT_IDENTITY=/etc/ms1504-root-identity
STATE_BASE=/var/lib/ms1504-extra
MIGRATION_BASE=/home/.ms1504-workspace-migration
STATE=$STATE_BASE
MIGRATION=$MIGRATION_BASE
RETENTION_TOOL="$(dirname -- "$(readlink -f "$0")")/workspace-retention.py"
RETENTION_POLICY=full
RSYNC_SELECTION=()
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
if [[ $MODE != plan && $MODE != retention-plan ]]; then
    mkdir -p "$STATE"; chmod 0700 "$STATE"
    [[ $(stat -c '%u:%g:%a' "$STATE") == 0:0:700 ]] || fail 'state directory ownership or mode is unsafe.'
    exec 9>"$STATE/lock"
    flock -n 9 || fail 'another extra-disk operation is running.'
fi
[[ ! -L $MOUNT ]] || fail '/extra mountpoint must not be a symlink.'

# The base lock serializes old and selected migration journals alike.
use_selected_migration() {
    STATE=$STATE_BASE/installed-continuation-v1
    MIGRATION=$MIGRATION_BASE/installed-continuation-v1
    DATA=$MIGRATION/data
    RETENTION_POLICY=installed-continuation-v1
    [[ ! -L $STATE && ! -L $MIGRATION && ! -L $MIGRATION_BASE ]] || fail 'selected migration paths are unsafe.'
    local excludes marker
    for marker in initialize.started initialize.done partition.done luks.started luks.done filesystem.started filesystem.done restore.done; do
        [[ ! -e $STATE_BASE/$marker ]] || fail 'legacy initialization conflicts with the selected migration journal.'
    done
    excludes=$(python3 "$RETENTION_TOOL" excludes) || fail 'cannot read retention policy.'
    RSYNC_SELECTION=()
    while IFS= read -r pattern; do RSYNC_SELECTION+=("--exclude=$pattern"); done <<< "$excludes"
}

check_retention_policy() {
    [[ $RETENTION_POLICY != full ]] || return 0
    [[ -f $STATE/retention-policy.json && ! -L $STATE/retention-policy.json ]] || fail 'selected migration policy receipt is missing.'
    local current recorded
    current=$(python3 "$RETENTION_TOOL" policy) || fail 'cannot read current retention policy.'
    recorded=$(cat "$STATE/retention-policy.json")
    [[ $current == "$recorded" ]] || fail 'retention policy changed; do not reuse this migration journal.'
}

if [[ -e $STATE_BASE/active-migration || -L $STATE_BASE/active-migration ]]; then
    [[ -f $STATE_BASE/active-migration && ! -L $STATE_BASE/active-migration &&
        $(stat -c '%u:%g:%a' "$STATE_BASE/active-migration") == 0:0:600 &&
        $(cat "$STATE_BASE/active-migration") == installed-continuation-v1 ]] || fail 'invalid active migration selector.'
    use_selected_migration
    check_retention_policy
fi

check_installed_continuation() {
    python3 "$RETENTION_TOOL" installed
    python3 "$(dirname "$RETENTION_TOOL")/cpu-policy.py" \
        --receipt /etc/portage/native-cpu-policy.json --detector /usr/bin/cpuid2cpuflags --compiler /usr/bin/gcc
}

select_migration_policy() {
    [[ $RETENTION_POLICY == full ]] || return 0
    local marker
    for marker in initialize.started initialize.done partition.done luks.started luks.done filesystem.started filesystem.done restore.done; do
        [[ ! -e $STATE_BASE/$marker ]] || fail 'legacy extra initialization has started; retaining its full-migration journal.'
    done
    # Keep all legacy receipts and the legacy /data tree separate and unchanged.
    use_selected_migration
    mkdir -p "$STATE"; chmod 0700 "$STATE"
    [[ $(stat -c '%u:%g:%a' "$STATE") == 0:0:700 ]] || fail 'selected state ownership is unsafe.'
    if [[ -e $STATE/retention-policy.json ]]; then
        check_retention_policy
    else
        python3 "$RETENTION_TOOL" policy > "$STATE/retention-policy.json.tmp"
        chmod 0600 "$STATE/retention-policy.json.tmp"
        sync "$STATE/retention-policy.json.tmp"
        mv "$STATE/retention-policy.json.tmp" "$STATE/retention-policy.json"
    fi
    printf 'installed-continuation-v1\n' > "$STATE_BASE/active-migration.tmp"
    chmod 0600 "$STATE_BASE/active-migration.tmp"; sync "$STATE_BASE/active-migration.tmp"
    mv "$STATE_BASE/active-migration.tmp" "$STATE_BASE/active-migration"; sync "$STATE_BASE"
}

check_retained_source() {
    [[ $RETENTION_POLICY != full ]] || return 0
    check_retention_policy
    python3 "$RETENTION_TOOL" snapshot "$SOURCE_MOUNT" > "$STATE/source.snapshot.tmp"
    if [[ -e $STATE/source.snapshot ]]; then
        cmp -s "$STATE/source.snapshot" "$STATE/source.snapshot.tmp" || fail 'retained source changed since migration started; preserve the journal and inspect it.'
        rm "$STATE/source.snapshot.tmp"
    else
        [[ ! -e $STATE/migration.started ]] || fail 'started migration lacks its retained source snapshot.'
        sync "$STATE/source.snapshot.tmp"; mv "$STATE/source.snapshot.tmp" "$STATE/source.snapshot"; sync "$STATE"
    fi
}

workspace_copy_differences() {
    rsync -aHAXnci --modify-window=-1 --out-format='%i%f' --numeric-ids --one-file-system --delete \
        "${RSYNC_SELECTION[@]}" "$SOURCE_MOUNT/" "$DATA/" | awk '$NF !~ /(^|\/)\.$/ {print}'
}

disk_serial() {
    local disk=${1} value
    # Use the same sysfs/VPD fallback as bootstrap discovery when udev has
    # not populated SERIAL (notably QEMU USB storage after installed boot).
    value=$(gentoo_disk_serial "$disk") || fail "cannot read disk serial: $disk"
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
    # lvs expects an LV name; /dev/dm-N may be parsed as a VG name.
    root_devs=$(lvs --noheadings --options devices "$VG/$ROOT_LV" 9>&- | tr -d '[:space:]')
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
    check_retention_policy
    [[ ! -L $MIGRATION && ! -L $DATA && -d $MIGRATION && -d $DATA ]] || fail 'migration staging tree is missing or unsafe.'
    [[ $(stat -c '%u:%g:%a' "$MIGRATION") == 0:0:700 && $(stat -c '%u:%g:%a' "$DATA") == 0:0:700 ]] || fail 'migration staging must be root-owned mode 0700.'
    [[ $(findmnt -rn -T "$DATA" -o TARGET) == /home ]] || fail 'migration data is not stored on /home.'
    [[ -s $STATE/migration.done && -s $STATE/workspace.sha256 ]] || fail 'workspace migration is incomplete.'
    grep -Fxq "extra serial=$EXTRA_SERIAL" "$STATE/migration.done" || fail 'migration belongs to another extra disk.'
    (cd "$DATA" && sha256sum --check --status "$STATE/workspace.sha256") || fail 'preserved migration data failed its file hash manifest.'
    if [[ $RETENTION_POLICY != full ]]; then
        grep -Fxq "retention policy sha256=$(sha256sum "$STATE/retention-policy.json" | awk '{print $1}')" "$STATE/migration.done" || fail 'migration receipt has a different retention policy.'
        python3 "$RETENTION_TOOL" snapshot --strict "$DATA" > "$STATE/staging.snapshot.tmp"
        cmp -s "$STATE/source.snapshot" "$STATE/staging.snapshot.tmp" || fail 'retained staging metadata or content changed.'
        rm "$STATE/staging.snapshot.tmp"
    fi
}

check_workspace_source() {
    local source=$1 device parent actual_uuid actual_label source_options aliases
    source=$(readlink -f "$source")
    mountpoint -q "$source" || fail "workspace source is not a mountpoint: $source"
    [[ $source != / && $source != /home && $source != "$MIGRATION"* && $source != "$MOUNT"* ]] || fail 'unsafe workspace source mountpoint.'
    device=$(findmnt -rn -M "$source" -o SOURCE)
    [[ -b $device ]] || fail 'workspace source is not a block device.'
    source_options=$(findmnt -rn -M "$source" -o OPTIONS)
    [[ ,$source_options, == *,ro,* ]] || fail 'workspace source must be mounted read-only.'
    aliases=$(findmnt -rn -S "$device" -o TARGET)
    [[ $aliases == "$source" ]] || fail 'workspace source is also mounted elsewhere; unmount aliases before migration.'
    [[ $(blkid -p -D -s TYPE -o value "$device") == ext4 ]] || fail 'workspace source is not ext4.'
    actual_uuid=$(blkid -p -D -s UUID -o value "$device"); actual_label=$(blkid -p -D -s LABEL -o value "$device")
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

mount_recorded_hardware_workspace() {
    local candidates device parent mounts target options entries
    [[ $PROFILE == hardware ]] || fail 'VM migration requires --source MOUNT.'
    [[ ! -e $STATE/initialize.started && ! -e $STATE/restore.done ]] || fail 'extra initialization has started; inspect existing migration state.'
    candidates=$(blkid -c /dev/null -t "UUID=$WORKSPACE_UUID" -o device) || fail 'recorded workspace UUID is unavailable.'
    [[ -n $candidates && $(printf '%s\n' "$candidates" | wc -l) -eq 1 ]] || fail 'recorded workspace UUID is ambiguous.'
    device=$(readlink -f "$candidates")
    [[ -b $device ]] || fail 'recorded workspace is not a block device.'
    [[ $(blkid -p -D -s TYPE -o value "$device") == ext4 && $(blkid -p -D -s LABEL -o value "$device") == "$WORKSPACE_LABEL" ]] || fail 'recorded workspace type or label differs.'
    parent=$(lsblk -dnro PKNAME "$device" | tr -d '[:space:]')
    [[ -n $parent && $(readlink -f "/dev/$parent") == "$EXTRA_REAL" ]] || fail 'recorded workspace is not on the identified extra disk.'
    target=/mnt/gentoo-workspace
    [[ -d /mnt && ! -L /mnt && ! -L $target ]] || fail 'workspace mount path is unsafe.'
    # findmnt returns 1 for no match; other errors must not authorize a mount.
    if mounts=$(findmnt -rn -S "$device" -o TARGET); then
        [[ $mounts == "$target" ]] || fail 'workspace is already mounted elsewhere; unmount it before automatic migration.'
    else
        [[ $? -eq 1 ]] || fail 'cannot inspect existing workspace mounts.'
        [[ ! -e $target || -d $target ]] || fail 'workspace mount path is not a directory.'
        mountpoint -q "$target" && fail 'workspace mountpoint is occupied.'
        if [[ -d $target ]]; then
            entries=$(find "$target" -mindepth 1 -maxdepth 1 -print -quit) || fail 'cannot inspect workspace mountpoint.'
            [[ -z $entries ]] || fail 'workspace mountpoint is not empty.'
            [[ $(stat -c '%u:%g:%a' "$target") == 0:0:755 ]] || fail 'workspace mountpoint must be root-owned mode 0755.'
        fi
        install -d -m 0755 "$target"
        # noload prevents ext4 journal replay from writing through a read-only mount.
        mount -t ext4 -o ro,noload "$device" "$target"
    fi
    options=$(findmnt -rn -M "$target" -o OPTIONS)
    [[ ,$options, == *,ro,* ]] || fail 'workspace source must be mounted read-only.'
    check_workspace_source "$target"
    printf 'Recorded workspace mounted read-only at %s; retained for final initialization comparison.\n' "$target"
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
    if [[ $RETENTION_POLICY == full ]]; then
        source_bytes=$(du -sx --block-size=1 "$SOURCE_MOUNT" | awk 'NR==1 {print $1}')
    else
        python3 "$RETENTION_TOOL" report "$SOURCE_MOUNT"
        source_bytes=$(python3 "$RETENTION_TOOL" bytes "$SOURCE_MOUNT")
        check_retained_source
    fi
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
    rsync -aHAXc --modify-window=-1 --numeric-ids --sparse --one-file-system --delete "${RSYNC_SELECTION[@]}" "$SOURCE_MOUNT/" "$DATA/"
    chown 0:0 "$MIGRATION" "$DATA"; chmod 0700 "$MIGRATION" "$DATA"
    tree_manifest "$DATA" "$STATE/workspace.sha256.tmp"
    sync "$STATE/workspace.sha256.tmp"; mv "$STATE/workspace.sha256.tmp" "$STATE/workspace.sha256"
    (cd "$DATA" && sha256sum --check "$STATE/workspace.sha256")
    local differences
    differences=$(workspace_copy_differences)
    [[ -z $differences ]] || fail "staging copy differs from the source: $differences"
    check_retained_source
    if [[ $RETENTION_POLICY != full ]]; then
        python3 "$RETENTION_TOOL" snapshot --strict "$DATA" > "$STATE/staging.snapshot.tmp"
        cmp -s "$STATE/source.snapshot" "$STATE/staging.snapshot.tmp" || fail 'retained staging content or metadata differs from source.'
        rm "$STATE/staging.snapshot.tmp"
    fi
    printf 'complete %s\nsource device=%s\nsource UUID=%s\nextra serial=%s\nworkspace allocated bytes=%s\nfiles=%s\n' \
        "$(date -u +%FT%TZ)" "$SOURCE_DEVICE" "$WORKSPACE_UUID" "$EXTRA_SERIAL" "$source_bytes" "$(wc -l < "$STATE/workspace.sha256")" > "$STATE/migration.done.tmp"
    if [[ $RETENTION_POLICY != full ]]; then
        printf 'retention policy sha256=%s\n' "$(sha256sum "$STATE/retention-policy.json" | awk '{print $1}')" >> "$STATE/migration.done.tmp"
    fi
    sync "$STATE/migration.done.tmp"; mv "$STATE/migration.done.tmp" "$STATE/migration.done"; sync "$STATE"
}

extra_probe_type() {
    local device=$1 status=0
    EXTRA_SIGNATURE=$(blkid -p -D -s TYPE -o value "$device") || status=$?
    case $status in
        0) [[ -n $EXTRA_SIGNATURE ]] || fail "empty successful signature probe: $device" ;;
        2) [[ -z $EXTRA_SIGNATURE ]] || fail "inconsistent absent signature probe: $device" ;;
        *) fail "signature probe failed ($status): $device; refusing to infer blank storage." ;;
    esac
}

extra_require_blank() {
    local device=$1 signatures
    extra_probe_type "$device"
    [[ -z $EXTRA_SIGNATURE ]] || fail "existing signature on $device: $EXTRA_SIGNATURE"
    signatures=$(wipefs --no-act --noheadings --output TYPE "$device") || fail "cannot inspect signatures: $device"
    [[ -z $signatures ]] || fail "storage is not verified blank: $device"
}

extra_check_mounts() {
    local allowed=${1:-} table children child major targets
    table=$(findmnt -rn -o MAJ:MIN,TARGET) || fail 'cannot inspect mounted filesystems.'
    [[ -n $table ]] || fail 'mount inventory is empty.'
    children=$(lsblk -nrpo NAME "$EXTRA_REAL") || fail 'cannot inspect extra-disk descendants.'
    [[ ${children%%$'\n'*} == "$EXTRA_REAL" ]] || fail 'invalid extra-disk descendant inventory.'
    while IFS= read -r child; do
        major=$(lsblk -dnro MAJ:MIN "$child") || fail 'cannot inspect descendant device identity.'
        [[ $major =~ ^[0-9]+:[0-9]+$ ]] || fail 'invalid descendant device identity.'
        targets=$(printf '%s\n' "$table" | awk -v id="$major" '$1 == id {print $2}')
        [[ -z $targets || ( -n $allowed && $targets == "$allowed" && "$child" == "$SOURCE_DEVICE" ) ]] ||
            fail "extra-disk descendant is mounted: $child at $targets"
    done <<< "$children"
}

extra_partition_identity() {
    local parent type number label
    [[ -b $EXTRA_PART ]] || fail 'extra partition is unavailable.'
    parent=$(lsblk -dnro PKNAME "$EXTRA_PART") || fail 'cannot read extra partition parent.'
    [[ -n $parent && $(readlink -f "/dev/$parent") == "$EXTRA_REAL" ]] || fail 'extra partition has a different physical parent.'
    type=$(blkid -p -s PART_ENTRY_TYPE -o value "$EXTRA_PART") || fail 'cannot read extra partition type.'
    number=$(blkid -p -s PART_ENTRY_NUMBER -o value "$EXTRA_PART") || fail 'cannot read extra partition number.'
    label=$(blkid -p -s PART_ENTRY_NAME -o value "$EXTRA_PART") || fail 'cannot read extra partition label.'
    EXTRA_PART_UUID=$(blkid -p -s PART_ENTRY_UUID -o value "$EXTRA_PART") || fail 'cannot read extra partition UUID.'
    [[ ${type,,} == ca7d7ccb-63ed-4c53-861c-1742536059cc && $number == 1 && $label == MS1504-EXTRA &&
        $EXTRA_PART_UUID =~ ^[A-Fa-f0-9-]{36}$ ]] || fail 'extra GPT partition identity differs.'
}

extra_verify_partition_receipt() {
    extra_partition_identity
    [[ -s $STATE/partition.done ]] && grep -Fxq "partition UUID=$EXTRA_PART_UUID" "$STATE/partition.done" ||
        fail 'extra partition differs from its durable receipt.'
}

extra_verify_key() {
    local key_digest
    [[ -f $KEY && ! -L $KEY && $(stat -c '%u:%g:%a:%s' "$KEY") == 0:0:400:64 ]] || fail 'original 64-byte root-only extra key is missing or unsafe.'
    if [[ $RETENTION_POLICY != full || -e $STATE/key.done ]]; then
        [[ -f $STATE/key.done && ! -L $STATE/key.done ]] || fail 'extra key receipt is missing.'
        key_digest=$(sha256sum "$KEY") || fail 'cannot hash the original extra key.'
        key_digest=${key_digest%% *}
        [[ $key_digest =~ ^[0-9a-f]{64}$ ]] || fail 'invalid extra key digest.'
        grep -Fxq "key sha256=$key_digest" "$STATE/key.done" || fail 'extra key differs from its durable receipt.'
    fi
}

verify_target_mount() {
    local source luks_uuid filesystem_uuid
    extra_verify_partition_receipt
    extra_verify_key
    cryptsetup status "$MAPPER" >/dev/null 2>&1 || fail 'extra LUKS mapping is not open.'
    source=$(cryptsetup status "$MAPPER" | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
    [[ -b $source && $(readlink -f "$source") == "$(readlink -f "$EXTRA_PART")" ]] || fail 'extra mapper is backed by another partition.'
    mountpoint -q "$MOUNT" || fail '/extra is not mounted.'
    [[ $(readlink -f "$(findmnt -rn -M "$MOUNT" -o SOURCE)") == "$(readlink -f "/dev/mapper/$MAPPER")" ]] || fail '/extra is mounted from an unexpected device.'
    [[ $(blkid -p -D -s LABEL -o value "/dev/mapper/$MAPPER") == "$WORKSPACE_LABEL" ]] || fail '/extra filesystem lost the workspace label.'
    luks_uuid=$(cryptsetup luksUUID "$EXTRA_PART")
    filesystem_uuid=$(blkid -p -D -s UUID -o value "/dev/mapper/$MAPPER")
    [[ -s $STATE/luks.done ]] && grep -Fxq "LUKS UUID=$luks_uuid" "$STATE/luks.done" || fail 'LUKS UUID differs from its receipt.'
    [[ -s $STATE/filesystem.done ]] && grep -Fxq "UUID=$filesystem_uuid" "$STATE/filesystem.done" || fail 'filesystem UUID differs from its receipt.'
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
    [ "$(blkid -p -D -s TYPE -o value "/dev/mapper/$MAPPER")" = ext4 ] &&
        [ "$(blkid -p -D -s LABEL -o value "/dev/mapper/$MAPPER")" = "$LABEL" ] || { eend 1 "unexpected extra filesystem"; return 1; }
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
        printf 'Migration policy: %s\n' "$RETENTION_POLICY"
        printf 'Migration destination: %s on encrypted /home (%s bytes currently free)\nEncrypted restore: %s; key: %s\nPlan only; no disk changes made.\n' \
            "$MIGRATION" "$home_free" "$MOUNT" "$KEY"
        ;;
    retention-plan)
        [[ $# -eq 2 && $1 == --source ]] || fail 'retention-plan requires --source MOUNT.'
        check_workspace_source "$2"
        python3 "$RETENTION_TOOL" report "$SOURCE_MOUNT"
        ;;
    migrate)
        if [[ $# -eq 0 ]]; then
            mount_recorded_hardware_workspace
        else
            [[ $# -eq 2 && $1 == --source ]] || fail 'usage: migrate [--source MOUNT].'
            check_workspace_source "$2"
        fi
        check_installed_continuation
        select_migration_policy
        copy_and_verify_migration
        echo 'Workspace copy and file manifest verified; source disk is unchanged.'
        ;;
    initialize)
        [[ $# -eq 2 && $1 == --source ]] || fail 'initialize requires --source MOUNT for a final content comparison.'
        validate_home_migration
        check_workspace_source "$2"
        check_retained_source
        differences=$(workspace_copy_differences)
        [[ -z $differences ]] || fail 'workspace changed after migration; rerun migrate and inspect its manifest.'
        [[ ! -e $STATE/initialize.started && ! -e $STATE/initialize.done ]] || fail 'initialization has already started; inspect state and use resume.'
        mountpoint -q "$MOUNT" && fail '/extra is mounted; stop its service and unmount before initialization.'
        cryptsetup status "$MAPPER" >/dev/null 2>&1 && fail 'extra mapper is already open; stop its service and inspect it before initialization.'
        [[ ! -e $KEY && ! -L $KEY ]] || fail 'extra key path already exists; inspect it rather than replacing it.'
        [[ ! -L /root/.keys ]] || fail 'root key directory must not be a symlink.'
        extra_check_mounts "$SOURCE_MOUNT"
        expected="ERASE-EXTRA $EXTRA_SERIAL $EXTRA_MAJMIN"
        topology=$(lsblk -nrpo NAME,TYPE "$EXTRA_REAL") || fail 'cannot read extra-disk topology.'
        [[ -n $topology ]] || fail 'empty extra-disk topology.'
        partitions=$(printf '%s\n' "$topology" | awk '$2 == "part" {print $1}')
        unexpected=$(printf '%s\n' "$topology" | awk 'NR > 1 && $2 != "part" {print}')
        [[ -z $unexpected ]] || fail "extra disk has unexpected mapped descendants: $unexpected"
        if [[ $PROFILE == hardware ]]; then
            [[ $(printf '%s\n' "$partitions" | wc -l) -eq 1 && $(readlink -f "$partitions") == "$SOURCE_DEVICE" ]] ||
                fail 'hardware extra disk must contain only the verified mounted workspace partition.'
        else
            [[ -z $partitions ]] || fail 'VM extra target already has partitions; inspect it instead of reformatting.'
            extra_require_blank "$EXTRA_REAL"
        fi
        printf 'This erases the identified extra disk after the workspace copy has been verified.\nType exactly: %s\n' "$expected" > /dev/tty
        IFS= read -r confirmation < /dev/tty
        [[ $confirmation == "$expected" ]] || fail 'confirmation mismatch; no disk changes requested.'
        printf 'started %s\nserial=%s\nsize=%s\nmajor:minor=%s\n' "$(date -u +%FT%TZ)" "$EXTRA_SERIAL" "$EXTRA_BYTES" "$EXTRA_MAJMIN" > "$STATE/initialize.started.tmp"
        sync "$STATE/initialize.started.tmp"; mv "$STATE/initialize.started.tmp" "$STATE/initialize.started"; sync "$STATE"
        install -d -m 0700 /root/.keys
        [[ $(stat -c '%u:%g:%a' /root/.keys) == 0:0:700 ]] || fail 'extra key directory is unsafe.'
        key_tmp=$(mktemp /root/.keys/.ms1504-extra.XXXXXX)
        dd if=/dev/urandom of="$key_tmp" bs=64 count=1 iflag=fullblock status=none
        chmod 0400 "$key_tmp"; sync "$key_tmp"; mv "$key_tmp" "$KEY"; sync /root/.keys
        key_digest=$(sha256sum "$KEY") || fail 'cannot hash the new extra key.'
        key_digest=${key_digest%% *}
        [[ $key_digest =~ ^[0-9a-f]{64}$ ]] || fail 'invalid new extra key digest.'
        printf 'key sha256=%s\n' "$key_digest" > "$STATE/key.done.tmp"
        sync "$STATE/key.done.tmp"; mv "$STATE/key.done.tmp" "$STATE/key.done"; sync "$STATE"
        extra_verify_key
        if [[ $PROFILE == hardware ]]; then umount "$SOURCE_MOUNT"; fi
        extra_check_mounts
        sgdisk --zap-all --clear --new=1:0:0 --typecode=1:8309 --change-name=1:MS1504-EXTRA "$EXTRA_REAL"
        sync; blockdev --rereadpt "$EXTRA_REAL"; udevadm settle --timeout=30
        [[ -b $EXTRA_PART ]] || fail 'partition did not appear; leave the disk unchanged and inspect it.'
        extra_partition_identity
        printf 'partition UUID=%s\n' "$EXTRA_PART_UUID" > "$STATE/partition.done.tmp"
        sync "$STATE/partition.done.tmp"; mv "$STATE/partition.done.tmp" "$STATE/partition.done"; sync "$STATE"
        set --
        ;&
    resume)
        [[ $# -eq 0 ]] || fail 'resume takes no additional arguments.'
        validate_home_migration
        [[ -s $STATE/initialize.started && -s $STATE/partition.done ]] || fail 'resume requires durable initialization and partition receipts; inspect before proceeding.'
        extra_partition_identity
        grep -Fxq "serial=$EXTRA_SERIAL" "$STATE/initialize.started" && grep -Fxq "size=$EXTRA_BYTES" "$STATE/initialize.started" &&
            grep -Fxq "major:minor=$EXTRA_MAJMIN" "$STATE/initialize.started" &&
            grep -Fxq "partition UUID=$EXTRA_PART_UUID" "$STATE/partition.done" || fail 'extra partition identity changed.'
        extra_verify_key
        extra_probe_type "$EXTRA_PART"
        if [[ $EXTRA_SIGNATURE == crypto_LUKS ]]; then
            cryptsetup isLuks "$EXTRA_PART" || fail 'LUKS signature has no readable header; inspect manually.'
            cryptsetup open --test-passphrase --key-file "$KEY" "$EXTRA_PART" || fail 'LUKS header does not match the recorded key.'
            luks_uuid=$(cryptsetup luksUUID "$EXTRA_PART")
            if [[ -s $STATE/luks.done ]]; then
                grep -Fxq "LUKS UUID=$luks_uuid" "$STATE/luks.done" || fail 'LUKS UUID differs from its durable receipt.'
            else
                printf 'LUKS UUID=%s\n' "$luks_uuid" > "$STATE/luks.done.tmp"
                sync "$STATE/luks.done.tmp"; mv "$STATE/luks.done.tmp" "$STATE/luks.done"; sync "$STATE"
            fi
        else
            [[ -z $EXTRA_SIGNATURE ]] || fail "unexpected signature on extra partition: $EXTRA_SIGNATURE"
            [[ ! -e $STATE/luks.done ]] || fail 'LUKS header is missing despite a completion receipt; inspect manually.'
            [[ ! -e $STATE/luks.started ]] || fail 'LUKS formatting was interrupted without a readable header; inspect manually before any reformat.'
            extra_require_blank "$EXTRA_PART"
            extra_check_mounts
            printf 'started %s\n' "$(date -u +%FT%TZ)" > "$STATE/luks.started.tmp"
            sync "$STATE/luks.started.tmp"; mv "$STATE/luks.started.tmp" "$STATE/luks.started"; sync "$STATE"
            cryptsetup luksFormat --batch-mode --type luks2 --pbkdf argon2id --label MS1504-EXTRA --key-file "$KEY" "$EXTRA_PART"
            cryptsetup isLuks "$EXTRA_PART" || fail 'LUKS header verification failed.'
            luks_uuid=$(cryptsetup luksUUID "$EXTRA_PART") || fail 'cannot read the new LUKS UUID.'
            [[ $luks_uuid =~ ^[A-Fa-f0-9-]{36}$ ]] || fail 'invalid new LUKS UUID.'
            printf 'LUKS UUID=%s\n' "$luks_uuid" > "$STATE/luks.done.tmp"
            sync "$STATE/luks.done.tmp"; mv "$STATE/luks.done.tmp" "$STATE/luks.done"; sync "$STATE"
        fi
        luks_uuid=$(cryptsetup luksUUID "$EXTRA_PART")
        if ! cryptsetup status "$MAPPER" >/dev/null 2>&1; then cryptsetup open --key-file "$KEY" "$EXTRA_PART" "$MAPPER"; fi
        mapper_source=$(cryptsetup status "$MAPPER" | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
        [[ -n $mapper_source && $(readlink -f "$mapper_source") == "$(readlink -f "$EXTRA_PART")" ]] || fail 'existing extra mapper belongs to another partition.'
        mkdir -p "$MOUNT"
        extra_probe_type "/dev/mapper/$MAPPER"
        filesystem=$EXTRA_SIGNATURE
        if [[ $filesystem == ext4 ]]; then
            [[ -s $STATE/filesystem.started || -s $STATE/filesystem.done ]] || fail 'ext4 exists without a recorded format intent.'
            [[ $(blkid -p -D -s LABEL -o value "/dev/mapper/$MAPPER") == "$WORKSPACE_LABEL" ]] || fail 'existing extra filesystem has an unexpected label.'
        elif [[ -z $filesystem ]]; then
            [[ ! -e $STATE/filesystem.done ]] || fail 'filesystem is missing despite a completion receipt; inspect manually.'
            [[ ! -e $STATE/filesystem.started ]] || fail 'filesystem formatting was interrupted; inspect the mapper manually.'
            extra_require_blank "/dev/mapper/$MAPPER"
            extra_check_mounts
            printf 'started %s\n' "$(date -u +%FT%TZ)" > "$STATE/filesystem.started.tmp"
            sync "$STATE/filesystem.started.tmp"; mv "$STATE/filesystem.started.tmp" "$STATE/filesystem.started"; sync "$STATE"
            mkfs.ext4 -F -m 0 -L "$WORKSPACE_LABEL" "/dev/mapper/$MAPPER"
        else fail "unexpected filesystem on the extra mapper: $filesystem"; fi
        blkid -p -D -s TYPE -o value "/dev/mapper/$MAPPER" | grep -Fxq ext4 || fail 'extra filesystem verification failed.'
        filesystem_uuid=$(blkid -p -D -s UUID -o value "/dev/mapper/$MAPPER") || fail 'cannot read the extra filesystem UUID.'
        [[ $filesystem_uuid =~ ^[A-Fa-f0-9-]{36}$ ]] || fail 'invalid extra filesystem UUID.'
        if [[ -s $STATE/filesystem.done ]]; then
            grep -Fxq "label=$WORKSPACE_LABEL" "$STATE/filesystem.done" && grep -Fxq "UUID=$filesystem_uuid" "$STATE/filesystem.done" || fail 'filesystem identity differs from its durable receipt.'
        else
            printf 'label=%s\nUUID=%s\n' "$WORKSPACE_LABEL" "$filesystem_uuid" > "$STATE/filesystem.done.tmp"
            sync "$STATE/filesystem.done.tmp"; mv "$STATE/filesystem.done.tmp" "$STATE/filesystem.done"; sync "$STATE"
        fi
        printf 'complete %s\npartition UUID=%s\nLUKS UUID=%s\nfilesystem UUID=%s\n' \
            "$(date -u +%FT%TZ)" "$EXTRA_PART_UUID" "$luks_uuid" "$filesystem_uuid" > "$STATE/initialize.done.tmp"
        sync "$STATE/initialize.done.tmp"; mv "$STATE/initialize.done.tmp" "$STATE/initialize.done"; sync "$STATE"
        echo 'Encrypted extra filesystem is initialized; use restore to copy and verify the workspace.'
        ;;
    restore)
        [[ $# -eq 0 ]] || fail 'restore takes no additional arguments.'
        validate_home_migration
        extra_verify_partition_receipt
        extra_verify_key
        [[ -s $STATE/filesystem.done && -f $KEY && ! -L $KEY ]] || fail 'encrypted extra filesystem is not initialized; use resume.'
        if ! cryptsetup status "$MAPPER" >/dev/null 2>&1; then cryptsetup open --key-file "$KEY" "$EXTRA_PART" "$MAPPER"; fi
        [[ -b /dev/mapper/$MAPPER ]] || fail 'extra mapper is unavailable.'
        mapper_source=$(cryptsetup status "$MAPPER" | sed -n 's/^[[:space:]]*device:[[:space:]]*//p')
        [[ -n $mapper_source && $(readlink -f "$mapper_source") == "$(readlink -f "$EXTRA_PART")" ]] || fail 'existing extra mapper belongs to another partition.'
        [[ $(blkid -p -D -s TYPE -o value "/dev/mapper/$MAPPER") == ext4 && $(blkid -p -D -s LABEL -o value "/dev/mapper/$MAPPER") == "$WORKSPACE_LABEL" ]] || fail 'extra mapper does not contain the expected workspace filesystem.'
        if mountpoint -q "$MOUNT"; then verify_target_mount; else mount "/dev/mapper/$MAPPER" "$MOUNT"; fi
        verify_target_mount
        required=$(sed -n 's/^workspace allocated bytes=//p' "$STATE/migration.done")
        [[ $required =~ ^[0-9]+$ ]] || fail 'migration receipt has no valid workspace size.'
        required=$((required + required / 20 + 536870912))
        available=$(df -B1 --output=avail "$MOUNT" | awk 'NR==2 {print $1}')
        [[ $available =~ ^[0-9]+$ && $available -ge $required ]] || fail "actual /extra filesystem free space is insufficient: need $required bytes including reserve, have ${available:-unknown}."
        rsync -aHAX --modify-window=-1 --numeric-ids --sparse --one-file-system --delete --exclude=/lost+found "$DATA/" "$MOUNT/"
        (cd "$MOUNT" && sha256sum --check "$STATE/workspace.sha256")
        differences=$(rsync -aHAXnci --modify-window=-1 --out-format='%i%f' --exclude=/lost+found --numeric-ids --one-file-system --delete "$DATA/" "$MOUNT/" | awk '$NF !~ /(^|\/)\.$/ {print}')
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
            differences=$(rsync -aHAXnci --modify-window=-1 --out-format='%i%f' --exclude=/lost+found --numeric-ids --one-file-system --delete "$DATA/" "$MOUNT/" | awk '$NF !~ /(^|\/)\.$/ {print}')
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
        differences=$(rsync -aHAXnci --modify-window=-1 --out-format='%i%f' --exclude=/lost+found --numeric-ids --one-file-system --delete "$DATA/" "$MOUNT/" | awk '$NF !~ /(^|\/)\.$/ {print}')
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
