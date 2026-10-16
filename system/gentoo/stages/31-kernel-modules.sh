#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
kernel_modules_action() {
    cmp "$KERNEL_BUILD/.config" "$KERNEL_ARTIFACTS/config"
    [ "$(kernel_make -s --no-print-directory kernelrelease)" = "$KERNEL_RELEASE" ]
    kernel_make -j"$JOBS" modules
    kernel_make INSTALL_MOD_PATH="$KERNEL_ARTIFACTS/modules" DEPMOD=/bin/true modules_install
    # These build/source symlinks are not runtime inputs and point outside the
    # durable artifact bundle. Never copy them into a root or initramfs.
    rm -f "$KERNEL_ARTIFACTS/modules/lib/modules/$KERNEL_RELEASE/"{build,source}
    kernel_check_modules
    # Paths are relative, so host extraction and later root installation can
    # verify identical content without reconstructing absolute guest paths.
    temporary=$(mktemp "$WORK_ROOT/state/.kernel-manifest.XXXXXX")
    (cd "$KERNEL_ARTIFACTS" && find . -type f ! -name SHA256SUMS -print0 |
        LC_ALL=C sort -z | xargs -0 sha256sum) > "$temporary"
    sync
    mv "$temporary" "$KERNEL_ARTIFACTS/SHA256SUMS"
    sync
}
kernel_modules_verify() {
    kernel_verify || return 1
    (cd "$KERNEL_ARTIFACTS" && sha256sum -c SHA256SUMS) || return 1
    kernel_check_modules
}
fingerprint=$(gentoo_toolchain_fingerprint stages/31-kernel-modules.sh lib/kernel.sh "$WORK_ROOT/state/30-kernel.done")
gentoo_run_stage 31-kernel-modules "$fingerprint" kernel_modules_action kernel_modules_verify
