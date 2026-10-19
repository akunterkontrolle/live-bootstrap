#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Publish an independent, versioned root entry before the shutdown milestone.
# The root argument permits tests against a private filesystem fixture.
gentoo_publish_bootstrap_resume() (
    set -Eeuo pipefail
    umask 022
    local root=${1%/} directory temporary name entry
    directory="$root/etc/gentoo-bootstrap-resume/$input_digest"
    install -d -m 0755 "$root/etc/gentoo-bootstrap-resume" "$root/usr/local/sbin"
    temporary=$(mktemp -d "$root/etc/gentoo-bootstrap-resume/.pending.XXXXXX")
    install -d -m 0755 "$temporary/lib"
    for name in bootstrap-resume.sh config.sh lib/common.sh lib/devices.sh lib/mounts.sh lib/hardware-discovery.sh; do
        install -m 0644 "$SCRIPT_DIR/$name" "$temporary/$name"
    done
    chmod 0755 "$temporary/bootstrap-resume.sh" "$temporary"
    printf '%s\n' "$input_digest" > "$temporary/bundle-digest"
    printf '%s\n' "$GENTOO_DISCOVERED_WORKSPACE_UUID" > "$temporary/workspace-uuid"
    (cd "$temporary" && sha256sum bootstrap-resume.sh config.sh bundle-digest workspace-uuid lib/*.sh > SHA256SUMS)
    sync
    if [ -e "$directory" ] || [ -L "$directory" ]; then
        [ -d "$directory" ] && [ ! -L "$directory" ] &&
            cmp "$temporary/SHA256SUMS" "$directory/SHA256SUMS" &&
            (cd "$directory" && sha256sum -c SHA256SUMS) || gentoo_die 'existing bootstrap resume entry differs'
        rm -r "$temporary"
    else
        mv "$temporary" "$directory"
    fi
    sync
    entry="$root/usr/local/sbin/gentoo-resume"
    [ ! -e "$entry" ] || [ -L "$entry" ] || gentoo_die 'refusing to replace an existing gentoo-resume command'
    # Absolute link is resolved on the bootstrap root, including after reboot.
    ln -s "/etc/gentoo-bootstrap-resume/$input_digest/bootstrap-resume.sh" "$entry.tmp.$$"
    mv -Tf "$entry.tmp.$$" "$entry"
    sync
)
