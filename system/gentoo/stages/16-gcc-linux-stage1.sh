#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
gentoo_prepare_source sources/gcc.lock
gcc_linux_stage1_action() {
    mkdir -p "$BUILDS/gcc-linux-stage1"
    cd "$BUILDS/gcc-linux-stage1"
    if [ ! -f Makefile ]; then
        "$GCC_SOURCE/configure" --target="$TARGET" --prefix="$PREFIX" --with-sysroot="$SYSROOT" \
            --with-build-time-tools="$TOOLBIN" --with-as="$TOOLBIN/$TARGET-as" --with-ld="$TOOLBIN/$TARGET-ld" \
            --with-gmp=/usr --with-mpfr=/usr --with-mpc=/usr \
            --with-newlib --without-headers --enable-languages=c \
            --disable-bootstrap --disable-nls --disable-shared --disable-threads \
            --disable-libatomic --disable-libgomp --disable-libquadmath --disable-libssp \
            --disable-libstdcxx --disable-libvtv --disable-libsanitizer --disable-lto \
            --enable-multilib --with-multilib-list=m32,m64 --disable-werror
    fi
    # No libgcc yet: both ABI glibc header/startup sets must exist first.
    make -j"$JOBS" all-gcc
    make install-gcc
}
fingerprint=$(gentoo_toolchain_fingerprint stages/16-gcc-linux-stage1.sh sources/gcc.lock "$WORK_ROOT/state/15-binutils-linux.done")
gentoo_run_stage 16-gcc-linux-stage1 "$fingerprint" gcc_linux_stage1_action gentoo_verify_multilib
