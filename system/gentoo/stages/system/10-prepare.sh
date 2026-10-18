# Configure the pinned multilib profile, source-only policy, and native-root mounts.
# Sourced by stages/80-native-gentoo-system.sh; shares its checked state.
mkdir -p "$STATE" "$DISTFILES" "$NATIVE_ROOT/usr/src" "$NATIVE_ROOT/usr/src/archives" \
    "$NATIVE_ROOT/var/run" "$NATIVE_ROOT/var/cache/edb" "$NATIVE_ROOT/etc/portage/env" \
    "$NATIVE_ROOT/etc/portage/package.env" "$NATIVE_ROOT/var/log/portage" \
    "$SCRATCH/portage-tmp" "$SCRATCH/distfiles" "$SCRATCH/binpkgs"

if [ -L "$NATIVE_ROOT/var/run" ]; then
    [ "$(readlink -f "$NATIVE_ROOT/var/run")" = "$(readlink -f "$NATIVE_ROOT/run")" ] || {
        echo 'Native-root /var/run points somewhere other than persistent /run.' >&2; exit 1;
    }
fi

# The temporary trust roots are certificate data, not executable bootstrap
# inputs. Gentoo's ca-certificates package will provide the installed bundle.
host_ca=${GENTOO_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}
[ -s "$host_ca" ] || { echo "No host CA bundle at $host_ca." >&2; exit 1; }
ca_hash=$(sha256 "$host_ca")
ca_stamp="$STATE/native-gentoo-bootstrap-ca.sha256"
if [ -s "$ca_stamp" ]; then
    [ "$(cat "$ca_stamp")" = "$ca_hash" ] || {
        echo 'Host CA bundle changed after this native stage began.' >&2; exit 1;
    }
else
    mkdir -p "$NATIVE_ROOT/etc/ssl/certs"
    cp "$host_ca" "$NATIVE_ROOT/etc/ssl/certs/ca-certificates.crt"
    chmod 0644 "$NATIVE_ROOT/etc/ssl/certs/ca-certificates.crt"
    if [ -e "$NATIVE_ROOT/etc/ssl/cert.pem" ] && [ ! -L "$NATIVE_ROOT/etc/ssl/cert.pem" ]; then
        echo 'Refusing to replace an existing native-root /etc/ssl/cert.pem.' >&2; exit 1;
    fi
    ln -sfn certs/ca-certificates.crt "$NATIVE_ROOT/etc/ssl/cert.pem"
    printf '%s\n' "$ca_hash" > "$ca_stamp.tmp"
    sync "$ca_stamp.tmp"
    mv "$ca_stamp.tmp" "$ca_stamp"
    sync "$STATE"
fi

# Build the encrypted base from Gentoo's minimal OpenRC profile. The desktop
# profile is reserved for complete-desktop after encrypted boot succeeds.
# Gentoo's first source-built system stages run in a deliberately minimal
# environment. Keep ebuild test phases and test USE dependencies disabled from
# the first @system merge; retain the driver's focused bootstrap integrity
# checks separately.
bootstrap_progress_file="$NATIVE_ROOT/var/run/bootstrap-progress"
multilib_system_marker="$STATE/native-gentoo-at-system-multilib.done"
multilib_profile_marker="$STATE/native-gentoo-multilib-profile-enabled.done"
gentoo_profile_path=/usr/portage/profiles/default/linux/amd64/23.0/split-usr
[ -d "$NATIVE_ROOT$gentoo_profile_path" ] || {
    echo "Pinned Gentoo multilib profile is missing: $gentoo_profile_path" >&2; exit 1;
}
# The pinned Gentoo bootstrap and profile queries can invoke the unversioned
# `python` command before the first package merge. The native seed deliberately
# installs Python 3.12.15 as python3.12, so provide that checked bootstrap alias
# before any Portage helper runs. Later @system merges may replace it with
# python-exec once Gentoo has registered its interpreters.
python_link="$NATIVE_ROOT/usr/bin/python"
if [ ! -e "$python_link" ] && [ ! -L "$python_link" ]; then
    [ -x "$NATIVE_ROOT/usr/bin/python3.12" ] || {
        echo 'The source-built Python 3.12.15 bootstrap interpreter is missing.' >&2; exit 1;
    }
    ln -s python3.12 "$python_link"
