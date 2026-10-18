#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# The host is the completed, source-built live-bootstrap musl environment.
# Nothing in this series downloads host binaries or uses GCC download_prerequisites.
mkdir -p "$BUILDS/probes" "$WORK_ROOT/distfiles" "$SOURCES" "$HOST_TOOLS" "$TOOLBIN" "$PREFIX/bin"
for tool in /usr/bin/gcc /usr/bin/g++ /usr/bin/readelf make tar xz bzip2 gzip \
    curl dhcpcd sha512sum awk bison flex perl python3 autoreconf autoconf automake nm; do
    command -v "$tool" >/dev/null || gentoo_die "missing bootstrap prerequisite: $tool"
done
for header in gmp.h mpfr.h mpc.h zlib.h; do
    [ -s "/usr/include/$header" ] || gentoo_die "missing bootstrap header: $header"
done
# Bind the original host compiler and prerequisite headers, excluding the
# running kernel, CPU count and job settings so the modern-kernel boot is safe.
identity=$(mktemp "$WORK_ROOT/state/.host-toolchain.XXXXXX")
{
    /usr/bin/gcc -dumpmachine
    /usr/bin/gcc -dumpfullversion
    sha256sum /usr/bin/gcc /usr/bin/g++ /usr/include/{gmp,mpfr,mpc,zlib}.h
    for compiler in cc1 cc1plus; do
        path=$(/usr/bin/gcc -print-prog-name="$compiler")
        [ -f "$path" ] || gentoo_die "missing host compiler component: $path"
        sha256sum "$path"
    done
    for library in libgmp.a libmpfr.a libmpc.a libz.a; do
        path=$(/usr/bin/gcc -print-file-name="$library")
        [ -s "$path" ] || gentoo_die "missing source-built host archive: $library"
        sha256sum "$path"
    done
} > "$identity"
if [ -f "$WORK_ROOT/state/host-toolchain.inputs" ]; then
    cmp -s "$identity" "$WORK_ROOT/state/host-toolchain.inputs" || gentoo_die 'host toolchain changed; use a fresh build lineage'
    rm "$identity"
else
    sync
    mv "$identity" "$WORK_ROOT/state/host-toolchain.inputs"
    sync
fi
cat > "$BUILDS/probes/host-libraries.c" <<'PROBE'
#include <gmp.h>
#include <mpfr.h>
#include <mpc.h>
#include <zlib.h>
int main(void) {
    mpc_t x;
    mpc_init2(x, 64);
    mpc_clear(x);
    return zlibVersion()[0] == 0;
}
PROBE
/usr/bin/gcc "$BUILDS/probes/host-libraries.c" -lmpc -lmpfr -lgmp -lz -o "$BUILDS/probes/host-libraries"
"$BUILDS/probes/host-libraries"
printf 'int main() { return 0; }\n' > "$BUILDS/probes/host-cxx.cc"
/usr/bin/g++ "$BUILDS/probes/host-cxx.cc" -o "$BUILDS/probes/host-cxx"
"$BUILDS/probes/host-cxx"

# Catch the actual static bootstrap TLS/zlib prerequisite before any long
# compiler/kernel build, rather than discovering it at the Portage-host stage.
. "$SCRIPT_DIR/lib/python.sh"
gentoo_python_host_inputs
