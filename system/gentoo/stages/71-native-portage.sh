#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'native Portage requires the verified modern-kernel handoff'
. "$SCRIPT_DIR/lib/native.sh"
. "$SCRIPT_DIR/lib/portage.sh"

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
    grep -Fq 'Portage 3.0.82.2' "$WORK_ROOT/state/71-native-emerge-version.txt" || gentoo_die 'native Portage version check failed'
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
    grep -Fq 'Portage 3.0.82.2' "$WORK_ROOT/state/71-native-emerge-version.txt" || return 1
    grep -Fxq "MAKEOPTS=\"-j$GENTOO_PORTAGE_MAKE_JOBS\"" "$NATIVE_ROOT/etc/portage/make.conf" || return 1
    grep -Fxq "EMERGE_DEFAULT_OPTS=\"--jobs=$GENTOO_PORTAGE_JOBS --load-average=$GENTOO_PORTAGE_LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n\"" \
        "$NATIVE_ROOT/etc/portage/make.conf" || return 1
    grep -Fxq 'FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"' "$NATIVE_ROOT/etc/portage/make.conf" || return 1
    grep -Fxq 'PORTAGE_BZIP2_COMMAND="/usr/bin/bzip2"' "$NATIVE_ROOT/etc/portage/make.conf" || return 1
    portage_verify_guest_account "$NATIVE_ROOT/etc/group" "$NATIVE_ROOT/etc/passwd" "$NATIVE_ROOT/bin/false" || return 1
    chroot "$NATIVE_ROOT" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin LANG=C LC_ALL=C \
        PYTHONPATH=/usr/lib/portage/lib /usr/bin/python3.12 /usr/lib/portage/bin/emerge --version 2>&1 |
        grep -F 'Portage 3.0.82.2' >/dev/null || return 1
    chroot "$NATIVE_ROOT" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin LANG=C LC_ALL=C \
        PYTHONPATH=/usr/lib/portage/lib /usr/bin/python3.12 -c \
        'import locale; from portage.process import find_binary; assert locale.setlocale(locale.LC_ALL, "") == "C"; assert find_binary("bzip2") == "/usr/bin/bzip2"' || return 1
    portage_manifest_guard_check || return 1
    portage_repository_guard_check || return 1
}
fingerprint=$(gentoo_fingerprint stages/71-native-portage.sh lib/native.sh lib/portage.sh \
    lib/portage-host.sh lib/manifests.sh lib/sources.sh \
    sources/portage.lock sources/gentoo-repository.lock \
    "$WORK_ROOT/state/70-native-runtimes.done" "$WORK_ROOT/state/50-gentoo-sources.done")
# Concurrency is operational policy, not a source/build input. Re-render it on
# every invocation so changed TOML settings apply without invalidating builds.
native_portage_configure
gentoo_run_stage 71-native-portage "$fingerprint" native_portage_action native_portage_verify
printf 'Pinned Gentoo repository and Portage are ready in the native root; no ebuild has run.\n'
