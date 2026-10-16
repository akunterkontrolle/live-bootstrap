#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'Gentoo sources require the verified modern-kernel handoff'
portage_sources_action() {
    gentoo_prepare_source sources/gentoo-repository.lock
    [ "$(cat "$GENTOO_REPO_TREE/profiles/repo_name")" = gentoo ] || gentoo_die 'repository identity mismatch'
    grep -Fq 'PYTHON_COMPAT=( python3_{12..15} )' \
        "$GENTOO_REPO_TREE/sys-apps/portage/portage-3.0.82.2.ebuild" ||
        gentoo_die 'pinned Portage ebuild lacks the reviewed Python 3.12 compatibility'
    portage_manifest_guard_init
    portage_repository_guard_init
}
portage_sources_verify() {
    gentoo_prepare_source sources/gentoo-repository.lock || return 1
    [ -s "$GENTOO_REPO_TREE/profiles/repo_name" ] || return 1
    [ "$(cat "$GENTOO_REPO_TREE/profiles/repo_name")" = gentoo ] || return 1
    grep -Fq 'PYTHON_COMPAT=( python3_{12..15} )' \
        "$GENTOO_REPO_TREE/sys-apps/portage/portage-3.0.82.2.ebuild" || return 1
    portage_manifest_guard_check || return 1
    portage_repository_guard_check
}
fingerprint=$(gentoo_fingerprint stages/50-gentoo-sources.sh lib/manifests.sh lib/sources.sh \
    sources/gentoo-repository.lock "$WORK_ROOT/state/43-runtime-acceptance.done")
gentoo_run_stage 50-gentoo-sources "$fingerprint" portage_sources_action portage_sources_verify
