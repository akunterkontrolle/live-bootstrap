#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'native tools require the verified modern-kernel handoff'
. "$SCRIPT_DIR/lib/native.sh"

native_cross_cc() {
    "$NATIVE_CROSS_GCC" --sysroot="$NATIVE_ROOT" \
        -B"$NATIVE_ROOT/usr/lib64/" -L"$NATIVE_ROOT/usr/lib64" "$@"
}
native_cross_cxx() {
    "$NATIVE_CROSS_GXX" --sysroot="$NATIVE_ROOT" \
        -B"$NATIVE_ROOT/usr/lib64/" -L"$NATIVE_ROOT/usr/lib64" "$@"
}
native_build_one() {
    local archive=$1 lock=$2 package=$3 binary=$4 version_arg=$5 configure_flags=$6
    local source build="$NATIVE_BUILDS/$package" marker="$WORK_ROOT/state/61-$package.done"
    gentoo_prepare_source "$lock"
    source="$WORK_ROOT/sources/${archive%.tar.*}"
    [ -x "$source/configure" ] || gentoo_die "$package pinned source lacks configure"
    if [ ! -s "$marker" ]; then
        mkdir -p "$build"
        cd "$build"
        if [ ! -s config.status ]; then
            env CC="$NATIVE_CROSS_GCC --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
                CXX="$NATIVE_CROSS_GXX --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
                CFLAGS='-O2 -pipe' CXXFLAGS='-O2 -pipe' \
                "$source/configure" --build="$(/usr/bin/gcc -dumpmachine)" --host="$NATIVE_TRIPLE" \
                --prefix=/usr --libdir=/usr/lib64 --disable-nls $configure_flags
        fi
        make -j"$NATIVE_JOBS"
        make DESTDIR="$NATIVE_ROOT" install
        [ -x "$NATIVE_ROOT/usr/bin/$binary" ] || gentoo_die "$package did not install $binary"
        native_verify_elf "$NATIVE_ROOT/usr/bin/$binary" ELF64 || gentoo_die "$package output is not ELF64"
        native_run "$NATIVE_ROOT/usr/bin/$binary" "$version_arg" >/dev/null
        printf 'complete %s\narchive=%s\nrecipe=stage61-%s-v1\n' \
            "$(date -u +%FT%TZ)" "$(sha256sum "$lock" | cut -d' ' -f1)" "$package" \
            > "$marker.tmp"
        sync "$marker.tmp"
        mv "$marker.tmp" "$marker"
        sync "$WORK_ROOT/state"
    fi
    [ -x "$NATIVE_ROOT/usr/bin/$binary" ] || gentoo_die "completed $package output is missing"
    native_run "$NATIVE_ROOT/usr/bin/$binary" "$version_arg" >/dev/null
}

native_tools_action() {
    # Bash and coreutils establish the native shell/utility floor before the
    # compiler pass; configure and make themselves still run on the host.
    native_build_one bash-5.3.tar.gz sources/native-bash.lock bash-5.3 bash --version '--without-bash-malloc'
    native_build_one coreutils-9.11.tar.xz sources/native-coreutils.lock coreutils-9.11 env --version '--enable-install-program=hostname'
}
native_tools_verify() {
    [ -x "$NATIVE_ROOT/usr/bin/bash" ] && [ -x "$NATIVE_ROOT/usr/bin/env" ] || return 1
    native_verify_elf "$NATIVE_ROOT/usr/bin/bash" ELF64 || return 1
    native_verify_elf "$NATIVE_ROOT/usr/bin/env" ELF64 || return 1
    native_run "$NATIVE_ROOT/usr/bin/bash" --version >/dev/null &&
        native_run "$NATIVE_ROOT/usr/bin/env" --version >/dev/null
}
fingerprint=$(native_recipe_fingerprint stages/61-native-shell-tools.sh \
    sources/native-bash.lock sources/native-coreutils.lock \
    "$WORK_ROOT/state/60-native-root.done")
gentoo_run_stage 61-native-shell-tools "$fingerprint" native_tools_action native_tools_verify
