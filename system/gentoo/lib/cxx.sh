#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
GCC_CXX_BUILD="$BUILDS/gcc-linux-cxx"
CXX_PROBES="$BUILDS/probes/cxx"
mkdir -p "$CXX_PROBES"

cxx_fingerprint() {
    gentoo_toolchain_fingerprint lib/cxx.sh "$@"
}

cxx_write_wrappers() {
    local language frontend runtime32 runtime64 shared32 shared64
    # xgcc/xg++ do not inherit the installed driver's runtime search paths.
    # Cache private and shared paths before replacing the installed driver.
    runtime32=$("$PREFIX/bin/$TARGET-gcc" -m32 -print-libgcc-file-name)
    runtime64=$("$PREFIX/bin/$TARGET-gcc" -m64 -print-libgcc-file-name)
    gentoo_verify_elf "$runtime32" ELF32
    gentoo_verify_elf "$runtime64" ELF64
    shared32=$("$PREFIX/bin/$TARGET-gcc" -m32 -print-file-name=libgcc_s.so.1)
    shared64=$("$PREFIX/bin/$TARGET-gcc" -m64 -print-file-name=libgcc_s.so.1)
    gentoo_verify_elf "$shared32" ELF32
    gentoo_verify_elf "$shared64" ELF64
    for language in cc cxx; do
        frontend=xgcc
        [ "$language" != cxx ] || frontend=xg++
        cat > "$GCC_CXX_BUILD/$language-for-target" <<WRAPPER
#!/bin/sh
set -eu
libdir=/usr/lib64
runtime="$(dirname "$runtime64")"
shared="$(dirname "$shared64")"
for arg do
    case "\$arg" in
        -m32) libdir=/usr/lib32; runtime="$(dirname "$runtime32")"; shared="$(dirname "$shared32")" ;;
        -m64) libdir=/usr/lib64; runtime="$(dirname "$runtime64")"; shared="$(dirname "$shared64")" ;;
    esac
done
exec "$GCC_CXX_BUILD/gcc/$frontend" \\
    --sysroot="$SYSROOT" \\
    -B"$GCC_CXX_BUILD/gcc/" \\
    -B"$PREFIX/$TARGET/bin/" \\
    -B"$SYSROOT\$libdir/" \\
    -L"\$runtime" -L"\$shared" -L"$SYSROOT\$libdir" \\
    -isystem "$PREFIX/$TARGET/include" \\
    -isystem "$PREFIX/$TARGET/sys-include" "\$@"
WRAPPER
        chmod 0755 "$GCC_CXX_BUILD/$language-for-target"
    done
}

cxx_fix_nm() {
    local file="$GCC_CXX_BUILD/gcc/nm"
    [ -f "$file" ] || gentoo_die 'configure-gcc did not generate gcc/nm'
    [ "$(grep -c '^ORIGINAL_NM_FOR_TARGET=' "$file")" = 1 ] ||
        gentoo_die 'unexpected generated nm wrapper'
    sed "s|^ORIGINAL_NM_FOR_TARGET=.*$|ORIGINAL_NM_FOR_TARGET=\"$TOOLBIN/$TARGET-nm\"|" \
        "$file" > "$file.tmp"
    chmod 0755 "$file.tmp"
    mv "$file.tmp" "$file"
    grep -Fx "ORIGINAL_NM_FOR_TARGET=\"$TOOLBIN/$TARGET-nm\"" "$file"
}

cxx_remove_shadow_crt() {
    local name file
    for name in crti.o crtn.o; do
        file="$GCC_CXX_BUILD/gcc/$name"
        if [ -e "$file" ] || [ -L "$file" ]; then
            [ -L "$file" ] && [ "$(readlink "$file")" = "$SYSROOT/usr/lib64/$name" ] ||
                gentoo_die "unexpected compiler startup object: $file"
            rm "$file"
        fi
    done
}

