# Verify both ABIs, remove temporary providers, and record the usable native system.
# Sourced by stages/80-native-gentoo-system.sh; shares its checked state.
# The fresh source-built continuation root and Gentoo profile are already
# multilib. Keep the selected profile stable across the first @system pass.
if [ ! -s "$multilib_profile_marker" ]; then
    expected_seed_profile=/usr/portage/profiles/default/linux/amd64/23.0/split-usr/no-multilib
    [ "$(readlink "$NATIVE_ROOT/etc/portage/make.profile")" = "$expected_seed_profile" ] || {
        echo 'Refusing to change an unexpected Gentoo seed profile to multilib.' >&2; exit 1;
    }
    gentoo_profile_path=/usr/portage/profiles/default/linux/amd64/23.0/split-usr
    ln -sfn "$gentoo_profile_path" "$NATIVE_ROOT/etc/portage/make.profile"
    [ "$(readlink "$NATIVE_ROOT/etc/portage/make.profile")" = "$gentoo_profile_path" ] || {
        echo 'Could not activate the pinned amd64 multilib Gentoo profile.' >&2; exit 1;
    }
    printf 'complete %s\nGentoo repository commit=%s\nprevious profile=%s\nactive profile=%s\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$expected_seed_profile" \
        "$gentoo_profile_path" "$driver_hash" > "$multilib_profile_marker.tmp"
    sync "$multilib_profile_marker.tmp"
    mv "$multilib_profile_marker.tmp" "$multilib_profile_marker"
    sync "$STATE"
else
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$multilib_profile_marker" &&
        grep -Fxq 'active profile=/usr/portage/profiles/default/linux/amd64/23.0/split-usr' \
            "$multilib_profile_marker" &&
        marker_driver_recorded "$multilib_profile_marker" &&
        [ "$(readlink "$NATIVE_ROOT/etc/portage/make.profile")" = \
            /usr/portage/profiles/default/linux/amd64/23.0/split-usr ] || {
        echo 'Gentoo multilib profile marker and active profile do not match.' >&2; exit 1;
    }
fi
if grep -Fxq 'ABI_X86="64"' "$NATIVE_ROOT/etc/portage/make.conf"; then
    sed -i 's/^ABI_X86="64"$/ABI_X86="64 32"/' "$NATIVE_ROOT/etc/portage/make.conf"
fi
grep -Fxq 'ABI_X86="64 32"' "$NATIVE_ROOT/etc/portage/make.conf" || {
    echo 'The fresh Gentoo bootstrap lost its selected amd64 multilib ABI.' >&2; exit 1;
}

