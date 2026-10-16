#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install the encrypted-root runtime and recovery tools into the native root.
[ "${GENTOO_MODERN_KERNEL_READY:-0}" = 1 ] || gentoo_die 'base runtime requires the modern-kernel handoff'
. "$SCRIPT_DIR/lib/native-gentoo-system.sh"
. "$SCRIPT_DIR/lib/manifests.sh"

BASE_PACKAGES_INPUT=${GENTOO_PACKAGES_BASE:-policy/defaults/base-packages.list}
BASE_USE_INPUT=${GENTOO_PACKAGES_BASE_USE:-policy/defaults/base-package.use}
BASE_LICENSE_INPUT=${GENTOO_PACKAGES_BASE_LICENSE:-policy/defaults/base-package.license}
BASE_KEYWORDS_INPUT=${GENTOO_PACKAGES_BASE_KEYWORDS:-policy/defaults/base-package.accept_keywords}
FIRMWARE_INPUT=${GENTOO_PACKAGES_FIRMWARE:-policy/defaults/firmware-linux-firmware}

base_runtime_action() (
    set -Eeuo pipefail
    STATE="$WORK_ROOT/state"
    NATIVE_ROOT="$WORK_ROOT/native-root-x86_64"
    GENTOO_TREE=$GENTOO_REPO_TREE
    REPO_COMMIT=$GENTOO_REPO_COMMIT
    JOBS=$GENTOO_PORTAGE_MAKE_JOBS
    LOAD_AVERAGE=$GENTOO_PORTAGE_LOAD_AVERAGE
    PACKAGE_JOBS=$GENTOO_PORTAGE_JOBS
    native_gentoo_system_mounts
    [ -s "$STATE/80-native-gentoo-system.done" ] && [ -s "$STATE/native-gentoo-system-ready.receipt" ] ||
        gentoo_die 'native Gentoo @system prerequisite receipt is missing'

    for file in "$BASE_PACKAGES_INPUT" "$BASE_USE_INPUT" "$BASE_LICENSE_INPUT" "$BASE_KEYWORDS_INPUT" "$FIRMWARE_INPUT" \
        policy/90-encrypted-base-dracut.conf; do
        [ -f "$SCRIPT_DIR/$file" ] && [ ! -L "$SCRIPT_DIR/$file" ] || gentoo_die "missing runtime policy input: $file"
    done
    mapfile -t atoms < <(sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' "$SCRIPT_DIR/$BASE_PACKAGES_INPUT")
    [ "${#atoms[@]}" -gt 0 ] || gentoo_die 'runtime package selection is empty'
    for mandatory in app-admin/sudo app-admin/sysklogd app-crypt/gnupg app-misc/mc net-misc/dhcpcd \
        net-misc/networkmanager sys-fs/cryptsetup sys-fs/e2fsprogs sys-fs/lvm2 sys-kernel/dracut \
        sys-apps/systemd-utils\[udev\]; do
        printf '%s\n' "${atoms[@]}" | grep -Fxq "$mandatory" || gentoo_die "required runtime atom absent: $mandatory"
    done
    firmware_atoms=()
    for atom in "${atoms[@]}"; do
        [[ "$atom" =~ ^=?[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+(-[0-9][A-Za-z0-9._+-]*)?(\[[A-Za-z0-9_,+-]+\])?$ ]] ||
            gentoo_die "invalid package atom in runtime policy: $atom"
        case "$atom" in =sys-kernel/linux-firmware-*) firmware_atoms+=("$atom") ;; esac
    done
    [ "${#firmware_atoms[@]}" -eq 1 ] || gentoo_die 'runtime policy must select exactly one pinned linux-firmware version'
    firmware_atom=${firmware_atoms[0]#=}
    firmware_version=${firmware_atom#sys-kernel/linux-firmware-}
    [ -s "$WORK_ROOT/artifacts/kernel-6.12.111/config" ] || gentoo_die 'verified kernel config is missing'
    grep -Fxq 'CONFIG_DM_CRYPT=y' "$WORK_ROOT/artifacts/kernel-6.12.111/config" &&
        grep -Fxq 'CONFIG_BLK_DEV_DM=y' "$WORK_ROOT/artifacts/kernel-6.12.111/config" &&
        grep -Fxq 'CONFIG_EXT4_FS=y' "$WORK_ROOT/artifacts/kernel-6.12.111/config" ||
        gentoo_die 'kernel lacks built-in encrypted-root, device-mapper, or ext4 support'

    mkdir -p "$NATIVE_ROOT/etc/portage/package.use" "$NATIVE_ROOT/etc/portage/package.license" \
        "$NATIVE_ROOT/etc/portage/package.accept_keywords" "$NATIVE_ROOT/etc/portage/savedconfig/sys-kernel" \
        "$NATIVE_ROOT/etc/dracut.conf.d" "$NATIVE_ROOT/usr/src/linux"
    install_policy() {
        local source=$1 target=$2
        if [ -e "$target" ] || [ -L "$target" ]; then
            [ -f "$target" ] && [ ! -L "$target" ] && cmp -s "$source" "$target" ||
                gentoo_die "existing policy differs; inspect before replacing: $target"
        else
            install -m 0644 "$source" "$target"
        fi
    }
    install_policy "$SCRIPT_DIR/$BASE_USE_INPUT" "$NATIVE_ROOT/etc/portage/package.use/90-encrypted-base"
    install_policy "$SCRIPT_DIR/$BASE_LICENSE_INPUT" "$NATIVE_ROOT/etc/portage/package.license/90-encrypted-base"
    install_policy "$SCRIPT_DIR/$BASE_KEYWORDS_INPUT" "$NATIVE_ROOT/etc/portage/package.accept_keywords/90-encrypted-base"
    install_policy "$SCRIPT_DIR/$FIRMWARE_INPUT" "$NATIVE_ROOT/etc/portage/savedconfig/sys-kernel/linux-firmware-$firmware_version"
    install_policy "$SCRIPT_DIR/policy/90-encrypted-base-dracut.conf" "$NATIVE_ROOT/etc/dracut.conf.d/90-encrypted-base.conf"
    [ "$(grep -c '^ACCEPT_LICENSE=' "$NATIVE_ROOT/etc/portage/make.conf")" -eq 1 ] ||
        gentoo_die 'expected one existing ACCEPT_LICENSE setting'
    if grep -Fxq 'ACCEPT_LICENSE="*"' "$NATIVE_ROOT/etc/portage/make.conf"; then
        sed -i 's/^ACCEPT_LICENSE="\*"$/ACCEPT_LICENSE="-* @FREE"/' "$NATIVE_ROOT/etc/portage/make.conf"
    fi
    grep -Fxq 'ACCEPT_LICENSE="-* @FREE"' "$NATIVE_ROOT/etc/portage/make.conf" ||
        gentoo_die 'unexpected ACCEPT_LICENSE policy; refusing to broaden it'

    mkdir -p "$NATIVE_ROOT/tmp" "$NATIVE_ROOT/proc" "$NATIVE_ROOT/sys" "$NATIVE_ROOT/dev" \
        "$NATIVE_ROOT/gentoo-scratch"
    for point in "$NATIVE_ROOT/proc" "$NATIVE_ROOT/sys" "$NATIVE_ROOT/dev" "$NATIVE_ROOT/gentoo-scratch"; do
        mountpoint -q "$point" && gentoo_die "unexpected existing runtime chroot mount: $point"
    done
    trap 'for p in "$NATIVE_ROOT/dev" "$NATIVE_ROOT/sys" "$NATIVE_ROOT/proc" "$NATIVE_ROOT/gentoo-scratch"; do mountpoint -q "$p" && umount -R "$p"; done' EXIT
    mount -t proc proc "$NATIVE_ROOT/proc"
    mount --rbind /sys "$NATIVE_ROOT/sys"; mount --make-rslave "$NATIVE_ROOT/sys"
    mount --rbind /dev "$NATIVE_ROOT/dev"; mount --make-rslave "$NATIVE_ROOT/dev"
    mount --bind "$SCRATCH" "$NATIVE_ROOT/gentoo-scratch"
    mount --make-rslave "$NATIVE_ROOT/gentoo-scratch"
    run_emerge() {
        local status=0
        portage_manifest_guard_check && portage_repository_guard_check || gentoo_die 'pinned Gentoo source guard failed before emerge'
        chroot_env /usr/bin/python3.12 /usr/lib/portage/bin/emerge --usepkg=n --getbinpkg=n --buildpkg=n "$@" || status=$?
        portage_manifest_guard_check && portage_repository_guard_check || gentoo_die 'pinned Gentoo source guard failed after emerge'
        return "$status"
    }
    printf '%s\n' "${atoms[@]}" > "$NATIVE_ROOT/tmp/base-runtime-atoms.txt"
    cp "$NATIVE_ROOT/tmp/base-runtime-atoms.txt" "$STATE/native-base-runtime-atoms.txt"
    chroot_env /usr/bin/python3.12 -c 'from portage.dep import Atom; import sys; [Atom(x) for x in open(sys.argv[1]).read().splitlines()]' \
        /tmp/base-runtime-atoms.txt 2>/dev/null || {
        chroot "$NATIVE_ROOT" /usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/usr/lib/portage/lib \
            /usr/bin/python3.12 -c 'from portage.dep import Atom; import sys; [Atom(x) for x in open(sys.argv[1]).read().splitlines()]' \
            /tmp/base-runtime-atoms.txt || gentoo_die 'Portage rejected a runtime package atom'
    }
    run_emerge --pretend --tree --verbose --color=n "${atoms[@]}" | tee "$STATE/native-base-runtime-plan.txt"
    install -m 0755 "$SCRIPT_DIR/audit-resolved-sources.py" "$NATIVE_ROOT/tmp/audit-resolved-sources.py"
    cp "$STATE/native-base-runtime-plan.txt" "$NATIVE_ROOT/tmp/base-runtime-plan.txt"
    chroot_env /usr/bin/python3.12 /tmp/audit-resolved-sources.py \
        /tmp/base-runtime-plan.txt /tmp/base-runtime-source-audit.json
    cp "$NATIVE_ROOT/tmp/base-runtime-source-audit.json" "$STATE/native-base-runtime-source-audit.json"
    run_emerge --pretend --fetchonly "${atoms[@]}" | tee "$STATE/native-base-runtime-fetch-plan.txt"
    run_emerge --update --deep --newuse --verbose --color=n "${atoms[@]}" | tee "$STATE/native-base-runtime-merge.txt"
    run_emerge --oneshot --noreplace "${atoms[@]}"
    run_emerge --pretend --newuse --deep @world | tee "$STATE/native-base-runtime-world-plan.txt"
    plan_is_empty "$STATE/native-base-runtime-world-plan.txt" || gentoo_die 'runtime package selection left @world unconverged'
    run_emerge --pretend --verbose --color=n "$firmware_atom" | tee "$STATE/native-base-runtime-firmware-plan.txt"
    sync "$NATIVE_ROOT/etc/portage"
    trap - EXIT
    for p in "$NATIVE_ROOT/dev" "$NATIVE_ROOT/sys" "$NATIVE_ROOT/proc" "$NATIVE_ROOT/gentoo-scratch"; do
        mountpoint -q "$p" && umount -R "$p"
    done
)

base_runtime_verify() (
    set -Eeuo pipefail
    local state="$WORK_ROOT/state" root="$WORK_ROOT/native-root-x86_64" vdb command firmware firmware_atom firmware_version savedconfig
    [ -s "$state/native-base-runtime-source-audit.json" ] && [ -s "$state/native-base-runtime-world-plan.txt" ] || return 1
    [ "$(cat "$root/etc/portage/make.conf" | grep -c '^ACCEPT_LICENSE="-\* @FREE"$')" -eq 1 ] || return 1
    grep -Fxq 'hostonly="no"' "$root/etc/dracut.conf.d/90-encrypted-base.conf" || return 1
    mapfile -t firmware_atoms < <(sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' "$SCRIPT_DIR/$BASE_PACKAGES_INPUT" |
        grep '^=sys-kernel/linux-firmware-')
    [ "${#firmware_atoms[@]}" -eq 1 ] || return 1
    firmware_atom=${firmware_atoms[0]#=}
    firmware_version=${firmware_atom#sys-kernel/linux-firmware-}
    savedconfig="$root/etc/portage/savedconfig/sys-kernel/linux-firmware-$firmware_version"
    [ -s "$savedconfig" ] || return 1
    vdb=$(find "$root/var/db/pkg/sys-kernel" -mindepth 1 -maxdepth 1 -type d \
        -name 'linux-firmware-*' -print -quit 2>/dev/null)
    [ -n "$vdb" ] && [ -s "$vdb/CONTENTS" ] || return 1
    while IFS= read -r firmware; do
        [ -n "$firmware" ] || continue
        [[ "$firmware" != /* && "$firmware" != *..* ]] || return 1
        [ -s "$root/lib/firmware/$firmware" ] || return 1
        grep -Fq " /lib/firmware/$firmware " "$vdb/CONTENTS" || return 1
    done < <(sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' "$savedconfig")
    for command in cryptsetup lvm openrc NetworkManager nmcli wpa_supplicant dhcpcd sudo \
        gdisk e2fsck smartctl lspci lsusb loadkeys mc gpg dracut; do
        [ -x "$root/usr/bin/$command" ] || [ -x "$root/usr/sbin/$command" ] ||
            [ -x "$root/bin/$command" ] || [ -x "$root/sbin/$command" ] || return 1
    done
    [ -d "$root/etc/init.d" ] && [ -x "$root/sbin/openrc" ] || return 1
    [ -x "$root/etc/init.d/NetworkManager" ] || [ -L "$root/etc/init.d/NetworkManager" ] || return 1
    grep -qw argon2 "$root"/var/db/pkg/sys-fs/cryptsetup-*/USE || return 1
    grep -qw lvm "$root"/var/db/pkg/sys-fs/lvm2-*/USE || return 1
    for point in "$root/proc" "$root/sys" "$root/dev"; do mountpoint -q "$point" && return 1; done
    trap 'for p in "$root/dev" "$root/sys" "$root/proc"; do mountpoint -q "$p" && umount -R "$p"; done' EXIT
    mount -t proc proc "$root/proc"
    mount --rbind /sys "$root/sys"; mount --make-rslave "$root/sys"
    mount --rbind /dev "$root/dev"; mount --make-rslave "$root/dev"
    chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/usr/lib/portage/lib \
        /usr/bin/python3.12 -c 'import portage,sys; from portage.dep import Atom; db=portage.db["/"]["vartree"].dbapi; missing=[a for a in open(sys.argv[1]).read().splitlines() if not db.match(Atom(a))]; sys.exit(bool(missing))' \
        /tmp/base-runtime-atoms.txt || return 1
    chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin cryptsetup --version >/dev/null 2>&1 || return 1
    chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin lvm version >/dev/null 2>&1 || return 1
    chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin openrc --version >/dev/null 2>&1 || return 1
    chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin NetworkManager --version >/dev/null 2>&1 || return 1
    chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin nmcli --version >/dev/null 2>&1 || return 1
    chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin wpa_supplicant -v >/dev/null 2>&1 || return 1
    chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin dhcpcd --version >/dev/null 2>&1 || return 1
    chroot "$root" /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin sudo -V >/dev/null 2>&1 || return 1
)

base_runtime_fingerprint=$(gentoo_fingerprint stages/90-native-base-runtime.sh \
    lib/native-gentoo-system.sh lib/manifests.sh \
    "$BASE_PACKAGES_INPUT" "$BASE_USE_INPUT" "$BASE_LICENSE_INPUT" "$BASE_KEYWORDS_INPUT" \
    "$FIRMWARE_INPUT" policy/90-encrypted-base-dracut.conf \
    audit-resolved-sources.py config.inputs.sh \
    "$WORK_ROOT/state/80-native-gentoo-system.done" \
    "$WORK_ROOT/state/native-gentoo-system-ready.receipt" \
    "$WORK_ROOT/artifacts/kernel-6.12.111/config") || gentoo_die 'base runtime inputs or prerequisites are missing'
gentoo_run_stage 90-native-base-runtime "$base_runtime_fingerprint" base_runtime_action base_runtime_verify
printf 'Encrypted-base runtime packages are installed and verified in the native Gentoo root.\n'
