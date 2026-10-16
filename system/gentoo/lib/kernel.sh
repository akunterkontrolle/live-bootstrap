#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
KERNEL_RELEASE=6.12.111
KERNEL_BUILD="$BUILDS/linux-continuation"
KERNEL_ARTIFACTS="$WORK_ROOT/artifacts/kernel-$KERNEL_RELEASE"
KERNEL_FRAGMENT="$SCRIPT_DIR/kernel-$KERNEL_RELEASE.config.fragment"

kernel_make() {
    make -C "$LINUX_SOURCE" O="$KERNEL_BUILD" ARCH=x86_64 \
        CROSS_COMPILE="$PREFIX/bin/$TARGET-" HOSTCC=/usr/bin/gcc \
        HOSTCFLAGS="-O2 -I$HOST_TOOLS/include" \
        HOSTLDFLAGS="-L$HOST_TOOLS/lib -L$HOST_TOOLS/lib64 -lz" \
        KBUILD_BUILD_USER=live-bootstrap KBUILD_BUILD_HOST=gentoo-continuation "$@"
}

kernel_check_config() {
    local configuration=$1 required
    while IFS= read -r required; do
        case "$required" in
            CONFIG_*=*|'# CONFIG_'*' is not set')
                grep -Fxq "$required" "$configuration" || {
                    printf 'Missing kernel setting: %s\n' "$required" >&2
                    return 1
                } ;;
        esac
    done < "$KERNEL_FRAGMENT"
}

kernel_check_image() {
    local image=$1 flags
    [ -s "$image" ] || return 1
    [ "$(od -An -tx1 -j 510 -N 2 "$image" | tr -d ' \n')" = 55aa ] || return 1
    [ "$(od -An -tx1 -j 514 -N 4 "$image" | tr -d ' \n')" = 48647253 ] || return 1
    # xloadflags bit 0 is the x86-64 kernel boot-protocol flag.
    flags=$(od -An -tu1 -j 566 -N 1 "$image") || return 1
    [ "$((flags & 1))" = 1 ]
}

kernel_running_build_id() {
    local notes
    notes=$(od -An -tx1 -v "${1:-/sys/kernel/notes}" | tr -d ' \n') || return 1
    # Little-endian GNU note: namesz=4, descsz=20, type=3, name="GNU".
    # Pinned Linux links vmlinux with --build-id=sha1. Compare only this
    # immutable descriptor; other notes can contain relocated addresses.
    [[ "$notes" =~ 040000001400000003000000474e5500([0-9a-f]{40}) ]] || return 1
    printf '%s\n' "${BASH_REMATCH[1]}"
}

kernel_check_modules() {
    local directory="$KERNEL_ARTIFACTS/modules/lib/modules/$KERNEL_RELEASE" module count=0
    [ -s "$directory/modules.order" ] && [ -s "$directory/modules.builtin" ] || return 1
    [ -d "$directory/kernel" ] || return 1
    while IFS= read -r -d '' module; do
        gentoo_verify_elf "$module" ELF64 || return 1
        /usr/bin/readelf -p .modinfo "$module" | grep -F "vermagic=$KERNEL_RELEASE " || return 1
        count=$((count + 1))
    done < <(find "$directory/kernel" -type f -name '*.ko' -print0)
    [ "$count" -gt 0 ]
}
