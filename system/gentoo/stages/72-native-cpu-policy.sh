#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the small x86 prerequisite directly from its three upstream translation
# units. This avoids a dependency on as-yet-unbuilt native autotools/Portage.
. "$SCRIPT_DIR/lib/cpu-policy.sh"
CPU_PREREQUISITE="$WORK_ROOT/native-build-tools/bin/cpuid2cpuflags"
CPU_RECEIPT="$WORK_ROOT/state/native-cpu-policy.json"
cpu_policy_action() {
    gentoo_prepare_source sources/cpuid2cpuflags.lock
    local source="$WORK_ROOT/sources/cpuid2cpuflags-18"
    install -d -m 0755 "$(dirname "$CPU_PREREQUISITE")"
    # The bootstrap cross driver does not infer the split usr/lib64 layout.
    # Bind startup objects and static libc just as the earlier link probes do.
    "$PREFIX/bin/$TARGET-gcc" -m64 --sysroot="$SYSROOT" \
        -B"$SYSROOT/usr/lib64/" -L"$SYSROOT/usr/lib64" -O2 -pipe -static \
        -DHAVE_STDINT_H '-DPACKAGE_STRING="cpuid2cpuflags 18"' \
        "$source/src/main.c" "$source/src/x86.c" "$source/src/x86-lib.c" \
        -o "$CPU_PREREQUISITE.tmp"
    "$CPU_PREREQUISITE.tmp" --version | grep -Fxq 'cpuid2cpuflags 18'
    mv "$CPU_PREREQUISITE.tmp" "$CPU_PREREQUISITE"
    sha256sum < "$CPU_PREREQUISITE" > "$WORK_ROOT/state/cpu-detector.sha256"
    sync "$WORK_ROOT/state/cpu-detector.sha256"
    python3 "$SCRIPT_DIR/cpu-policy.py" --initialize --receipt "$CPU_RECEIPT" \
        --detector "$CPU_PREREQUISITE" --compiler "$PREFIX/bin/$TARGET-gcc"
}
cpu_policy_verify() {
    [ "$(sha256sum < "$CPU_PREREQUISITE")" = "$(cat "$WORK_ROOT/state/cpu-detector.sha256")" ] || return 1
    python3 "$SCRIPT_DIR/cpu-policy.py" --receipt "$CPU_RECEIPT" \
        --detector "$CPU_PREREQUISITE" --compiler "$PREFIX/bin/$TARGET-gcc" || return 1
}
fingerprint=$(gentoo_fingerprint stages/72-native-cpu-policy.sh lib/cpu-policy.sh cpu-policy.py \
    sources/cpuid2cpuflags.lock policy/00-cpu-flags.accept_keywords "$WORK_ROOT/state/71-native-portage.done")
gentoo_run_stage 72-native-cpu-policy "$fingerprint" cpu_policy_action cpu_policy_verify
# Install the semantic inputs in the native root; rsync preserves them in the
# installed base. Future desktop builds compare against the same capabilities.
install -d -m 0755 "$NATIVE_ROOT/etc/portage/package.accept_keywords" "$NATIVE_ROOT/etc/portage/package.use" "$NATIVE_ROOT/usr/local/libexec/gentoo-continuation"
python3 "$SCRIPT_DIR/cpu-policy.py" --receipt "$CPU_RECEIPT" \
    --detector "$CPU_PREREQUISITE" --compiler "$PREFIX/bin/$TARGET-gcc" \
    --package-use "$NATIVE_ROOT/etc/portage/package.use/00-cpu-flags"
install -m 0644 "$CPU_RECEIPT" "$NATIVE_ROOT/etc/portage/native-cpu-policy.json"
install -m 0644 "$SCRIPT_DIR/cpu-policy.py" "$NATIVE_ROOT/usr/local/libexec/gentoo-continuation/cpu-policy.py"
keyword="$NATIVE_ROOT/etc/portage/package.accept_keywords/00-cpu-flags"
if [ -e "$keyword" ] || [ -L "$keyword" ]; then
    [ -f "$keyword" ] && [ ! -L "$keyword" ] &&
        cmp -s "$SCRIPT_DIR/policy/00-cpu-flags.accept_keywords" "$keyword" ||
        gentoo_die 'existing CPU detector keyword policy differs'
else
    temporary=$(mktemp "$NATIVE_ROOT/etc/portage/package.accept_keywords/.cpu-keyword.XXXXXX")
    install -m 0644 "$SCRIPT_DIR/policy/00-cpu-flags.accept_keywords" "$temporary"
    sync "$temporary"
    mv "$temporary" "$keyword"
    sync "$NATIVE_ROOT/etc/portage/package.accept_keywords"
fi
