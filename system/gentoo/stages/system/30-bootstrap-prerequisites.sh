# Break the fresh account, unpacker, Python, util-linux, and awk dependency cycles.
# Sourced by stages/80-native-gentoo-system.sh; shares its checked state.
# The temporary BOOTSTRAP_USE value is written to make.conf so the upstream
# script's own `portageq envvar BOOTSTRAP_USE` reads it. Passing this only in
# the process environment would be ignored when that script recomputes it.

# The pinned Gentoo tree has an unavoidable bootstrap cycle: xz-utils needs
# elt-patches through libtool.eclass, while elt-patches needs xz-utils to
# unpack its source. xz 5.8.3 is already source-built and provenance-checked
# by the preceding native-tools phase. Gentoo-functions is a runtime
# dependency of elt-patches, so stage its pinned shell-function source data
# from its Manifest-verified archive for this bootstrap invocation. Portage
# will later build/install the complete Gentoo-functions package normally.
PHASE=seed-pinned-elt-patches-cycle
cycle_marker="$STATE/native-gentoo-elt-patches-cycle-seed.done"
native_xz_marker="$STATE/63-xz-5.8.3.done"
native_xz_record=$(lock_entry xz-5.8.3.tar.gz "$SCRIPT_DIR/sources/native-xz-5.8.3.lock")
native_xz_hash=${native_xz_record##*sha512=}
native_tar_record=$(lock_entry tar-1.35.tar.xz "$SCRIPT_DIR/sources/native-tar-1.35.lock")
native_tar_hash=${native_tar_record##*sha512=}
grep -Fxq "source sha512=$native_xz_hash" "$native_xz_marker" &&
    chroot_env /usr/bin/xz --version | grep -Fq 'XZ Utils) 5.8.3' || {
    echo 'The required source-built native xz 5.8.3 input is missing or unverified.' >&2; exit 1;
}
if [ ! -e "$NATIVE_ROOT/usr/bin/tar" ] && [ -x "$NATIVE_ROOT/usr/bin/tar.bak" ]; then
    grep -Fxq "source sha512=$native_tar_hash" "$STATE/63-tar-1.35.done" &&
        chroot_env /usr/bin/tar.bak --version | grep -Fq 'GNU tar) 1.35' || {
        echo 'Refusing to restore an unverified tar.bak from an interrupted Gentoo bootstrap.' >&2; exit 1;
    }
    mv "$NATIVE_ROOT/usr/bin/tar.bak" "$NATIVE_ROOT/usr/bin/tar"
fi
grep -Fxq "source sha512=$native_tar_hash" "$STATE/63-tar-1.35.done" &&
    chroot_env /usr/bin/tar --version | grep -Fq 'GNU tar) 1.35' || {
    echo 'The required source-built native GNU tar 1.35 input is missing or unverified.' >&2; exit 1;
}
if [ -e "$NATIVE_ROOT/usr/bin/gtar" ] || [ -L "$NATIVE_ROOT/usr/bin/gtar" ]; then
    [ "$(readlink "$NATIVE_ROOT/usr/bin/gtar" 2>/dev/null || true)" = tar ] || {
        echo 'Refusing to replace an unexpected native-root gtar executable.' >&2; exit 1;
    }
else
    ln -s tar "$NATIVE_ROOT/usr/bin/gtar"
fi
elt_source="elt-patches-20250718.tar.xz"
elt_manifest_record=$(awk -v name="$elt_source" \
    '$1 == "DIST" && $2 == name {n++; for(i=1;i<=NF;i++) if($i=="SHA512") digest=$(i+1)} END {if(n!=1 || digest=="") exit 1; print digest}' \
    "$GENTOO_TREE/app-portage/elt-patches/Manifest") || {
    echo 'Pinned Gentoo Manifest lacks a unique SHA-512 for elt-patches.' >&2; exit 1;
}
gentoo_functions_source="gentoo-functions-1.7.7.tar.bz2"
gentoo_functions_hash=$(awk -v name="$gentoo_functions_source" \
    '$1 == "DIST" && $2 == name {n++; for(i=1;i<=NF;i++) if($i=="SHA512") digest=$(i+1)} END {if(n!=1 || digest=="") exit 1; print digest}' \
    "$GENTOO_TREE/sys-apps/gentoo-functions/Manifest") || {
    echo 'Pinned Gentoo Manifest lacks a unique SHA-512 for gentoo-functions.' >&2; exit 1;
}
if [ ! -s "$cycle_marker" ]; then
    run_emerge --fetchonly --nodeps sys-apps/gentoo-functions
    functions_archive="$SCRATCH/distfiles/$gentoo_functions_source"
    printf '%s  %s\n' "$gentoo_functions_hash" "$functions_archive" \
        | sha512sum --check --status || {
        echo 'Fetched gentoo-functions source differs from its pinned Gentoo Manifest.' >&2; exit 1;
    }
    helper_source="$NATIVE_ROOT/usr/src/gentoo-functions-1.7.7-bootstrap-helper"
    if [ ! -d "$helper_source" ]; then
        mkdir -p "$helper_source.extracting"
        tar -xjf "$functions_archive" --strip-components=1 -C "$helper_source.extracting"
        [ -s "$helper_source.extracting/functions.sh" ] &&
            [ -d "$helper_source.extracting/functions" ] || {
            echo 'Pinned gentoo-functions archive lacks its shell library sources.' >&2; exit 1;
        }
        mv "$helper_source.extracting" "$helper_source"
    fi
    # The native seed contains libc, not the Gentoo shell-library directory.
    # Create the destination before copying the verified helper and modules.
    install -d -m 0755 "$NATIVE_ROOT/lib/gentoo"
    helper_functions="$NATIVE_ROOT/lib/gentoo/functions.sh"
    if [ -e "$helper_functions" ]; then
        cmp -s "$helper_source/functions.sh" "$helper_functions" || {
            echo 'Refusing to replace a different native-root Gentoo functions file.' >&2; exit 1;
        }
    else
        cp -a "$helper_source/functions.sh" "$helper_functions"
    fi
    if [ -d "$NATIVE_ROOT/lib/gentoo/functions" ]; then
        diff -qr "$helper_source/functions" "$NATIVE_ROOT/lib/gentoo/functions" >/dev/null || {
            echo 'Refusing to replace different Gentoo function helper modules.' >&2; exit 1;
        }
    else
        cp -a "$helper_source/functions" "$NATIVE_ROOT/lib/gentoo/functions"
    fi
    installed_elt=$(chroot_env /usr/bin/portageq best_version / app-portage/elt-patches 2>/dev/null || true)
    if [ "$installed_elt" != 'app-portage/elt-patches-20250718' ]; then
        run_emerge --fetchonly --nodeps app-portage/elt-patches
        printf '%s  %s\n' "$elt_manifest_record" "$SCRATCH/distfiles/$elt_source" \
            | sha512sum --check --status || {
            echo 'Fetched elt-patches source differs from its pinned Gentoo Manifest.' >&2; exit 1;
        }
        run_emerge --oneshot --nodeps app-portage/elt-patches
    fi
    installed_elt=$(chroot_env /usr/bin/portageq best_version / app-portage/elt-patches)
    [ "$installed_elt" = 'app-portage/elt-patches-20250718' ] || {
        echo "Unexpected elt-patches seed package after source merge: $installed_elt" >&2; exit 1;
    }
    printf 'complete %s\nGentoo repository commit=%s\nsource package=%s\nsource SHA-512=%s\ngentoo-functions helper source SHA-512=%s\ngentoo-functions helper file sha256=%s\nsource-built xz SHA-512=%s\nsource-built GNU tar SHA-512=%s\ncycle resolution=Portage-built elt-patches ebuild with verified source-built xz; --nodeps applied only to this audited seed package; gentoo-functions helper data staged from its verified source archive; native GNU tar exposed as gtar\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$installed_elt" "$elt_manifest_record" \
        "$gentoo_functions_hash" "$(sha256 "$helper_functions")" \
        "$native_xz_hash" "$native_tar_hash" "$driver_hash" > "$cycle_marker.tmp"
    sync "$cycle_marker.tmp"
    mv "$cycle_marker.tmp" "$cycle_marker"
    sync "$STATE"
else
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$cycle_marker" &&
        grep -Fxq "source package=app-portage/elt-patches-20250718" "$cycle_marker" &&
        grep -Fxq "source SHA-512=$elt_manifest_record" "$cycle_marker" &&
        grep -Fxq "gentoo-functions helper source SHA-512=$gentoo_functions_hash" "$cycle_marker" &&
        grep -Fxq "gentoo-functions helper file sha256=$(sha256 "$NATIVE_ROOT/lib/gentoo/functions.sh")" "$cycle_marker" &&
        grep -Fxq "source-built xz SHA-512=$native_xz_hash" "$cycle_marker" &&
        grep -Fxq "source-built GNU tar SHA-512=$native_tar_hash" "$cycle_marker" &&
        marker_driver_recorded "$cycle_marker" || {
        echo 'elt-patches cycle-seed marker does not match the pinned source lineage.' >&2; exit 1;
    }
fi
installed_elt=$(chroot_env /usr/bin/portageq best_version / app-portage/elt-patches 2>/dev/null || true)
[ "$installed_elt" = 'app-portage/elt-patches-20250718' ] || {
    echo 'Pinned elt-patches package is absent from Portage VDB after cycle seeding.' >&2; exit 1;
}

# The current util-linux/shadow graph also schedules acct-group/root before
# shadow, while native-root already has the correct root group. Register this
# metadata-only account ebuild first so the bootstrap does not pull the entire
# shadow/gettext/Meson chain back into util-linux's build dependencies.
root_group_ebuild="$GENTOO_TREE/acct-group/root/root-0-r2.ebuild"
[ -s "$root_group_ebuild" ] || { echo 'Pinned root-group ebuild is missing.' >&2; exit 1; }
root_group_gid=$(awk -F= '$1 == "ACCT_GROUP_ID" {n++; value=$2} END {if(n!=1) exit 1; print value}' \
    "$root_group_ebuild") || { echo 'Pinned root group ebuild has no unique GID.' >&2; exit 1; }
[ "$root_group_gid" = 0 ] || { echo "Pinned root group GID is unexpected: $root_group_gid" >&2; exit 1; }
root_group_gid_in_file=$(awk -F: '$1 == "root" {n++; gid=$3} END {if(n!=1) exit 1; print gid}' \
    "$NATIVE_ROOT/etc/group") || { echo 'Native-root group file lacks a unique root group.' >&2; exit 1; }
[ "$root_group_gid_in_file" = 0 ] || {
    echo "Native root group has unexpected GID $root_group_gid_in_file." >&2; exit 1;
}
root_group_marker="$STATE/native-gentoo-root-account-cycle-seed.done"
if [ ! -s "$root_group_marker" ]; then
    root_group_installed=$(chroot_env /usr/bin/portageq best_version / acct-group/root 2>/dev/null || true)
    if [ "$root_group_installed" != 'acct-group/root-0-r2' ]; then
        run_emerge --oneshot --nodeps acct-group/root
    fi
    root_group_installed=$(chroot_env /usr/bin/portageq best_version / acct-group/root 2>/dev/null || true)
    [ "$root_group_installed" = 'acct-group/root-0-r2' ] || {
        echo 'Pinned acct-group/root did not register in Portage VDB.' >&2; exit 1;
    }
    printf 'complete %s\nGentoo repository commit=%s\nebuild sha256=%s\npackage=%s\ngroup=root\nGID=0\nsource policy=metadata-only account package; existing root group verified; --nodeps breaks only the acct-group/root-shadow cycle\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$(sha256 "$root_group_ebuild")" \
        "$root_group_installed" "$driver_hash" > "$root_group_marker.tmp"
    sync "$root_group_marker.tmp"
    mv "$root_group_marker.tmp" "$root_group_marker"
    sync "$STATE"
else
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$root_group_marker" &&
        grep -Fxq "ebuild sha256=$(sha256 "$root_group_ebuild")" "$root_group_marker" &&
        grep -Fxq 'package=acct-group/root-0-r2' "$root_group_marker" &&
        grep -Fxq 'group=root' "$root_group_marker" &&
        grep -Fxq 'GID=0' "$root_group_marker" &&
        marker_driver_recorded "$root_group_marker" || {
        echo 'Root group cycle marker does not match pinned account metadata.' >&2; exit 1;
    }
fi

# The current shadow ebuilds also form a package cycle: sys-apps/shadow
# requires acct-group/shadow, while that metadata-only account package has a
# runtime dependency on sys-apps/shadow. Seed the declared group from the
# pinned ebuild's GID, then register the account package without dependencies;
# its ebuild sees the existing group and does not need groupadd yet.
shadow_group_ebuild="$GENTOO_TREE/acct-group/shadow/shadow-0.ebuild"
shadow_gid=$(awk -F= '$1 == "ACCT_GROUP_ID" {n++; value=$2} END {if(n!=1) exit 1; print value}' \
    "$shadow_group_ebuild") || { echo 'Pinned shadow group ebuild has no unique GID.' >&2; exit 1; }
case "$shadow_gid" in ''|*[!0-9]*) echo 'Pinned shadow GID is not numeric.' >&2; exit 1 ;; esac
shadow_group_count=$(awk -F: '$1 == "shadow" {n++} END {print n+0}' "$NATIVE_ROOT/etc/group")
shadow_group_gid=$(awk -F: '$1 == "shadow" {print $3}' "$NATIVE_ROOT/etc/group")
case "$shadow_group_count" in 0|1) ;; *)
    echo 'Native-root group file contains duplicate shadow group entries.' >&2; exit 1 ;;
