#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
. "$SCRIPT_DIR/lib/handoff.sh"
input_digest=$(basename "$SCRIPT_DIR")
[[ "$input_digest" =~ ^[0-9a-f]{64}$ ]] || gentoo_die 'handoff must use a content-addressed input snapshot'
handoff="$WORK_ROOT/handoff/$input_digest"
render_handoff() {
    local manifest kernel_receipt modules_receipt boot_id
    manifest=$(sha256sum < "$KERNEL_ARTIFACTS/SHA256SUMS" | cut -d' ' -f1)
    kernel_receipt=$(sha256sum < "$WORK_ROOT/state/30-kernel.done" | cut -d' ' -f1)
    modules_receipt=$(sha256sum < "$WORK_ROOT/state/31-kernel-modules.done" | cut -d' ' -f1)
    boot_id=$(cat "$KERNEL_ARTIFACTS/build-boot-id")
    [[ "$boot_id" =~ ^[0-9a-f-]{36}$ && "$GENTOO_DISCOVERED_WORKSPACE_UUID" =~ ^[0-9a-fA-F-]{36}$ ]] || return 1
    cat <<RECORD
{
  "version": 1,
  "release": "$KERNEL_RELEASE",
  "boot_id": "$boot_id",
  "workspace_uuid": "$GENTOO_DISCOVERED_WORKSPACE_UUID",
  "inputs": "$input_digest",
  "kernel_manifest_sha256": "$manifest",
  "kernel_receipt_sha256": "$kernel_receipt",
  "modules_receipt_sha256": "$modules_receipt"
}
RECORD
}
handoff_action() {
    local temporary
    mkdir -p "$WORK_ROOT/handoff"
    temporary=$(mktemp -d "$WORK_ROOT/handoff/.pending.XXXXXX")
    render_handoff > "$temporary/handoff.json"
    cp "$SCRIPT_DIR/post-reboot.sh" "$temporary/resume.sh"
    cp "$SCRIPT_DIR/shutdown.sh" "$temporary/shutdown.sh"
    sync
    # A crash after publication but before its receipt may leave this directory.
    # Compare it on resumption; never overwrite a different handoff silently.
    if [ -d "$handoff" ]; then
        cmp "$temporary/handoff.json" "$handoff/handoff.json"
        cmp "$temporary/resume.sh" "$handoff/resume.sh"
        cmp "$temporary/shutdown.sh" "$handoff/shutdown.sh"
        rm -r "$temporary"
    else
        mv "$temporary" "$handoff"
    fi
    sync
}
handoff_verify() {
    local expected
    expected=$(render_handoff) || return 1
    [ "$(cat "$handoff/handoff.json")" = "$expected" ] || return 1
    cmp "$SCRIPT_DIR/post-reboot.sh" "$handoff/resume.sh" || return 1
    cmp "$SCRIPT_DIR/shutdown.sh" "$handoff/shutdown.sh"
}
fingerprint=$(gentoo_fingerprint stages/32-handoff.sh post-reboot.sh shutdown.sh SHA256SUMS \
    "$KERNEL_ARTIFACTS/SHA256SUMS" "$WORK_ROOT/state/30-kernel.done" "$WORK_ROOT/state/31-kernel-modules.done")
gentoo_run_stage "32-handoff-$input_digest" "$fingerprint" handoff_action handoff_verify
# This command remains available while /build is unmounted after cold boot.
gentoo_publish_bootstrap_resume "${GENTOO_BOOTSTRAP_ROOT:-/}"
# The host reads this only after the workspace is cleanly unmounted.
temporary=$(mktemp "$WORK_ROOT/.handoff-current.XXXXXX")
printf '%s\n' "$input_digest" > "$temporary"
sync
mv "$temporary" "$WORK_ROOT/current-handoff"
sync
GENTOO_MODERN_KERNEL_READY=0
if [ "$(uname -r)" = "$KERNEL_RELEASE" ] && [ "$(uname -m)" = x86_64 ] &&
    [ "$(cat /proc/sys/kernel/random/boot_id)" != "$(cat "$KERNEL_ARTIFACTS/build-boot-id")" ]; then
    [ "$(kernel_running_build_id)" = "$(cat "$KERNEL_ARTIFACTS/build-id")" ] ||
        gentoo_die 'running kernel build ID differs from the saved kernel; boot the verified handoff image'
    GENTOO_MODERN_KERNEL_READY=1
    printf 'Modern-kernel cold boot verified.\n'
else
    printf '\nSHUTDOWN MILESTONE: kernel, modules and all continuation inputs are durable.\n'
    printf 'From the guest console: cd /; bash %s/shutdown.sh\n' "$handoff"
    printf 'VM host: python3 system/gentoo/vm.py continue --run-dir RUN_DIRECTORY --apply\n'
    printf 'After cold boot, guest root: /usr/local/sbin/gentoo-resume\n'
    printf 'T440p: boot the saved kernel with the existing bootstrap root; see README.\n'
fi
