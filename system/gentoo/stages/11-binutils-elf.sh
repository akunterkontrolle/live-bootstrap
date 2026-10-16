#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Bare ELF tools keep the first cross compiler independent of Linux libc.
gentoo_prepare_source sources/binutils.lock
binutils_elf_action() {
    mkdir -p "$BUILDS/binutils-x86_64-elf"
    cd "$BUILDS/binutils-x86_64-elf"
    if [ ! -f Makefile ]; then
        "$SOURCES/binutils-2.46.1/configure" --target=x86_64-elf --prefix="$ELF_PREFIX" \
            --disable-nls --disable-gprofng --disable-werror --disable-multilib
    fi
    make -j"$JOBS"
    make install
}
binutils_elf_verify() {
    "$ELF_PREFIX/bin/x86_64-elf-as" --version || return 1
    printf '.text\n.globl probe\nprobe: ret\n' > "$BUILDS/probes/elf.s"
    "$ELF_PREFIX/bin/x86_64-elf-as" --64 "$BUILDS/probes/elf.s" -o "$BUILDS/probes/elf.o" || return 1
    "$ELF_PREFIX/bin/x86_64-elf-ld" -r "$BUILDS/probes/elf.o" -o "$BUILDS/probes/elf-linked.o" || return 1
    gentoo_verify_elf "$BUILDS/probes/elf-linked.o" ELF64
}
fingerprint=$(gentoo_toolchain_fingerprint stages/11-binutils-elf.sh sources/binutils.lock)
gentoo_run_stage 11-binutils-elf "$fingerprint" binutils_elf_action binutils_elf_verify
