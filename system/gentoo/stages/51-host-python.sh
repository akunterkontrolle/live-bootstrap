#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'host Python requires the verified modern-kernel handoff'
gentoo_python_host_inputs
host_python_action() {
    local build_triple
    gentoo_prepare_source sources/bzip2.lock
    gentoo_prepare_source sources/python.lock
    mkdir -p "$HOST_PREFIX/bin" "$WORK_ROOT/build"
    cat > "$HOST_PREFIX/bin/gcc-python-host" <<'WRAPPER'
#!/bin/sh
# Correct only GCC's configure-time multiarch query for this musl host.
for arg do
    if [ "$arg" = --print-multiarch ]; then printf '%s\n' i386-linux-musl; exit 0; fi
done
exec /usr/bin/gcc "$@"
WRAPPER
    chmod 0755 "$HOST_PREFIX/bin/gcc-python-host"
    cd "$SOURCES/bzip2-1.0.8"
    make -j"$JOBS" CC=/usr/bin/gcc libbz2.a bzip2 bzip2recover
    make install PREFIX="$HOST_PREFIX" CC=/usr/bin/gcc
    [ -s "$HOST_PREFIX/include/bzlib.h" ] && [ -s "$HOST_PREFIX/lib/libbz2.a" ] ||
        gentoo_die 'source-built bzip2 outputs are incomplete'
    build_triple=$(/usr/bin/gcc -dumpmachine)
    mkdir -p "$WORK_ROOT/build/python-3.12.15-host"
    cd "$WORK_ROOT/build/python-3.12.15-host"
    # Mirror the original bootstrap Python's static native modules. OpenSSL,
    # zlib and the bzip2 above provide archives, not shared development libraries.
    mkdir -p Modules
    cat > Modules/Setup.local <<SETUP
*static*
_bz2 _bz2module.c -I$HOST_PREFIX/include -L$HOST_PREFIX/lib -l:libbz2.a
zlib zlibmodule.c -l:libz.a
_ssl _ssl.c \$(OPENSSL_INCLUDES) \$(OPENSSL_LDFLAGS) -l:libssl.a -Wl,--exclude-libs,libssl.a -l:libcrypto.a -Wl,--exclude-libs,libcrypto.a
_hashlib _hashopenssl.c \$(OPENSSL_INCLUDES) \$(OPENSSL_LDFLAGS) -l:libcrypto.a -Wl,--exclude-libs,libcrypto.a
SETUP
    if [ ! -f Makefile ]; then
        CC="$HOST_PREFIX/bin/gcc-python-host" CXX=/usr/bin/g++ \
            CPPFLAGS="-I$HOST_PREFIX/include" LDFLAGS="-L$HOST_PREFIX/lib" \
            "$SOURCES/Python-3.12.15/configure" \
            --build="$build_triple" --host="$build_triple" --prefix="$HOST_PREFIX" \
            --with-ensurepip=no --without-readline --disable-test-modules --with-openssl=/usr
    fi
    make -j"$JOBS"
    make install
}
host_python_verify() {
    gentoo_prepare_source sources/bzip2.lock || return 1
    gentoo_prepare_source sources/python.lock || return 1
    "$HOST_PREFIX/bin/python3.12" -c 'import sys; assert sys.version_info[:3] == (3, 12, 15)' || return 1
    "$HOST_PREFIX/bin/python3.12" -c 'import sys, _ssl, _hashlib, _bz2, zlib; assert {"_ssl", "_hashlib", "_bz2", "zlib"} <= set(sys.builtin_module_names)' || return 1
    "$HOST_PREFIX/bin/python3.12" -c 'import bz2, ssl, zlib; assert bz2.decompress(bz2.compress(b"ok")) == b"ok"; print("host Python bz2/ssl/zlib imports passed")' || return 1
    "$HOST_PREFIX/bin/python3.12" -c 'import ssl; ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); print(ssl.OPENSSL_VERSION)' || return 1
    "$HOST_PREFIX/bin/python3.12" -c 'import hashlib, json, urllib.request; assert hashlib.sha512(b"x").digest()' || return 1
    [ -s "$HOST_PREFIX/include/bzlib.h" ] && [ -s "$HOST_PREFIX/lib/libbz2.a" ]
}
fingerprint=$(gentoo_fingerprint stages/51-host-python.sh lib/python.sh sources/python.lock sources/bzip2.lock \
    "$WORK_ROOT/state/host-toolchain.inputs" "$WORK_ROOT/state/host-python.inputs" \
    "$WORK_ROOT/state/50-gentoo-sources.done")
gentoo_run_stage 51-host-python "$fingerprint" host_python_action host_python_verify
