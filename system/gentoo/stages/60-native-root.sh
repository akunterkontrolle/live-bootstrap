#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'native root requires the verified modern-kernel handoff'
. "$SCRIPT_DIR/lib/native.sh"
native_root_action() {
    [ -x "$NATIVE_CROSS_GCC" ] && [ -x "$NATIVE_CROSS_GXX" ] || gentoo_die 'verified multilib cross compiler is missing'
    [ -s "$WORK_ROOT/state/20-sysroot-links.done" ] || gentoo_die 'verified GNU sysroot receipt is missing'
    mkdir -p "$NATIVE_SOURCES" "$NATIVE_BUILDS" "$NATIVE_DISTFILES" "$WORK_ROOT/state"
    if [ ! -s "$WORK_ROOT/state/60-native-root.started" ]; then
        printf 'started %s\nsysroot sha256=%s\n' "$(date -u +%FT%TZ)" \
            "$(sha256sum < "$WORK_ROOT/state/20-sysroot-links.done" | cut -d' ' -f1)" \
            > "$WORK_ROOT/state/60-native-root.started.tmp"
        sync "$WORK_ROOT/state/60-native-root.started.tmp"
        mv "$WORK_ROOT/state/60-native-root.started.tmp" "$WORK_ROOT/state/60-native-root.started"
        sync "$WORK_ROOT/state"
    fi
    if [ ! -s "$WORK_ROOT/state/60-native-root.copied" ]; then
        [ ! -e "$NATIVE_ROOT" ] || gentoo_die "unreceipted native root exists; inspect before continuing: $NATIVE_ROOT"
        local temporary="$WORK_ROOT/.native-root-x86_64.$$.tmp"
        mkdir "$temporary"
        cp -a "$SYSROOT/." "$temporary/"
        printf '%s\n' "$(sha256sum < "$WORK_ROOT/state/20-sysroot-links.done" | cut -d' ' -f1)" > "$temporary/.seed-receipt"
        sync
        mv "$temporary" "$NATIVE_ROOT"
        printf 'copied %s\n' "$(cat "$NATIVE_ROOT/.seed-receipt")" > "$WORK_ROOT/state/60-native-root.copied.tmp"
        sync "$WORK_ROOT/state/60-native-root.copied.tmp"
        mv "$WORK_ROOT/state/60-native-root.copied.tmp" "$WORK_ROOT/state/60-native-root.copied"
        sync "$WORK_ROOT/state"
    else
        grep -Fxq "copied $(sha256sum < "$WORK_ROOT/state/20-sysroot-links.done" | cut -d' ' -f1)" \
            "$WORK_ROOT/state/60-native-root.copied" || gentoo_die 'native-root seed receipt mismatch'
    fi
    install -d "$NATIVE_ROOT/usr/bin" "$NATIVE_ROOT/usr/sbin" "$NATIVE_ROOT/bin" \
        "$NATIVE_ROOT/etc" "$NATIVE_ROOT/dev" "$NATIVE_ROOT/tmp" "$NATIVE_ROOT/var/tmp"
    chmod 1777 "$NATIVE_ROOT/tmp" "$NATIVE_ROOT/var/tmp"
    [ -x "$NATIVE_ROOT/lib64/ld-linux-x86-64.so.2" ] || gentoo_die 'native root lacks its ELF64 loader'
    [ -d "$NATIVE_ROOT/usr/include" ] && [ -s "$NATIVE_ROOT/usr/lib32/crt1.o" ] &&
        [ -s "$NATIVE_ROOT/usr/lib64/crt1.o" ] || gentoo_die 'native root lacks verified multilib headers/startup objects'
}
native_root_verify() {
    [ -x "$NATIVE_ROOT/lib64/ld-linux-x86-64.so.2" ] &&
        [ -s "$NATIVE_ROOT/usr/include/stdio.h" ] &&
        [ -s "$NATIVE_ROOT/usr/lib32/crt1.o" ] && [ -s "$NATIVE_ROOT/usr/lib64/crt1.o" ]
}
fingerprint=$(native_recipe_fingerprint stages/60-native-root.sh \
    "$WORK_ROOT/state/20-sysroot-links.done" "$WORK_ROOT/state/43-runtime-acceptance.done")
gentoo_run_stage 60-native-root "$fingerprint" native_root_action native_root_verify