esac
if [ "$shadow_group_count" -eq 0 ]; then
    gid_owner=$(awk -F: -v gid="$shadow_gid" '$3 == gid {print $1; n++} END {if(n>1) exit 2}' \
        "$NATIVE_ROOT/etc/group") || { echo "GID $shadow_gid is ambiguous in native-root group file." >&2; exit 1; }
    [ -z "$gid_owner" ] || { echo "Pinned shadow GID $shadow_gid is already used by $gid_owner." >&2; exit 1; }
    printf 'shadow:x:%s:\n' "$shadow_gid" >> "$NATIVE_ROOT/etc/group"
elif [ "$shadow_group_gid" != "$shadow_gid" ]; then
    echo "Native-root shadow group has GID $shadow_group_gid, but the pinned ebuild requires $shadow_gid." >&2
    exit 1
fi
if ! awk -F: '$1 == "shadow" {found=1} END {exit !found}' "$NATIVE_ROOT/etc/gshadow"; then
    printf 'shadow:!::\n' >> "$NATIVE_ROOT/etc/gshadow"
fi
shadow_group_marker="$STATE/native-gentoo-shadow-account-cycle-seed.done"
if [ ! -s "$shadow_group_marker" ]; then
    shadow_group_installed=$(chroot_env /usr/bin/portageq best_version / acct-group/shadow 2>/dev/null || true)
    if [ "$shadow_group_installed" != 'acct-group/shadow-0' ]; then
        run_emerge --oneshot --nodeps acct-group/shadow
    fi
    shadow_group_installed=$(chroot_env /usr/bin/portageq best_version / acct-group/shadow 2>/dev/null || true)
    [ "$shadow_group_installed" = 'acct-group/shadow-0' ] || {
        echo 'Pinned acct-group/shadow did not register in Portage VDB.' >&2; exit 1;
    }
    printf 'complete %s\nGentoo repository commit=%s\nebuild sha256=%s\npackage=%s\ngroup=shadow\nGID=%s\nsource policy=metadata-only account package; group identity from pinned ebuild; --nodeps breaks only the shadow package cycle\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$(sha256 "$shadow_group_ebuild")" \
        "$shadow_group_installed" "$shadow_gid" "$driver_hash" > "$shadow_group_marker.tmp"
    sync "$shadow_group_marker.tmp"
    mv "$shadow_group_marker.tmp" "$shadow_group_marker"
    sync "$STATE"
