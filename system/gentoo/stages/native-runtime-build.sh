#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Executed inside NATIVE_ROOT with /mnt/work bound to the durable workspace.
set -Eeuo pipefail
jobs=$1
state=/mnt/work/state
sources=/mnt/work/sources
builds=/mnt/work/build/native
mkdir -p "$builds" /usr/src /usr/bin /usr/lib64 /etc/ssl
export PATH=/usr/bin:/bin HOME=/root LANG=C LC_ALL=C CC=/usr/bin/gcc CXX=/usr/bin/g++

record_hash() { awk -F '|' 'NF==3 {sub(/^sha512=/,"",$3); print $3}' "$1"; }
complete() {
    local name=$1 hash=$2 recipe=$3 output=$4
    [ -s "$state/70-$name.done" ] &&
        grep -Fxq "source sha512=$hash" "$state/70-$name.done" &&
        grep -Fxq "recipe sha256=$recipe" "$state/70-$name.done" &&
        [ -s "$output" ] && grep -Fxq "output sha256=$(sha256sum "$output" | cut -d' ' -f1)" "$state/70-$name.done"
}
mark() {
    local name=$1 hash=$2 recipe=$3 output=$4 temporary
    temporary=$(mktemp "$state/.70-$name.XXXXXX")
    printf 'source sha512=%s\nrecipe sha256=%s\noutput sha256=%s\n' \
        "$hash" "$recipe" "$(sha256sum "$output" | cut -d' ' -f1)" > "$temporary"
    sync "$temporary"; mv "$temporary" "$state/70-$name.done"; sync "$state"
}
check_elf() { /usr/bin/readelf -h "$1" | grep -q 'Class:.*ELF64'; }

# Build each runtime library into the native root before consumers configure.
zlib_hash=$(record_hash /mnt/work/locks/zlib.lock)
zlib_recipe=$(printf '%s\n' zlib-1.3.2-shared-prefix-usr-v1 | sha256sum | cut -d' ' -f1)
zlib_out=/usr/lib64/libz.so.1.3.2
if ! complete zlib "$zlib_hash" "$zlib_recipe" "$zlib_out"; then
    cd "$sources/zlib-1.3.2"; ./configure --prefix=/usr; make -j"$jobs"; make check; make install
    /sbin/ldconfig; mark zlib "$zlib_hash" "$zlib_recipe" "$zlib_out"
fi

bzip_hash=$(record_hash /mnt/work/locks/bzip2.lock)
bzip_recipe=$(printf '%s\n' bzip2-1.0.8-shared-prefix-usr-lib64-v1 | sha256sum | cut -d' ' -f1)
bzip_out=/usr/lib64/libbz2.so.1.0.8
if ! complete bzip2 "$bzip_hash" "$bzip_recipe" "$bzip_out"; then
    cd "$sources/bzip2-1.0.8"; make -j"$jobs" -f Makefile-libbz2_so; make -j"$jobs"; make test
    make install PREFIX=/usr; cp -a libbz2.so* /usr/lib64/
    ln -sfn libbz2.so.1.0.8 /usr/lib64/libbz2.so.1.0; ln -sfn libbz2.so.1.0.8 /usr/lib64/libbz2.so
    /sbin/ldconfig; mark bzip2 "$bzip_hash" "$bzip_recipe" "$bzip_out"
fi

ffi_hash=$(record_hash /mnt/work/locks/libffi.lock)
ffi_recipe=$(printf '%s\n' libffi-3.4.8-shared-prefix-usr-lib64-v1 | sha256sum | cut -d' ' -f1)
ffi_out=/usr/lib64/libffi.so.8.1.2
if ! complete libffi "$ffi_hash" "$ffi_recipe" "$ffi_out"; then
    mkdir -p "$builds/libffi-3.4.8"; cd "$builds/libffi-3.4.8"
    if [ ! -f Makefile ]; then
        "$sources/libffi-3.4.8/configure" --build=x86_64-pc-linux-gnu --host=x86_64-pc-linux-gnu \
            --prefix=/usr --libdir=/usr/lib64 --disable-static --enable-shared
    fi
    make -j"$jobs"; make check; make install; /sbin/ldconfig
    mark libffi "$ffi_hash" "$ffi_recipe" "$ffi_out"