# This lineage entered Gentoo bootstrap with source-built 32/64-bit glibc and
# GCC already present, so the first Gentoo @system pass is itself multilib.
# Prove Gentoo's own GCC/glibc outputs before recording the equivalent markers
# used by the final verification; never infer completion from the early
# cross sysroot alone.
multilib_gcc_marker="$STATE/native-gentoo-multilib-gcc.done"
multilib_glibc_marker="$STATE/native-gentoo-multilib-glibc.done"
multilib_gcc_full_marker="$STATE/native-gentoo-multilib-gcc-full.done"
if [ ! -s "$multilib_gcc_marker" ] || [ ! -s "$multilib_glibc_marker" ] ||
    [ ! -s "$multilib_gcc_full_marker" ]; then
    [ -s "$system_marker" ] || {
        echo 'Cannot validate the first Gentoo multilib toolchain before @system completes.' >&2; exit 1;
    }
    gentoo_glibc=$(chroot_env /usr/bin/portageq best_version / sys-libs/glibc)
    gentoo_gcc=$(chroot_env /usr/bin/portageq best_version / sys-devel/gcc)
    gentoo_gcc_vdb="$NATIVE_ROOT/var/db/pkg/$gentoo_gcc"
    [ "$gentoo_glibc" = "sys-libs/glibc-$BOOTSTRAP_GLIBC_VERSION" ] &&
        grep -qw multilib "$gentoo_gcc_vdb/USE" && grep -qw cxx "$gentoo_gcc_vdb/USE" || {
        echo 'The first Gentoo @system pass did not register multilib glibc and full GCC.' >&2; exit 1;
    }
    gentoo_glibc32=
    for candidate in "$NATIVE_ROOT/lib/libc.so.6" "$NATIVE_ROOT/usr/lib/libc.so.6" \
        "$NATIVE_ROOT/lib32/libc.so.6" "$NATIVE_ROOT/usr/lib32/libc.so.6"; do
        if chroot_env /usr/bin/readelf -h "${candidate#"$NATIVE_ROOT"}" 2>/dev/null |
            awk '$1 == "Class:" && $2 == "ELF32" { found=1 } END { exit !found }'; then
            gentoo_glibc32=$candidate
            break
        fi
    done
    [ -n "$gentoo_glibc32" ] || {
        echo 'The first Gentoo @system pass has no ELF32 glibc output.' >&2; exit 1;
    }
    chroot_env /bin/bash -c '
        set -eu
        printf "%s\n" "long long abi_probe(long long n) { return n / 3; }" > /tmp/gentoo-abi-probe.c
        gcc -m32 -fPIC -nostdlib -shared -Wl,--no-undefined \
            -o /tmp/gentoo-abi-probe.so /tmp/gentoo-abi-probe.c -lgcc
        readelf -h /tmp/gentoo-abi-probe.so |
            awk '\''$1 == "Class:" && $2 == "ELF32" { found=1 } END { exit !found }'\''
        printf "%s\n" "#include <string>" "int main() { std::string s(8, char(120)); return s.size() != 8; }" \
            > /tmp/gentoo-abi-probe.cc
        g++ -m32 -o /tmp/gentoo-abi-probe /tmp/gentoo-abi-probe.cc
        readelf -h /tmp/gentoo-abi-probe |
            awk '\''$1 == "Class:" && $2 == "ELF32" { found=1 } END { exit !found }'\''
        /tmp/gentoo-abi-probe
        rm -f /tmp/gentoo-abi-probe.c /tmp/gentoo-abi-probe.so \
            /tmp/gentoo-abi-probe.cc /tmp/gentoo-abi-probe
    ' || { echo 'The first Gentoo @system multilib C/C++ runtime probes failed.' >&2; exit 1; }
    run_emerge --pretend --newuse --deep @system \
        | tee "$STATE/native-gentoo-at-system-multilib-remaining.txt"
    if ! plan_is_empty \
        "$STATE/native-gentoo-at-system-multilib-remaining.txt"; then
        echo 'The first Gentoo multilib @system pass is not converged.' >&2
        exit 1
    fi
    if [ ! -s "$multilib_gcc_marker" ]; then
        printf 'complete %s\nGentoo repository commit=%s\nGentoo GCC package=%s\nmultilib USE=enabled\n32-bit no-libc libgcc link=passed\nfirst Gentoo @system pass already multilib\nGCC VDB sha256=%s\ndriver sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$gentoo_gcc" \
            "$(sha256 "$gentoo_gcc_vdb/USE")" "$driver_hash" > "$multilib_gcc_marker.tmp"
        sync "$multilib_gcc_marker.tmp"; mv "$multilib_gcc_marker.tmp" "$multilib_gcc_marker"
    fi
    if [ ! -s "$multilib_glibc_marker" ]; then
        printf 'complete %s\nGentoo repository commit=%s\nABI_X86=64 32\nGentoo glibc package=%s\nELF32 glibc=%s\nfirst Gentoo @system pass already multilib\ndriver sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$gentoo_glibc" "$gentoo_glibc32" \
            "$driver_hash" > "$multilib_glibc_marker.tmp"
        sync "$multilib_glibc_marker.tmp"; mv "$multilib_glibc_marker.tmp" "$multilib_glibc_marker"
    fi
    if [ ! -s "$multilib_gcc_full_marker" ]; then
        printf 'complete %s\nGentoo repository commit=%s\nGentoo GCC package=%s\nmultilib USE=enabled\nC++ USE=enabled\n32-bit C++ compile/link/run=passed\nfirst Gentoo @system pass already multilib\nGCC VDB sha256=%s\ndriver sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$gentoo_gcc" \
            "$(sha256 "$gentoo_gcc_vdb/USE")" "$driver_hash" > "$multilib_gcc_full_marker.tmp"
        sync "$multilib_gcc_full_marker.tmp"; mv "$multilib_gcc_full_marker.tmp" "$multilib_gcc_full_marker"
    fi
    if [ ! -s "$multilib_system_marker" ]; then
        package_names=$(find "$NATIVE_ROOT/var/db/pkg" -mindepth 2 -maxdepth 2 -type d -printf '%P\n' | sort)
        package_count=$(printf '%s\n' "$package_names" | wc -l)
        package_digest=$(printf '%s\n' "$package_names" | sha256sum | awk '{print $1}')
        printf 'complete %s\nGentoo repository commit=%s\nABI_X86=64 32\nfirst Gentoo @system pass=multilib\nmultilib package database entries=%s\nmultilib package names sha256=%s\ndriver sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$package_count" "$package_digest" "$driver_hash" \
            > "$multilib_system_marker.tmp"
        sync "$multilib_system_marker.tmp"; mv "$multilib_system_marker.tmp" "$multilib_system_marker"
    fi
    sync "$STATE"
