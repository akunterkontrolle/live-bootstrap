#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Refresh the C runtimes against completed glibc, then verify real link results.
gcc_linux_final_action() {
    cd "$GCC_C_BUILD"
    make -j"$JOBS" all-gcc
    gcc_linux_build_libgcc
    make install-gcc
}
fingerprint=$(gentoo_toolchain_fingerprint stages/20-verify-sysroot.sh stages/18-gcc-linux-runtime.sh sources/gcc.lock \
    "$WORK_ROOT/state/19-glibc-32.done" "$WORK_ROOT/state/19-glibc-64.done")
gentoo_run_stage 20-gcc-linux-final "$fingerprint" gcc_linux_final_action gcc_linux_runtime_verify

sysroot_link_verify() {
    local abi interpreter result
    [ "$("$PREFIX/bin/$TARGET-gcc" -print-sysroot)" = "$SYSROOT" ] || return 1
    gentoo_verify_multilib || return 1
    gentoo_verify_libgcc || return 1
    printf '#include <stdio.h>\nint main(void) { return puts("continuation libc probe") < 0; }\n' > "$BUILDS/probes/glibc.c"
    for abi in 64 32; do
        result="$BUILDS/probes/glibc-probe-$abi"
        "$PREFIX/bin/$TARGET-gcc" "-m$abi" --sysroot="$SYSROOT" -B"$SYSROOT/usr/lib$abi/" \
            "$BUILDS/probes/glibc.c" -o "$result" || return 1
        gentoo_verify_elf "$result" "ELF$abi" || return 1
        if [ "$abi" = 32 ]; then interpreter=/lib/ld-linux.so.2
        else interpreter=/lib64/ld-linux-x86-64.so.2; fi
        /usr/bin/readelf -l "$result" | grep -F "[Requesting program interpreter: $interpreter]" || return 1
        /usr/bin/readelf -d "$result" | grep -F 'Shared library: [libc.so.6]' || return 1
    done
}
sysroot_link_action() {
    # The verifier always recompiles/relinks, including on resumption. Nothing
    # attempts to execute ELF64 on the original 32-bit bootstrap kernel.
    sysroot_link_verify
}
fingerprint=$(gentoo_toolchain_fingerprint stages/20-verify-sysroot.sh "$WORK_ROOT/state/20-gcc-linux-final.done")
gentoo_run_stage 20-sysroot-links "$fingerprint" sysroot_link_action sysroot_link_verify
printf 'Initial GNU sysroot verified: ELF64/ELF32 startup objects, loaders, libc and C link probes.\n'
printf 'Compiler: %s/bin/%s-gcc\nSysroot: %s\n' "$PREFIX" "$TARGET" "$SYSROOT"
printf 'Execution tests wait for the modern x86_64 kernel and batch 4.\n'
