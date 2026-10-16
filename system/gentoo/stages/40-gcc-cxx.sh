#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Defense in depth: never configure/build C++ before the verified cold boot.
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'C++ completion requires the verified modern-kernel handoff'
gentoo_prepare_source sources/gcc.lock
gcc_cxx_action() {
    mkdir -p "$GCC_CXX_BUILD"
    cd "$GCC_CXX_BUILD"
    if [ ! -f Makefile ]; then
        build_triple=$(/usr/bin/gcc -dumpmachine)
        "$GCC_SOURCE/configure" --build="$build_triple" --host="$build_triple" --target="$TARGET" \
            --prefix="$PREFIX" --with-sysroot="$SYSROOT" --with-build-sysroot="$SYSROOT" \
            --with-headers="$SYSROOT/usr/include" --with-build-time-tools="$TOOLBIN" \
            --with-as="$TOOLBIN/$TARGET-as" --with-ld="$TOOLBIN/$TARGET-ld" \
            --with-gmp=/usr --with-mpfr=/usr --with-mpc=/usr \
            --enable-languages=c,c++ --enable-threads=posix --disable-bootstrap --disable-nls \
            --enable-multilib --enable-shared --with-multilib-list=m32,m64 \
            --disable-libatomic --disable-libgomp --disable-libquadmath --disable-libssp \
            --disable-libvtv --disable-libsanitizer --disable-lto --disable-werror
    fi
    # Top-level configure defers gcc/ and its generated exec-tool wrappers.
    make configure-gcc
    cxx_fix_nm
    make -j"$JOBS" all-gcc
}
fingerprint=$(cxx_fingerprint stages/40-gcc-cxx.sh sources/gcc.lock "$WORK_ROOT/state/20-sysroot-links.done")
gentoo_run_stage 40-gcc-cxx "$fingerprint" gcc_cxx_action cxx_verify_frontends