fi

perl_hash=$(record_hash /mnt/work/locks/perl.lock)
perl_recipe=$(printf '%s\n' perl-5.42.2-prefix-usr-shared-v1 | sha256sum | cut -d' ' -f1)
perl_out=/usr/bin/perl
if ! complete perl "$perl_hash" "$perl_recipe" "$perl_out"; then
    cd "$sources/perl-5.42.2"
    ./Configure -des -Dprefix=/usr -Dman1dir=none -Dman3dir=none -Dcc=/usr/bin/gcc -Duseshrplib=true
    make -j"$jobs"; make install
    mark perl "$perl_hash" "$perl_recipe" "$perl_out"
fi

openssl_hash=$(record_hash /mnt/work/locks/openssl.lock)
openssl_recipe=$(printf '%s\n' openssl-3.5.9-shared-prefix-usr-zlib-v1 | sha256sum | cut -d' ' -f1)
openssl_out=/usr/lib64/libssl.so.3
if ! complete openssl "$openssl_hash" "$openssl_recipe" "$openssl_out"; then
    cd "$sources/openssl-3.5.9"; ./Configure linux-x86_64 --prefix=/usr --openssldir=/etc/ssl shared zlib
    make -j"$jobs"; make test; make install_sw install_ssldirs; /sbin/ldconfig
    mark openssl "$openssl_hash" "$openssl_recipe" "$openssl_out"
fi

python_hash=$(record_hash /mnt/work/locks/python.lock)
python_recipe=$(printf '%s\n' python-3.12.15-prefix-usr-openssl-bz2-lzma-zlib-libffi-v1 | sha256sum | cut -d' ' -f1)
python_out=/usr/bin/python3.12
if ! complete python "$python_hash" "$python_recipe" "$python_out"; then
    # CPython's configure probes libffi through pkg-config; this minimal root
    # uses a narrow, deterministic provider for exactly that native library.
    cat > /tmp/native-libffi-pkg-config <<'PKG_CONFIG'
#!/bin/sh
case " $* " in *' libffi '*) ;; *) exit 1 ;; esac
case " $* " in
  *' --exists '*) exit 0 ;;
  *' --modversion '*) printf '%s\n' 3.4.8 ;;
  *' --variable=libdir '*) printf '%s\n' /usr/lib64 ;;
  *' --variable=includedir '*) printf '%s\n' /usr/include ;;
  *' --cflags '*|*' --libs '*) printf '%s\n' '-I/usr/include -L/usr/lib64 -lffi' ;;
  *) exit 1 ;;
esac
PKG_CONFIG
    chmod 0755 /tmp/native-libffi-pkg-config
    mkdir -p "$builds/python-3.12.15"; cd "$builds/python-3.12.15"
    if [ ! -f Makefile ]; then
        PKG_CONFIG=/tmp/native-libffi-pkg-config \
            "$sources/Python-3.12.15/configure" --build=x86_64-pc-linux-gnu --host=x86_64-pc-linux-gnu \
            --prefix=/usr --with-openssl=/usr --with-openssl-rpath=auto --with-system-ffi \
            --with-ensurepip=no --without-readline --disable-test-modules
    fi
    make -j"$jobs"; make install
    rm -f /tmp/native-libffi-pkg-config
    mark python "$python_hash" "$python_recipe" "$python_out"
fi

check_elf /usr/bin/perl && check_elf /usr/bin/python3.12
/usr/bin/python3.12 -c 'import _ctypes, bz2, ctypes, lzma, ssl, zlib; callback=ctypes.CFUNCTYPE(ctypes.c_int)(lambda: 7); assert callback() == 7; print("Python _ctypes/libffi/bz2/lzma/ssl/zlib passed")'
/usr/bin/perl -MConfig -MExtUtils::MakeMaker -MDigest::SHA -MIPC::Cmd -e 'print "Perl $] required modules passed\n"'
