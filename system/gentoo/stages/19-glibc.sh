#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Reuse each ABI's configured bootstrap tree, now with real source-built libgcc.
glibc_full_action() {
    glibc_configure
    if [ "$ABI" = 64 ] && [ -s "$SYSROOT/usr/include/gnu/stubs-32.h" ] && \
        [ ! -s "$SYSROOT/usr/include/gnu/stubs-64.h" ]; then
        # i686 installation writes its generated dispatcher before x86_64
        # exists. Restore the upstream generic template until x86_64 installs.
        make install-headers install_root="$SYSROOT"
        install -D -m 0644 "$GLIBC_SOURCE/include/gnu/stubs.h" "$SYSROOT/usr/include/gnu/stubs.h"
        cmp "$GLIBC_SOURCE/include/gnu/stubs.h" "$SYSROOT/usr/include/gnu/stubs.h"
    fi
    # Keep the handwritten signal-restorer unwind record while suppressing
    # only the conflicting compiler-generated CFI with this pinned GCC/glibc.
    touch configparms
    if ! grep -Fxq 'CFLAGS-libc_sigaction.c = -fno-asynchronous-unwind-tables' configparms; then
        printf '%s\n' 'CFLAGS-libc_sigaction.c = -fno-asynchronous-unwind-tables' >> configparms
    fi
    # No target libstdc++ exists yet. glibc uses its C DSO helper at this stage.
    make -j"$JOBS" CXX=
    make CXX= install_root="$SYSROOT" install
    if [ "$ABI" = 32 ]; then
        mkdir -p "$SYSROOT/lib"
        # The i386 interpreter ABI is /lib/ld-linux.so.2, not /lib32.
        ln -sfn ../lib32/ld-linux.so.2 "$SYSROOT/lib/ld-linux.so.2"
    fi
}
glibc_full_verify() {
    local crt loader
    [ -s "$SYSROOT/usr/include/gnu/stubs-$ABI.h" ] || return 1
    [ -s "$SYSROOT$ABI_LIB/libc.so" ] || return 1
    # libc.so must now be the real linker script, not the temporary empty DSO.
    grep -F 'GROUP (' "$SYSROOT$ABI_LIB/libc.so" || return 1
    gentoo_verify_elf "$SYSROOT/lib$ABI/libc.so.6" "ELF$ABI" || return 1
    for crt in crt1 crti crtn; do
        gentoo_verify_elf "$SYSROOT$ABI_LIB/$crt.o" "ELF$ABI" || return 1
    done
    if [ "$ABI" = 32 ]; then loader=ld-linux.so.2; else loader=ld-linux-x86-64.so.2; fi
    gentoo_verify_elf "$SYSROOT/lib$ABI/$loader" "ELF$ABI" || return 1
    if [ "$ABI" = 32 ]; then
        [ "$(readlink "$SYSROOT/lib/ld-linux.so.2")" = ../lib32/ld-linux.so.2 ] || return 1
    fi
}
ABI=32 GLIBC_HOST=i686-pc-linux-gnu ABI_LIB=/usr/lib32 GLIBC_BUILD="$BUILDS/glibc-i686"
fingerprint=$(gentoo_toolchain_fingerprint stages/19-glibc.sh stages/17-glibc-bootstrap.sh sources/glibc.lock "$WORK_ROOT/state/18-gcc-linux-runtime.done")
gentoo_run_stage 19-glibc-32 "$fingerprint" glibc_full_action glibc_full_verify
ABI=64 GLIBC_HOST=$TARGET ABI_LIB=/usr/lib64 GLIBC_BUILD="$BUILDS/glibc-x86_64"
fingerprint=$(gentoo_toolchain_fingerprint stages/19-glibc.sh stages/17-glibc-bootstrap.sh sources/glibc.lock \
    "$WORK_ROOT/state/18-gcc-linux-runtime.done" "$WORK_ROOT/state/19-glibc-32.done")
gentoo_run_stage 19-glibc-64 "$fingerprint" glibc_full_action glibc_full_verify