else
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$shadow_group_marker" &&
        grep -Fxq "ebuild sha256=$(sha256 "$shadow_group_ebuild")" "$shadow_group_marker" &&
        grep -Fxq "package=acct-group/shadow-0" "$shadow_group_marker" &&
        grep -Fxq "GID=$shadow_gid" "$shadow_group_marker" &&
        marker_driver_recorded "$shadow_group_marker" || {
        echo 'Shadow group cycle marker does not match pinned account metadata.' >&2; exit 1;
    }
fi



# Gentoo's own glibc is installed by the first multilib @system merge. Before
# then, only validate the source-built native runtime used to run Portage.
glibc_runtime_marker="$STATE/native-gentoo-glibc-runtime-aligned.done"
python_cycle_file="$NATIVE_ROOT/etc/portage/package.use/85-native-python-zstd-cycle"
python_cycle_marker="$STATE/native-gentoo-python-zstd-cycle.done"
python_cycle_ebuild="$GENTOO_TREE/dev-lang/python/python-3.14.7.ebuild"
util_linux_ebuild="$GENTOO_TREE/sys-apps/util-linux/util-linux-2.42.3.ebuild"
awk_alternative_ebuild="$GENTOO_TREE/app-alternatives/awk/awk-4.ebuild"
python_cycle_content=$(printf '# Temporary pinned Gentoo bootstrap and first-system cycle breaks.\ndev-lang/python build\nsys-apps/util-linux -pam -su\napp-alternatives/awk -gawk mawk\n')
ebuild_has_iuse() {
    local ebuild=$1 flag=$2
    awk -v wanted="$flag" '
        function inspect_iuse(value, parts, count, i, token) {
            sub(/^[^"]*"/, "", value)
            sub(/".*$/, "", value)
            count = split(value, parts, /[[:space:]]+/)
            for (i = 1; i <= count; i++) {
                token = parts[i]
                sub(/^[+-]/, "", token)
                if (token == wanted) found = 1
            }
        }
        /^[[:space:]]*IUSE[+]?[[:space:]]*=/ {
            collecting = 1
            value = $0
            if (index(value, "\"") && substr(value, index(value, "\"") + 1) ~ /"/) {
                inspect_iuse(value)
                collecting = 0
            }
            next
        }
        collecting {
            value = value " " $0
            if (index($0, "\"")) {
                inspect_iuse(value)
                collecting = 0
            }
        }
        END { exit !found }
    ' "$ebuild"
}
if [ ! -s "$STATE/native-gentoo-at-system-source-built.done" ]; then
[ -f "$python_cycle_ebuild" ] || { echo "Pinned Python ebuild is missing: $python_cycle_ebuild" >&2; exit 1; }
grep -Fq 'app-arch/zstd' "$python_cycle_ebuild" || {
    echo 'Pinned Python ebuild no longer declares the zstd dependency used by this cycle workaround.' >&2; exit 1;
}
grep -Eq '(^|[^[:alnum:]_])use(v)?[[:space:]]+build([[:space:]]|$)' "$python_cycle_ebuild" || {
    echo 'Pinned Python ebuild no longer uses the build USE flag expected by this cycle workaround.' >&2; exit 1;
}
[ -f "$util_linux_ebuild" ] || { echo "Pinned util-linux ebuild is missing: $util_linux_ebuild" >&2; exit 1; }
ebuild_has_iuse "$util_linux_ebuild" pam || {
    echo 'Pinned util-linux ebuild does not declare the pam USE flag expected by this cycle workaround.' >&2; exit 1;
}
ebuild_has_iuse "$util_linux_ebuild" su || {
    echo 'Pinned util-linux ebuild does not declare the su USE flag expected by this cycle workaround.' >&2; exit 1;
}
[ -f "$awk_alternative_ebuild" ] || { echo "Pinned awk alternative ebuild is missing: $awk_alternative_ebuild" >&2; exit 1; }
grep -Eq '(^|[^[:alnum:]_])gawk([^[:alnum:]_]|$)' "$awk_alternative_ebuild" || {
    echo 'Pinned awk alternative ebuild does not declare the gawk USE flag expected by this cycle workaround.' >&2; exit 1;
}
grep -Eq '(^|[^[:alnum:]_])mawk([^[:alnum:]_]|$)' "$awk_alternative_ebuild" || {
    echo 'Pinned awk alternative ebuild does not declare the mawk USE flag expected by this cycle workaround.' >&2; exit 1;
}
python_cycle_ebuild_hash=$(sha256 "$python_cycle_ebuild")
util_linux_ebuild_hash=$(sha256 "$util_linux_ebuild")
awk_alternative_ebuild_hash=$(sha256 "$awk_alternative_ebuild")
mkdir -p "$(dirname "$python_cycle_file")"
if [ -e "$python_cycle_file" ]; then
    [ -f "$python_cycle_file" ] || { echo 'Unexpected Python package.use override path.' >&2; exit 1; }
    [ "$(cat "$python_cycle_file")" = "$python_cycle_content" ] || {
        echo 'Unexpected Python bootstrap cycle settings.' >&2; exit 1;
    }
else
    printf '%s' "$python_cycle_content" > "$python_cycle_file.tmp"
    chmod 0644 "$python_cycle_file.tmp"
    mv "$python_cycle_file.tmp" "$python_cycle_file"
    sync "$python_cycle_file"
fi
python_cycle_file_hash=$(sha256 "$python_cycle_file")
if [ -s "$python_cycle_marker" ]; then
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$python_cycle_marker" &&
        grep -Fxq "Python ebuild sha256=$python_cycle_ebuild_hash" "$python_cycle_marker" &&
        grep -Fxq "util-linux ebuild sha256=$util_linux_ebuild_hash" "$python_cycle_marker" &&
        grep -Fxq "awk alternative ebuild sha256=$awk_alternative_ebuild_hash" "$python_cycle_marker" &&
        grep -Fxq "package.use sha256=$python_cycle_file_hash" "$python_cycle_marker" &&
        grep -Fxq 'setting=dev-lang/python build;sys-apps/util-linux -pam -su;app-alternatives/awk -gawk mawk; breaks Gentoo bootstrap and first-system dependency cycles' "$python_cycle_marker" &&
        marker_driver_recorded "$python_cycle_marker" || {
        echo 'Temporary first-system cycle receipt does not match its pinned ebuilds.' >&2; exit 1;
    }
else
    printf 'active %s\nGentoo repository commit=%s\nPython ebuild sha256=%s\nutil-linux ebuild sha256=%s\nawk alternative ebuild sha256=%s\npackage.use sha256=%s\nsetting=dev-lang/python build;sys-apps/util-linux -pam -su;app-alternatives/awk -gawk mawk; breaks Gentoo bootstrap and first-system dependency cycles\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$python_cycle_ebuild_hash" "$util_linux_ebuild_hash" \
        "$awk_alternative_ebuild_hash" \
        "$python_cycle_file_hash" "$driver_hash" > "$python_cycle_marker.tmp"
    sync "$python_cycle_marker.tmp"
    mv "$python_cycle_marker.tmp" "$python_cycle_marker"
    sync "$STATE"
fi
fi
# Gentoo's own multilib glibc belongs to the first @system transaction.
# Before bootstrap, Portage runs against the verified source-built native libc.
native_runtime=$(native_gentoo_glibc_runtime_version) || {
    echo 'The source-built native glibc runtime required by Portage is unavailable.' >&2; exit 1;
}
case "$native_runtime" in 2.44*) ;; *)
    echo "Source-built native glibc runtime must be 2.44 before Gentoo bootstrap; found $native_runtime." >&2; exit 1;; esac