elif [ -L "$python_link" ]; then
    python_link_target=$(readlink "$python_link")
    case "$python_link_target" in
        python3.12) ;;
        python-exec2c)
            if ! chroot "$NATIVE_ROOT" /usr/bin/env -i PATH=/usr/bin:/bin \
                /usr/bin/python --version >/dev/null 2>&1; then
                ln -sfn python3.12 "$python_link"
                echo 'Restored the source-built Python 3.12.15 alias before Gentoo bootstrap; python-exec had no registered interpreter.'
            fi
            ;;
        *) echo "Unexpected unversioned Python link in native root: $python_link_target" >&2; exit 1 ;;
    esac
elif [ -e "$python_link" ]; then
    echo 'Unexpected unversioned Python executable in native root.' >&2
    exit 1
fi
# The fresh native root already has source-built 32- and 64-bit glibc and GCC.
# Select Gentoo's multilib profile before its bootstrap and first @system pass.
current_profile=$(readlink "$NATIVE_ROOT/etc/portage/make.profile" 2>/dev/null || true)
case "$current_profile" in
    ''|/usr/portage/profiles/default/linux/amd64/23.0/split-usr/no-multilib|"$gentoo_profile_path") ;;
    *) echo "Unexpected Gentoo profile before the first package merge: $current_profile" >&2; exit 1 ;;
esac
ln -sfn "$gentoo_profile_path" "$NATIVE_ROOT/etc/portage/make.profile"
if [ -s "$multilib_profile_marker" ]; then
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$multilib_profile_marker" &&
        grep -Fxq "active profile=$gentoo_profile_path" "$multilib_profile_marker" &&
        marker_driver_recorded "$multilib_profile_marker" || {
        echo 'Early Gentoo multilib-profile marker does not match the pinned repository/driver.' >&2; exit 1;
    }
else
    printf 'complete %s\nGentoo repository commit=%s\nactive profile=%s\nprofile selected before bootstrap and first @system\nsource-built native C and C++ runtimes=ELF32+ELF64\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$gentoo_profile_path" "$driver_hash" \
        > "$multilib_profile_marker.tmp"
    sync "$multilib_profile_marker.tmp"
    mv "$multilib_profile_marker.tmp" "$multilib_profile_marker"
    sync "$STATE"
fi
if [ "$(cat "$bootstrap_progress_file" 2>/dev/null || true)" = BOOTSTRAP_STAGE=3 ] ||
    [ -s "$STATE/native-gentoo-bootstrap.done" ]; then
    bootstrap_nls_flag=
    bootstrap_abi_x86=' 32'
    bootstrap_use_conf_line=
