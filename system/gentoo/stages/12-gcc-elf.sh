#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
gentoo_prepare_source sources/gcc.lock
gcc_elf_action() {
    mkdir -p "$BUILDS/gcc-x86_64-elf"
    cd "$BUILDS/gcc-x86_64-elf"
    if [ ! -f Makefile ]; then
        PATH="$ELF_PREFIX/bin:$PATH" "$GCC_SOURCE/configure" \
            --target=x86_64-elf --prefix="$ELF_PREFIX" --with-sysroot="$WORK_ROOT/sysroot" \
            --with-gmp=/usr --with-mpfr=/usr --with-mpc=/usr \
            --with-newlib --without-headers --enable-languages=c \
            --disable-bootstrap --disable-nls --disable-shared --disable-threads \
            --disable-libatomic --disable-libgomp --disable-libquadmath --disable-libssp \
            --disable-libstdcxx --disable-libvtv --disable-libsanitizer --disable-lto \
            --disable-multilib --disable-werror
    fi
    PATH="$ELF_PREFIX/bin:$PATH" make -j"$JOBS" all-gcc all-target-libgcc
    PATH="$ELF_PREFIX/bin:$PATH" make install-gcc install-target-libgcc
}
gcc_elf_verify() {
    [ "$("$ELF_PREFIX/bin/x86_64-elf-gcc" -dumpmachine)" = x86_64-elf ] || return 1
    [ "$("$ELF_PREFIX/bin/x86_64-elf-gcc" -dumpfullversion)" = 16.2.0 ] || return 1
    printf 'int continuation_probe;\n' > "$BUILDS/probes/bare.c"
    "$ELF_PREFIX/bin/x86_64-elf-gcc" -c "$BUILDS/probes/bare.c" -o "$BUILDS/probes/bare.o" || return 1
    gentoo_verify_elf "$BUILDS/probes/bare.o" ELF64 || return 1
    local archive
    archive=$("$ELF_PREFIX/bin/x86_64-elf-gcc" -print-libgcc-file-name) || return 1
    gentoo_verify_elf "$archive" ELF64
}
fingerprint=$(gentoo_toolchain_fingerprint stages/12-gcc-elf.sh sources/gcc.lock "$WORK_ROOT/state/11-binutils-elf.done")
gentoo_run_stage 12-gcc-elf "$fingerprint" gcc_elf_action gcc_elf_verify
