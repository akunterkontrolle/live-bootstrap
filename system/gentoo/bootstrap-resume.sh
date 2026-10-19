#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Installed on the bootstrap root before shutdown; needs no workspace to start.
set -Eeuo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run gentoo-resume as root.' >&2; exit 1; }
HERE=$(CDPATH= cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
(cd "$HERE" && sha256sum -c SHA256SUMS) || { echo 'Bootstrap resume inputs changed.' >&2; exit 1; }
. "$HERE/lib/common.sh"
. "$HERE/lib/mounts.sh"
. "$HERE/config.sh"
IFS= read -r digest < "$HERE/bundle-digest"
IFS= read -r GENTOO_WORKSPACE_UUID < "$HERE/workspace-uuid"
[[ "$digest" =~ ^[0-9a-f]{64}$ && "$GENTOO_WORKSPACE_UUID" =~ ^[0-9a-fA-F-]{36}$ ]] || gentoo_die 'invalid recorded workspace/input identity'
export GENTOO_WORKSPACE_UUID
gentoo_prepare_virtual_filesystems
. "$HERE/lib/devices.sh"
gentoo_mount_workspace
snapshot="/build/gentoo-bootstrap/inputs/$digest"
[ "$(sha256sum < "$snapshot/SHA256SUMS" | cut -d' ' -f1)" = "$digest" ] || gentoo_die 'durable source identity changed'
gentoo_verify_bundle "$snapshot"
resume="/build/gentoo-bootstrap/handoff/$digest/resume.sh"
cmp "$snapshot/post-reboot.sh" "$resume" || gentoo_die 'durable resume script changed'
exec bash "$resume" "$@"
