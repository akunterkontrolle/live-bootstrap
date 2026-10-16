#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Harden an extracted, hash-pinned Libreboot GRUB payload. Never modifies a ROM.
set -Eeuo pipefail
umask 077

die() { printf 'harden-libreboot-payload: %s\n' "$*" >&2; exit 1; }
usage() {
    die 'usage: harden-libreboot-payload.sh --source PAYLOAD --revision ID --sha256 HASH --boot-key FILE --password-hash-file FILE --output DIR'
}
SOURCE= REVISION= EXPECTED= KEY= HASH_FILE= OUTPUT_DIR=
while (($#)); do
    case "$1" in
        --source) (($# >= 2)) || usage; SOURCE=$2; shift 2 ;;
        --revision) (($# >= 2)) || usage; REVISION=$2; shift 2 ;;
        --sha256) (($# >= 2)) || usage; EXPECTED=$2; shift 2 ;;
        --boot-key) (($# >= 2)) || usage; KEY=$2; shift 2 ;;
        --password-hash-file) (($# >= 2)) || usage; HASH_FILE=$2; shift 2 ;;
        --output) (($# >= 2)) || usage; OUTPUT_DIR=$2; shift 2 ;;
        *) usage ;;
    esac
done
[[ -f $SOURCE && ! -L $SOURCE && -s $SOURCE ]] || die 'source payload must be a regular file.'
[[ $REVISION =~ ^[A-Za-z0-9._+-]{1,80}$ && $EXPECTED =~ ^[0-9a-f]{64}$ ]] || die 'invalid revision or source SHA-256.'
[[ -s $KEY && ! -L $KEY && -s $HASH_FILE && ! -L $HASH_FILE && -n $OUTPUT_DIR ]] || usage
for command in awk date gpg grub-script-check grep id readlink sha256sum stat wc; do
    command -v "$command" >/dev/null 2>&1 || die "required command is missing: $command"
done
[[ $(sha256sum "$SOURCE" | awk '{print $1}') == "$EXPECTED" ]] || die 'source payload hash differs from the reviewed firmware revision.'

hash_mode=$(stat -c '%a' "$HASH_FILE")
[[ $hash_mode =~ ^[0-7]{3,4}$ ]] || die 'cannot inspect password-verifier permissions.'
(( (8#$hash_mode & 077) == 0 )) || die 'password-verifier file must be owner-only.'
[[ $(stat -c '%u' "$HASH_FILE") == $(id -u) && $(wc -l < "$HASH_FILE") -eq 1 ]] || die 'password-verifier file must be one owner-only line.'
password_hash=$(<"$HASH_FILE")
[[ $password_hash =~ ^grub\.pbkdf2\.sha512\.[0-9]+\.[[:xdigit:]]+\.[[:xdigit:]]+$ ]] || die 'invalid GRUB PBKDF2-SHA512 verifier.'
if LC_ALL=C grep -aFq -- '-----BEGIN PGP PUBLIC KEY BLOCK-----' "$KEY"; then die 'GRUB requires binary OpenPGP public-key data.'; fi
key_info=$(gpg --batch --import-options show-only --import --with-colons -- "$KEY" 2>/dev/null) || die 'boot key is not a valid OpenPGP key.'
read -r pub_count secret_count fingerprint < <(printf '%s\n' "$key_info" | awk -F: '$1=="pub"{p++} $1=="sec"||$1=="ssb"{s++} $1=="fpr"&&!f{f=$10} END{print p+0,s+0,f}')
[[ $pub_count -eq 1 && $secret_count -eq 0 && $fingerprint =~ ^[[:xdigit:]]{40,64}$ ]] || die 'boot.key must contain one public key and no secret material.'

unset_count=$(awk '$0 == "\t\tunset superusers" {n++} END {print n+0}' "$SOURCE")
marker_count=$(grep -Fxc 'set default="0"' "$SOURCE")
[[ $unset_count -eq 1 && $marker_count -eq 1 ]] || die 'source must have the reviewed superuser reset and unique menu-init marker; inspect this revision.'
[[ ! -L $OUTPUT_DIR ]] || die 'output directory must not be a symlink.'
mkdir -p -m 0700 "$OUTPUT_DIR"
[[ ! -e $OUTPUT_DIR/grub-payload && ! -e $OUTPUT_DIR/grub-payload.receipt ]] || die 'output already exists; preserve it and choose a new directory.'
tmp=$OUTPUT_DIR/.payload.$$
header=$OUTPUT_DIR/.header.$$
trap 'rm -f "$tmp" "$header"' EXIT HUP INT TERM
sed '/^[[:space:]]*unset superusers$/d' "$SOURCE" > "$tmp"
if grep -q 'unset superusers' "$tmp"; then die 'a second superuser reset remains.'; fi
{
    printf 'trust (cbfsdisk)/boot.key\n'
    printf 'set check_signatures=enforce\n'
    printf 'set superusers="root"\n'
    printf 'password_pbkdf2 root %s\n' "$password_hash"
} > "$header"
unset password_hash
awk -v header="$header" '$0=="set default=\"0\"" {while((getline l < header)>0) print l; close(header)} {print}' "$tmp" > "$tmp.new"
mv "$tmp.new" "$tmp"
grub-script-check "$tmp" || die 'matching host GRUB syntax check failed.'
chmod 0600 "$tmp"
mv "$tmp" "$OUTPUT_DIR/grub-payload"
source_hash=$(sha256sum "$SOURCE" | awk '{print $1}')
key_hash=$(sha256sum "$KEY" | awk '{print $1}')
payload_hash=$(sha256sum "$OUTPUT_DIR/grub-payload" | awk '{print $1}')
printf 'complete %s\nfirmware revision=%s\nsource sha256=%s\nsource path=%s\npublic key fingerprint=%s\npublic key sha256=%s\npayload sha256=%s\nvalidation=host grub-script-check passed; matching firmware build and disposable signed-boot tests remain required\n' \
    "$(date -u +%FT%TZ)" "$REVISION" "$source_hash" "$(readlink -f "$SOURCE")" "$fingerprint" "$key_hash" "$payload_hash" \
    > "$OUTPUT_DIR/grub-payload.receipt"
chmod 0600 "$OUTPUT_DIR/grub-payload.receipt"
rm -f "$header"
trap - EXIT HUP INT TERM
printf 'Hardened GRUB payload: %s/grub-payload\nReceipt: %s/grub-payload.receipt\n' "$OUTPUT_DIR" "$OUTPUT_DIR"
