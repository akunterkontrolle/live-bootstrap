#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# argp compensates for musl; static libelf is needed by the next batch's objtool.
gentoo_prepare_source sources/argp.lock
host_argp_action() {
    if [ ! -f "$SOURCES/argp-standalone-1.5.0/configure" ]; then
        (cd "$SOURCES/argp-standalone-1.5.0" && autoreconf -fi)
    fi
    mkdir -p "$BUILDS/argp-host" "$HOST_TOOLS/include" "$HOST_TOOLS/lib"
    cd "$BUILDS/argp-host"
    if [ ! -f Makefile ]; then
        "$SOURCES/argp-standalone-1.5.0/configure" --prefix="$HOST_TOOLS"
    fi
    make -j"$JOBS" libargp.a
    install -m 0644 libargp.a "$HOST_TOOLS/lib/libargp.a"
    install -m 0644 "$SOURCES/argp-standalone-1.5.0/argp.h" "$HOST_TOOLS/include/argp.h"
}
host_argp_verify() {
    printf '#include <argp.h>\nint main(int n, char **v) { struct argp a = {0}; return argp_parse(&a,n,v,0,0,0); }\n' > "$BUILDS/probes/argp.c"
    /usr/bin/gcc -I"$HOST_TOOLS/include" "$BUILDS/probes/argp.c" "$HOST_TOOLS/lib/libargp.a" -o "$BUILDS/probes/argp" || return 1
    "$BUILDS/probes/argp"
}
fingerprint=$(gentoo_toolchain_fingerprint stages/13-host-tools.sh sources/argp.lock)
gentoo_run_stage 13-host-argp "$fingerprint" host_argp_action host_argp_verify

gentoo_prepare_source sources/elfutils.lock
host_libelf_action() {
    mkdir -p "$BUILDS/elfutils-host"
    cd "$BUILDS/elfutils-host"
    if [ ! -f Makefile ]; then
        CPPFLAGS="-I$HOST_TOOLS/include" LDFLAGS="-L$HOST_TOOLS/lib" LIBS=-largp \
            ac_cv_search_fts_close='none required' ac_cv_search__obstack_free='none required' \
            "$SOURCES/elfutils-0.196/configure" --prefix="$HOST_TOOLS" \
                --disable-nls --disable-debuginfod --disable-libdebuginfod
    fi
    # The selected lib/libelf targets do not need fts or obstack. Do not build
    # elfutils programs or install a shared libelf with host-runtime dependencies.
    make -j"$JOBS" -C lib CPPFLAGS="-I$HOST_TOOLS/include"
    make -j"$JOBS" -C libelf CPPFLAGS="-I$HOST_TOOLS/include" libelf.a
    install -m 0644 libelf/libelf.a "$HOST_TOOLS/lib/libelf.a"
    install -m 0644 "$SOURCES/elfutils-0.196/libelf/"{libelf,gelf,nlist,elf}.h "$HOST_TOOLS/include/"
}
host_libelf_verify() {
    local header
    for header in libelf gelf nlist elf; do [ -s "$HOST_TOOLS/include/$header.h" ] || return 1; done
    printf '#include <libelf.h>\nint main(void) { return elf_version(EV_CURRENT) == EV_NONE; }\n' > "$BUILDS/probes/elf.c"
    /usr/bin/gcc -I"$HOST_TOOLS/include" "$BUILDS/probes/elf.c" "$HOST_TOOLS/lib/libelf.a" -lz -o "$BUILDS/probes/elf" || return 1
    "$BUILDS/probes/elf"
}
fingerprint=$(gentoo_toolchain_fingerprint stages/13-host-tools.sh sources/elfutils.lock "$WORK_ROOT/state/13-host-argp.done")
gentoo_run_stage 13-host-libelf "$fingerprint" host_libelf_action host_libelf_verify

# Linux headers_install invokes rsync. Build only its required host features.
gentoo_prepare_source sources/rsync.lock
host_rsync_action() {
    mkdir -p "$BUILDS/rsync-host"
    cd "$BUILDS/rsync-host"
    if [ ! -f Makefile ]; then
        "$SOURCES/rsync-3.5.1/configure" --prefix="$HOST_TOOLS" \
            --disable-acl-support --disable-xattr-support --disable-iconv --disable-idn \
            --disable-openssl --disable-xxhash --disable-zstd --disable-lz4 \
            --with-included-popt --with-included-zlib
    fi
    make -j"$JOBS"
    make install
}
host_rsync_verify() {
    "$HOST_TOOLS/bin/rsync" --version || return 1
    mkdir -p "$BUILDS/probes/rsync-from" "$BUILDS/probes/rsync-to"
    printf 'header-copy-probe\n' > "$BUILDS/probes/rsync-from/header"
    "$HOST_TOOLS/bin/rsync" -a "$BUILDS/probes/rsync-from/" "$BUILDS/probes/rsync-to/" || return 1
    cmp "$BUILDS/probes/rsync-from/header" "$BUILDS/probes/rsync-to/header"
}
fingerprint=$(gentoo_toolchain_fingerprint stages/13-host-tools.sh sources/rsync.lock)
gentoo_run_stage 13-host-rsync "$fingerprint" host_rsync_action host_rsync_verify
