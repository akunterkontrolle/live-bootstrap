#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'Portage host requires the verified modern-kernel handoff'
portage_host_action() {
    gentoo_prepare_source sources/portage.lock
    [ -x "$HOST_PREFIX/bin/python3.12" ] || gentoo_die 'source-built Python 3.12 is unavailable'
    grep -Fq 'PYTHON_COMPAT=( python3_{12..15} )' \
        "$GENTOO_REPO_TREE/sys-apps/portage/portage-3.0.82.2.ebuild" ||
        gentoo_die 'Portage ebuild compatibility changed from the pinned review'
    portage_ensure_guest_account
    portage_render_config
    portage_manifest_guard_check
    portage_repository_guard_check
    env PYTHONPATH="$PORTAGE_SOURCE/lib" PORTAGE_CONFIGROOT="$PORTAGE_CONFIG" \
        PORTAGE_TMPDIR="$WORK_ROOT/tmp/portage" DISTDIR="$WORK_ROOT/distfiles" \
        FEATURES='-test -getbinpkg -buildpkg -assume-digests -digest strict' \
        EMERGE_DEFAULT_OPTS="--jobs=$GENTOO_PORTAGE_JOBS --load-average=$GENTOO_PORTAGE_LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n" \
        MAKEOPTS="-j$GENTOO_PORTAGE_MAKE_JOBS" PORTAGE_BINHOST= \
        "$HOST_PREFIX/bin/python3.12" "$PORTAGE_SOURCE/bin/emerge" --version 2>&1 |
        tee "$WORK_ROOT/state/portage-version.txt"
    portage_source_version_verify "$PORTAGE_SOURCE" "$WORK_ROOT/state/portage-version.txt" "$HOST_PREFIX/bin/python3.12" ||
        gentoo_die 'source Portage version self-check failed'
    PYTHONPATH="$PORTAGE_SOURCE/lib" "$HOST_PREFIX/bin/python3.12" \
        "$SCRIPT_DIR/probes/portage-integrity.py"
    portage_manifest_guard_check
    portage_repository_guard_check
}
portage_host_verify() {
    grep -Fxq 'FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"' \
        "$PORTAGE_CONFIG/etc/portage/make.conf" || return 1
    grep -Fxq "EMERGE_DEFAULT_OPTS=\"--jobs=$GENTOO_PORTAGE_JOBS --load-average=$GENTOO_PORTAGE_LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n\"" \
        "$PORTAGE_CONFIG/etc/portage/make.conf" || return 1
    grep -Fxq "MAKEOPTS=\"-j$GENTOO_PORTAGE_MAKE_JOBS\"" \
        "$PORTAGE_CONFIG/etc/portage/make.conf" || return 1
    [ "$(readlink "$PORTAGE_CONFIG/etc/portage/make.profile")" = \
        "$GENTOO_REPO_TREE/profiles/default/linux/amd64/23.0/desktop" ] || return 1
    gentoo_prepare_source sources/gentoo-repository.lock || return 1
    gentoo_prepare_source sources/portage.lock || return 1
    portage_verify_guest_account || return 1
    [ -s "$WORK_ROOT/state/portage-version.txt" ] || return 1
    portage_source_version_verify "$PORTAGE_SOURCE" "$WORK_ROOT/state/portage-version.txt" "$HOST_PREFIX/bin/python3.12" || return 1
    portage_manifest_guard_check || return 1
    portage_repository_guard_check || return 1
    "$HOST_PREFIX/bin/python3.12" -c 'import bz2, ssl, zlib' || return 1
    env PYTHONPATH="$PORTAGE_SOURCE/lib" PORTAGE_CONFIGROOT="$PORTAGE_CONFIG" \
        PORTAGE_TMPDIR="$WORK_ROOT/tmp/portage" DISTDIR="$WORK_ROOT/distfiles" \
        FEATURES='-test -getbinpkg -buildpkg -assume-digests -digest strict' \
        EMERGE_DEFAULT_OPTS="--jobs=$GENTOO_PORTAGE_JOBS --load-average=$GENTOO_PORTAGE_LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n" \
        MAKEOPTS="-j$GENTOO_PORTAGE_MAKE_JOBS" PORTAGE_BINHOST= \
        "$HOST_PREFIX/bin/python3.12" "$PORTAGE_SOURCE/bin/emerge" --version 2>&1 |
        tee "$WORK_ROOT/state/portage-version-check.txt" || return 1
    portage_source_version_verify "$PORTAGE_SOURCE" "$WORK_ROOT/state/portage-version-check.txt" "$HOST_PREFIX/bin/python3.12" || return 1
    PYTHONPATH="$PORTAGE_SOURCE/lib" "$HOST_PREFIX/bin/python3.12" \
        "$SCRIPT_DIR/probes/portage-integrity.py" || return 1
    portage_manifest_guard_check || return 1
    portage_repository_guard_check
}
# Portage settings affect runtime commands but do not fingerprint built sources.
portage_render_config
fingerprint=$(gentoo_fingerprint stages/52-portage-host.sh lib/portage-host.sh lib/manifests.sh lib/sources.sh \
    probes/portage-integrity.py probes/portage-version.py sources/portage.lock \
    "$WORK_ROOT/state/50-gentoo-sources.done" "$WORK_ROOT/state/51-host-python.done")
gentoo_run_stage 52-portage-host "$fingerprint" portage_host_action portage_host_verify
printf 'Source-built Python and pinned source-only Portage host are ready.\n'
printf 'Repository Manifests are guarded; the next batch adds native build tools.\n'
