#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Called before any resume can mutate a root built for an earlier guest CPU.
gentoo_verify_recorded_cpu() {
    local receipt="$WORK_ROOT/state/native-cpu-policy.json"
    if [ ! -f "$receipt" ]; then
        [ ! -e "$WORK_ROOT/state/80-native-gentoo-system.inputs" ] &&
            [ ! -e "$WORK_ROOT/state/80-native-gentoo-system.done" ] ||
            gentoo_die 'existing Gentoo builds have no native CPU receipt; preserve this root and start a fresh lineage'
        return 0
    fi
    [ "$(sha256sum < "$WORK_ROOT/native-build-tools/bin/cpuid2cpuflags")" = "$(cat "$WORK_ROOT/state/cpu-detector.sha256")" ] || gentoo_die 'CPU detector identity changed'
    python3 "$SCRIPT_DIR/cpu-policy.py" --receipt "$receipt" \
        --detector "$WORK_ROOT/native-build-tools/bin/cpuid2cpuflags" \
        --compiler "$WORK_ROOT/linux-toolchain/bin/x86_64-pc-linux-gnu-gcc" || \
        gentoo_die 'CPU compatibility check failed before resumption'
}
