#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'native Portage requires the verified modern-kernel handoff'
. "$SCRIPT_DIR/lib/native.sh"
. "$SCRIPT_DIR/lib/portage.sh"

# Stage 70 removes its mounts. Every Portage entry (including verification of
# a completed stage) needs a live proc-fd view inside this separate GNU root.
native_portage_with_devices() (
    set -Eeuo pipefail
    local point
    # EXIT can run after function-local variables have been unwound by errexit.
    # These names live only in this subshell and remain available to its trap.
    gentoo_portage_owned=()
    native_portage_cleanup() {
        gentoo_portage_status=$?
        trap - EXIT
        for ((gentoo_portage_index=${#gentoo_portage_owned[@]}-1; gentoo_portage_index>=0; gentoo_portage_index--)); do
            if ! gentoo_unmount_tree "${gentoo_portage_owned[gentoo_portage_index]}"; then
                printf 'gentoo: failed to unmount native Portage path: %s\n' "${gentoo_portage_owned[gentoo_portage_index]}" >&2
                [ "$gentoo_portage_status" -ne 0 ] || gentoo_portage_status=1
            fi
        done
        exit "$gentoo_portage_status"
    }
    [ -d "$NATIVE_ROOT" ] && [ "$NATIVE_ROOT" != / ] || return 1
    mkdir -p "$NATIVE_ROOT/proc" "$NATIVE_ROOT/sys" "$NATIVE_ROOT/dev" || return 1
    for point in "$NATIVE_ROOT/proc" "$NATIVE_ROOT/sys" "$NATIVE_ROOT/dev"; do
        if mountpoint -q "$point"; then
            printf 'gentoo: unexpected existing native Portage mount: %s\n' "$point" >&2
            return 1
        fi
    done
    trap native_portage_cleanup EXIT
    mount -t proc proc "$NATIVE_ROOT/proc" || return 1
    gentoo_portage_owned+=("$NATIVE_ROOT/proc")
    mount --rbind /sys "$NATIVE_ROOT/sys" || return 1
    gentoo_portage_owned+=("$NATIVE_ROOT/sys")
    mount --make-rslave "$NATIVE_ROOT/sys" || return 1
    mount --rbind /dev "$NATIVE_ROOT/dev" || return 1
    gentoo_portage_owned+=("$NATIVE_ROOT/dev")
    mount --make-rslave "$NATIVE_ROOT/dev" || return 1
    # Match pinned Portage's sanity test, rather than checking link spelling.
    chroot "$NATIVE_ROOT" /bin/bash -c \
        'test -c /dev/null && test -r /proc/self/status && [[ $(< <(echo foo) ) == foo ]]' || {
        printf 'gentoo: native Portage proc/device process-substitution probe failed\n' >&2
        return 1
    }
    "$@"
)

native_portage_tree_hash() {
    (
        cd "$1"
        { find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum
          find . -type f -printf 'F %m %p\n'; find . -type d -printf 'D %m %p\n'
          find . -type l -printf 'L %m %p -> %l\n'; } | LC_ALL=C sort | sha256sum | cut -d' ' -f1
    )
}
native_portage_configure() {
    local portage_root="$NATIVE_ROOT/etc/portage" temporary
    mkdir -p "$portage_root/repos.conf" "$portage_root/profile" "$NATIVE_ROOT/var/cache/distfiles" \
        "$NATIVE_ROOT/var/tmp/portage" "$NATIVE_ROOT/var/db/pkg" "$NATIVE_ROOT/run" \
        "$NATIVE_ROOT/root" "$NATIVE_ROOT/usr/portage" "$NATIVE_ROOT/usr/lib/portage"
    [ -x "$NATIVE_ROOT/usr/bin/false" ] || gentoo_die 'native coreutils false is missing'
    if [ -e "$NATIVE_ROOT/bin/false" ] || [ -L "$NATIVE_ROOT/bin/false" ]; then
        [ "$(readlink "$NATIVE_ROOT/bin/false" 2>/dev/null || true)" = ../usr/bin/false ] ||
            gentoo_die 'unexpected native /bin/false path'
    else
        ln -s ../usr/bin/false "$NATIVE_ROOT/bin/false"
    fi
    if [ ! -s "$NATIVE_ROOT/etc/passwd" ]; then
        printf 'root:x:0:0:root:/root:/bin/bash\n' > "$NATIVE_ROOT/etc/passwd"
    elif ! grep -q '^root:' "$NATIVE_ROOT/etc/passwd"; then
        { printf 'root:x:0:0:root:/root:/bin/bash\n'; cat "$NATIVE_ROOT/etc/passwd"; } > "$NATIVE_ROOT/etc/passwd.tmp"
        mv "$NATIVE_ROOT/etc/passwd.tmp" "$NATIVE_ROOT/etc/passwd"
    fi
    if [ ! -s "$NATIVE_ROOT/etc/group" ]; then
        printf 'root:x:0:\n' > "$NATIVE_ROOT/etc/group"
    elif ! grep -q '^root:' "$NATIVE_ROOT/etc/group"; then
        { printf 'root:x:0:\n'; cat "$NATIVE_ROOT/etc/group"; } > "$NATIVE_ROOT/etc/group.tmp"
        mv "$NATIVE_ROOT/etc/group.tmp" "$NATIVE_ROOT/etc/group"
    fi
    [ -s "$NATIVE_ROOT/etc/shadow" ] || printf 'root:!:19000:0:99999:7:::\n' > "$NATIVE_ROOT/etc/shadow"
    [ -s "$NATIVE_ROOT/etc/gshadow" ] || printf 'root:!::\n' > "$NATIVE_ROOT/etc/gshadow"
    chmod 0644 "$NATIVE_ROOT/etc/passwd" "$NATIVE_ROOT/etc/group"
    chmod 0600 "$NATIVE_ROOT/etc/shadow"
    chmod 0640 "$NATIVE_ROOT/etc/gshadow"
    portage_ensure_guest_account "$NATIVE_ROOT/etc/group" "$NATIVE_ROOT/etc/passwd" "$NATIVE_ROOT/bin/false"
    temporary=$(mktemp "$portage_root/repos.conf/.gentoo.XXXXXX")
    cat > "$temporary" <<'EOF_REPOS'
[DEFAULT]
main-repo = gentoo

[gentoo]
location = /usr/portage
auto-sync = no
EOF_REPOS
    chmod 0644 "$temporary"; mv "$temporary" "$portage_root/repos.conf/gentoo.conf"
    temporary=$(mktemp "$portage_root/.make-conf.XXXXXX")
    cat > "$temporary" <<EOF_MAKE
CHOST="x86_64-pc-linux-gnu"
ARCH="amd64"
ACCEPT_KEYWORDS="amd64"
ACCEPT_LICENSE="*"
CFLAGS="-O2 -pipe -march=x86-64 -mtune=generic -fstack-protector-strong -D_FORTIFY_SOURCE=2"
CXXFLAGS="\${CFLAGS}"
DISTDIR="/var/cache/distfiles"
FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"
EMERGE_DEFAULT_OPTS="--jobs=$GENTOO_PORTAGE_JOBS --load-average=$GENTOO_PORTAGE_LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n"
MAKEOPTS="-j$GENTOO_PORTAGE_MAKE_JOBS"
PORTAGE_BINHOST=""
PORTAGE_BZIP2_COMMAND="/usr/bin/bzip2"
GENTOO_MIRRORS="https://distfiles.gentoo.org"
EOF_MAKE
    chmod 0644 "$temporary"; mv "$temporary" "$portage_root/make.conf"
    ln -sfn /usr/portage/profiles/default/linux/amd64/23.0/split-usr/no-multilib "$portage_root/make.profile"
}
native_portage_action() {
    local repo_hash portage_hash repository="$NATIVE_ROOT/usr/portage" portage_dir="$NATIVE_ROOT/usr/lib/portage"
    portage_manifest_guard_check
    portage_repository_guard_check
    [ -f "$PORTAGE_SOURCE/bin/emerge" ] && [ -s "$GENTOO_REPO_TREE/profiles/repo_name" ] ||
        gentoo_die 'pinned Portage or Gentoo source tree is unavailable'
    [ "$(cat "$GENTOO_REPO_TREE/profiles/repo_name")" = gentoo ] || gentoo_die 'repository identity mismatch'
    repo_hash=$(native_portage_tree_hash "$GENTOO_REPO_TREE")
    portage_hash=$(native_portage_tree_hash "$PORTAGE_SOURCE")
    cp -a "$GENTOO_REPO_TREE/." "$repository/"
    cp -a "$PORTAGE_SOURCE/." "$portage_dir/"
    [ "$(native_portage_tree_hash "$repository")" = "$repo_hash" ] || gentoo_die 'native Gentoo repository copy differs from pinned source'
    [ "$(native_portage_tree_hash "$portage_dir")" = "$portage_hash" ] || gentoo_die 'native Portage copy differs from pinned source'
    mkdir -p "$NATIVE_ROOT/usr/bin"
    ln -sfn ../lib/portage/bin/emerge "$NATIVE_ROOT/usr/bin/emerge"
    ln -sfn ../lib/portage/bin/portageq "$NATIVE_ROOT/usr/bin/portageq"
    ln -sfn ../lib/portage/bin/ebuild "$NATIVE_ROOT/usr/bin/ebuild"
    ln -sfn ../lib/portage/bin/env-update "$NATIVE_ROOT/usr/bin/env-update"
    [ "$(readlink "$NATIVE_ROOT/etc/portage/make.profile")" = \
        /usr/portage/profiles/default/linux/amd64/23.0/split-usr/no-multilib ] || gentoo_die 'seed profile link mismatch'
    chroot "$NATIVE_ROOT" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin LANG=C LC_ALL=C \
        PYTHONPATH=/usr/lib/portage/lib /usr/bin/python3.12 /usr/lib/portage/bin/emerge --version |
        tee "$WORK_ROOT/state/71-native-emerge-version.txt"
    portage_source_version_verify "$PORTAGE_SOURCE" "$WORK_ROOT/state/71-native-emerge-version.txt" "$HOST_PREFIX/bin/python3.12" || gentoo_die 'native Portage version check failed'
    chroot "$NATIVE_ROOT" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin LANG=C LC_ALL=C \
        PYTHONPATH=/usr/lib/portage/lib /usr/bin/python3.12 -c \
        'import locale; from portage.process import find_binary; assert locale.setlocale(locale.LC_ALL, "") == "C"; assert find_binary("bzip2") == "/usr/bin/bzip2"; print("Portage locale and bzip2 command passed")'
    portage_manifest_guard_check
    portage_repository_guard_check
    printf 'repository tree sha256=%s\nportage tree sha256=%s\n' "$repo_hash" "$portage_hash" > \
        "$WORK_ROOT/state/71-native-portage-trees.receipt.tmp"
    sync "$WORK_ROOT/state/71-native-portage-trees.receipt.tmp"
    mv "$WORK_ROOT/state/71-native-portage-trees.receipt.tmp" "$WORK_ROOT/state/71-native-portage-trees.receipt"
}
native_portage_verify() {
    local expected_repo expected_portage
    [ -x "$NATIVE_ROOT/usr/bin/emerge" ] && [ -x "$NATIVE_ROOT/usr/bin/portageq" ] || return 1
    [ "$(readlink "$NATIVE_ROOT/usr/bin/emerge")" = ../lib/portage/bin/emerge ] || return 1
    [ "$(readlink "$NATIVE_ROOT/usr/bin/portageq")" = ../lib/portage/bin/portageq ] || return 1
    [ "$(readlink "$NATIVE_ROOT/etc/portage/make.profile")" = \
        /usr/portage/profiles/default/linux/amd64/23.0/split-usr/no-multilib ] || return 1
    expected_repo=$(sed -n 's/^repository tree sha256=//p' "$WORK_ROOT/state/71-native-portage-trees.receipt" 2>/dev/null)
    expected_portage=$(sed -n 's/^portage tree sha256=//p' "$WORK_ROOT/state/71-native-portage-trees.receipt" 2>/dev/null)
    [ -n "$expected_repo" ] && [ -n "$expected_portage" ] || return 1
    [ "$(native_portage_tree_hash "$NATIVE_ROOT/usr/portage")" = "$expected_repo" ] || return 1
    [ "$(native_portage_tree_hash "$NATIVE_ROOT/usr/lib/portage")" = "$expected_portage" ] || return 1
    portage_source_version_verify "$PORTAGE_SOURCE" "$WORK_ROOT/state/71-native-emerge-version.txt" "$HOST_PREFIX/bin/python3.12" || return 1
    grep -Fxq "MAKEOPTS=\"-j$GENTOO_PORTAGE_MAKE_JOBS\"" "$NATIVE_ROOT/etc/portage/make.conf" || return 1
    grep -Fxq "EMERGE_DEFAULT_OPTS=\"--jobs=$GENTOO_PORTAGE_JOBS --load-average=$GENTOO_PORTAGE_LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n\"" \
        "$NATIVE_ROOT/etc/portage/make.conf" || return 1
    grep -Fxq 'FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"' "$NATIVE_ROOT/etc/portage/make.conf" || return 1
    grep -Fxq 'PORTAGE_BZIP2_COMMAND="/usr/bin/bzip2"' "$NATIVE_ROOT/etc/portage/make.conf" || return 1
    portage_verify_guest_account "$NATIVE_ROOT/etc/group" "$NATIVE_ROOT/etc/passwd" "$NATIVE_ROOT/bin/false" || return 1
    chroot "$NATIVE_ROOT" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin LANG=C LC_ALL=C \
        PYTHONPATH=/usr/lib/portage/lib /usr/bin/python3.12 /usr/lib/portage/bin/emerge --version 2>&1 |
        tee "$WORK_ROOT/state/71-native-emerge-version-check.txt" || return 1
    portage_source_version_verify "$PORTAGE_SOURCE" "$WORK_ROOT/state/71-native-emerge-version-check.txt" "$HOST_PREFIX/bin/python3.12" || return 1
    chroot "$NATIVE_ROOT" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin LANG=C LC_ALL=C \
        PYTHONPATH=/usr/lib/portage/lib /usr/bin/python3.12 -c \
        'import locale; from portage.process import find_binary; assert locale.setlocale(locale.LC_ALL, "") == "C"; assert find_binary("bzip2") == "/usr/bin/bzip2"' || return 1
    portage_manifest_guard_check || return 1
    portage_repository_guard_check || return 1
}
fingerprint=$(gentoo_fingerprint stages/71-native-portage.sh lib/native.sh lib/portage.sh \
    lib/portage-host.sh lib/manifests.sh lib/sources.sh \
    probes/portage-version.py sources/portage.lock sources/gentoo-repository.lock \
    "$WORK_ROOT/state/70-native-runtimes.done" "$WORK_ROOT/state/50-gentoo-sources.done")
# Concurrency is operational policy, not a source/build input. Re-render it on
# every invocation so changed TOML settings apply without invalidating builds.
native_portage_configure
native_portage_mounted_action() { native_portage_with_devices native_portage_action; }
native_portage_mounted_verify() { native_portage_with_devices native_portage_verify; }
gentoo_run_stage 71-native-portage "$fingerprint" native_portage_mounted_action native_portage_mounted_verify
printf 'Pinned Gentoo repository and Portage are ready in the native root; no ebuild has run.\n'