else
    # Gentoo's upstream bootstrap derives allowed USE flags from the global
    # profile and explicitly ignores package.use. The current bootstrap cycle
    # requires NLS off; @system gets the normal profile USE after stage 3.
    bootstrap_nls_flag=' -nls'
    bootstrap_abi_x86=' 32'
    bootstrap_profile_use_marker="$STATE/native-gentoo-bootstrap-profile-use.multilib.done"
    if [ -s "$bootstrap_profile_use_marker" ]; then
        grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$bootstrap_profile_use_marker" || {
            echo 'Bootstrap USE profile marker belongs to a different Gentoo revision.' >&2; exit 1;
        }
        marker_driver_recorded "$bootstrap_profile_use_marker" || {
            echo 'Bootstrap USE profile marker was recorded by an unreviewed driver.' >&2; exit 1;
        }
        bootstrap_profile_use=$(sed -n 's/^profile BOOTSTRAP_USE=//p' "$bootstrap_profile_use_marker")
        [ -n "$bootstrap_profile_use" ] || {
            echo 'Bootstrap USE profile marker is missing its value.' >&2; exit 1;
        }
    else
        bootstrap_profile_use=$(chroot "$NATIVE_ROOT" /usr/bin/env -i \
            PATH=/usr/bin:/bin:/usr/sbin:/sbin PYTHONPATH=/usr/lib/portage/lib \
            LD_LIBRARY_PATH=/usr/lib/perl5/5.42.2/x86_64-linux/CORE \
            /usr/bin/portageq envvar BOOTSTRAP_USE)
        [ -n "$bootstrap_profile_use" ] || {
            echo 'Pinned profile produced an empty BOOTSTRAP_USE value.' >&2; exit 1;
        }
        printf 'complete %s\nGentoo repository commit=%s\nprofile BOOTSTRAP_USE=%s\ndriver sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$bootstrap_profile_use" "$driver_hash" \
            > "$bootstrap_profile_use_marker.tmp"
        sync "$bootstrap_profile_use_marker.tmp"
        mv "$bootstrap_profile_use_marker.tmp" "$bootstrap_profile_use_marker"
        sync "$STATE"
    fi
    # Upstream exports USE="-* ... ${BOOTSTRAP_USE}" for both emerges.
    # That reset also clears USE_EXPAND flags from ABI_X86 in make.conf.
    # Keep both ABIs explicit so stage 3 cannot remove an already required
    # abi_x86_32 from virtual/zlib and other multilib prerequisites.
    bootstrap_profile_use=$(printf '%s\n' "$bootstrap_profile_use" | awk '{for(i=1;i<=NF;i++) if($i!="zstd" && $i!="python_targets_python3_14" && $i!="python_single_target_python3_14" && $i!~/^-?abi_x86_/) printf "%s%s", (n++?" ":""), $i; print ""}')
    bootstrap_use_conf_value="$bootstrap_profile_use -zstd -python_targets_python3_14 python_targets_python3_12 -python_single_target_python3_14 python_single_target_python3_12 -gawk mawk reference gnu bash bison flex abi_x86_64 abi_x86_32"
    case "$bootstrap_use_conf_value" in *[!a-zA-Z0-9_+\ -]*)
        echo 'Pinned bootstrap USE value contains unexpected characters.' >&2; exit 1 ;;
    esac
    bootstrap_use_conf_line="BOOTSTRAP_USE=\"$bootstrap_use_conf_value\""
fi
cat > "$NATIVE_ROOT/etc/portage/make.conf" <<EOF
CHOST="x86_64-pc-linux-gnu"
ARCH="amd64"
ACCEPT_KEYWORDS="amd64"
ACCEPT_LICENSE="*"
ABI_X86="64${bootstrap_abi_x86}"
CFLAGS="-O2 -pipe -march=x86-64 -mtune=generic -fstack-protector-strong -D_FORTIFY_SOURCE=2"
CXXFLAGS="\${CFLAGS}"
USE="-test${bootstrap_nls_flag}"
${bootstrap_use_conf_line}
FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"
MAKEOPTS="-j$JOBS"
EMERGE_DEFAULT_OPTS="--jobs=$PACKAGE_JOBS --load-average=$LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n"
PORTAGE_BINHOST=""
PORTAGE_TMPDIR="/gentoo-scratch/portage-tmp"
DISTDIR="/gentoo-scratch/distfiles"
PKGDIR="/gentoo-scratch/binpkgs"
PORTAGE_BZIP2_COMMAND="/usr/bin/bzip2"
BINPKG_COMPRESS="bzip2"
GENTOO_MIRRORS="https://distfiles.gentoo.org"
EOF
grep -Eq '^USE="-test[^\"]*"$' "$NATIVE_ROOT/etc/portage/make.conf" &&
    grep -Fxq 'FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"' \
        "$NATIVE_ROOT/etc/portage/make.conf" || {
    echo 'The first-system Portage configuration must keep USE and FEATURES tests globally disabled.' >&2
    exit 1
}
test_policy_marker="$STATE/native-gentoo-package-tests-disabled.done"
test_policy_line='Portage ebuild test phases=disabled from first multilib @system through later builds; USE=-test and FEATURES=-test'
source_integrity_line='Portage source integrity=Portage Manifest digest verification enabled; FEATURES=-digest -assume-digests strict; binary packages disabled'
if [ -s "$test_policy_marker" ]; then
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$test_policy_marker" &&
        grep -Fxq "$test_policy_line" "$test_policy_marker" &&
        grep -Fxq "$source_integrity_line" "$test_policy_marker" &&
        marker_driver_recorded "$test_policy_marker" || {
        echo 'Global test/source-integrity policy marker does not match the current driver and pinned repository.' >&2; exit 1;
    }
