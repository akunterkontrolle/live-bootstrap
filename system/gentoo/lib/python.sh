#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# The original bootstrap builds OpenSSL with no-shared and zlib with --static.
# Bind their headers/archives and exercise the actual native link before Python.
# Optional compiler/include arguments also permit disposable dependency fixtures.
gentoo_python_host_inputs() {
    local compiler=${1:-/usr/bin/gcc} include=${2:-/usr/include}
    local destination=$WORK_ROOT/state/host-python.inputs temporary library path
    local probes="$WORK_ROOT/build/probes/host-python"
    local -a archives=()
    [ -s "$include/openssl/ssl.h" ] && [ -s "$include/zlib.h" ] && [ -s "$include/zconf.h" ] ||
        gentoo_die 'host OpenSSL and zlib development headers are required for Python ssl/zlib modules'
    for library in libssl.a libcrypto.a libz.a; do
        path=$("$compiler" -print-file-name="$library") || gentoo_die "cannot locate host development archive: $library"
        [ -s "$path" ] || gentoo_die "missing host development archive: $library"
        archives+=("$path")
    done
    mkdir -p "$probes"
    cat > "$probes/libraries.c" <<'PROBE'
#include <openssl/ssl.h>
#include <openssl/evp.h>
#include <zlib.h>
#include <string.h>
int main(void) {
    SSL_CTX *ctx = SSL_CTX_new(TLS_method());
    unsigned char hash[EVP_MAX_MD_SIZE], packed[64], plain[8];
    unsigned int hash_size = 0;
    uLongf packed_size = sizeof packed, plain_size = sizeof plain;
    if (!ctx) return 1;
    SSL_CTX_free(ctx);
    if (!EVP_Digest("ok", 2, hash, &hash_size, EVP_sha256(), 0) || hash_size != 32) return 2;
    if (compress(packed, &packed_size, (const Bytef *)"ok", 2) != Z_OK) return 3;
    if (uncompress(plain, &plain_size, packed, packed_size) != Z_OK) return 4;
    return plain_size != 2 || memcmp(plain, "ok", 2);
}
PROBE
    "$compiler" -I"$include" "$probes/libraries.c" "${archives[@]}" -ldl -pthread \
        -o "$probes/libraries" || gentoo_die 'host OpenSSL/zlib static archive link failed'
    "$probes/libraries" || gentoo_die 'host OpenSSL/zlib static runtime probe failed'
    temporary=$(mktemp "$WORK_ROOT/state/.host-python.XXXXXX")
    {
        printf 'build triple: %s\n' "$("$compiler" -dumpmachine)"
        sha256sum "$include"/openssl/*.h "$include/zlib.h" "$include/zconf.h"
        for path in "${archives[@]}"; do
            printf '%s\n' "${path##*/}"
            sha256sum < "$path"
        done
        sha256sum < "$WORK_ROOT/state/host-toolchain.inputs"
    } > "$temporary"
    sync
    mv "$temporary" "$destination"
    sync
    printf 'Bootstrap static OpenSSL/zlib link and runtime probe passed.\n'
}
