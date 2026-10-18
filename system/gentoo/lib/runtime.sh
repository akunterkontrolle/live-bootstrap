#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Invoke the saved source-built loader directly; the bootstrap root retains its
# original musl libraries. Never install a GNU interpreter in its /lib paths.
cxx_runtime_owned_path() {
    local resolved
    resolved=$(readlink -f "$1") || return 1
    [ -s "$resolved" ] || return 1
    case "$resolved" in "$PREFIX/"*|"$SYSROOT/"*) ;; *) return 1 ;; esac
}

# xg++ runs from its build tree and does not inherit the installed driver's
# C++ include paths. Query the working installed driver for each ABI after
# libstdc++ is installed, retaining the common, target and backward directories
# in the driver's order. The caller provides a local cxx_header_flags array.
cxx_runtime_headers() {
    local abi=$1 directory resolved base= config=0
    local search="$CXX_PROBES/header-search-$abi" list="$CXX_PROBES/header-dirs-$abi"
    cxx_header_flags=(-nostdinc++)
    "$PREFIX/bin/$TARGET-g++" "-m$abi" --sysroot="$SYSROOT" \
        -E -x c++ -v - </dev/null > "$search.out" 2> "$search" || return 1
    awk '
        /^#include <\.\.\.> search starts here:$/ {inside=1; next}
        /^End of search list\.$/ {inside=0}
        inside {sub(/^[[:space:]]+/, ""); print}
    ' "$search" > "$list" || return 1
    while IFS= read -r directory; do
        if [ -f "$directory/iostream" ]; then
            resolved=$(readlink -f "$directory") || return 1
            case "$resolved" in "$PREFIX/"*) ;; *) return 1 ;; esac
            [ -z "$base" ] || return 1
            base=$resolved
        fi
    done < "$list"
    [ -n "$base" ] || return 1
    while IFS= read -r directory; do
        resolved=$(readlink -f "$directory") || return 1
        case "$resolved" in "$PREFIX/"*) ;; *) continue ;; esac
        case "$resolved" in
            "$base"|"$base/"*) ;;
            *) [ -f "$resolved/bits/c++config.h" ] || continue ;;
        esac
        cxx_header_flags+=(-isystem "$resolved")
        [ ! -f "$resolved/bits/c++config.h" ] || config=1
    done < "$list"
    [ "$config" = 1 ]
}

cxx_execute_probe() {
    local abi=$1 binary=$2 expected=$3 loader library_paths library name listing output path
    local paths="$CXX_PROBES/loader-paths-$1"
    if [ "$abi" = 64 ]; then loader="$SYSROOT/lib64/ld-linux-x86-64.so.2"
    elif [ "$abi" = 32 ]; then loader="$SYSROOT/lib32/ld-linux.so.2"
    else return 1; fi
    cxx_runtime_owned_path "$loader" || return 1
    gentoo_verify_elf "$loader" "ELF$abi" || return 1
    library_paths="$SYSROOT/lib$abi:$SYSROOT/usr/lib$abi"
    for name in libstdc++.so.6 libgcc_s.so.1; do
        library=$("$PREFIX/bin/$TARGET-g++" "-m$abi" "-print-file-name=$name") || return 1
        cxx_runtime_owned_path "$library" || return 1
        gentoo_verify_elf "$library" "ELF$abi" || return 1
        library_paths="$library_paths:$(dirname "$library")"
    done
    listing=$(env -i LC_ALL=C "$loader" --inhibit-cache --library-path "$library_paths" --list "$binary") || return 1
    printf '%s\n' "$listing"
    # Every resolved DSO must come from this run; no /usr or bootstrap fallback.
    printf '%s\n' "$listing" | awk '$2=="=>" {if ($3 ~ /^\//) print $3; next} $1 ~ /^\// {print $1}' > "$paths" || return 1
    while IFS= read -r path; do
        cxx_runtime_owned_path "$path" || return 1
        gentoo_verify_elf "$path" "ELF$abi" || return 1
    done < "$paths"
    output=$(env -i LC_ALL=C "$loader" --inhibit-cache --library-path "$library_paths" "$binary") || return 1
    [ "$output" = "$expected" ] || return 1
    printf 'ELF%s %s: %s\n' "$abi" "$(basename "$binary")" "$output"
}

cxx_runtime_verify() {
    local abi language driver compiler source expected binary interpreter mode
    local -a cxx_header_flags=()
    libstdcxx_verify || return 1
    cxx_verify_crt_search || return 1
    for abi in 64 32; do
        if [ "$abi" = 64 ]; then interpreter=/lib64/ld-linux-x86-64.so.2
        else interpreter=/lib/ld-linux.so.2; fi
        for language in c cxx; do
            if [ "$language" = c ]; then
                driver=gcc; source=runtime.c; expected='gentoo C runtime ok'
            else
                driver=g++; source=runtime.cc; expected='gentoo C++ runtime ok'
            fi
            # Exercise installed drivers AND GCC's intermediate compiler path.
            # Both must find their ABI's libc, libgcc and (for C++) libstdc++.
            for mode in installed intermediate; do
                cxx_header_flags=()
                if [ "$mode" = installed ]; then compiler="$PREFIX/bin/$TARGET-$driver"
                elif [ "$language" = c ]; then compiler="$GCC_CXX_BUILD/cc-for-target"
                else
                    compiler="$GCC_CXX_BUILD/cxx-for-target"
                    cxx_runtime_headers "$abi" || return 1
                fi
                binary="$CXX_PROBES/$language-$mode-$abi"
                "$compiler" "-m$abi" --sysroot="$SYSROOT" -B"$SYSROOT/usr/lib$abi/" \
                    "${cxx_header_flags[@]}" -pthread "$SCRIPT_DIR/probes/$source" -o "$binary" || return 1
                gentoo_verify_elf "$binary" "ELF$abi" || return 1
                /usr/bin/readelf -l "$binary" | grep -F "[Requesting program interpreter: $interpreter]" || return 1
                /usr/bin/readelf -d "$binary" | grep -F 'Shared library: [libc.so.6]' || return 1
                if [ "$language" = cxx ]; then
                    /usr/bin/readelf -d "$binary" | grep -F 'Shared library: [libstdc++.so.6]' || return 1
                fi
                cxx_execute_probe "$abi" "$binary" "$expected" || return 1
            done
        done
    done
}