else
    printf 'complete %s\nGentoo repository commit=%s\n%s\n%s\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$test_policy_line" \
        "$source_integrity_line" "$driver_hash" \
        > "$test_policy_marker.tmp"
    sync "$test_policy_marker.tmp"
    mv "$test_policy_marker.tmp" "$test_policy_marker"
    sync "$STATE"
fi
glibc_keyword_file="$NATIVE_ROOT/etc/portage/package.accept_keywords/90-native-system-glibc"
glibc_keyword_content=$(printf '# Keep Gentoo glibc aligned with the source-built native host ABI.\n%s\n' \
    "$BOOTSTRAP_GLIBC_KEYWORD_ENTRY")
glibc_keyword_hash=$(printf '%s' "$glibc_keyword_content" | sha256sum | awk '{print $1}')
glibc_ebuild="$GENTOO_TREE/sys-libs/glibc/glibc-$BOOTSTRAP_GLIBC_VERSION.ebuild"
glibc_ebuild_hash=$(sha256 "$glibc_ebuild")
glibc_manifest_hash=$(awk -v want="glibc-2.44.tar.xz" \
    '$1 == "DIST" && $2 == want {for(i=1;i<=NF;i++) if($i == "SHA512") {n++; value=$(i+1)}} END {if(n!=1) exit 1; print value}' \
    "$GENTOO_TREE/sys-libs/glibc/Manifest")
glibc_patches_manifest_hash=$(awk -v want="glibc-2.44-patches-4.tar.xz" \
    '$1 == "DIST" && $2 == want {for(i=1;i<=NF;i++) if($i == "SHA512") {n++; value=$(i+1)}} END {if(n!=1) exit 1; print value}' \
    "$GENTOO_TREE/sys-libs/glibc/Manifest")
[ -s "$glibc_ebuild" ] && [ -n "$glibc_manifest_hash" ] &&
    [ -n "$glibc_patches_manifest_hash" ] || {
    echo 'Pinned Gentoo glibc 2.44 ebuild or Manifest entries are missing.' >&2; exit 1;
}
if [ -e "$glibc_keyword_file" ]; then
    [ "$(cat "$glibc_keyword_file")" = "$(printf '%s' "$glibc_keyword_content")" ] || {
        echo 'Refusing an unexpected glibc keyword override.' >&2; exit 1;
    }
else
    printf '%s' "$glibc_keyword_content" > "$glibc_keyword_file.tmp"
    sync "$glibc_keyword_file.tmp"
    mv "$glibc_keyword_file.tmp" "$glibc_keyword_file"
    sync "$(dirname "$glibc_keyword_file")"
fi
glibc_keyword_marker="$STATE/native-gentoo-bootstrap-glibc-keyword.done"
if [ -s "$glibc_keyword_marker" ]; then
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$glibc_keyword_marker" &&
        grep -Fxq "selected Gentoo glibc=sys-libs/glibc-$BOOTSTRAP_GLIBC_VERSION" "$glibc_keyword_marker" &&
        grep -Fxq "keyword override sha256=$glibc_keyword_hash" "$glibc_keyword_marker" &&
        grep -Fxq "ebuild sha256=$glibc_ebuild_hash" "$glibc_keyword_marker" &&
        grep -Fxq "glibc source Manifest sha512=$glibc_manifest_hash" "$glibc_keyword_marker" &&
        grep -Fxq "glibc patches Manifest sha512=$glibc_patches_manifest_hash" "$glibc_keyword_marker" &&
        marker_driver_recorded "$glibc_keyword_marker" || {
        echo 'Gentoo bootstrap glibc keyword marker does not match pinned source inputs.' >&2; exit 1;
    }
