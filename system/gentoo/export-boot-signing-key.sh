#!/bin/bash
# Export a GRUB-compatible public key and an additionally encrypted secret
# export. Run this on the trusted offline key workstation, never on Gentoo.
set -euo pipefail
umask 077

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 PRIMARY-FINGERPRINT OUTPUT-DIRECTORY" >&2
    exit 2
fi

fingerprint=${1//[[:space:]]/}
output=$2
[[ $fingerprint =~ ^[A-Fa-f0-9]{40,64}$ ]] || {
    echo 'Expected a full OpenPGP primary fingerprint.' >&2
    exit 2
}
mkdir -p -- "$output"
[[ ! -e $output/boot.key && ! -e $output/secret-key.gpg ]] || {
    echo "Refusing to overwrite signing-key exports in $output." >&2
    exit 1
}

gpg --batch --show-keys --with-colons < <(gpg --batch --export "$fingerprint") |
    awk -F: -v expected="${fingerprint^^}" '$1 == "fpr" && toupper($10) == expected { found=1 } END { exit !found }' || {
        echo 'The selected key cannot be exported or fingerprint did not match.' >&2
        exit 1
    }

gpg --batch --yes --output "$output/boot.key" --export "$fingerprint"
[[ -s $output/boot.key ]] || { echo 'Public key export is empty.' >&2; exit 1; }

# Secret key bytes travel directly between GPG processes; no plaintext export
# is written to disk. Pinentry requests any key passphrase and the offline
# container passphrase without exposing either to this script.
set -o pipefail
gpg --export-secret-keys "$fingerprint" |
    gpg --symmetric --cipher-algo AES256 --s2k-digest-algo SHA512 \
        --output "$output/secret-key.gpg"
chmod 0644 "$output/boot.key"
chmod 0600 "$output/secret-key.gpg"
printf 'Public key: %s/boot.key\nEncrypted secret export: %s/secret-key.gpg\nFingerprint: %s\n' \
    "$output" "$output" "${fingerprint^^}"
