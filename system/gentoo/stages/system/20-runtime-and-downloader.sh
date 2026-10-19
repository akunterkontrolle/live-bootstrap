# Provide guarded Portage calls and build the source-locked HTTPS downloader.
# Sourced by stages/80-native-gentoo-system.sh; shares its checked state.
chroot_env() {
    chroot "$NATIVE_ROOT" /usr/bin/env -i \
        HOME=/root TERM="${TERM:-vt100}" PATH=/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin \
        LANG=C LC_ALL=C PYTHONPATH=/usr/lib/portage/lib \
        LD_LIBRARY_PATH=/usr/lib/perl5/5.42.2/x86_64-linux/CORE \
        EMERGE_DEFAULT_OPTS="--jobs=$PACKAGE_JOBS --load-average=$LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n" \
        MAKEOPTS="-j$GENTOO_PORTAGE_MAKE_JOBS" PORTAGE_BINHOST= "$@"
}
portage_manifest_guard_check
portage_repository_guard_check
run_emerge() {
    local emerge_status=0
    portage_manifest_guard_check && portage_repository_guard_check || return 1
    chroot_env /usr/local/sbin/emerge "$@" || emerge_status=$?
    portage_manifest_guard_check && portage_repository_guard_check || return 1
    return "$emerge_status"
}
# Plan acceptance helpers come from lib/native-gentoo-system.sh.
native_gentoo_glibc_runtime_version() {
    # Python's _ctypes is optional when CPython is configured without libffi.
    # glibc's own ldd reports the linked runtime without that optional module.
    chroot_env /usr/bin/ldd --version | awk 'NR == 1 {print $NF}'
}
verify_native_gentoo_glibc() {
    local installed runtime
    installed=$(chroot_env /usr/bin/portageq best_version / sys-libs/glibc)
    [ "$installed" = "sys-libs/glibc-$BOOTSTRAP_GLIBC_VERSION" ] || {
        echo "Gentoo glibc package is not source-compatible: $installed" >&2; return 1;
    }
    runtime=$(native_gentoo_glibc_runtime_version) || return 1
    case "$runtime" in 2.44*) ;; *)
        echo "Native glibc runtime is not source-compatible: $runtime" >&2; return 1 ;; esac
}

run_gentoo_bootstrap() {
    local bootstrap_status=0
    chroot "$NATIVE_ROOT" /usr/bin/env -i \
        HOME=/root TERM="${TERM:-vt100}" PATH=/usr/local/sbin:/usr/bin:/bin:/usr/sbin:/sbin \
        LANG=C LC_ALL=C PYTHONPATH=/usr/lib/portage/lib USE=-test \
        LD_LIBRARY_PATH=/usr/lib/perl5/5.42.2/x86_64-linux/CORE \
        myLIBC=sys-libs/glibc \
        BOOTSTRAP_USE="$bootstrap_use" \
        FEATURES='-test -getbinpkg -buildpkg -assume-digests -digest strict' \
        EMERGE_DEFAULT_OPTS="--jobs=$PACKAGE_JOBS --load-average=$LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n" \
        MAKEOPTS="-j$GENTOO_PORTAGE_MAKE_JOBS" PORTAGE_BINHOST= \
        /bin/bash /usr/portage/scripts/bootstrap.sh "$@" || bootstrap_status=$?
    portage_manifest_guard_check && portage_repository_guard_check || return 1
    return "$bootstrap_status"
}

fetch_wget
verify_locked wget-1.25.0.tar.lz "$SYSTEM_LOCK"
wget_source="$NATIVE_ROOT/usr/src/wget-1.25.0"
if [ -d "$wget_source" ]; then
    [ -s "$wget_source/.source-archive.sha512" ] &&
        [ "$(cat "$wget_source/.source-archive.sha512")" = "$wget_archive_hash" ] || {
        echo 'Existing native Wget source tree lacks the matching archive receipt.' >&2; exit 1;
    }
else
    source_partial="$wget_source.extracting"
    [ ! -e "$source_partial" ] || {
        echo "Inspect interrupted Wget extraction at $source_partial." >&2; exit 1;
    }
    mkdir -p "$source_partial" "$NATIVE_ROOT/usr/src/archives"
    cp "$DISTFILES/wget-1.25.0.tar.lz" "$NATIVE_ROOT/usr/src/archives/wget-1.25.0.tar.lz"
    host_lzip="$WORK_ROOT/native-build-tools/bin/lzip"
    [ -x "$host_lzip" ] || { echo 'Source-built live-bootstrap lzip is missing.' >&2; exit 1; }
    PATH="$(dirname "$host_lzip"):$PATH" tar --lzip -xf \
        "$NATIVE_ROOT/usr/src/archives/wget-1.25.0.tar.lz" \
        --strip-components=1 -C "$source_partial"
    [ -x "$source_partial/configure" ] || { echo 'Pinned Wget source lacks configure.' >&2; exit 1; }
    printf '%s\n' "$wget_archive_hash" > "$source_partial/.source-archive.sha512"
    mv "$source_partial" "$wget_source"
fi