else
    printf 'complete %s\nGentoo repository commit=%s\nselected Gentoo glibc=sys-libs/glibc-%s\nkeyword override sha256=%s\nebuild sha256=%s\nglibc source Manifest sha512=%s\nglibc patches Manifest sha512=%s\nnative source-built glibc provider=2.44; Gentoo glibc must retain compatible GLIBC_2.44 runtime symbols\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$BOOTSTRAP_GLIBC_VERSION" \
        "$glibc_keyword_hash" "$glibc_ebuild_hash" "$glibc_manifest_hash" \
        "$glibc_patches_manifest_hash" "$driver_hash" > "$glibc_keyword_marker.tmp"
    sync "$glibc_keyword_marker.tmp"
    mv "$glibc_keyword_marker.tmp" "$glibc_keyword_marker"
    sync "$STATE"
fi
cat > "$NATIVE_ROOT/etc/portage/env/gentoo-memory.conf" <<EOF
# GCC, glibc and Binutils have high peak memory use in the VM.
MAKEOPTS="-j$GENTOO_PORTAGE_MAKE_JOBS"
EOF
cat > "$NATIVE_ROOT/etc/portage/env/gentoo-gcc-no-tests.conf" <<EOF
# GCC's test suite failed because this Gentoo snapshot lacks its reference
# DejaGNU baseline. Keep its test phase disabled for all future rebuilds.
MAKEOPTS="-j$GENTOO_PORTAGE_MAKE_JOBS"
FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"
EOF
cat > "$NATIVE_ROOT/etc/portage/package.env/gentoo-memory" <<'EOF'
sys-devel/binutils gentoo-memory.conf
sys-devel/gcc gentoo-gcc-no-tests.conf
sys-libs/glibc gentoo-memory.conf
dev-db/mongodb gentoo-memory.conf
dev-libs/mongo-c-driver gentoo-memory.conf
EOF
mkdir -p "$NATIVE_ROOT/etc/portage/package.use"
cat > "$NATIVE_ROOT/etc/portage/package.use/99-native-gcc-tests-disabled" <<'EOF'
# GCC's test suite failed because this Gentoo snapshot lacks its reference
# baseline. Keep test disabled for both @system passes and later GCC rebuilds.
sys-devel/gcc -test
EOF
bootstrap_cycle_file="$NATIVE_ROOT/etc/portage/package.use/20-native-bootstrap-cycle"
bootstrap_cycle_content=$(printf '# Temporary source-only cycle break required by the pinned Gentoo bootstrap resolver.\nsys-apps/util-linux -nls\n')
bootstrap_cycle_hash=$(printf '%s' "$bootstrap_cycle_content" | sha256sum | awk '{print $1}')
if [ -e "$bootstrap_cycle_file" ]; then
    [ -f "$bootstrap_cycle_file" ] && [ "$(sha256 "$bootstrap_cycle_file")" = "$bootstrap_cycle_hash" ] || {
        echo 'Refusing to remove a changed prior bootstrap cycle configuration.' >&2; exit 1;
    }
    rm -f "$bootstrap_cycle_file"
fi

mkdir -p "$NATIVE_ROOT/etc/portage/repos.conf"
cat > "$NATIVE_ROOT/etc/portage/repos.conf/gentoo.conf" <<EOF
[DEFAULT]
main-repo = gentoo

