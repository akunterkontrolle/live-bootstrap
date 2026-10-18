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
    cd "$GCC_C_BUILD"
    make "CC_FOR_TARGET=$wrapper" configure-target-libgcc
    # Top-level CC_FOR_TARGET does not replace every generated recursive CC.
    # Bind those that still name xgcc, including multilib sub-Makefiles.
    while IFS= read -r target_makefile; do
        if grep -Eq '^CC = .*/gcc/xgcc([[:space:]]|$)' "$target_makefile"; then
            awk -v wrapper="$wrapper" '
                /^CC = .*\/gcc\/xgcc([[:space:]]|$)/ {print "CC = " wrapper; next} {print}
                ' "$target_makefile" > "$target_makefile.tmp"
            mv "$target_makefile.tmp" "$target_makefile"
        fi
        # configure-target-libgcc may append ABI-specific -B and -m32 flags
        # directly to CC. Validate the executable token, not an exact line
        # match, so those required recursive-make arguments remain intact.
        if awk -v wrapper="$wrapper" '
            /^CC[[:space:]]*=/ {
                sub(/^CC[[:space:]]*=[[:space:]]*/, "")
                if ($0 == wrapper || index($0, wrapper " ") == 1 ||
                    index($0, wrapper "\t") == 1) found=1
            }
            END {exit !found}
        ' "$target_makefile"; then bound=$((bound + 1)); fi
    done < <(find "$GCC_C_BUILD/$TARGET/libgcc" -type f -name Makefile -print)
    [ "$bound" -gt 0 ] || gentoo_die 'no recursive libgcc rule uses the ABI-aware compiler'
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
