#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Separate, opt-in T440p firmware helper. No force programmer or fallback path.
set -Eeuo pipefail
umask 077

die() { printf 'firmware-flash: %s\n' "$*" >&2; exit 1; }
usage() {
    die 'usage: firmware-flash.sh {preflight|backup|flash} --backup-dir DIR --product EXACT --board EXACT --bios EXACT [--image ROM --build-receipt FILE]'
}
ACTION=${1:-}; [ "$#" -gt 0 ] || usage; shift
case "$ACTION" in preflight|backup|flash) ;; *) usage ;; esac
BACKUP_DIR= PRODUCT= BOARD= BIOS= FLASHROM_VERSION= IMAGE= BUILD_RECEIPT=
while (($#)); do
    case "$1" in
        --backup-dir) (($# >= 2)) || usage; BACKUP_DIR=$2; shift 2 ;;
        --product) (($# >= 2)) || usage; PRODUCT=$2; shift 2 ;;
        --board) (($# >= 2)) || usage; BOARD=$2; shift 2 ;;
        --bios) (($# >= 2)) || usage; BIOS=$2; shift 2 ;;
        --flashrom-version) (($# >= 2)) || usage; FLASHROM_VERSION=$2; shift 2 ;;
        --image) (($# >= 2)) || usage; IMAGE=$2; shift 2 ;;
        --build-receipt) (($# >= 2)) || usage; BUILD_RECEIPT=$2; shift 2 ;;
        *) usage ;;
    esac
done
[ "$(id -u)" = 0 ] || die 'run as root.'
[[ $BACKUP_DIR = /* && -n $PRODUCT && -n $BOARD && $BIOS =~ ^[A-Za-z0-9._+-]{1,80}$ && -n $FLASHROM_VERSION ]] || usage
[[ $PRODUCT != *$'\n'* && $BOARD != *$'\n'* && $FLASHROM_VERSION != *$'\n'* ]] || die 'unsafe or incomplete exact hardware identity.'
for command in awk cat chmod cmp date dmidecode findmnt flashrom grep id mkdir readlink rm sed sha256sum stat sync; do
    command -v "$command" >/dev/null 2>&1 || die "required command is missing: $command"
done
mkdir -p -m 0700 "$BACKUP_DIR"
[[ ! -L $BACKUP_DIR ]] || die 'backup directory must not be a symlink.'
chmod 0700 "$BACKUP_DIR"
backup_mount=$(findmnt -rn -T "$BACKUP_DIR" -o SOURCE)
root_mount=$(findmnt -rn -T / -o SOURCE)
[[ $(readlink -f "$backup_mount") != "$(readlink -f "$root_mount")" ]] || die 'store firmware backups on a separate mounted device.'
[[ $(dmidecode -s system-product-name | sed 's/[[:space:]]*$//') == "$PRODUCT" ]] || die 'system product does not match the operator-supplied T440p identity.'
[[ $(dmidecode -s baseboard-product-name | sed 's/[[:space:]]*$//') == "$BOARD" ]] || die 'baseboard product does not match the operator-supplied identity.'
[[ $(dmidecode -s bios-version | sed 's/[[:space:]]*$//') == "$BIOS" ]] || die 'working firmware revision changed.'

probe=$BACKUP_DIR/internal-probe.txt
probe_image=$BACKUP_DIR/.probe-$$.bin
trap 'rm -f "$probe_image"' EXIT HUP INT TERM
if ! flashrom -p internal --verbose -r "$probe_image" > "$probe" 2>&1; then
    die 'internal read-only probe failed; stop and use the documented external recovery/programmer route.'
fi
grep -Eq 'Found .* flash chip' "$probe" || die 'probe did not identify exactly one supported chip.'
[[ $(grep -Ec 'Found .* flash chip' "$probe") -eq 1 ]] || die 'probe found an ambiguous number of flash chips.'
if grep -Eiq '(write[ -]?(protect|disabled)|not writable|cannot write|locked for writing)' "$probe"; then
    die 'chip reports write protection or unavailable write access; stop and use the external recovery route.'
fi
chip=$(sed -n 's/^Found \(.*\) flash chip.*/\1/p' "$probe")
[[ -n $chip && $chip != *$'\n'* ]] || die 'could not record the exact flash chip identity.'
flashrom --version > "$BACKUP_DIR/flashrom-version.txt" 2>&1
[[ $(cat "$BACKUP_DIR/flashrom-version.txt") == "$FLASHROM_VERSION" ]] || die 'flashrom version differs from the operator-approved version.'
printf 'product=%s\nboard=%s\nbios=%s\nchip=%s\nprobe sha256=%s\n' \
    "$PRODUCT" "$BOARD" "$BIOS" "$chip" "$(sha256sum "$probe" | awk '{print $1}')" \
    > "$BACKUP_DIR/hardware-preflight.receipt"
chmod 0600 "$probe" "$BACKUP_DIR/flashrom-version.txt" "$BACKUP_DIR/hardware-preflight.receipt"
rm -f "$probe_image"
trap - EXIT HUP INT TERM
sync "$BACKUP_DIR"
if [ "$ACTION" = preflight ]; then
    printf 'Read-only chip probe passed: %s\nReceipt: %s/hardware-preflight.receipt\nNo flash write was attempted.\n' "$chip" "$BACKUP_DIR"
    exit 0
fi

if [ "$ACTION" = backup ]; then
    [[ ! -e $BACKUP_DIR/working-a.bin && ! -e $BACKUP_DIR/working-b.bin ]] || die 'firmware backup files already exist; preserve and inspect them.'
    flashrom -p internal -r "$BACKUP_DIR/working-a.bin" > "$BACKUP_DIR/read-a.log" 2>&1 || die 'first read-only firmware backup failed; stop and use external recovery.'
    flashrom -p internal -r "$BACKUP_DIR/working-b.bin" > "$BACKUP_DIR/read-b.log" 2>&1 || die 'second read-only firmware backup failed; stop and use external recovery.'
    cmp -s "$BACKUP_DIR/working-a.bin" "$BACKUP_DIR/working-b.bin" || die 'two independent firmware reads differ; do not flash.'
    chmod 0600 "$BACKUP_DIR/working-a.bin" "$BACKUP_DIR/working-b.bin" "$BACKUP_DIR/read-a.log" "$BACKUP_DIR/read-b.log"
    size=$(stat -c '%s' "$BACKUP_DIR/working-a.bin")
    hash=$(sha256sum "$BACKUP_DIR/working-a.bin" | awk '{print $1}')
    printf 'size=%s\nsha256=%s\nchip=%s\nproduct=%s\nboard=%s\nbios=%s\nflashrom version=%s\nbackup reads=2 identical\n' \
        "$size" "$hash" "$chip" "$PRODUCT" "$BOARD" "$BIOS" "$FLASHROM_VERSION" > "$BACKUP_DIR/verified-backup.receipt"
    chmod 0600 "$BACKUP_DIR/verified-backup.receipt"
    sync "$BACKUP_DIR"
    printf 'Two identical backups verified: %s bytes, SHA-256 %s\nStore a second verified copy offline.\n' "$size" "$hash"
    exit 0
fi

[[ -s $IMAGE && ! -L $IMAGE && -s $BUILD_RECEIPT && ! -L $BUILD_RECEIPT ]] || die 'flash requires a regular hardened ROM and its build receipt.'
[[ -s $BACKUP_DIR/working-a.bin && -s $BACKUP_DIR/working-b.bin && -s $BACKUP_DIR/verified-backup.receipt ]] || die 'verified dual firmware backups are required first.'
cmp -s "$BACKUP_DIR/working-a.bin" "$BACKUP_DIR/working-b.bin" || die 'verified firmware backups no longer match.'
original_hash=$(sha256sum "$BACKUP_DIR/working-a.bin" | awk '{print $1}')
original_size=$(stat -c '%s' "$BACKUP_DIR/working-a.bin")
grep -Fxq "sha256=$original_hash" "$BACKUP_DIR/verified-backup.receipt" || die 'backup receipt hash mismatch.'
grep -Fxq "size=$original_size" "$BACKUP_DIR/verified-backup.receipt" || die 'backup receipt size mismatch.'
grep -Fxq "chip=$chip" "$BACKUP_DIR/verified-backup.receipt" || die 'flash chip differs from the verified backup.'
grep -Fxq "product=$PRODUCT" "$BACKUP_DIR/verified-backup.receipt" || die 'backup belongs to another system product.'
grep -Fxq "board=$BOARD" "$BACKUP_DIR/verified-backup.receipt" || die 'backup belongs to another baseboard.'
grep -Fxq "bios=$BIOS" "$BACKUP_DIR/verified-backup.receipt" || die 'backup belongs to another BIOS revision.'
grep -Fxq "flashrom version=$FLASHROM_VERSION" "$BACKUP_DIR/verified-backup.receipt" || die 'flashrom version differs from the verified backup procedure.'
[[ $(stat -c '%s' "$IMAGE") == "$original_size" ]] || die 'hardened image size differs from the verified working firmware.'
grep -Fxq "base firmware sha256=$original_hash" "$BUILD_RECEIPT" || die 'build receipt does not bind this exact working firmware backup.'
grep -Fxq "board=$BOARD" "$BUILD_RECEIPT" || die 'hardened build receipt is for another board.'
grep -Fxq "firmware revision=$BIOS" "$BUILD_RECEIPT" || die 'hardened build receipt is for another firmware revision.'
grep -Fxq 'board-specific data preserved=yes' "$BUILD_RECEIPT" || die 'build receipt does not attest board-specific data preservation.'
[[ ! -e $BACKUP_DIR/flash-in-progress.receipt && ! -e $BACKUP_DIR/flash-complete.receipt ]] || die 'previous flash state exists; inspect before proceeding.'
current=$BACKUP_DIR/current-before-flash.bin
flashrom -p internal -r "$current" > "$BACKUP_DIR/current-read.log" 2>&1 || die 'current read-back failed; no flash write attempted.'
cmp -s "$current" "$BACKUP_DIR/working-a.bin" || die 'working firmware changed after backup; no flash write attempted.'
image_hash=$(sha256sum "$IMAGE" | awk '{print $1}')
confirmation="FLASH T440P $BOARD $BIOS $chip $image_hash"
printf 'This writes the internal firmware chip. Keep AC power connected and verified external recovery available.\nType exactly: %s\n' "$confirmation" > /dev/tty
IFS= read -r answer < /dev/tty
[[ $answer == "$confirmation" ]] || die 'confirmation mismatch; no firmware write attempted.'
printf 'image=%s\nimage sha256=%s\nbase firmware sha256=%s\nboard=%s\nfirmware revision=%s\nchip=%s\nstarted=%s\n' \
    "$(readlink -f "$IMAGE")" "$image_hash" "$original_hash" "$BOARD" "$BIOS" "$chip" "$(date -u +%FT%TZ)" \
    > "$BACKUP_DIR/flash-in-progress.receipt"
chmod 0600 "$BACKUP_DIR/flash-in-progress.receipt" "$current" "$BACKUP_DIR/current-read.log"
sync "$BACKUP_DIR"
if ! flashrom -p internal -w "$IMAGE" > "$BACKUP_DIR/write.log" 2>&1; then
    die 'internal write failed; do not retry. Follow the documented external recovery route.'
fi
flashrom -p internal -v "$IMAGE" > "$BACKUP_DIR/verify.log" 2>&1 || die 'flashrom verification failed; stop and use external recovery.'
flashrom -p internal -r "$BACKUP_DIR/readback.bin" > "$BACKUP_DIR/readback.log" 2>&1 || die 'post-write read-back failed; stop and use external recovery.'
cmp -s "$BACKUP_DIR/readback.bin" "$IMAGE" || die 'post-write full-image comparison failed; use external recovery.'
printf 'complete %s\nimage sha256=%s\nreadback sha256=%s\nbase backup sha256=%s\nboard=%s\nfirmware revision=%s\nchip=%s\n' \
    "$(date -u +%FT%TZ)" "$image_hash" "$(sha256sum "$BACKUP_DIR/readback.bin" | awk '{print $1}')" \
    "$original_hash" "$BOARD" "$BIOS" "$chip" > "$BACKUP_DIR/flash-complete.receipt"
chmod 0600 "$BACKUP_DIR"/*.log "$BACKUP_DIR/readback.bin" "$BACKUP_DIR/flash-complete.receipt"
sync "$BACKUP_DIR"
printf 'Read-back matches the hardened image. Preserve backup and receipt, then cold-boot and run hardware acceptance.\n'
