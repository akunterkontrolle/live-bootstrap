#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Native x86_64 root assembled from the already verified multilib sysroot.
. "$SCRIPT_DIR/lib/mounts.sh"
NATIVE_ROOT="$WORK_ROOT/native-root-x86_64"
NATIVE_SOURCES="$WORK_ROOT/sources/native"
NATIVE_BUILDS="$WORK_ROOT/build/native"
NATIVE_DISTFILES="$WORK_ROOT/distfiles"
NATIVE_JOBS=${GENTOO_BUILD_SOURCE_JOBS:?missing source-build concurrency}
NATIVE_CROSS_GCC="$PREFIX/bin/$TARGET-gcc"
NATIVE_CROSS_GXX="$PREFIX/bin/$TARGET-g++"
NATIVE_TRIPLE=x86_64-pc-linux-gnu

native_verify_elf() {
    local path=$1 class=$2
    /usr/bin/readelf -h "$path" | awk -v class="$class" '$1=="Class:" && $2==class {ok=1} END {exit !ok}'
}

native_run() {
    "$NATIVE_ROOT/lib64/ld-linux-x86-64.so.2" \
        --library-path "$NATIVE_ROOT/lib64:$NATIVE_ROOT/usr/lib64:$NATIVE_ROOT/usr/lib" "$@"
}

native_recipe_fingerprint() {
    gentoo_fingerprint lib/native.sh lib/sources.sh lib/state.sh "$@"
}