[gentoo]
location = /usr/portage
auto-sync = no
EOF
[ "$(readlink "$NATIVE_ROOT/etc/portage/make.profile")" = "$gentoo_profile_path" ] || {
    echo 'The fresh Gentoo root lost its selected multilib profile.' >&2; exit 1;
}
ln -sfn "$gentoo_profile_path" "$NATIVE_ROOT/etc/portage/make.profile"
[ "$(cat "$GENTOO_TREE/profiles/repo_name")" = gentoo ] || {
    echo 'Pinned Gentoo tree has an unexpected repository name.' >&2; exit 1;
}
# Native Portage was staged from its source tree for the earlier host
# prerequisite; install the pinned global configuration data at the standard
# path expected by Portage (especially config/sets/portage.conf).
portage_config_source="$NATIVE_ROOT/usr/lib/portage/cnf"
[ -s "$portage_config_source/sets/portage.conf" ] || {
    echo 'Pinned Portage source is missing cnf/sets/portage.conf.' >&2; exit 1;
}
mkdir -p "$NATIVE_ROOT/usr/share/portage/config"
cp -a "$portage_config_source/." "$NATIVE_ROOT/usr/share/portage/config/"
cat > "$NATIVE_ROOT/etc/portage/package.use/10-native-build-alternatives" <<'EOF'
# Gentoo's pinned app-alternatives/ninja requires exactly one implementation.
app-alternatives/ninja reference
EOF

created_dev_shm_mount=0
created_devpts_mount=0
native_build_bind_mounted=0
created_dev_standard_links=()
cleanup_mounts() {
    set +e
    # The live-bootstrap image uses BusyBox umount, which has no -R option.
    # A recursive bind of /dev includes nested mounts such as devpts and shm;
    # unmount descendants deepest-first before unmounting the bind itself.
    for nested_mount in $(awk -v prefix="$NATIVE_ROOT/dev/" \
        'index($2, prefix) == 1 {print $2}' /proc/mounts | sort -r); do
        umount "$nested_mount" 2>/dev/null || true
    done
    umount "$NATIVE_ROOT/dev" 2>/dev/null || true
    if [ "$native_build_bind_mounted" = 1 ]; then
        umount "$NATIVE_ROOT/host-build" 2>/dev/null || true
    fi
    umount "$NATIVE_ROOT/proc" 2>/dev/null
    umount "$NATIVE_ROOT/sys" 2>/dev/null
    umount "$NATIVE_ROOT/gentoo-scratch" 2>/dev/null
    for dev_link in "${created_dev_standard_links[@]}"; do
        link_target=${dev_link##*|}
        link_path=${dev_link%|*}
        if [ "$(readlink "$link_path" 2>/dev/null || true)" = "$link_target" ]; then
            rm -f "$link_path"
        fi
    done
    if [ "$created_dev_shm_mount" = 1 ]; then
        umount /dev/shm 2>/dev/null
    fi
    if [ "$created_devpts_mount" = 1 ]; then
        umount /dev/pts 2>/dev/null
    fi
}
trap cleanup_mounts EXIT

mount_is_present() {
    awk -v path="$1" '$2 == path {found=1} END {exit !found}' /proc/mounts
}
for point in "$NATIVE_ROOT/dev" "$NATIVE_ROOT/proc" "$NATIVE_ROOT/sys" \
    "$NATIVE_ROOT/gentoo-scratch" "$NATIVE_ROOT/host-build"; do
    mount_is_present "$point" && {
        echo "Unexpected pre-existing native-root mount at $point." >&2
        exit 1
    }
done
mkdir -p "$NATIVE_ROOT/dev" "$NATIVE_ROOT/proc" "$NATIVE_ROOT/sys" \
    "$NATIVE_ROOT/gentoo-scratch" "$NATIVE_ROOT/host-build"
# The live-bootstrap root may have device nodes but omit the standard proc-fd
# links. sandbox's #288863 test relies on /dev/stdin resolving to its pipe;
# without these links, touch instead targets a missing file under /dev and the
# test correctly gets denied by its addpredict / policy.
for dev_name in stdin stdout stderr; do
    case "$dev_name" in
        stdin) dev_fd=0 ;;
        stdout) dev_fd=1 ;;
        stderr) dev_fd=2 ;;
    esac
    dev_path="/dev/$dev_name"
    expected_target="/proc/self/fd/$dev_fd"
    if [ -L "$dev_path" ]; then
        [ "$(readlink "$dev_path")" = "$expected_target" ] || {
            echo "Unexpected /dev/$dev_name symlink; refusing to replace it." >&2; exit 1;
        }
    elif [ -f "$dev_path" ] && [ ! -s "$dev_path" ]; then
        # A prior diagnostic against this minimal root can leave an empty
        # regular placeholder where a standard proc-fd link belongs. Replace
        # only that harmless, empty file; preserve every other unexpected node.
        echo "Replacing empty /dev/$dev_name placeholder with its proc-fd link."
        rm -f "$dev_path"
    fi
    # Let ln perform the final no-clobber existence check. This also avoids a
    # check/use race on the VM's devtmpfs while preserving unexpected nodes.
    if [ ! -L "$dev_path" ]; then
        if ln -s "$expected_target" "$dev_path" 2>/dev/null; then
            created_dev_standard_links+=("$dev_path|$expected_target")
        else
            echo "Unexpected existing /dev/$dev_name; refusing to replace it:" >&2
            ls -ld "$dev_path" >&2 || true
            stat -c 'type=%F size=%s mode=%a owner=%u:%g' "$dev_path" >&2 || true
            exit 1
        fi
    fi
