#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'native runtimes require the verified modern-kernel handoff'
. "$SCRIPT_DIR/lib/native.sh"
. "$SCRIPT_DIR/lib/native-tools.sh"

native_runtime_cleanup() {
    local root
    for root in "$NATIVE_ROOT/dev" "$NATIVE_ROOT/sys" "$NATIVE_ROOT/proc" \
        "$NATIVE_ROOT/mnt/work/locks" "$NATIVE_ROOT/mnt/work/state" \
        "$NATIVE_ROOT/mnt/work/build/native" "$NATIVE_ROOT/mnt/work/sources"; do
        gentoo_unmount_tree "$root" || return 1
    done
}
native_runtime_action() {
    local lock
    for lock in sources/zlib.lock sources/bzip2.lock sources/libffi.lock \
        sources/perl.lock sources/openssl.lock sources/python.lock; do
        gentoo_prepare_source "$lock"
    done
    mkdir -p "$WORK_ROOT/build/native" "$NATIVE_ROOT/mnt/work/locks" \
        "$NATIVE_ROOT/mnt/work/state" "$NATIVE_ROOT/mnt/work/build/native" \
        "$NATIVE_ROOT/mnt/work/sources" "$NATIVE_ROOT/tmp" "$NATIVE_ROOT/proc" \
        "$NATIVE_ROOT/sys" "$NATIVE_ROOT/dev" "$NATIVE_ROOT/etc"
    cat > "$NATIVE_ROOT/etc/hosts" <<'EOF_HOSTS'
127.0.0.1 localhost localhost.localdomain
::1 localhost localhost.localdomain
EOF_HOSTS
    cat > "$NATIVE_ROOT/etc/nsswitch.conf" <<'EOF_NSSWITCH'
passwd: files
group: files
shadow: files
hosts: files dns
EOF_NSSWITCH
    if [ -r /etc/resolv.conf ]; then
        cp -L /etc/resolv.conf "$NATIVE_ROOT/etc/resolv.conf.tmp"
        mv "$NATIVE_ROOT/etc/resolv.conf.tmp" "$NATIVE_ROOT/etc/resolv.conf"
    fi
    for point in "$NATIVE_ROOT/mnt/work/sources" "$NATIVE_ROOT/mnt/work/build/native" \
        "$NATIVE_ROOT/mnt/work/state" "$NATIVE_ROOT/mnt/work/locks"; do
        mountpoint -q "$point" && gentoo_die "unexpected existing native runtime bind: $point"
    done
    for point in "$NATIVE_ROOT/proc" "$NATIVE_ROOT/sys" "$NATIVE_ROOT/dev"; do
        mountpoint -q "$point" && gentoo_die "unexpected existing native runtime mount: $point"
    done
    trap native_runtime_cleanup EXIT
    mount --bind "$WORK_ROOT/sources" "$NATIVE_ROOT/mnt/work/sources"
    mount --bind "$WORK_ROOT/build/native" "$NATIVE_ROOT/mnt/work/build/native"
    mount --bind "$WORK_ROOT/state" "$NATIVE_ROOT/mnt/work/state"
    mount --bind "$SCRIPT_DIR/sources" "$NATIVE_ROOT/mnt/work/locks"
    mount -t proc proc "$NATIVE_ROOT/proc"
    mount --rbind /sys "$NATIVE_ROOT/sys"; mount --make-rslave "$NATIVE_ROOT/sys"
    mount --rbind /dev "$NATIVE_ROOT/dev"; mount --make-rslave "$NATIVE_ROOT/dev"
    install -m 0755 "$SCRIPT_DIR/stages/native-runtime-build.sh" "$NATIVE_ROOT/tmp/native-runtime-build.sh"
    cd /
    chroot "$NATIVE_ROOT" /bin/bash /tmp/native-runtime-build.sh "$NATIVE_JOBS"
    native_runtime_cleanup
    trap - EXIT
}
native_runtime_verify() {
    local binary package output
    for binary in perl python3.12; do
        [ -x "$NATIVE_ROOT/usr/bin/$binary" ] || return 1
        native_verify_elf "$NATIVE_ROOT/usr/bin/$binary" ELF64 || return 1
        chroot "$NATIVE_ROOT" /usr/bin/env LD_LIBRARY_PATH=/usr/lib/perl5/5.42.2/x86_64-linux/CORE "/usr/bin/$binary" --version >/dev/null 2>&1 || return 1
    done
    for package in zlib bzip2 libffi perl openssl python; do
        [ -s "$WORK_ROOT/state/70-$package.done" ] || return 1
        case "$package" in
            zlib) output="$NATIVE_ROOT/usr/lib64/libz.so.1.3.2" ;;
            bzip2) output="$NATIVE_ROOT/usr/lib64/libbz2.so.1.0.8" ;;
            libffi) output="$NATIVE_ROOT/usr/lib64/libffi.so.8" ;;
            perl) output="$NATIVE_ROOT/usr/bin/perl" ;;
            openssl) output="$NATIVE_ROOT/usr/lib64/libssl.so.3" ;;
            python) output="$NATIVE_ROOT/usr/bin/python3.12" ;;
        esac
        [ -s "$output" ] && grep -Fxq "output sha256=$(sha256sum "$output" | cut -d' ' -f1)" \
            "$WORK_ROOT/state/70-$package.done" || return 1
    done
    chroot "$NATIVE_ROOT" /usr/bin/python3.12 -c \
        'import _ctypes, bz2, ctypes, lzma, ssl, zlib; callback=ctypes.CFUNCTYPE(ctypes.c_int)(lambda: 7); assert callback() == 7' || return 1
    chroot "$NATIVE_ROOT" /usr/bin/env LD_LIBRARY_PATH=/usr/lib/perl5/5.42.2/x86_64-linux/CORE /usr/bin/perl -MConfig -MExtUtils::MakeMaker -MDigest::SHA -MIPC::Cmd \
        -e 'print $] . "\n"' | grep -Fxq '5.042002' || return 1
}
fingerprint=$(gentoo_fingerprint stages/70-native-runtimes.sh stages/native-runtime-build.sh \
    lib/native.sh lib/native-tools.sh lib/mounts.sh lib/sources.sh lib/state.sh \
    sources/zlib.lock sources/bzip2.lock sources/libffi.lock sources/perl.lock \
    sources/openssl.lock sources/python.lock \
    "$WORK_ROOT/state/63-native-tool-floor.done" "$WORK_ROOT/state/52-portage-host.done")
gentoo_run_stage 70-native-runtimes "$fingerprint" native_runtime_action native_runtime_verify
printf 'Native Python, Perl and required runtime modules are ready.\n'
