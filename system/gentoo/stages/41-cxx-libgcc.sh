#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
cxx_libgcc_action() {
    cd "$GCC_CXX_BUILD"
    cxx_write_wrappers
    make "CC_FOR_TARGET=$GCC_CXX_BUILD/cc-for-target" configure-target-libgcc
    cxx_bind_libgcc
    # GCC's generated ELF64 links otherwise shadow the ELF32 glibc objects.
    cxx_remove_shadow_crt
    cxx_verify_crt_search
    make -j"$JOBS" "CC=$GCC_CXX_BUILD/cc-for-target" \
        "CC_FOR_TARGET=$GCC_CXX_BUILD/cc-for-target" all-target-libgcc
    # libstdc++ configure needs installed compiler headers and shared libgcc.
    make "CC=$GCC_CXX_BUILD/cc-for-target" \
        "CC_FOR_TARGET=$GCC_CXX_BUILD/cc-for-target" install-gcc install-target-libgcc
}
cxx_libgcc_verify() {
    cxx_verify_crt_search || return 1
    gentoo_verify_multilib || return 1
    gentoo_verify_libgcc
}
fingerprint=$(cxx_fingerprint stages/41-cxx-libgcc.sh "$WORK_ROOT/state/40-gcc-cxx.done")
gentoo_run_stage 41-cxx-libgcc "$fingerprint" cxx_libgcc_action cxx_libgcc_verify
