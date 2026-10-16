#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Bind a separately built hardened ROM to an audited working-firmware backup.
set -Eeuo pipefail
umask 077

die() { printf 'firmware-build-receipt: %s\n' "$*" >&2; exit 1; }
usage() { die 'usage: firmware-build-receipt.sh --base ROM --backup-receipt FILE --image ROM --payload-receipt FILE --preservation-report FILE --board EXACT --revision ID --output FILE'; }
BASE= BACKUP_RECEIPT= IMAGE= PAYLOAD_RECEIPT= PRESERVATION= BOARD= REVISION= OUTPUT=
while (($#)); do
    case "$1" in
        --base) (($# >= 2)) || usage; BASE=$2; shift 2 ;;
        --backup-receipt) (($# >= 2)) || usage; BACKUP_RECEIPT=$2; shift 2 ;;
        --image) (($# >= 2)) || usage; IMAGE=$2; shift 2 ;;
        --payload-receipt) (($# >= 2)) || usage; PAYLOAD_RECEIPT=$2; shift 2 ;;
        --preservation-report) (($# >= 2)) || usage; PRESERVATION=$2; shift 2 ;;
        --board) (($# >= 2)) || usage; BOARD=$2; shift 2 ;;
        --revision) (($# >= 2)) || usage; REVISION=$2; shift 2 ;;
        --output) (($# >= 2)) || usage; OUTPUT=$2; shift 2 ;;
        *) usage ;;
    esac
done
[[ -s $BASE && ! -L $BASE && -s $IMAGE && ! -L $IMAGE && -s $BACKUP_RECEIPT && -s $PAYLOAD_RECEIPT && -s $PRESERVATION && -n $OUTPUT ]] || usage
[[ $BOARD != *$'\n'* && $REVISION =~ ^[A-Za-z0-9._+-]{1,80}$ && $OUTPUT = /* ]] || die 'invalid board, revision, or output path.'
for command in awk chmod date grep sha256sum stat sync; do command -v "$command" >/dev/null 2>&1 || die "required command missing: $command"; done
base_hash=$(sha256sum "$BASE" | awk '{print $1}')
image_hash=$(sha256sum "$IMAGE" | awk '{print $1}')
base_size=$(stat -c '%s' "$BASE"); image_size=$(stat -c '%s' "$IMAGE")
[[ $base_size == "$image_size" ]] || die 'hardened image size differs from working firmware.'
grep -Fxq "sha256=$base_hash" "$BACKUP_RECEIPT" || die 'base image differs from the verified backup receipt.'
grep -Fxq "firmware revision=$REVISION" "$PAYLOAD_RECEIPT" || die 'payload receipt belongs to another firmware revision.'
grep -Fxq "board=$BOARD" "$PRESERVATION" || die 'preservation report belongs to another board.'
grep -Fxq "firmware revision=$REVISION" "$PRESERVATION" || die 'preservation report belongs to another firmware revision.'
grep -Fxq "base firmware sha256=$base_hash" "$PRESERVATION" || die 'preservation report is not bound to the verified working backup.'
grep -Fxq "hardened image sha256=$image_hash" "$PRESERVATION" || die 'preservation report is not bound to this output image.'
grep -Fxq 'board-specific data preserved=yes' "$PRESERVATION" || die 'preservation report does not confirm board-data preservation.'
grep -Eq '^preservation method=[A-Za-z0-9._+:/ -]{5,160}$' "$PRESERVATION" || die 'preservation report must identify the reviewed extraction/build method.'
[[ ! -e $OUTPUT && ! -L $OUTPUT ]] || die 'refusing to overwrite a build receipt.'
method=$(sed -n 's/^preservation method=//p' "$PRESERVATION")
[[ $(printf '%s\n' "$method" | wc -l) -eq 1 ]] || die 'preservation method must be unique.'
printf 'complete %s\nbase firmware sha256=%s\nhardened image sha256=%s\nboard=%s\nfirmware revision=%s\nboard-specific data preserved=yes\npreservation method=%s\npayload receipt sha256=%s\npreservation report sha256=%s\nimage bytes=%s\n' \
    "$(date -u +%FT%TZ)" "$base_hash" "$image_hash" "$BOARD" "$REVISION" "$method" \
    "$(sha256sum "$PAYLOAD_RECEIPT" | awk '{print $1}')" \
    "$(sha256sum "$PRESERVATION" | awk '{print $1}')" "$image_size" > "$OUTPUT.tmp"
chmod 0600 "$OUTPUT.tmp"
sync "$OUTPUT.tmp"
mv "$OUTPUT.tmp" "$OUTPUT"
printf 'Build receipt: %s\n' "$OUTPUT"
