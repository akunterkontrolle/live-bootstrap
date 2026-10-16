#!/bin/bash
set -Eeuo pipefail

# Build only the ELF reader needed by early ebuild install phases. Source
# verification is performed by the caller against the pinned Gentoo Manifest.
# Match the sources and header probes in pax-utils 1.3.11's meson.build, without
# optional libcap/seccomp support or a dependency on Meson/Python/Ninja.
[ "$#" -eq 2 ] || { echo "Usage: $0 SOURCE_DIRECTORY BUILD_DIRECTORY" >&2; exit 2; }
scanelf_source=$(readlink -f "$1")
scanelf_build=$(readlink -m "$2")
grep -Eq "version[[:space:]]*:[[:space:]]*'1[.]3[.]11'" "$scanelf_source/meson.build" || {
    echo 'Expected pinned pax-utils 1.3.11 sources.' >&2; exit 1;
}
mkdir -p "$scanelf_build"
: > "$scanelf_build/config.h"
for header in endian.h byteswap.h sys/endian.h sys/isa_defs.h machine/endian.h \
    linux/seccomp.h linux/securebits.h sys/prctl.h elf-hints.h glob.h; do
    if printf '#include <%s>\n' "$header" | \
        gcc -D_GNU_SOURCE -D_FILE_OFFSET_BITS=64 -x c -fsyntax-only - \
            > "$scanelf_build/header-probe.log" 2>&1; then
        macro=$(printf '%s' "$header" | tr '[:lower:]/.-' '[:upper:]___')
        printf '#define HAVE_%s 1\n' "$macro" >> "$scanelf_build/config.h"
    fi
done
gcc -O2 -std=gnu11 -D_GNU_SOURCE -D_FILE_OFFSET_BITS=64 '-DVERSION="1.3.11"' \
    -I "$scanelf_build" -I "$scanelf_source" \
    "$scanelf_source/scanelf.c" "$scanelf_source/paxelf.c" \
    "$scanelf_source/paxldso.c" "$scanelf_source/paxinc.c" \
    "$scanelf_source/security.c" "$scanelf_source/xfuncs.c" \
    -o "$scanelf_build/scanelf"
"$scanelf_build/scanelf" --version

# Verify the operation required by gen_usr_ldscript on both ABIs. The fixture
# libraries use no libc, so this also works on the earlier ABI64-only host.
printf '.text\n.globl bootstrap_scanelf_probe\nbootstrap_scanelf_probe:\n ret\n' \
    > "$scanelf_build/soname-probe.s"
for bits in 32 64; do
    as --"$bits" "$scanelf_build/soname-probe.s" -o "$scanelf_build/probe-$bits.o"
    case "$bits" in 32) emulation=elf_i386 ;; 64) emulation=elf_x86_64 ;; esac
    ld -m "$emulation" -shared -soname "libbootstrap-probe-$bits.so.1" \
        "$scanelf_build/probe-$bits.o" -o "$scanelf_build/probe-$bits.so"
    "$scanelf_build/scanelf" -qBF '%S#F' "$scanelf_build/probe-$bits.so" | \
        grep -Fxq "libbootstrap-probe-$bits.so.1"
done
echo 'Bootstrap scanelf reads ELF32 and ELF64 SONAMEs.'
