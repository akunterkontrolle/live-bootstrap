#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
gentoo_prepare_source sources/linux.lock
# Retain the pinned detached signature for host verification before cold boot.
IFS='|' read -r signature_name signature_url signature_digest < <(gentoo_source_record sources/linux-signature.lock)
gentoo_fetch_source "$signature_url" "$signature_digest" "$WORK_ROOT/distfiles/$signature_name"
. "$SCRIPT_DIR/lib/kernel.sh"
kernel_action() {
    mkdir -p "$KERNEL_BUILD" "$KERNEL_ARTIFACTS"
    kernel_check_compiler
    kernel_make x86_64_defconfig
    (cd "$KERNEL_BUILD" && "$LINUX_SOURCE/scripts/kconfig/merge_config.sh" -m .config "$KERNEL_FRAGMENT")
    # Always resolve new symbols noninteractively, including after clock skew.
    kernel_make olddefconfig
    kernel_check_config "$KERNEL_BUILD/.config"
    [ "$(kernel_make -s --no-print-directory kernelrelease)" = "$KERNEL_RELEASE" ]
    kernel_make -j"$JOBS" bzImage
    install -m 0644 "$KERNEL_BUILD/arch/x86/boot/bzImage" "$KERNEL_ARTIFACTS/bzImage"
    install -m 0644 "$KERNEL_BUILD/.config" "$KERNEL_ARTIFACTS/config"
    install -m 0644 "$KERNEL_BUILD/System.map" "$KERNEL_ARTIFACTS/System.map"
    build_id=$(/usr/bin/readelf -n "$KERNEL_BUILD/vmlinux" | awk '/Build ID:/ {print $3}')
    [[ "$build_id" =~ ^[0-9a-f]{40}$ ]]
    printf '%s\n' "$build_id" > "$KERNEL_ARTIFACTS/build-id"
    printf '%s\n' "$KERNEL_RELEASE" > "$KERNEL_ARTIFACTS/kernelrelease"
    cat /proc/sys/kernel/random/boot_id > "$KERNEL_ARTIFACTS/build-boot-id"
    # Stage 31 extends this artifact directory with matching modules. Retain a
    # separate kernel-only manifest so it can be verified before that stage.
    (cd "$KERNEL_ARTIFACTS" && sha256sum bzImage config System.map kernelrelease build-boot-id build-id) > "$KERNEL_BUILD/kernel.SHA256SUMS"
    install -m 0644 "$KERNEL_BUILD/kernel.SHA256SUMS" "$KERNEL_ARTIFACTS/kernel.SHA256SUMS"
}
kernel_verify() {
    (cd "$KERNEL_ARTIFACTS" && sha256sum -c kernel.SHA256SUMS) || return 1
    kernel_check_image "$KERNEL_ARTIFACTS/bzImage" || return 1
    kernel_check_config "$KERNEL_ARTIFACTS/config" || return 1
    cmp "$KERNEL_BUILD/.config" "$KERNEL_ARTIFACTS/config" || return 1
    [ "$(cat "$KERNEL_ARTIFACTS/kernelrelease")" = "$KERNEL_RELEASE" ]
}
fingerprint=$(gentoo_toolchain_fingerprint stages/30-kernel.sh lib/kernel.sh \
    kernel-6.12.111.config.fragment sources/linux.lock \
    "$WORK_ROOT/state/20-sysroot-links.done" "$WORK_ROOT/state/13-host-libelf.done")
gentoo_run_stage 30-kernel "$fingerprint" kernel_action kernel_verify