fi

# The early-multilib route must prove its own outputs above. Never fall back
# to the historical C-only/no-multilib conversion sequence on missing receipts.
for early_marker in "$multilib_gcc_marker" "$multilib_glibc_marker" \
    "$multilib_gcc_full_marker" "$multilib_system_marker"; do
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$early_marker" || {
        echo "Early multilib receipt is invalid: $early_marker" >&2; exit 1;
    }
done

# Reassert the global no-test policy after the profile transition. No
# package.use entries may re-enable Gentoo ebuild test suites.
grep -Fxq 'FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"' \
    "$NATIVE_ROOT/etc/portage/make.conf" &&
    grep -Eq '^USE="-test[^"]*"$' "$NATIVE_ROOT/etc/portage/make.conf" || {
    echo 'Global no-test policy was lost during the multilib profile transition.' >&2; exit 1;
}

# At this point the Gentoo @system set has source-built its libc and crypt
# provider. Remove the temporary native-root assertions, then ensure Gentoo's
# own Perl ebuild is registered (it may already have been selected by @system).
installed_gentoo_glibc=$(chroot_env /usr/bin/portageq best_version / sys-libs/glibc 2>/dev/null || true)
if [ -z "$installed_gentoo_glibc" ]; then
    run_emerge --oneshot sys-libs/glibc
    installed_gentoo_glibc=$(chroot_env /usr/bin/portageq best_version / sys-libs/glibc)
fi
case "$installed_gentoo_glibc" in sys-libs/glibc-*) ;; *)
    echo "Gentoo's own glibc ebuild is not installed after @system: $installed_gentoo_glibc" >&2; exit 1 ;;
esac
# libc's private nscd interface depends on its build configuration. A seed
# daemon copied with the GNU sysroot is not tracked by Portage and can survive
# replacement of libc. Preserve package-owned binaries; retire only the seed.
python3 "$SCRIPT_DIR/retire-bootstrap-nscd.py" --root "$NATIVE_ROOT" \
    --sysroot "$WORK_ROOT/sysroot-x86_64" --backup "$WORK_ROOT/repair-state/nscd-native" --apply

retire_bootstrap_perl_provider
installed_gentoo_perl=$(chroot_env /usr/bin/portageq best_version / dev-lang/perl 2>/dev/null || true)
if [ -z "$installed_gentoo_perl" ]; then
    run_emerge --oneshot dev-lang/perl
    installed_gentoo_perl=$(chroot_env /usr/bin/portageq best_version / dev-lang/perl)
fi
case "$installed_gentoo_perl" in dev-lang/perl-*) ;; *)
    echo "Gentoo's own Perl ebuild is not installed after @system: $installed_gentoo_perl" >&2; exit 1 ;;
esac
printf 'Gentoo Perl runtime=%s\n' "$installed_gentoo_perl"

PHASE=verify-source-built-wget-tls
wget_marker="$STATE/native-wget-1.25.0.done"
if [ -s "$wget_marker" ]; then
    grep -Fxq "source sha512=$wget_archive_hash" "$wget_marker" &&
        grep -Fxq "system lock sha256=$system_lock_hash" "$wget_marker" &&
        marker_driver_recorded "$wget_marker" || {
        echo 'Wget runtime marker does not match its locked inputs/driver.' >&2; exit 1;
    }
