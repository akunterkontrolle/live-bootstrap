#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
GCC_C_BUILD="$BUILDS/gcc-linux-c"
gcc_linux_runtime_action() {
    mkdir -p "$GCC_C_BUILD"
    cd "$GCC_C_BUILD"
    if [ ! -f Makefile ]; then
        build_triple=$(/usr/bin/gcc -dumpmachine)
        "$GCC_SOURCE/configure" --build="$build_triple" --host="$build_triple" --target="$TARGET" \
            --prefix="$PREFIX" --with-sysroot="$SYSROOT" --with-build-sysroot="$SYSROOT" \
            --with-headers="$SYSROOT/usr/include" --with-build-time-tools="$TOOLBIN" \
            --with-as="$TOOLBIN/$TARGET-as" --with-ld="$TOOLBIN/$TARGET-ld" \
            --with-gmp=/usr --with-mpfr=/usr --with-mpc=/usr \
            --enable-languages=c --disable-bootstrap --disable-nls \
            --enable-multilib --enable-shared --with-multilib-list=m32,m64 \
            --disable-libatomic --disable-libgomp --disable-libquadmath --disable-libssp \
            --disable-libstdcxx --disable-libvtv --disable-libsanitizer --disable-lto --disable-werror
    fi
    make -j"$JOBS" all-gcc
    make install-gcc
    # GCC's own recursive libgcc rules must select the same ABI's startup
    # objects as external probes. --sysroot alone does not find usr/lib64/crti.o.
    cat > "$GCC_C_BUILD/cc-for-target" <<WRAPPER
#!/bin/sh
set -eu
libdir=/usr/lib64
for arg do
    case "\$arg" in -m32) libdir=/usr/lib32 ;; -m64) libdir=/usr/lib64 ;; esac
done
exec "$GCC_C_BUILD/gcc/xgcc" \\
    -B"$GCC_C_BUILD/gcc/" \\
    -B"$PREFIX/$TARGET/bin/" \\
    -B"$PREFIX/$TARGET/lib/" \\
    -B"$SYSROOT\$libdir/" \\
    -isystem "$PREFIX/$TARGET/include" \\
    -isystem "$PREFIX/$TARGET/sys-include" "\$@"
WRAPPER
    chmod 0755 "$GCC_C_BUILD/cc-for-target"
    gcc_linux_build_libgcc
}
gcc_linux_build_libgcc() {
    local wrapper="$GCC_C_BUILD/cc-for-target" target_makefile bound=0
    local list="$GCC_C_BUILD/.libgcc-makefiles"
    cd "$GCC_C_BUILD"
    make "CC_FOR_TARGET=$wrapper" configure-target-libgcc
    # Both ABI directories are siblings beneath the target directory. Use a
    # regular list file so this also works without /proc/self/fd support.
    find "$GCC_C_BUILD/$TARGET" -type f -path '*/libgcc/Makefile' -print > "$list"
    while IFS= read -r target_makefile; do
        awk -v wrapper="$wrapper" '
            /^CC = .*\/gcc\/xgcc([[:space:]]|$)/ {
                command=$0
                sub(/^CC = /, "", command)
                sub(/^[^[:space:]]+/, "", command)
                print "CC = " wrapper command
                next
            }
            {print}
        ' "$target_makefile" > "$target_makefile.tmp"
        mv "$target_makefile.tmp" "$target_makefile"
        # Retain configure's -B and ABI flags while validating the executable.
        awk -v wrapper="$wrapper" '
            /^CC[[:space:]]*=/ {
                seen++
                sub(/^CC[[:space:]]*=[[:space:]]*/, "")
                if ($0 == wrapper || index($0, wrapper " ") == 1 ||
                    index($0, wrapper "\t") == 1) good++
            }
            END {exit !(seen == 1 && good == 1)}
        ' "$target_makefile" || gentoo_die "unexpected libgcc compiler binding: $target_makefile"
        bound=$((bound + 1))
    done < "$list"
    rm -f "$list"
    [ "$bound" -gt 0 ] || gentoo_die 'no recursive libgcc Makefiles found'
    make -j"$JOBS" "CC=$wrapper" "CC_FOR_TARGET=$wrapper" all-target-libgcc
    make "CC=$wrapper" "CC_FOR_TARGET=$wrapper" install-target-libgcc
}
gcc_linux_runtime_verify() {
    gentoo_verify_multilib || return 1
    gentoo_verify_libgcc
}
fingerprint=$(gentoo_toolchain_fingerprint stages/18-gcc-linux-runtime.sh sources/gcc.lock \
    "$WORK_ROOT/state/17-glibc-bootstrap-32.done" "$WORK_ROOT/state/17-glibc-bootstrap-64.done")
gentoo_run_stage 18-gcc-linux-runtime "$fingerprint" gcc_linux_runtime_action gcc_linux_runtime_verify
