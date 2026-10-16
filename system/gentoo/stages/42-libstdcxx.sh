#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
libstdcxx_action() {
    local file abi seen32=0 seen64=0
    cd "$GCC_CXX_BUILD"
    make "CC=$GCC_CXX_BUILD/cc-for-target" "CC_FOR_TARGET=$GCC_CXX_BUILD/cc-for-target" \
        "CXX_FOR_TARGET=$GCC_CXX_BUILD/cxx-for-target" configure-target-libstdc++-v3
    while IFS= read -r file; do
        case "$file" in */32/*) abi=32; seen32=1 ;; *) abi=64; seen64=1 ;; esac
        cxx_fix_libtool "$file" "$abi"
    done < <(find "$GCC_CXX_BUILD/$TARGET" -path '*/libstdc++-v3/*' -type f -name libtool -print)
    [ "$seen32$seen64" = 11 ] || gentoo_die 'libstdc++ configure did not produce both ABI libtools'
    make -C "$GCC_CXX_BUILD/$TARGET/libstdc++-v3" -j"$JOBS" \
        "CXX=$GCC_CXX_BUILD/cxx-for-target" all
    make "CC=$GCC_CXX_BUILD/cc-for-target" "CC_FOR_TARGET=$GCC_CXX_BUILD/cc-for-target" \
        "CXX_FOR_TARGET=$GCC_CXX_BUILD/cxx-for-target" "CXX=$GCC_CXX_BUILD/cxx-for-target" \
        install-target-libstdc++-v3
}
libstdcxx_verify() {
    local abi library name
    [ "$("$PREFIX/bin/$TARGET-g++" -dumpmachine)" = "$TARGET" ] || return 1
    [ "$("$PREFIX/bin/$TARGET-g++" -dumpfullversion)" = 16.2.0 ] || return 1
    for abi in 64 32; do
        for name in libstdc++.so.6 libstdc++.a libsupc++.a; do
            library=$("$PREFIX/bin/$TARGET-g++" "-m$abi" "-print-file-name=$name") || return 1
            case "$(readlink -f "$library")" in "$PREFIX/"*) ;; *) return 1 ;; esac
            gentoo_verify_elf "$library" "ELF$abi" || return 1
        done
        "$PREFIX/bin/$TARGET-g++" "-m$abi" --sysroot="$SYSROOT" -B"$SYSROOT/usr/lib$abi/" \
            -pthread "$SCRIPT_DIR/probes/runtime.cc" -o "$CXX_PROBES/libstdcxx-link-$abi" || return 1
        gentoo_verify_elf "$CXX_PROBES/libstdcxx-link-$abi" "ELF$abi" || return 1
    done
}
fingerprint=$(cxx_fingerprint stages/42-libstdcxx.sh probes/runtime.cc "$WORK_ROOT/state/41-cxx-libgcc.done")
gentoo_run_stage 42-libstdcxx "$fingerprint" libstdcxx_action libstdcxx_verify