else
    # Keep a runtime transport check, but do not run the Wget upstream test
    # suite or install test-only Perl servers under the global no-test policy.
    chroot_env /usr/bin/wget --spider --timeout=30 https://distfiles.gentoo.org/
    printf 'complete %s\nsource sha512=%s\nsystem lock sha256=%s\nGentoo repository commit=%s\nupstream make check=not-run under global ebuild no-test policy\nTLS peer verification=enabled; HTTPS spider passed\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$wget_archive_hash" "$system_lock_hash" "$REPO_COMMIT" "$driver_hash" \
        > "$wget_marker.tmp"
    sync "$wget_marker.tmp"
    mv "$wget_marker.tmp" "$wget_marker"
    sync "$STATE"
fi

PHASE=verify-native-gentoo-system
expected_gentoo_profile=/usr/portage/profiles/default/linux/amd64/23.0/split-usr
active_gentoo_profile=$(readlink "$NATIVE_ROOT/etc/portage/make.profile" 2>/dev/null || true)
[ "$active_gentoo_profile" = "$expected_gentoo_profile" ] || {
    echo "Native root is not using the required split-usr multilib profile: ${active_gentoo_profile:-missing}" >&2
    exit 1
}
installed_openrc=$(chroot_env /usr/bin/portageq best_version / sys-apps/openrc 2>/dev/null || true)
case "$installed_openrc" in
    sys-apps/openrc-*) ;;
    *) echo "Gentoo OpenRC package is not installed: ${installed_openrc:-not found}" >&2; exit 1 ;;
esac
openrc_vdb="$NATIVE_ROOT/var/db/pkg/$installed_openrc"
[ -d "$openrc_vdb" ] && [ -x "$NATIVE_ROOT/sbin/openrc" ] &&
    [ -x "$NATIVE_ROOT/sbin/openrc-run" ] || {
    echo "OpenRC VDB entry or expected split-usr executables are missing for $installed_openrc." >&2
    exit 1
}
run_emerge --version
chroot_env /usr/bin/gcc --version | sed -n '1p'
chroot_env /usr/bin/g++ --version | sed -n '1p'
chroot_env /usr/bin/ldd --version | sed -n '1p'
chroot_env /usr/bin/python3.12 -c \
    'import bz2, lzma, ssl, zlib; print("native Python modules: bz2/lzma/ssl/zlib passed")'
run_emerge --pretend --newuse --deep @system | tee "$STATE/native-gentoo-at-system-final-check.txt"
plan_is_empty "$STATE/native-gentoo-at-system-final-check.txt" || {
    echo 'The native root still has pending Gentoo @system changes.' >&2; exit 1;
}
printf 'complete %s\nworkspace UUID=%s\nVM scratch UUID=%s\nGentoo repository commit=%s\nsystem source lock sha256=%s\nbootstrap marker sha256=%s\n@system marker sha256=%s\nCA data sha256=%s\nprofile=default/linux/amd64/23.0/split-usr; OpenRC; amd64 multilib\nOpenRC package=%s\nOpenRC VDB sha256=%s\nvalidation=source-built Portage, Wget, GCC, G++, glibc, Python, OpenRC, and @system graph\nscript sha256=%s\n' \
    "$(date -u +%FT%TZ)" "$workspace_uuid" "$GENTOO_SCRATCH_UUID" "$REPO_COMMIT" \
    "$system_lock_hash" "$(sha256 "$bootstrap_marker")" "$(sha256 "$system_marker")" \
    "$ca_hash" "$installed_openrc" "$(sha256 "$openrc_vdb/CONTENTS")" "$driver_hash" \
    > "$STATE/native-gentoo-system-ready.receipt.tmp"
printf 'multilib glibc marker sha256=%s\nmultilib @system marker sha256=%s\n' \
    "$(sha256 "$multilib_glibc_marker")" "$(sha256 "$multilib_system_marker")" \
    >> "$STATE/native-gentoo-system-ready.receipt.tmp"
sync "$STATE/native-gentoo-system-ready.receipt.tmp"
mv "$STATE/native-gentoo-system-ready.receipt.tmp" "$STATE/native-gentoo-system-ready.receipt"
sync "$STATE"

echo 'The pinned Gentoo bootstrap and source-built @system stage passed.'
echo 'This is a Gentoo package base, not the encrypted target install or desktop.'
echo "Completion marker: $STATE/native-gentoo-system-ready.receipt"