wget_built_marker="$STATE/native-wget-1.25.0-built.done"
if [ -s "$wget_built_marker" ]; then
    grep -Fxq "source sha512=$wget_archive_hash" "$wget_built_marker" &&
        grep -Fxq "system lock sha256=$system_lock_hash" "$wget_built_marker" &&
        marker_driver_recorded "$wget_built_marker" &&
        [ -x "$NATIVE_ROOT/usr/bin/wget" ] || {
        echo 'Wget build marker does not match its locked inputs/driver.' >&2; exit 1;
    }
else
    wget_build="$NATIVE_ROOT/usr/src/build/wget-1.25.0"
    mkdir -p "$wget_build"
    if [ ! -f "$wget_build/Makefile" ]; then
        chroot_env OPENSSL_CFLAGS=-I/usr/include \
            OPENSSL_LIBS='-L/usr/lib64 -lssl -lcrypto' /bin/bash -c \
            'cd /usr/src/build/wget-1.25.0 && /usr/src/wget-1.25.0/configure --prefix=/usr --with-ssl=openssl --disable-nls'
    fi
    chroot_env /bin/bash -c "cd /usr/src/build/wget-1.25.0 && make -j$GENTOO_BUILD_SOURCE_JOBS"
    # Portage needs the native fetcher before the pinned bootstrap can fetch
    # its compiler/system inputs. Full Wget tests follow after Gentoo @system
    # supplies its Perl/Python runtime dependencies.
    chroot_env /bin/bash -c 'cd /usr/src/build/wget-1.25.0 && make install'
    chroot_env /usr/bin/wget --version | sed -n '1,2p'
    chroot_env /usr/bin/wget --spider --timeout=30 https://distfiles.gentoo.org/
    chroot_env /usr/bin/ldd /usr/bin/wget > "$STATE/native-wget-ldd.txt"
    grep -q libssl "$STATE/native-wget-ldd.txt" &&
        grep -q libcrypto "$STATE/native-wget-ldd.txt" || {
        echo 'Wget does not link to the source-built OpenSSL libraries.' >&2; exit 1;
    }
    printf 'complete %s\nsource sha512=%s\nsystem lock sha256=%s\nCA data sha256=%s\nTLS peer verification=enabled; HTTPS spider passed\nupstream make=passed\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$wget_archive_hash" "$system_lock_hash" "$ca_hash" "$driver_hash" \
        > "$wget_built_marker.tmp"
    sync "$wget_built_marker.tmp"
    mv "$wget_built_marker.tmp" "$wget_built_marker"
    sync "$STATE"
fi



PHASE=validate-portage-and-gentoo-bootstrap
native_python_version=$(chroot_env /usr/bin/python3.12 --version 2>&1)
case "$native_python_version" in
    'Python '* )
        # A Gentoo Python profile can have several versions installed at once.
        # `portageq best_version dev-lang/python` returns the newest one, which
        # need not be the interpreter used by Python 3.12-bound Portage. Check
        # the exact executable's VDB entry and repository instead.
        native_python_package="dev-lang/python-${native_python_version#Python }"
        native_python_vdb="$NATIVE_ROOT/var/db/pkg/$native_python_package"
        if [ -d "$native_python_vdb" ]; then
            [ "$(cat "$native_python_vdb/repository" 2>/dev/null || true)" = gentoo ] || {
                echo "Python $native_python_version has a VDB entry from an unexpected repository." >&2; exit 1;
            }
        elif [ "$native_python_version" = 'Python 3.12.15' ]; then
            # Before Gentoo's Python ebuild is installed, this exact version is
            # the source-built interpreter recorded by the predecessor phase.
            :
        else
            echo "Python $native_python_version is not backed by its exact Gentoo VDB entry." >&2
            exit 1
        fi
        ;;
    *)
        echo "The validated native Python interpreter is unexpected: $native_python_version" >&2
        exit 1
        ;;
esac
python_link="$NATIVE_ROOT/usr/bin/python"
if [ -L "$python_link" ]; then
    python_link_target=$(readlink "$python_link")
    case "$python_link_target" in
        python3.12) ;;
        python-exec2c)
            # Portage can install python-exec before the Gentoo Python ebuild
            # has registered an interpreter. Keep the already verified
            # source-built 3.12.15 available to the pinned bootstrap until
            # @system installs and configures its Python runtime.
            if ! chroot_env /usr/bin/python --version >/dev/null 2>&1; then
                ln -sfn python3.12 "$python_link"
                echo 'Restored the source-built Python 3.12.15 bootstrap alias; the unconfigured python-exec wrapper had no registered interpreter.'
            fi
            ;;
        *)
            echo "Unexpected unversioned Python link in native root: $python_link_target" >&2
            exit 1
            ;;
    esac
elif [ ! -e "$python_link" ]; then
    ln -s python3.12 "$python_link"
else
    echo 'Unexpected unversioned Python executable in native root.' >&2
    exit 1
fi
run_emerge --version
run_emerge --info | tee "$STATE/native-gentoo-emerge-info.txt"
bootstrap_use=$(chroot_env /usr/bin/portageq envvar BOOTSTRAP_USE)
case "$bootstrap_use" in *[!a-zA-Z0-9_+\ -]*)
    echo 'Pinned profile produced unexpected BOOTSTRAP_USE characters.' >&2; exit 1 ;;
esac
