#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'native utility floor requires the verified modern-kernel handoff'
. "$SCRIPT_DIR/lib/native.sh"
. "$SCRIPT_DIR/lib/native-tools.sh"

native_host_lzip() {
    local lock=sources/native-lzip-1.26.lock archive=lzip-1.26.tar.gz source build marker hash
    gentoo_prepare_source "$lock"
    source="$WORK_ROOT/sources/lzip-1.26"
    build="$WORK_ROOT/build/native-host-lzip-1.26"
    marker="$WORK_ROOT/state/63-native-host-lzip.receipt"
    hash=$(native_tool_lock_record "$archive" "$lock"); hash=${hash##*sha512=}
    mkdir -p "$build" "$WORK_ROOT/native-build-tools/bin"
    if [ ! -s "$marker" ]; then
        cd "$build"
        if [ ! -s config.status ]; then
            CXX=/usr/bin/g++ CXXFLAGS='-O2 -pipe' "$source/configure" --prefix="$WORK_ROOT/native-build-tools"
        fi
        CXX=/usr/bin/g++ CXXFLAGS='-O2 -pipe' make -j"$NATIVE_JOBS"
        CXX=/usr/bin/g++ CXXFLAGS='-O2 -pipe' make install
        [ -x "$WORK_ROOT/native-build-tools/bin/lzip" ] || gentoo_die 'host lzip build did not install its executable'
        "$WORK_ROOT/native-build-tools/bin/lzip" --version >/dev/null
        printf 'complete %s\nsource sha512=%s\nrecipe=host-lzip-1.26-v1\nprogram sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$hash" \
            "$(sha256sum "$WORK_ROOT/native-build-tools/bin/lzip" | cut -d' ' -f1)" > "$marker.tmp"
        sync "$marker.tmp"; mv "$marker.tmp" "$marker"; sync "$WORK_ROOT/state"
    else
        grep -Fxq "source sha512=$hash" "$marker" &&
            grep -Fxq "program sha256=$(sha256sum "$WORK_ROOT/native-build-tools/bin/lzip" | cut -d' ' -f1)" "$marker" ||
            gentoo_die 'host lzip receipt source mismatch'
        "$WORK_ROOT/native-build-tools/bin/lzip" --version >/dev/null
    fi
}

native_build_file() {
    local archive=file-5.48.tar.gz lock=sources/native-file-5.48.lock source
    local host_build="$NATIVE_BUILDS/file-5.48-host" target_build="$NATIVE_BUILDS/file-5.48-target"
    local host_file="$host_build/src/file" target_file="$NATIVE_ROOT/usr/bin/file"
    local host_marker="$WORK_ROOT/state/63-file-host.receipt" target_marker="$WORK_ROOT/state/63-file-target.receipt"
    local record source_hash
    gentoo_prepare_source "$lock"
    source="$WORK_ROOT/sources/file-5.48"
    record=$(native_tool_lock_record "$archive" "$lock") || gentoo_die 'File source lock is invalid'
    source_hash=${record##*sha512=}
    mkdir -p "$host_build" "$target_build"
    if [ ! -s "$host_marker" ]; then
        cd "$host_build"
        if [ ! -s config.status ]; then
            env CC=/usr/bin/gcc CXX=/usr/bin/g++ CONFIG_SITE= CFLAGS='-O2 -pipe' CXXFLAGS='-O2 -pipe' \
                "$source/configure" --disable-bzlib --disable-libseccomp --disable-xzlib --disable-zlib
        fi
        env CC=/usr/bin/gcc CXX=/usr/bin/g++ CFLAGS='-O2 -pipe' CXXFLAGS='-O2 -pipe' make -j"$NATIVE_JOBS"
        [ -x "$host_file" ] || gentoo_die 'host File generator did not build'
        "$host_file" --version >/dev/null
        printf 'source sha512=%s\nprogram sha256=%s\nrecipe=host-file-5.48-v1\n' \
            "$source_hash" "$(sha256sum "$host_file" | cut -d' ' -f1)" > "$host_marker.tmp"
        sync "$host_marker.tmp"; mv "$host_marker.tmp" "$host_marker"; sync "$WORK_ROOT/state"
    else
        grep -Fxq "source sha512=$source_hash" "$host_marker" && [ -x "$host_file" ] &&
            grep -Fxq "program sha256=$(sha256sum "$host_file" | cut -d' ' -f1)" "$host_marker" ||
            gentoo_die 'host File generator receipt does not match its output'
    fi
    if [ ! -s "$target_marker" ]; then
        cd "$target_build"
        if [ ! -s config.status ]; then
            env CC="$NATIVE_CROSS_GCC --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
                CXX="$NATIVE_CROSS_GXX --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
                CFLAGS='-O2 -pipe' CXXFLAGS='-O2 -pipe' CONFIG_SITE= \
                "$source/configure" --build="$(/usr/bin/gcc -dumpmachine)" --host="$NATIVE_TRIPLE" \
                --prefix=/usr --libdir=/usr/lib64 --disable-nls
        fi
        make -j"$NATIVE_JOBS" "FILE_COMPILE=$host_file"
        make DESTDIR="$NATIVE_ROOT" install
        rm -f "$NATIVE_ROOT/usr/lib64/libmagic.la"
        [ -x "$target_file" ] || gentoo_die 'native File install did not create /usr/bin/file'
        native_verify_elf "$target_file" ELF64 || gentoo_die 'native File output is not ELF64'
        native_run "$target_file" --version >/dev/null
        printf 'source sha512=%s\nprogram sha256=%s\nrecipe=cross-file-5.48-v1\n' \
            "$source_hash" "$(sha256sum "$target_file" | cut -d' ' -f1)" > "$target_marker.tmp"
        sync "$target_marker.tmp"; mv "$target_marker.tmp" "$target_marker"; sync "$WORK_ROOT/state"
    else
        grep -Fxq "source sha512=$source_hash" "$target_marker" &&
            grep -Fxq "program sha256=$(sha256sum "$target_file" | cut -d' ' -f1)" "$target_marker" ||
            gentoo_die 'native File receipt does not match its output'
        native_run "$target_file" --version >/dev/null
    fi
}

native_utility_action() {
    # m4 and ncurses precede shell utilities; make's .tar.lz extractor is
    # source-built on the bootstrap host from its separate lock.
    native_tool_build m4-1.4.21.tar.xz sources/native-m4-1.4.21.lock m4-1.4.21 m4 --version m4-cross-v1
    native_tool_build ncurses-6.6.tar.gz sources/native-ncurses-6.6.lock ncurses-6.6 tic -V ncurses-cross-v1 \
        --with-shared --without-debug --without-ada --enable-widec
    native_tool_build diffutils-3.12.tar.xz sources/native-diffutils-3.12.lock diffutils-3.12 diff --version diffutils-cross-v1 \
        gl_cv_func_strcasecmp_works=yes
    native_tool_build findutils-4.11.0.tar.xz sources/native-findutils-4.11.0.lock findutils-4.11.0 find --version findutils-cross-v1 \
        --localstatedir=/var/lib/locate

    # The reviewed LFS fix removes a bootstrap-only Gawk extra target that
    # otherwise tries to execute a target binary during this cross build.
    gentoo_prepare_source sources/native-gawk-5.4.1.lock
    local gawk_source="$WORK_ROOT/sources/gawk-5.4.1" patch_receipt="$WORK_ROOT/state/63-gawk-source-patch.receipt"
    if [ ! -s "$patch_receipt" ]; then
        sed -i 's/extras//' "$gawk_source/Makefile.in"
        ! grep -q extras "$gawk_source/Makefile.in" || gentoo_die 'Gawk extras workaround did not apply'
        printf 'source sha512=%s\npatched Makefile.in sha256=%s\nrecipe=lfs13-gawk-extras-removal-v1\n' \
            "$(awk -F '|' '{sub(/^sha512=/,"",$3);print $3}' sources/native-gawk-5.4.1.lock)" \
            "$(sha256sum "$gawk_source/Makefile.in" | cut -d' ' -f1)" > "$patch_receipt.tmp"
        sync "$patch_receipt.tmp"; mv "$patch_receipt.tmp" "$patch_receipt"; sync "$WORK_ROOT/state"
    else
        grep -Fxq "patched Makefile.in sha256=$(sha256sum "$gawk_source/Makefile.in" | cut -d' ' -f1)" "$patch_receipt" ||
            gentoo_die 'Gawk patched source no longer matches its receipt'
    fi
    native_tool_build gawk-5.4.1.tar.xz sources/native-gawk-5.4.1.lock gawk-5.4.1 gawk --version gawk-cross-v1
    native_tool_build grep-3.12.tar.xz sources/native-grep-3.12.lock grep-3.12 grep --version grep-cross-v1
    native_tool_build gzip-1.14.tar.xz sources/native-gzip-1.14.lock gzip-1.14 gzip --version gzip-cross-v1

    native_host_lzip
    native_tool_build make-4.4.1.tar.lz sources/native-make-4.4.1.lock make-4.4.1 make --version make-cross-v1
    native_tool_build patch-2.8.tar.xz sources/native-patch-2.8.lock patch-2.8 patch --version patch-cross-v1
    native_tool_build sed-4.10.tar.xz sources/native-sed-4.10.lock sed-4.10 sed --version sed-cross-v1
    native_tool_build tar-1.35.tar.xz sources/native-tar-1.35.lock tar-1.35 tar --version tar-cross-v1
    native_tool_build xz-5.8.3.tar.gz sources/native-xz-5.8.3.lock xz-5.8.3 xz --version xz-cross-v1 \
        --disable-static --docdir=/usr/share/doc/xz-5.8.3
    native_build_file
}

native_utility_verify() {
    local program package marker
    for package in m4-1.4.21 ncurses-6.6 diffutils-3.12 findutils-4.11.0 gawk-5.4.1 \
        grep-3.12 gzip-1.14 make-4.4.1 patch-2.8 sed-4.10 tar-1.35 xz-5.8.3; do
        marker="$WORK_ROOT/state/63-$package.done"
        [ -s "$marker" ] || return 1
        program="$NATIVE_ROOT/usr/bin/$(awk -F= '$1=="program" {print $2}' "$marker")"
        [ -x "$program" ] || return 1
        grep -Fxq "program sha256=$(sha256sum "$program" | cut -d' ' -f1)" "$marker" || return 1
    done
    for program in m4 tic diff find gawk grep gzip make patch sed tar xz file; do
        [ -x "$NATIVE_ROOT/usr/bin/$program" ] || return 1
        native_verify_elf "$NATIVE_ROOT/usr/bin/$program" ELF64 || return 1
    done
    for program in m4 tic diff find gawk grep gzip make patch sed tar xz file; do
        native_run "$NATIVE_ROOT/usr/bin/$program" --version >/dev/null 2>&1 ||
            native_run "$NATIVE_ROOT/usr/bin/$program" -V >/dev/null 2>&1 || return 1
    done
    [ -s "$WORK_ROOT/state/63-native-host-lzip.receipt" ] &&
        grep -Fxq "program sha256=$(sha256sum "$WORK_ROOT/native-build-tools/bin/lzip" | cut -d' ' -f1)" \
            "$WORK_ROOT/state/63-native-host-lzip.receipt" || return 1
    grep -Fxq "program sha256=$(sha256sum "$NATIVE_ROOT/usr/bin/file" | cut -d' ' -f1)" \
        "$WORK_ROOT/state/63-file-target.receipt" || return 1
    grep -Fxq "program sha256=$(sha256sum "$NATIVE_BUILDS/file-5.48-host/src/file" | cut -d' ' -f1)" \
        "$WORK_ROOT/state/63-file-host.receipt" || return 1
    grep -Fxq "patched Makefile.in sha256=$(sha256sum "$WORK_ROOT/sources/gawk-5.4.1/Makefile.in" | cut -d' ' -f1)" \
        "$WORK_ROOT/state/63-gawk-source-patch.receipt" || return 1
}
fingerprint=$(gentoo_fingerprint stages/63-native-tool-floor.sh lib/toolchain.sh lib/native.sh lib/native-tools.sh \
    lib/sources.sh lib/state.sh \
    sources/native-m4-1.4.21.lock sources/native-ncurses-6.6.lock \
    sources/native-diffutils-3.12.lock sources/native-findutils-4.11.0.lock \
    sources/native-gawk-5.4.1.lock sources/native-grep-3.12.lock sources/native-gzip-1.14.lock \
    sources/native-lzip-1.26.lock sources/native-make-4.4.1.lock \
    sources/native-patch-2.8.lock sources/native-sed-4.10.lock sources/native-tar-1.35.lock \
    sources/native-xz-5.8.3.lock sources/native-file-5.48.lock \
    "$WORK_ROOT/state/62-native-compiler.done")
gentoo_run_stage 63-native-tool-floor "$fingerprint" native_utility_action native_utility_verify
printf 'Native GNU temporary utility floor is ready; no ebuild operation has been run.\n'
