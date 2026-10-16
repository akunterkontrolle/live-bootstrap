#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Fixed layout for batches 2–4. Later-stage recipes live in separate files.
TARGET=x86_64-pc-linux-gnu
ELF_PREFIX="$WORK_ROOT/toolchain"
LINUX_BINUTILS="$WORK_ROOT/linux-target-binutils"
PREFIX="$WORK_ROOT/linux-toolchain"
TOOLBIN="$WORK_ROOT/linux-target-tools/bin"
HOST_TOOLS="$WORK_ROOT/host-tools"
SYSROOT="$WORK_ROOT/sysroot-x86_64"
SOURCES="$WORK_ROOT/sources"
BUILDS="$WORK_ROOT/build"
GCC_SOURCE="$SOURCES/gcc-16.2.0"
GLIBC_SOURCE="$SOURCES/glibc-2.44"
LINUX_SOURCE="$SOURCES/linux-6.12.111"
JOBS=${GENTOO_BUILD_SOURCE_JOBS:?missing source-build concurrency}
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || gentoo_die 'invalid source_jobs'
# Do not let the invoking shell inject native flags or a different toolchain.
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS LIBS CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH LIBRARY_PATH
unset GCC_EXEC_PREFIX COMPILER_PATH MAKEFLAGS MFLAGS CONFIG_SITE AS LD AR NM RANLIB STRIP
export CC=/usr/bin/gcc CXX=/usr/bin/g++ LC_ALL=C
export PATH="$TOOLBIN:$PREFIX/bin:$HOST_TOOLS/bin:/usr/bin:/usr/sbin:/bin:/sbin"

# Hash only the recipe, its repeated helpers, selected locks and explicit
# prerequisite receipts. Concurrency, documentation and later stages stay out.
gentoo_toolchain_fingerprint() {
    gentoo_fingerprint lib/toolchain.sh lib/sources.sh lib/state.sh \
        "$WORK_ROOT/state/host-toolchain.inputs" "$@"
}

gentoo_verify_elf() {
    local file=$1 expected=$2 machine
    case "$expected" in ELF32) machine='Intel 80386' ;; ELF64) machine='Advanced Micro Devices X86-64' ;; *) return 1 ;; esac
    # Inspect EVERY member when file is an archive, not just the first match.
    /usr/bin/readelf -h "$file" | awk -v want="$expected" -v machine="$machine" '
        $1=="Class:" {count++; if ($2 != want) bad=1}
        $1=="Machine:" {sub(/^[[:space:]]*Machine:[[:space:]]*/, ""); machines++; if ($0 != machine) bad=1}
        END {exit !(count && machines==count && !bad)}'
}

gentoo_verify_c_compiler() {
    local compiler=$1 triple=$2 abi
    [ "$("$compiler" -dumpmachine)" = "$triple" ] || return 1
    [ "$("$compiler" -dumpfullversion)" = 16.2.0 ] || return 1
    printf 'int continuation_object;\n' > "$BUILDS/probes/object.c"
    for abi in 64 32; do
        "$compiler" "-m$abi" -ffreestanding -c "$BUILDS/probes/object.c" \
            -o "$BUILDS/probes/object-$abi.o" || return 1
        gentoo_verify_elf "$BUILDS/probes/object-$abi.o" "ELF$abi" || return 1
    done
}

gentoo_verify_multilib() {
    local rows
    rows=$("$PREFIX/bin/$TARGET-gcc" -print-multi-lib) || return 1
    printf '%s\n' "$rows" | grep -Fx '.;' || return 1
    printf '%s\n' "$rows" | grep -E '^32;@?m32(;.*)?$' || return 1
    gentoo_verify_c_compiler "$PREFIX/bin/$TARGET-gcc" "$TARGET"
}

gentoo_verify_libgcc() {
    local abi archive shared
    for abi in 32 64; do
        archive=$("$PREFIX/bin/$TARGET-gcc" "-m$abi" -print-file-name=libgcc.a) || return 1
        shared=$("$PREFIX/bin/$TARGET-gcc" "-m$abi" -print-file-name=libgcc_s.so.1) || return 1
        [ -s "$archive" ] && [ -s "$shared" ] || return 1
        gentoo_verify_elf "$archive" "ELF$abi" || return 1
        gentoo_verify_elf "$shared" "ELF$abi" || return 1
        printf 'long long divide(long long x, long long y) { return x / y; }\n' > "$BUILDS/probes/libgcc.c"
        "$PREFIX/bin/$TARGET-gcc" "-m$abi" --sysroot="$SYSROOT" \
            -fPIC -nostdlib -nostartfiles -shared -Wl,--no-undefined \
            "$BUILDS/probes/libgcc.c" -lgcc -o "$BUILDS/probes/libgcc-$abi.so" || return 1
        gentoo_verify_elf "$BUILDS/probes/libgcc-$abi.so" "ELF$abi" || return 1
    done
}
