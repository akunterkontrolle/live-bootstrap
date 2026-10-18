#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
native_tool_lock_record() {
    awk -F '|' -v name="$1" '$1==name {n++; row=$0} END {if(n!=1) exit 1; print row}' "$2"
}

native_tool_unpack_lzip() {
    local archive=$1 lock=$2 record digest source temporary
    record=$(native_tool_lock_record "$archive" "$lock") || gentoo_die "invalid lzip source lock: $lock"
    IFS='|' read -r _ url checksum <<< "$record"
    digest=${checksum#sha512=}
    source="$WORK_ROOT/sources/${archive%.tar.lz}"
    gentoo_fetch_source "$url" "$checksum" "$WORK_ROOT/distfiles/$archive"
    if [ -d "$source" ]; then
        [ "$(cat "$source/.source-pin" 2>/dev/null || true)" = "$record" ] ||
            gentoo_die "unrecognized native source tree: $source"
        printf '%s\n' "$source"
        return
    fi
    temporary=$(mktemp -d "$WORK_ROOT/sources/.native-lzip.XXXXXX")
    "$WORK_ROOT/native-build-tools/bin/lzip" -dc "$WORK_ROOT/distfiles/$archive" | tar -xf - --strip-components=1 -C "$temporary"
    printf '%s\n' "$record" > "$temporary/.source-pin"
    sync
    mv "$temporary" "$source"
    sync
    printf '%s\n' "$source"
}

native_tool_build() (
    local archive=$1 lock=$2 package=$3 binary=$4 version_arg=$5 recipe=$6
    shift 6
    case "$lock" in /*) ;; *) lock="$SCRIPT_DIR/$lock" ;; esac
    local source build marker source_hash configure_hash program
    local build_triple=$(/usr/bin/gcc -dumpmachine)
    local -a configure_args=("$@")
    local tic_wrapper=
    local record
    record=$(native_tool_lock_record "$archive" "$lock") || gentoo_die "missing native lock record: $archive"
    source_hash=${record##*sha512=}
    if [[ "$archive" == *.tar.lz ]]; then
        source=$(native_tool_unpack_lzip "$archive" "$lock")
    else
        gentoo_prepare_source "$lock"
        source="$WORK_ROOT/sources/${archive%.tar.*}"
    fi
    build="$NATIVE_BUILDS/$package-$recipe"
    marker="$WORK_ROOT/state/63-$package.done"
    program="$NATIVE_ROOT/usr/bin/$binary"
    if [ "$package" = ncurses-6.6 ]; then
        tic_wrapper="$build/native-tic"
        configure_args+=("--with-tic-path=$tic_wrapper")
        mkdir -p "$build"
        cat > "$tic_wrapper" <<EOF
#!/bin/sh
exec "$NATIVE_ROOT/lib64/ld-linux-x86-64.so.2" --library-path "$NATIVE_ROOT/lib64:$NATIVE_ROOT/usr/lib64" "$build/progs/tic" "\$@"
EOF
        chmod 0755 "$tic_wrapper"
    fi
    configure_hash=$(printf '%s\n' "$NATIVE_TRIPLE" "$build_triple" --prefix=/usr --libdir=/usr/lib64 \
        --disable-nls "${configure_args[@]}" "$recipe" | sha256sum | cut -d' ' -f1)
    if [ -s "$marker" ]; then
        grep -Fxq "source sha512=$source_hash" "$marker" &&
            grep -Fxq "configure sha256=$configure_hash" "$marker" &&
            grep -Fxq "program sha256=$(sha256sum "$program" | cut -d' ' -f1)" "$marker" ||
            gentoo_die "native tool receipt does not match inputs: $package"
        [ -x "$program" ] || gentoo_die "completed native tool is missing: $program"
        native_run "$program" "$version_arg" >/dev/null
        return
    fi
    [ -x "$source/configure" ] || gentoo_die "native tool source lacks configure: $archive"
    mkdir -p "$build"
    cd "$build"
    if [ ! -s config.status ] || [ "$(cat .configure.sha256 2>/dev/null || true)" != "$configure_hash" ]; then
        env CC="$NATIVE_CROSS_GCC --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
            CXX="$NATIVE_CROSS_GXX --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
            CFLAGS='-O2 -pipe' CXXFLAGS='-O2 -pipe' CONFIG_SITE= MAKEINFO=true \
            "$source/configure" --build="$build_triple" --host="$NATIVE_TRIPLE" \
            --prefix=/usr --libdir=/usr/lib64 --disable-nls "${configure_args[@]}"
        printf '%s\n' "$configure_hash" > .configure.sha256.tmp
        sync .configure.sha256.tmp
        mv .configure.sha256.tmp .configure.sha256
    fi
    make -j"$NATIVE_JOBS"
    if [ "$package" = ncurses-6.6 ]; then
        tic_path="$tic_wrapper" make DESTDIR="$NATIVE_ROOT" install
    else
        make DESTDIR="$NATIVE_ROOT" install
    fi
    [ -x "$program" ] || gentoo_die "$package did not install $program"
    native_verify_elf "$program" ELF64 || gentoo_die "$package output is not ELF64"
    native_run "$program" "$version_arg" >/dev/null
    printf 'complete %s\nsource sha512=%s\nconfigure sha256=%s\nrecipe=%s\nprogram=%s\nprogram sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$source_hash" "$configure_hash" "$recipe" "$binary" \
        "$(sha256sum "$program" | cut -d' ' -f1)" > "$marker.tmp"
    sync "$marker.tmp"
    mv "$marker.tmp" "$marker"
    sync "$WORK_ROOT/state"
)
