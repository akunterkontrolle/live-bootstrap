#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'native compiler requires the verified modern-kernel handoff'
. "$SCRIPT_DIR/lib/native.sh"

native_compiler_action() {
    gentoo_prepare_source "$SCRIPT_DIR/sources/binutils.lock"
    gentoo_prepare_source "$SCRIPT_DIR/sources/gcc.lock"
    # GCC's host is now ELF64/glibc. The bootstrap /usr math archives are
    # ELF32/musl and cannot be used to link the native compiler. GCC builds
    # these explicitly pinned in-tree prerequisites for its configured host.
    local package archive source link record
    for package in gmp mpfr mpc; do
        gentoo_prepare_source "$SCRIPT_DIR/sources/native-$package.lock"
        record=$(gentoo_source_record "$SCRIPT_DIR/sources/native-$package.lock")
        archive=${record%%|*}
        source="$WORK_ROOT/sources/${archive%.tar.*}"
        link="$WORK_ROOT/sources/gcc-16.2.0/$package"
        if [ -e "$link" ] || [ -L "$link" ]; then
            [ -L "$link" ] && [ "$(readlink -f "$link")" = "$source" ] ||
                gentoo_die "unexpected GCC in-tree prerequisite: $link"
        else
            ln -s "$source" "$link"
        fi
    done
    local binutils_source="$WORK_ROOT/sources/binutils-2.46.1"
    local gcc_source="$WORK_ROOT/sources/gcc-16.2.0"
    local multilib_fragment="$WORK_ROOT/sources/gcc-16.2.0/gcc/config/i386/t-linux64"
    local multilib_receipt="$WORK_ROOT/state/62-gcc-multilib-layout.receipt"
    local build_triple=$(/usr/bin/gcc -dumpmachine)
    local binutils_build="$NATIVE_BUILDS/binutils-2.46.1"
    local gcc_build="$NATIVE_BUILDS/gcc-16.2.0"
    [ -f "$multilib_fragment" ] || gentoo_die 'GCC x86_64 multilib layout fragment is missing'
    if ! grep -Fq 'm32=../lib32' "$multilib_fragment" || ! grep -Fq 'm64=../lib64' "$multilib_fragment"; then
        [ ! -s "$multilib_receipt" ] || gentoo_die 'GCC multilib patch receipt exists but the reviewed layout is absent'
        sed -i.orig \
            -e 's:[$][(]call if_multiarch[^)]*[)]::g' \
            -e '/^MULTILIB_OSDIRNAMES.*lib32/s:[$][(]if.*):../lib32:' \
            "$multilib_fragment"
    fi
    grep -Fq 'm32=../lib32' "$multilib_fragment" && grep -Fq 'm64=../lib64' "$multilib_fragment" ||
        gentoo_die 'GCC multilib layout does not select /usr/lib32 and /usr/lib64'
    printf 'source lock sha256=%s\npatched fragment sha256=%s\nrecipe=gentoo-lib32-lib64-multilib-v1\n' \
        "$(sha256sum < "$SCRIPT_DIR/sources/gcc.lock" | cut -d' ' -f1)" \
        "$(sha256sum "$multilib_fragment" | cut -d' ' -f1)" > "$multilib_receipt.tmp"
    if [ -e "$multilib_receipt" ]; then
        cmp -s "$multilib_receipt.tmp" "$multilib_receipt" || gentoo_die 'GCC multilib patch receipt changed'
        rm "$multilib_receipt.tmp"
    else
        sync "$multilib_receipt.tmp"
        mv "$multilib_receipt.tmp" "$multilib_receipt"
        sync "$WORK_ROOT/state"
    fi
    mkdir -p "$NATIVE_ROOT/dev" "$NATIVE_ROOT/tmp"
    [ -c "$NATIVE_ROOT/dev/null" ] || mknod -m 666 "$NATIVE_ROOT/dev/null" c 1 3
    mkdir -p "$binutils_build"
    if [ ! -s "$WORK_ROOT/state/62-native-binutils.receipt" ]; then
        cd "$binutils_build"
        if [ ! -s config.status ]; then
            env CC="$NATIVE_CROSS_GCC --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
                CXX="$NATIVE_CROSS_GXX --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
                CFLAGS='-O2 -pipe' CXXFLAGS='-O2 -pipe' MAKEINFO=true \
                "$binutils_source/configure" --build="$build_triple" --host="$NATIVE_TRIPLE" \
                --prefix=/usr --libdir=/usr/lib64 --disable-nls --enable-shared \
                --enable-64-bit-bfd --enable-new-dtags --disable-werror --enable-gprofng=no
        fi
        make -j"$NATIVE_JOBS"
        make DESTDIR="$NATIVE_ROOT" install
        for tool in as ld; do
            native_verify_elf "$NATIVE_ROOT/usr/bin/$tool" ELF64 || gentoo_die "native $tool is not ELF64"
            chroot "$NATIVE_ROOT" "/usr/bin/$tool" --version >/dev/null
        done
        printf '%s\n' "$(sha256sum < "$SCRIPT_DIR/sources/binutils.lock" | cut -d' ' -f1)" > "$WORK_ROOT/state/62-native-binutils.receipt.tmp"
        sync "$WORK_ROOT/state/62-native-binutils.receipt.tmp"
        mv "$WORK_ROOT/state/62-native-binutils.receipt.tmp" "$WORK_ROOT/state/62-native-binutils.receipt"
        sync "$WORK_ROOT/state"
    fi

    # This is a Canadian cross build: the new compiler runs inside the GNU
    # root, but make/configure still run on the musl bootstrap. Target runtime
    # recipes must use the existing runnable cross compiler, never new xgcc.
    mkdir -p "$gcc_build"
    local language compiler wrapper
    for language in gcc g++; do
        compiler="$PREFIX/bin/$TARGET-$language"
        wrapper="$gcc_build/cross-$language"
        cat > "$wrapper" <<WRAPPER
#!/bin/sh
set -eu
libdir=/usr/lib64
for arg do
    case "\$arg" in -m32) libdir=/usr/lib32 ;; -m64) libdir=/usr/lib64 ;; esac
