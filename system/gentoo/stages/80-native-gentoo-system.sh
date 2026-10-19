#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'Gentoo system requires the verified modern-kernel handoff'
. "$SCRIPT_DIR/lib/native-gentoo-system.sh"
. "$SCRIPT_DIR/lib/manifests.sh"

native_gentoo_system_action() (
    set -Eeuo pipefail
    STATE="$WORK_ROOT/state"
    DISTFILES="$WORK_ROOT/distfiles"
    NATIVE_ROOT="$WORK_ROOT/native-root-x86_64"
    GENTOO_TREE=$GENTOO_REPO_TREE
    REPO_COMMIT=$GENTOO_REPO_COMMIT
    BOOTSTRAP_GLIBC_VERSION=2.44-r3
    BOOTSTRAP_GLIBC_KEYWORD_ENTRY="=sys-libs/glibc-$BOOTSTRAP_GLIBC_VERSION ~amd64"
    workspace_uuid=$GENTOO_DISCOVERED_WORKSPACE_UUID
    JOBS=$GENTOO_PORTAGE_MAKE_JOBS
    LOAD_AVERAGE=$GENTOO_PORTAGE_LOAD_AVERAGE
    PACKAGE_JOBS=$GENTOO_PORTAGE_JOBS
    RUNTIME_LOCK="$SCRIPT_DIR/sources/perl.lock"
    BASE_SOURCES_LOCK="$SCRIPT_DIR/sources/glibc.lock"
    SYSTEM_LOCK="$SCRIPT_DIR/sources/wget.lock"
    PORTAGE_SOURCE="$PORTAGE_SOURCE"
    # Hash stable relative names and file contents. sha256sum FILE includes
    # the absolute path in its output, which made markers fail after the same
    # verified bundle was copied to a new content-addressed snapshot path.
    driver_hash=$( {
        for file in "$SCRIPT_DIR/lib/native-gentoo-system.sh" "$SCRIPT_DIR/build-bootstrap-scanelf.sh" \
            "$SCRIPT_DIR/audit-resolved-sources.py" "$SCRIPT_DIR/probes/reinstall-plan.py" \
            "$SCRIPT_DIR/probes/portage-mount-shim.py" "$SCRIPT_DIR/stages/system/"*.sh; do
            printf '%s\n' "${file#"$SCRIPT_DIR"/}"
            sha256sum < "$file"
        done
    } | sha256sum | cut -d' ' -f1 )
    marker_driver_recorded() {
        local recorded compatibility="$STATE/native-gentoo-compatible-drivers-$driver_hash.receipt"
        recorded=$(awk -F= '/^(driver|script) sha256=/ {value=$2; count++}
            END {if(count!=1 || length(value)!=64 || value~/[^0-9a-f]/) exit 1; print value}' "$1") || return 1
        [ "$recorded" = "$driver_hash" ] && return 0
        # A reviewed repair proves recipe/source equivalence against verified
        # archived inputs. Keep completed receipts intact: other receipts hash
        # their contents, so rewriting driver fields would break those links.
        [ -f "$compatibility" ] && [ ! -L "$compatibility" ] &&
            grep -Fxq "target driver sha256=$driver_hash" "$compatibility" &&
            grep -Fxq "compatible driver sha256=$recorded" "$compatibility"
    }
    native_gentoo_system_mounts

    fetch_wget() {
        local record url checksum destination
        record=$(lock_entry wget-1.25.0.tar.lz "$SYSTEM_LOCK") || gentoo_die 'invalid Wget lock'
        url=${record#*|}; url=${url%%|*}; checksum=${record##*|}
        destination="$DISTFILES/wget-1.25.0.tar.lz"
        gentoo_fetch_source "$url" "$checksum" "$destination"
    }
    run_emerge() {
        local status=0
        portage_manifest_guard_check && portage_repository_guard_check || gentoo_die 'pinned Gentoo source guard failed before emerge'
        chroot_env /usr/bin/python3.12 /usr/lib/portage/bin/emerge \
            --usepkg=n --getbinpkg=n --buildpkg=n "$@" || status=$?
        portage_manifest_guard_check && portage_repository_guard_check || gentoo_die 'pinned Gentoo source guard failed after emerge'
        return "$status"
    }
    system_lock_hash=$(sha256 "$SYSTEM_LOCK")
    runtime_lock_hash=$(sha256 "$RUNTIME_LOCK")
    wget_record=$(lock_entry wget-1.25.0.tar.lz "$SYSTEM_LOCK")
    wget_archive_hash=${wget_record##*sha512=}
    repo_archive=$(awk -F '|' 'NF==3 {print $1}' "$SCRIPT_DIR/sources/gentoo-repository.lock")
    repo_record=$(lock_entry "$repo_archive" "$SCRIPT_DIR/sources/gentoo-repository.lock")
    repo_archive_hash=${repo_record##*sha512=}
    portage_record=$(lock_entry portage-3.0.82.2.tar.bz2 "$SCRIPT_DIR/sources/portage.lock")
    portage_archive_hash=${portage_record##*sha512=}
    ensure_gentoo_mpfr_gawk() {
        local marker="$STATE/native-gentoo-gawk-mpfr.done" archive=gawk-5.4.1.tar.xz
        local record source_hash source build mpfr_hash binary_hash
        if [ -s "$marker" ]; then
            grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$marker" &&
                grep -Fxq 'recipe=source-built-native-gawk-5.4.1-mpfr-v1' "$marker" &&
                marker_driver_recorded "$marker" || gentoo_die 'MPFR Gawk receipt has a different lineage'
            [ "$(sha256 "$NATIVE_ROOT/usr/bin/gawk")" = "$(sed -n 's/^Gawk binary sha256=//p' "$marker")" ] ||
                gentoo_die 'MPFR Gawk binary changed after its receipt'
            chroot_env /usr/bin/gawk -M 'BEGIN { x = 2 ^ 100; if (x + 1 == x) exit 1 }' ||
                gentoo_die 'MPFR Gawk runtime probe failed'
            return
        fi
        record=$(lock_entry "$archive" "$SCRIPT_DIR/sources/native-gawk-5.4.1.lock")
        source_hash=${record##*sha512=}
        verify_locked "$archive" "$SCRIPT_DIR/sources/native-gawk-5.4.1.lock"
        grep -Fxq "source sha512=$source_hash" "$STATE/63-gawk-5.4.1.done" &&
            grep -Fxq "source sha512=$source_hash" "$STATE/63-gawk-source-patch.receipt" ||
            gentoo_die 'Patched source-built Gawk receipt is missing'
        source="$WORK_ROOT/sources/gawk-5.4.1"
        [ -x "$source/configure" ] && ! grep -q extras "$source/Makefile.in" ||
            gentoo_die 'Patched Gawk source tree is missing or unexpected'
        run_emerge --oneshot dev-libs/mpfr
        [ -s "$NATIVE_ROOT/usr/include/mpfr.h" ] && [ -e "$NATIVE_ROOT/usr/lib64/libmpfr.so" ] ||
            gentoo_die 'Gentoo MPFR prerequisite did not install its headers and library'
        chroot_env /usr/bin/env CPPFLAGS=-I/usr/include LDFLAGS=-L/usr/lib64 \
            GENTOO_BOOTSTRAP_JOBS="$GENTOO_BUILD_SOURCE_JOBS" /bin/bash -c '
                set -Eeuo pipefail
                source=/host-build/sources/gawk-5.4.1
                build=/host-build/build/gawk-5.4.1-gentoo-mpfr-v1
                mkdir -p "$build"; cd "$build"
                if [ ! -f Makefile ]; then "$source/configure" --prefix=/usr --libdir=/usr/lib64; fi
                make -j"$GENTOO_BOOTSTRAP_JOBS"
                make -j"$GENTOO_BOOTSTRAP_JOBS" install
            '
        chroot_env /usr/bin/gawk -M 'BEGIN { x = 2 ^ 100; if (x + 1 == x) exit 1; print "MPFR bignum passed" }' |
            grep -Fxq 'MPFR bignum passed' || gentoo_die 'MPFR Gawk bignum probe failed'
        binary_hash=$(sha256 "$NATIVE_ROOT/usr/bin/gawk")
        mpfr_hash=$(sha256 "$NATIVE_ROOT/usr/lib64/libmpfr.so.6.2.2")
        printf 'complete %s\nGentoo repository commit=%s\nsource sha512=%s\nsource adjustment=state/63-gawk-source-patch.receipt\nGMP/MPFR provider=source-built pinned Gentoo @system dependency\nMPFR library sha256=%s\nGawk configure CPPFLAGS=-I/usr/include LDFLAGS=-L/usr/lib64\nGawk upstream make check=not-run under global no-test policy\nGawk MPFR bignum runtime=passed\nGawk binary sha256=%s\nrecipe=source-built-native-gawk-5.4.1-mpfr-v1\ndriver sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$source_hash" "$mpfr_hash" "$binary_hash" "$driver_hash" > "$marker.tmp"
        sync "$marker.tmp"; mv "$marker.tmp" "$marker"; sync "$STATE"
    }
    . "$SCRIPT_DIR/stages/system/10-prepare.sh"
    . "$SCRIPT_DIR/stages/system/20-runtime-and-downloader.sh"
    . "$SCRIPT_DIR/stages/system/30-bootstrap-prerequisites.sh"
    . "$SCRIPT_DIR/stages/system/40-runtime-providers.sh"
    . "$SCRIPT_DIR/stages/system/50-elf-and-compiler.sh"
    . "$SCRIPT_DIR/stages/system/60-gentoo-bootstrap.sh"
    . "$SCRIPT_DIR/stages/system/70-system.sh"
    . "$SCRIPT_DIR/stages/system/80-verify.sh"
)

native_gentoo_system_verify() {
    local state_dir="$WORK_ROOT/state"
    local NATIVE_ROOT="$WORK_ROOT/native-root-x86_64"
    local receipt="$WORK_ROOT/state/native-gentoo-system-ready.receipt"
    [ -s "$receipt" ] && grep -Fxq "Gentoo repository commit=$GENTOO_REPO_COMMIT" "$receipt" &&
        [ -s "$state_dir/native-gentoo-at-system-source-built.done" ] &&
        [ -s "$state_dir/native-gentoo-multilib-gcc-full.done" ] &&
        [ -s "$state_dir/native-gentoo-multilib-glibc.done" ] &&
        [ ! -e "$NATIVE_ROOT/etc/portage/profile/package.provided" ] &&
        [ "$(readlink "$NATIVE_ROOT/etc/portage/make.profile" 2>/dev/null || true)" = \
            /usr/portage/profiles/default/linux/amd64/23.0/split-usr ] &&
        grep -Fxq 'ABI_X86="64 32"' "$NATIVE_ROOT/etc/portage/make.conf" &&
        grep -Fxq 'FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"' \
            "$NATIVE_ROOT/etc/portage/make.conf" &&
        chroot "$NATIVE_ROOT" /bin/bash --noprofile --norc -c '
            # Gentoo gcc-config uses absolute links within this root.
            for program in /usr/bin/gcc /usr/bin/g++ /usr/bin/emerge /sbin/openrc; do
                [ -x "$program" ] || {
                    printf "Missing native executable: %s\n" "$program" >&2
                    exit 1
                }
            done
        '
}

native_system_fingerprint=$(gentoo_fingerprint \
    stages/80-native-gentoo-system.sh lib/native-gentoo-system.sh \
    stages/system/10-prepare.sh stages/system/20-runtime-and-downloader.sh \
    stages/system/30-bootstrap-prerequisites.sh stages/system/40-runtime-providers.sh \
    stages/system/50-elf-and-compiler.sh stages/system/60-gentoo-bootstrap.sh \
    stages/system/70-system.sh stages/system/80-verify.sh retire-bootstrap-nscd.py \
    build-bootstrap-scanelf.sh audit-resolved-sources.py probes/reinstall-plan.py \
    probes/portage-mount-shim.py \
    sources/native-xz-5.8.3.lock sources/native-tar-1.35.lock sources/native-gawk-5.4.1.lock \
    sources/wget.lock sources/glibc.lock sources/perl.lock config.inputs.sh \
    "$WORK_ROOT/state/71-native-portage.done" "$WORK_ROOT/state/70-native-runtimes.done" \
    "$WORK_ROOT/state/50-gentoo-sources.done" "$WORK_ROOT/state/63-native-tool-floor.done") ||
    gentoo_die 'Gentoo system stage inputs or prerequisite receipts are missing'
gentoo_run_stage 80-native-gentoo-system "$native_system_fingerprint" \
    native_gentoo_system_action native_gentoo_system_verify
printf 'Pinned Gentoo bootstrap and multilib @system are complete; resume with bash %s/gentoo.sh build.\n' "$SCRIPT_DIR"
