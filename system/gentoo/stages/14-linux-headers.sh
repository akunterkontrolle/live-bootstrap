#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
gentoo_prepare_source sources/linux.lock
linux_headers_action() {
    mkdir -p "$BUILDS/linux-headers" "$SYSROOT/usr"
    # Pinned Linux lists headers_% among its no-dot-config targets. Export
    # UAPI headers without configuring or compiling the continuation kernel.
    # Keep generated files separate from the later kernel build.
    make -C "$LINUX_SOURCE" O="$BUILDS/linux-headers" ARCH=x86_64 HOSTCC=/usr/bin/gcc \
        headers_install INSTALL_HDR_PATH="$SYSROOT/usr"
}
linux_headers_verify() {
    local header
    for header in linux/version.h linux/types.h asm/unistd.h asm/unistd_32.h asm/unistd_64.h; do
        [ -s "$SYSROOT/usr/include/$header" ] || return 1
    done
    grep -Fx '#define LINUX_VERSION_CODE 396399' "$SYSROOT/usr/include/linux/version.h"
}
fingerprint=$(gentoo_toolchain_fingerprint stages/14-linux-headers.sh sources/linux.lock "$WORK_ROOT/state/13-host-rsync.done")
gentoo_run_stage 14-linux-headers "$fingerprint" linux_headers_action linux_headers_verify