done
exec "$compiler" --sysroot="$NATIVE_ROOT" -B"$NATIVE_ROOT\$libdir/" -L"$NATIVE_ROOT\$libdir" "\$@"
WRAPPER
        chmod 0755 "$wrapper"
    done
    cd "$gcc_build"
    if [ ! -s config.status ]; then
        env CC="$NATIVE_CROSS_GCC --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
            CXX="$NATIVE_CROSS_GXX --sysroot=$NATIVE_ROOT -B$NATIVE_ROOT/usr/lib64/ -L$NATIVE_ROOT/usr/lib64" \
            CC_FOR_BUILD=/usr/bin/gcc CXX_FOR_BUILD=/usr/bin/g++ \
            CC_FOR_TARGET="$gcc_build/cross-gcc" CXX_FOR_TARGET="$gcc_build/cross-g++ -nostdinc++" \
            AR="$PREFIX/bin/$TARGET-ar" RANLIB="$PREFIX/bin/$TARGET-ranlib" \
            NM="$PREFIX/bin/$TARGET-nm" AS="$PREFIX/bin/$TARGET-as" LD="$PREFIX/bin/$TARGET-ld" \
            CFLAGS='-O2 -pipe' CXXFLAGS='-O2 -pipe' MAKEINFO=true \
            "$gcc_source/configure" --build="$build_triple" --host="$NATIVE_TRIPLE" \
            --target="$NATIVE_TRIPLE" --prefix=/usr --with-sysroot=/ \
            --with-build-sysroot="$NATIVE_ROOT" --without-isl \
            --enable-languages=c,c++ --enable-threads=posix --enable-multilib \
            --with-multilib-list=m32,m64 --disable-bootstrap --disable-fixincludes --disable-nls \
            --disable-libatomic --disable-libgomp --disable-libquadmath --disable-libsanitizer \
            --disable-libssp --disable-libvtv --disable-lto --disable-werror \
            target_configargs=gcc_cv_target_thread_file=posix
    fi
    make -j"$NATIVE_JOBS" "CC_FOR_TARGET=$gcc_build/cross-gcc" \
        "CXX_FOR_TARGET=$gcc_build/cross-g++ -nostdinc++" all-gcc all-target-libgcc all-target-libstdc++-v3
    make DESTDIR="$NATIVE_ROOT" "CC_FOR_TARGET=$gcc_build/cross-gcc" \
        "CXX_FOR_TARGET=$gcc_build/cross-g++ -nostdinc++" install-gcc install-target-libgcc install-target-libstdc++-v3
    native_verify_elf "$NATIVE_ROOT/usr/bin/gcc" ELF64 || gentoo_die 'native GCC driver is not ELF64'
    native_verify_elf "$NATIVE_ROOT/usr/bin/g++" ELF64 || gentoo_die 'native G++ driver is not ELF64'
    native_compiler_probe 64
    native_compiler_probe 32
}

