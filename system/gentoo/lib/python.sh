#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Bind the system OpenSSL and zlib development inputs required for CPython's
# native ssl/zlib extensions. Source-built bzip2 is separately pinned.
gentoo_python_host_inputs() {
    local destination=$WORK_ROOT/state/host-python.inputs temporary library path
    [ -s /usr/include/openssl/ssl.h ] && [ -s /usr/include/zlib.h ] ||
        gentoo_die 'host OpenSSL and zlib development headers are required for Python ssl/zlib modules'
    temporary=$(mktemp "$WORK_ROOT/state/.host-python.XXXXXX")
    {
        printf 'build triple: %s\n' "$(/usr/bin/gcc -dumpmachine)"
        sha256sum /usr/include/openssl/*.h /usr/include/zlib.h /usr/include/zconf.h
        for library in libssl.so libcrypto.so libz.a; do
            path=$(/usr/bin/gcc -print-file-name="$library")
            [ -s "$path" ] || gentoo_die "missing host development library: $library"
            printf '%s\n' "$library"
            sha256sum < "$path"
        done
        sha256sum < "$WORK_ROOT/state/host-toolchain.inputs"
    } > "$temporary"
    sync
    mv "$temporary" "$destination"
    sync
}