done
mkdir -p /dev/shm
dev_shm_mount_type=$(awk '$2 == "/dev/shm" {print $3; exit}' /proc/mounts)
if [ -z "$dev_shm_mount_type" ]; then
    mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs /dev/shm
    created_dev_shm_mount=1
elif [ "$dev_shm_mount_type" != tmpfs ]; then
    echo "Expected tmpfs at /dev/shm, found $dev_shm_mount_type." >&2
    exit 1
fi
chmod 1777 /dev/shm
mkdir -p /dev/pts
devpts_mount_type=$(awk '$2 == "/dev/pts" {print $3; exit}' /proc/mounts)
if [ -z "$devpts_mount_type" ]; then
    mount -t devpts -o gid=5,mode=0620,ptmxmode=0666 devpts /dev/pts
    created_devpts_mount=1
elif [ "$devpts_mount_type" != devpts ]; then
    echo "Expected devpts at /dev/pts, found $devpts_mount_type." >&2
    exit 1
fi
mount --rbind /dev "$NATIVE_ROOT/dev"
mount --make-rslave "$NATIVE_ROOT/dev"
for dev_name in stdin stdout stderr; do
    case "$dev_name" in
        stdin) dev_fd=0 ;;
        stdout) dev_fd=1 ;;
        stderr) dev_fd=2 ;;
    esac
    [ "$(readlink "$NATIVE_ROOT/dev/$dev_name")" = "/proc/self/fd/$dev_fd" ] || {
        echo "Native-root /dev/$dev_name is not the standard proc-fd link." >&2; exit 1;
    }
done
mount -t proc proc "$NATIVE_ROOT/proc"
mount -t sysfs sysfs "$NATIVE_ROOT/sys"
mount --bind "$SCRATCH" "$NATIVE_ROOT/gentoo-scratch"
mount --make-rslave "$NATIVE_ROOT/gentoo-scratch"
mount --bind "$WORK_ROOT" "$NATIVE_ROOT/host-build"
native_build_bind_mounted=1

# The top-level driver owns the durable log and waits for it to drain. Do not
# install another asynchronous tee here: the old fragment referenced an unset
# LOG variable and could lose output or fail in its process-substitution child.
PHASE=build-source-wget
trap 'status=$?; printf "\nFAILED phase=%s exit=%s time=%s\n" "$PHASE" "$status" "$(date -u +%FT%TZ)"; exit "$status"' ERR