native_compiler_probe() {
    local abi=$1 source="$NATIVE_ROOT/tmp/native-probe.c" output="$NATIVE_ROOT/tmp/native-probe-$1"
    printf 'int main(void) { return 0; }\n' > "$source"
    chroot "$NATIVE_ROOT" /usr/bin/gcc "-m$abi" /tmp/native-probe.c -o "/tmp/native-probe-$abi" || return 1
    native_verify_elf "$output" "ELF$abi" || gentoo_die "native GCC did not produce ELF$abi"
    chroot "$NATIVE_ROOT" "/tmp/native-probe-$abi" || return 1
    printf '#include <iostream>\nint main() { std::cout << "native C++ ABI %s\\n"; }\n' "$abi" \
        > "$NATIVE_ROOT/tmp/native-probe.cc"
    chroot "$NATIVE_ROOT" /usr/bin/g++ "-m$abi" /tmp/native-probe.cc -o "/tmp/native-cxx-probe-$abi" || return 1
    native_verify_elf "$NATIVE_ROOT/tmp/native-cxx-probe-$abi" "ELF$abi" ||
        gentoo_die "native G++ did not produce ELF$abi"
    chroot "$NATIVE_ROOT" "/tmp/native-cxx-probe-$abi"
}

native_compiler_verify() {
    [ -s "$WORK_ROOT/state/62-native-binutils.receipt" ] || return 1
    [ -x "$NATIVE_ROOT/usr/bin/gcc" ] && [ -x "$NATIVE_ROOT/usr/bin/g++" ] || return 1
    gentoo_prepare_source "$SCRIPT_DIR/sources/binutils.lock" || return 1
    gentoo_prepare_source "$SCRIPT_DIR/sources/gcc.lock" || return 1
    grep -Fq 'm32=../lib32' "$WORK_ROOT/sources/gcc-16.2.0/gcc/config/i386/t-linux64" || return 1
    grep -Fq 'm64=../lib64' "$WORK_ROOT/sources/gcc-16.2.0/gcc/config/i386/t-linux64" || return 1
    grep -Fxq "patched fragment sha256=$(sha256sum "$WORK_ROOT/sources/gcc-16.2.0/gcc/config/i386/t-linux64" | cut -d' ' -f1)" \
        "$WORK_ROOT/state/62-gcc-multilib-layout.receipt" || return 1
    native_compiler_probe 64 && native_compiler_probe 32
}
fingerprint=$(native_recipe_fingerprint stages/62-native-compiler.sh \
    sources/binutils.lock sources/gcc.lock sources/native-gmp.lock sources/native-mpfr.lock sources/native-mpc.lock \
    "$WORK_ROOT/state/61-native-shell-tools.done" "$WORK_ROOT/state/42-libstdcxx.done")
gentoo_run_stage 62-native-compiler "$fingerprint" native_compiler_action native_compiler_verify
