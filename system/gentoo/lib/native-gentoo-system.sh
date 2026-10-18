#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

native_gentoo_system_mounts() {
    local cmdline_uuid token device source target workspace_host_free scratch_host_free
    SCRATCH=/gentoo-scratch
    GENTOO_SCRATCH_UUID=
    if [ "$GENTOO_MODE" = vm ]; then
        for token in $(cat /proc/cmdline); do
            case "$token" in
                gentoo_scratch_uuid=*) cmdline_uuid=${token#*=} ;;
                gentoo_workspace_host_free_kib=*) workspace_host_free=${token#*=} ;;
                gentoo_scratch_host_free_kib=*) scratch_host_free=${token#*=} ;;
            esac
        done
        [[ "${cmdline_uuid:-}" =~ ^[[:xdigit:]-]{36}$ ]] ||
            gentoo_die 'VM continuation has no valid launcher-recorded scratch UUID'
        device=$(blkid -c /dev/null -t "UUID=$cmdline_uuid" -o device) ||
            gentoo_die 'launcher-recorded scratch UUID is unavailable'
        [ "$(printf '%s\n' "$device" | wc -l)" -eq 1 ] || gentoo_die 'scratch UUID is ambiguous'
        device=$(readlink -f "$device")
        [ -b "$device" ] && [ "$(blkid -s TYPE -o value "$device")" = ext4 ] ||
            gentoo_die 'scratch device must be an existing ext4 filesystem'
        [ "$(blkid -s UUID -o value "$GENTOO_WORKSPACE_DEVICE")" != "$cmdline_uuid" ] ||
            gentoo_die 'scratch and workspace identities resolve to the same filesystem'
        mkdir -p "$SCRATCH"
        if ! awk -v target="$SCRATCH" '$2==target {found=1} END {exit !found}' /proc/mounts; then
            mount -t ext4 -o rw "$device" "$SCRATCH"
        fi
        awk -v target="$SCRATCH" -v dev="$device" \
            '$2==target && $3=="ext4" && $4 ~ /(^|,)rw(,|$)/ && $1==dev {found=1} END {exit !found}' /proc/mounts ||
            gentoo_die 'scratch mount does not match the recorded writable ext4 device'
        GENTOO_SCRATCH_UUID=$cmdline_uuid
        [[ "${workspace_host_free:-}" =~ ^[0-9]+$ && "${scratch_host_free:-}" =~ ^[0-9]+$ ]] ||
            gentoo_die 'VM launcher did not record host free-space values for both images'
        [ "$workspace_host_free" -ge 8388608 ] && [ "$scratch_host_free" -ge 73400320 ] ||
            gentoo_die 'VM host requires at least 8 GiB free for workspace and 70 GiB for scratch'
        [ "$(df -Pk /build | awk 'NR==2 {print $4}')" -ge 52428800 ] &&
            [ "$(df -Pk "$SCRATCH" | awk 'NR==2 {print $4}')" -ge 25165824 ] ||
            gentoo_die 'VM workspace needs 50 GiB and scratch needs 24 GiB free before Gentoo system builds'
    else
        # Hardware has no synthetic scratch disk. Keep the same paths on the
        # already identity-checked workspace and refuse a low-space run.
        source=/build/gentoo-scratch
        mkdir -p "$source"
        mkdir -p "$SCRATCH"
        if ! awk -v target="$SCRATCH" '$2==target {found=1} END {exit !found}' /proc/mounts; then
            mount --bind "$source" "$SCRATCH"
        fi
        [ "$(df -Pk /build | awk 'NR==2 {print $4}')" -ge 73400320 ] ||
            gentoo_die 'hardware workspace requires at least 70 GiB free for source builds'
    fi
    mkdir -p "$SCRATCH/portage-tmp" "$SCRATCH/distfiles" "$SCRATCH/binpkgs"
    export SCRATCH GENTOO_SCRATCH_UUID
}

sha256() { sha256sum < "$1" | cut -d' ' -f1; }
marker_driver_recorded() { grep -Fxq "driver sha256=$driver_hash" "$1" || grep -Fxq "script sha256=$driver_hash" "$1"; }

lock_entry() {
    awk -F '|' -v name="$1" '$1==name {n++; row=$0} END {if(n!=1) exit 1; print row}' "$2"
}
verify_locked() {
    local archive=$1 lock=$2 record checksum
    record=$(lock_entry "$archive" "$lock") || gentoo_die "invalid source lock: $archive"
    checksum=${record##*|}
    gentoo_verify_source "$DISTFILES/$archive" "$checksum" || gentoo_die "locked source verification failed: $archive"
}

chroot_env() {
    chroot "$NATIVE_ROOT" /usr/bin/env -i HOME=/root TERM="${TERM:-vt100}" \
        PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=C LC_ALL=C \
        PYTHONPATH=/usr/lib/portage/lib \
        LD_LIBRARY_PATH=/usr/lib/perl5/5.42.2/x86_64-linux/CORE \
        EMERGE_DEFAULT_OPTS="--jobs=$GENTOO_PORTAGE_JOBS --load-average=$GENTOO_PORTAGE_LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n" \
        MAKEOPTS="-j$GENTOO_PORTAGE_MAKE_JOBS" PORTAGE_BINHOST= "$@"
}
plan_is_empty() {
    grep -Eq '^Calculating dependencies[ .]*done!' "$1" &&
        ! grep -Eq '^\[(ebuild|binary|blocks|uninstall)|^!!!|^[[:space:]]*\* Error:' "$1"
}
native_gentoo_glibc_runtime_version() { chroot_env /usr/bin/ldd --version | awk 'NR==1 {print $NF}'; }
verify_native_gentoo_glibc() {
    local version
    version=$(chroot_env /usr/bin/portageq best_version / sys-libs/glibc)
    [ "$version" = "sys-libs/glibc-$BOOTSTRAP_GLIBC_VERSION" ] || return 1
    case "$(native_gentoo_glibc_runtime_version)" in 2.44*) ;; *) return 1 ;; esac
}


plan_is_only_installed_reinstalls() {
    local plan=$1
    install -m 0755 "$SCRIPT_DIR/probes/reinstall-plan.py" "$NATIVE_ROOT/tmp/reinstall-plan.py" || return 1
    cp "$plan" "$NATIVE_ROOT/tmp/reinstall-plan.txt" || return 1
    chroot_env /usr/bin/python3.12 /tmp/reinstall-plan.py /tmp/reinstall-plan.txt
}
