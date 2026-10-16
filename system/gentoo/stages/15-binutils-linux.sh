#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
gentoo_prepare_source sources/binutils.lock
binutils_linux_action() {
    mkdir -p "$BUILDS/binutils-linux" "$SYSROOT/lib64" "$PREFIX/$TARGET/bin"
    cd "$BUILDS/binutils-linux"
    if [ ! -f Makefile ]; then
        "$SOURCES/binutils-2.46.1/configure" --target="$TARGET" --prefix="$LINUX_BINUTILS" \
            --with-sysroot="$SYSROOT" \
            --with-lib-path==/usr/lib64:=/lib64:=/usr/lib32:=/lib32:=/usr/lib:=/lib \
            --disable-nls --disable-gprofng --disable-werror --disable-multilib
    fi
    make -j"$JOBS"
    make install
    # libgcc and glibc invoke triplet-prefixed tools recursively. Both triples
    # name the same multiformat tools; GCC passes the ABI emulation explicitly.
    local tuple tool source_tool
    for tuple in "$TARGET" i686-pc-linux-gnu; do
        for tool in ar as ld nm objcopy objdump ranlib readelf strip; do
            source_tool="$LINUX_BINUTILS/bin/$TARGET-$tool"
            [ -x "$source_tool" ] || gentoo_die "missing binutils tool: $source_tool"
            ln -sfn "$source_tool" "$TOOLBIN/$tuple-$tool"
            ln -sfn "$source_tool" "$PREFIX/bin/$tuple-$tool"
        done
    done
    for tool in ar as ld nm objcopy objdump ranlib readelf strip; do
        ln -sfn "$LINUX_BINUTILS/bin/$TARGET-$tool" "$PREFIX/$TARGET/bin/$tool"
    done
}
binutils_linux_verify() {
    local paths path tuple tool expected emulation
    "$LINUX_BINUTILS/bin/$TARGET-ld" -V | grep -F elf_i386 || return 1
    for emulation in elf_x86_64 elf_i386; do
        paths=$("$LINUX_BINUTILS/bin/$TARGET-ld" -m "$emulation" --verbose | grep -o 'SEARCH_DIR("[^"]*")' | sed 's/^SEARCH_DIR("//;s/")$//') || return 1
        [ -n "$paths" ] || return 1
        # '=' means relative to --sysroot. An absolute host /lib must never leak
        # into either ABI's default path, even if the desired paths also exist.
        while IFS= read -r path; do [[ "$path" == =/* ]] || return 1; done <<< "$paths"
        for path in /usr/lib64 /lib64 /usr/lib32 /lib32 /usr/lib /lib; do
            printf '%s\n' "$paths" | grep -Fx "=$path" || return 1
        done
    done
    for tuple in "$TARGET" i686-pc-linux-gnu; do
        for tool in ar as ld nm objcopy objdump ranlib readelf strip; do
            expected="$LINUX_BINUTILS/bin/$TARGET-$tool"
            [ -x "$expected" ] || return 1
            [ "$(readlink -f "$TOOLBIN/$tuple-$tool")" = "$expected" ] || return 1
            [ "$(readlink -f "$PREFIX/bin/$tuple-$tool")" = "$expected" ] || return 1
        done
    done
    for tool in ar as ld nm objcopy objdump ranlib readelf strip; do
        [ "$(readlink -f "$PREFIX/$TARGET/bin/$tool")" = "$LINUX_BINUTILS/bin/$TARGET-$tool" ] || return 1
    done
}
fingerprint=$(gentoo_toolchain_fingerprint stages/15-binutils-linux.sh sources/binutils.lock "$WORK_ROOT/state/14-linux-headers.done")
gentoo_run_stage 15-binutils-linux "$fingerprint" binutils_linux_action binutils_linux_verify