cxx_bind_libgcc() {
    local file count=0 list="$GCC_CXX_BUILD/.libgcc-makefiles"
    # ELF32 libgcc is a sibling of the default libgcc directory. Enumerate both
    # ABIs without process substitution, which needs working /proc/self/fd.
    find "$GCC_CXX_BUILD/$TARGET" -type f -path '*/libgcc/Makefile' -print > "$list"
    while IFS= read -r file; do
        awk -v wrapper="$GCC_CXX_BUILD/cc-for-target" '
            /^CC = .*\/gcc\/xgcc([[:space:]]|$)/ {
                command=$0
                sub(/^CC = /, "", command)
                sub(/^[^[:space:]]+/, "", command)
                print "CC = " wrapper command
                next
            }
            {print}
        ' "$file" > "$file.tmp"
        mv "$file.tmp" "$file"
        # Preserve configure's -B and ABI flags, checking the executable token.
        awk -v wrapper="$GCC_CXX_BUILD/cc-for-target" '
            /^CC[[:space:]]*=/ {
                seen++
                sub(/^CC[[:space:]]*=[[:space:]]*/, "")
                if ($0 == wrapper || index($0, wrapper " ") == 1 ||
                    index($0, wrapper "\t") == 1) good++
            }
            END {exit !(seen == 1 && good == 1)}
        ' "$file" || gentoo_die "unexpected libgcc compiler binding: $file"
        count=$((count + 1))
    done < "$list"
    rm -f "$list"
    [ "$count" -gt 0 ] || gentoo_die 'no recursive libgcc Makefiles found'
}

cxx_fix_libtool() {
    local file=$1 abi=$2
    [ "$abi" = 32 ] || [ "$abi" = 64 ] || gentoo_die 'invalid libtool ABI'
    # Rewrite whole startup-object tokens, including incorrectly absolute paths.
    # Only the CXX tag changes; reject missing/duplicate fields before publishing.
    awk -v wrapper="$GCC_CXX_BUILD/cxx-for-target" -v dir="$SYSROOT/usr/lib$abi" '
        /^# ### BEGIN LIBTOOL TAG CONFIG: CXX$/ {inside=1; tags++}
        inside && /^CC=/ {print "CC=\047" wrapper "\047"; cc++; next}
        inside && /^predep_objects=/ {
            n=gsub(/[^ =\047\042]*crti[.]o/, dir "/crti.o"); pre+=n
        }
        inside && /^postdep_objects=/ {
            n=gsub(/[^ =\047\042]*crtn[.]o/, dir "/crtn.o"); post+=n
        }
        {print}
        /^# ### END LIBTOOL TAG CONFIG: CXX$/ {inside=0; ends++}
        END {if (tags!=1 || ends!=1 || cc!=1 || pre!=1 || post!=1 || inside) exit 1}
    ' "$file" > "$file.tmp" || { rm -f "$file.tmp"; gentoo_die "unexpected CXX libtool configuration: $file"; }
    chmod 0755 "$file.tmp"
    mv "$file.tmp" "$file"
}

cxx_verify_frontends() {
    local abi
    [ "$("$GCC_CXX_BUILD/gcc/xgcc" -dumpmachine)" = "$TARGET" ] || return 1
    [ "$("$GCC_CXX_BUILD/gcc/xgcc" -dumpfullversion)" = 16.2.0 ] || return 1
    printf 'template<class T> T value(T x) { return x+1; }\nint f() { return value(41); }\n' > "$CXX_PROBES/frontend.cc"
    for abi in 64 32; do
        "$GCC_CXX_BUILD/gcc/xg++" -B"$GCC_CXX_BUILD/gcc/" "-m$abi" \
            -ffreestanding -c "$CXX_PROBES/frontend.cc" -o "$CXX_PROBES/frontend-$abi.o" || return 1
        gentoo_verify_elf "$CXX_PROBES/frontend-$abi.o" "ELF$abi" || return 1
    done
}

cxx_verify_crt_search() {
    local abi name resolved
    for abi in 64 32; do
        for name in crti.o crtn.o; do
            resolved=$("$GCC_CXX_BUILD/cc-for-target" "-m$abi" "-print-file-name=$name") || return 1
            [ "$resolved" = "$SYSROOT/usr/lib$abi/$name" ] || return 1
            gentoo_verify_elf "$resolved" "ELF$abi" || return 1
        done
    done
}
