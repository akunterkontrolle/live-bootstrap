#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
gentoo_prepare_source sources/glibc.lock

# The same recipe is applied explicitly to i686, then x86_64, in separate trees.
# ABI, GLIBC_HOST and ABI_LIB are assigned immediately before each stage call.
glibc_configure() {
    mkdir -p "$GLIBC_BUILD"
    cd "$GLIBC_BUILD"
    if [ ! -f Makefile ]; then
        env CC="$PREFIX/bin/$TARGET-gcc -m$ABI --sysroot=$SYSROOT" \
            AR="$TOOLBIN/$GLIBC_HOST-ar" RANLIB="$TOOLBIN/$GLIBC_HOST-ranlib" \
            AS="$TOOLBIN/$GLIBC_HOST-as" LD="$TOOLBIN/$GLIBC_HOST-ld" \
            CFLAGS="-O2 -g -m$ABI" libc_cv_slibdir="/lib$ABI" libc_cv_rtlddir="/lib$ABI" \
            libc_cv_forced_unwind=yes libc_cv_c_cleanup=yes \
            "$GLIBC_SOURCE/configure" --build="$(/usr/bin/gcc -dumpmachine)" \
                --host="$GLIBC_HOST" --prefix=/usr --libdir="$ABI_LIB" \
                --with-headers="$SYSROOT/usr/include" --enable-kernel=4.14.0 --disable-werror
    fi
}
glibc_bootstrap_action() {
    glibc_configure
    # glibc 2.44 has install-headers, not install-bootstrap-headers. Use the
    # exact upstream generic template, never fabricated stubs-32.h/stubs-64.h.
    make install-headers install_root="$SYSROOT"
    install -D -m 0644 "$GLIBC_SOURCE/include/gnu/stubs.h" "$SYSROOT/usr/include/gnu/stubs.h"
    cmp "$GLIBC_SOURCE/include/gnu/stubs.h" "$SYSROOT/usr/include/gnu/stubs.h"
    make -j"$JOBS" csu/subdir_lib
    mkdir -p "$SYSROOT$ABI_LIB"
    install -m 0644 csu/{crt1,crti,crtn}.o "$SYSROOT$ABI_LIB/"
    # Keep an audit copy: full glibc installation replaces the sysroot copy
    # with a linker script. Earlier receipts must accept that valid transition.
    "$PREFIX/bin/$TARGET-gcc" "-m$ABI" --sysroot="$SYSROOT" \
        -nostdlib -nostartfiles -shared -x c /dev/null -o libc-bootstrap.so
    install -m 0644 libc-bootstrap.so "$SYSROOT$ABI_LIB/libc.so"
}
glibc_bootstrap_verify() {
    local crt
    [ -s "$SYSROOT/usr/include/bits/stdio_lim.h" ] || return 1
    [ -s "$SYSROOT/usr/include/gnu/stubs.h" ] || return 1
    for crt in crt1 crti crtn; do
        gentoo_verify_elf "$GLIBC_BUILD/csu/$crt.o" "ELF$ABI" || return 1
        gentoo_verify_elf "$SYSROOT$ABI_LIB/$crt.o" "ELF$ABI" || return 1
    done
    gentoo_verify_elf "$GLIBC_BUILD/libc-bootstrap.so" "ELF$ABI"
}

ABI=32 GLIBC_HOST=i686-pc-linux-gnu ABI_LIB=/usr/lib32 GLIBC_BUILD="$BUILDS/glibc-i686"
fingerprint=$(gentoo_toolchain_fingerprint stages/17-glibc-bootstrap.sh sources/glibc.lock "$WORK_ROOT/state/16-gcc-linux-stage1.done")
gentoo_run_stage 17-glibc-bootstrap-32 "$fingerprint" glibc_bootstrap_action glibc_bootstrap_verify
ABI=64 GLIBC_HOST=$TARGET ABI_LIB=/usr/lib64 GLIBC_BUILD="$BUILDS/glibc-x86_64"
gentoo_run_stage 17-glibc-bootstrap-64 "$fingerprint" glibc_bootstrap_action glibc_bootstrap_verify
