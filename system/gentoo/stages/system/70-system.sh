# Enable compiler OpenMP, refresh Perl consumers, and build the first multilib @system.
# Sourced by stages/80-native-gentoo-system.sh; shares its checked state.
# Bootstrap has installed Gentoo GCC. Restore gettext's profile USE before
# resolving @system; --newuse below rebuilds it with OpenMP. This also handles
# a restart after the bootstrap receipt was written but before cleanup.
if [ -e "$bootstrap_openmp_file" ]; then
    [ -f "$bootstrap_openmp_file" ] && [ ! -L "$bootstrap_openmp_file" ] &&
        [ "$(cat "$bootstrap_openmp_file")" = "$bootstrap_openmp_content" ] || {
        echo 'Bootstrap OpenMP override changed during the build; refusing to remove it.' >&2; exit 1;
    }
    rm "$bootstrap_openmp_file"
    sync "$(dirname "$bootstrap_openmp_file")"
fi

# Upstream bootstrap's USE reset can leave its GCC without OpenMP despite
# the package.use request. Portage runs all @system pkg_pretend checks before
# it merges GCC, so build the compiler capability in a separate transaction.
PHASE=prepare-gentoo-gcc-openmp
gcc_openmp_probe() {
    chroot_env /bin/bash -c '
        set -eu
        probe=$(mktemp -d /tmp/gcc-openmp-probe.XXXXXX)
        trap '\''rm -rf "$probe"'\'' EXIT
        for abi in 64 32; do
            printf "#include <omp.h>\nint main(void) { return omp_get_max_threads() < 1; }\n" |
                gcc -m"$abi" -fopenmp -x c - -o "$probe/openmp-$abi"
            "$probe/openmp-$abi"
        done
    '
}
if ! gcc_openmp_probe; then
    gcc_openmp_file="$NATIVE_ROOT/etc/portage/package.use/87-native-gcc-openmp"
    if [ -e "$gcc_openmp_file" ] || [ -L "$gcc_openmp_file" ]; then
        [ -f "$gcc_openmp_file" ] && [ ! -L "$gcc_openmp_file" ] &&
            [ "$(cat "$gcc_openmp_file")" = 'sys-devel/gcc openmp' ] || {
            echo 'Unexpected GCC OpenMP override.' >&2; exit 1;
        }
    fi
    printf 'sys-devel/gcc openmp\n' > "$gcc_openmp_file"
    run_emerge --oneshot --newuse sys-devel/gcc
    gcc_openmp_probe || {
        echo 'Gentoo GCC still cannot compile, link, and run OpenMP for both ABIs.' >&2; exit 1;
    }
fi

PHASE=record-pinned-system-source-closure
install -m 0755 "$SCRIPT_DIR/audit-resolved-sources.py" "$NATIVE_ROOT/tmp/audit-resolved-sources.py"
run_emerge --pretend --emptytree --deep --with-bdeps=y --verbose --color=n @system \
    | tee "$STATE/native-gentoo-system-full-closure.txt"
cp "$STATE/native-gentoo-system-full-closure.txt" "$NATIVE_ROOT/tmp/system-full-closure.txt"
chroot_env /usr/bin/python3.12 /tmp/audit-resolved-sources.py \
    /tmp/system-full-closure.txt /tmp/system-source-audit.json
cp "$NATIVE_ROOT/tmp/system-source-audit.json" "$STATE/system-source-audit.json"

PHASE=fetch-pinned-at-system-sources
fetch_marker="$STATE/native-gentoo-at-system-sources-fetched.done"
if [ ! -s "$fetch_marker" ]; then
    # All package test flags stay globally disabled. This is especially
    # important for PAM and other packages whose tests need unavailable host
    # services or assumptions that do not hold in this bootstrap environment.
    run_emerge --pretend --tree @system | tee "$STATE/native-gentoo-at-system-plan.txt"
    run_emerge --fetchonly @system
    printf 'complete %s\nGentoo repository commit=%s\nsystem lock sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$system_lock_hash" > "$fetch_marker.tmp"
    sync "$fetch_marker.tmp"
    mv "$fetch_marker.tmp" "$fetch_marker"
    sync "$STATE"
fi

PHASE=source-build-at-system
system_marker="$STATE/native-gentoo-at-system-source-built.done"
# Perl's subslot changed after Gentoo Texinfo 7.3 was first installed. Its
# TreeElement XS module still targets Perl 5.42 and breaks makeinfo under 5.44.
# Rebuild Texinfo before resuming @system when Gentoo Perl is already present;
# on the first pass, Perl may be merged by @system, so a later restart will
# pick up the rebuild before the remaining package merge. Keep its ebuild test
# phase disabled under the global no-test policy.
installed_gentoo_perl=$(chroot_env /usr/bin/portageq best_version / dev-lang/perl 2>/dev/null || true)
texinfo_marker="$STATE/native-gentoo-texinfo-perl-rebuild.done"
if [ -n "$installed_gentoo_perl" ]; then
    case "$installed_gentoo_perl" in dev-lang/perl-*) ;; *)
        echo "Unexpected Gentoo Perl package identity: $installed_gentoo_perl" >&2; exit 1 ;;
    esac
    PHASE=rebuild-texinfo-for-current-perl
    texinfo_vdb=$(find "$NATIVE_ROOT/var/db/pkg/sys-apps" -mindepth 1 -maxdepth 1 \
        -type d -name 'texinfo-*' -print -quit 2>/dev/null || true)
    if [ -s "$texinfo_marker" ]; then
        grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$texinfo_marker" &&
            grep -Fxq "Gentoo Perl package=$installed_gentoo_perl" "$texinfo_marker" &&
            marker_driver_recorded "$texinfo_marker" || {
            echo 'Texinfo rebuild marker does not match the pinned repository/Perl.' >&2; exit 1;
        }
    fi
    if [ -z "$texinfo_vdb" ] || grep -qw test "$texinfo_vdb/USE" || [ ! -s "$texinfo_marker" ]; then
        # Perl's subslot changed; rebuild Texinfo against the current Perl ABI.
        # The global no-test policy remains in force for this and every ebuild.
        run_emerge --pretend --oneshot --newuse --verbose --color=n sys-apps/texinfo | \
            tee "$STATE/native-gentoo-texinfo-rebuild-plan.txt"
        run_emerge --oneshot --newuse sys-apps/texinfo
        texinfo_vdb=$(find "$NATIVE_ROOT/var/db/pkg/sys-apps" -mindepth 1 -maxdepth 1 \
            -type d -name 'texinfo-*' -print -quit 2>/dev/null || true)
        [ -n "$texinfo_vdb" ] && [ "$(cat "$texinfo_vdb/repository" 2>/dev/null || true)" = gentoo ] &&
            ! grep -qw test "$texinfo_vdb/USE" || {
            echo 'Texinfo did not rebuild with the global test-disabled package flags.' >&2; exit 1;
        }
        chroot_env /usr/bin/timeout 20 /usr/bin/makeinfo --version
        printf 'complete %s\nGentoo repository commit=%s\nGentoo Perl package=%s\nGentoo Texinfo package=%s\nTexinfo source rebuild=passed with USE=-test; global FEATURES=-test\nmakeinfo runtime check=passed\ndriver sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$installed_gentoo_perl" "${texinfo_vdb##*/}" "$driver_hash" \
            > "$texinfo_marker.tmp"
        sync "$texinfo_marker.tmp"
        mv "$texinfo_marker.tmp" "$texinfo_marker"
        sync "$STATE"
    fi
else
    [ ! -s "$texinfo_marker" ] || {
        echo 'Texinfo rebuild marker exists but Gentoo Perl is not registered.' >&2; exit 1;
    }
    echo 'Gentoo Perl is not registered yet; defer the Texinfo Perl-ABI rebuild until the next @system resume.'
fi

# Gentoo Perl itself says to use perl-cleaner when a Perl module is still
# unusable after a major Perl upgrade. Rebuild only stale module/libperl users;
# do not run its broader pre-clean actions or delete old-path leftovers.
perl_cleaner_marker="$STATE/native-gentoo-perl-cleaner.done"
if [ -n "$installed_gentoo_perl" ]; then
if [ -s "$perl_cleaner_marker" ]; then
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$perl_cleaner_marker" &&
        grep -Fxq "Gentoo Perl package=$installed_gentoo_perl" "$perl_cleaner_marker" &&
        [ "$(sha256 "$NATIVE_ROOT/usr/sbin/perl-cleaner")" = \
            "$(awk -F= '$1 == "perl-cleaner script sha256" {print $2}' "$perl_cleaner_marker")" ] &&
        chroot_env /usr/bin/perl -MConfig -e \
            'print "Perl core runtime passed\n"' | grep -Fxq 'Perl core runtime passed' &&
        marker_driver_recorded "$perl_cleaner_marker" || {
        echo 'Perl module rebuild marker does not match the pinned ABI/runtime.' >&2; exit 1;
    }
else
    PHASE=build-gentoo-perl-cleaner
    # The global no-test policy also applies to any dependencies planned here.
    run_emerge --pretend --oneshot --verbose --color=n app-admin/perl-cleaner |
        tee "$STATE/native-gentoo-perl-cleaner-build-plan.txt"
    # GCC is usually already installed; do not require a GCC merge merely to
    # install perl-cleaner. Check its test setting only when it is in the plan.
    if grep -Eq '^\[ebuild.*sys-devel/gcc-' "$STATE/native-gentoo-perl-cleaner-build-plan.txt"; then
        grep -E '^\[ebuild.*sys-devel/gcc-' "$STATE/native-gentoo-perl-cleaner-build-plan.txt" |
            grep -Eq 'USE="[^"]* -test([ "*]|$)' || {
            echo 'Portage planned GCC without USE=-test for the compiler capability merge.' >&2; exit 1;
        }
    fi
    run_emerge --oneshot app-admin/perl-cleaner
    [ -x "$NATIVE_ROOT/usr/sbin/perl-cleaner" ] || {
        echo 'Pinned Gentoo perl-cleaner did not install its source-built script.' >&2; exit 1;
    }
    PHASE=refresh-gentoo-perl-modules
    chroot_env /usr/sbin/perl-cleaner --pretend --modules --libperl --dont-delete-leftovers \
        2>&1 | tee "$STATE/native-gentoo-perl-cleaner-plan.txt"
    chroot_env /usr/sbin/perl-cleaner --modules --libperl --dont-delete-leftovers \
        2>&1 | tee "$STATE/native-gentoo-perl-cleaner.log"
    chroot_env /usr/bin/perl -MConfig -e \
        'print "Perl core runtime passed\n"'
    perl_cleaner_package=$(chroot_env /usr/bin/portageq best_version / app-admin/perl-cleaner)
    perl_cleaner_script_hash=$(sha256 "$NATIVE_ROOT/usr/sbin/perl-cleaner")
    printf 'complete %s\nGentoo repository commit=%s\nGentoo Perl package=%s\nGentoo perl-cleaner package=%s\nperl-cleaner script sha256=%s\noperation=--modules --libperl --dont-delete-leftovers\nsource policy=Portage strict Manifest verification; --usepkg=n --getbinpkg=n\nPerl core Config import=passed\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$installed_gentoo_perl" \
        "$perl_cleaner_package" "$perl_cleaner_script_hash" "$driver_hash" \
        > "$perl_cleaner_marker.tmp"
    sync "$perl_cleaner_marker.tmp"
    mv "$perl_cleaner_marker.tmp" "$perl_cleaner_marker"
    sync "$STATE"
fi
else
    [ ! -s "$perl_cleaner_marker" ] || {
        echo 'Perl module rebuild marker exists but Gentoo Perl is not registered.' >&2; exit 1;
    }
    echo 'Gentoo Perl is not registered yet; defer the Perl module rebuild until the next @system resume.'
fi

if [ ! -s "$system_marker" ]; then
    system_started="$STATE/native-gentoo-at-system-build.started"
    # The temporary LFS Gawk is built before the Gentoo GMP/MPFR libraries
    # exist. GCC 16's option-table generator needs GNU Gawk built with MPFR;
    # resume the source-built dependency first, then rebuild the same locked
    # Gawk source against Gentoo's own MPFR before the @system merge.
    gawk_mpfr_marker="$STATE/native-gentoo-gawk-mpfr.done"
    if [ ! -s "$gawk_mpfr_marker" ]; then
        PHASE=source-build-gentoo-gawk-mpfr
        native_gawk_archive=gawk-5.4.1.tar.xz
        native_gawk_record=$(lock_entry "$native_gawk_archive" "$SCRIPT_DIR/sources/native-gawk-5.4.1.lock")
        native_gawk_hash=${native_gawk_record##*sha512=}
        [ -n "$native_gawk_hash" ] || { echo 'Missing locked GNU Gawk source digest.' >&2; exit 1; }
        verify_locked "$native_gawk_archive" "$SCRIPT_DIR/sources/native-gawk-5.4.1.lock"
        grep -Fxq "source sha512=$native_gawk_hash" "$STATE/63-gawk-5.4.1.done" || {
            echo 'Temporary Gawk marker does not match the pinned native source.' >&2; exit 1;
        }
        grep -Fxq "source sha512=$native_gawk_hash" "$STATE/63-gawk-source-patch.receipt" || {
            echo 'The required pinned Gawk source adjustment is not recorded.' >&2; exit 1;
        }
        native_gawk_source="$WORK_ROOT/sources/gawk-5.4.1"
        native_gawk_build="$WORK_ROOT/build/gawk-5.4.1-gentoo-mpfr-v1"
        [ -x "$native_gawk_source/configure" ] &&
            ! grep -q extras "$native_gawk_source/Makefile.in" || {
            echo 'The locked and patched Gawk source tree is absent or unexpected.' >&2; exit 1;
        }
        # This pulls source-built GMP/MPFR from the pinned Gentoo tree before
        # GCC, without substitutes; Gentoo ebuild test phases are globally disabled.
        run_emerge --oneshot dev-libs/mpfr
        chroot_env /usr/bin/env CPPFLAGS=-I/usr/include LDFLAGS=-L/usr/lib64 \
            GENTOO_BOOTSTRAP_JOBS="$GENTOO_BUILD_SOURCE_JOBS" \
            /bin/bash -c '
                set -Eeuo pipefail
                source=/host-build/sources/gawk-5.4.1
                build=/host-build/build/gawk-5.4.1-gentoo-mpfr-v1
                mkdir -p "$build"
                cd "$build"
                if [ ! -f Makefile ]; then
                    "$source/configure" --prefix=/usr --libdir=/usr/lib64
                fi
                make -j"${GENTOO_BOOTSTRAP_JOBS:-1}"
                make -j"${GENTOO_BOOTSTRAP_JOBS:-1}" install
            '
        gawk_runtime=$(chroot_env /usr/bin/gawk --version | sed -n '1p')
        case "$gawk_runtime" in 'GNU Awk 5.4.1,'*) ;; *)
            echo "Unexpected source-built GNU Gawk after MPFR rebuild: $gawk_runtime" >&2; exit 1 ;; esac
        chroot_env /usr/bin/gawk --help | grep -Eq -- '(^|[[:space:]])-M([,[:space:]]|$)|--bignum' || {
            echo 'The rebuilt Gawk does not expose MPFR bignum support.' >&2; exit 1;
        }
        chroot_env /usr/bin/gawk -M 'BEGIN { x = 2 ^ 100; if (x + 1 == x) exit 1; print "MPFR bignum passed" }' \
            | grep -Fxq 'MPFR bignum passed' || {
            echo 'The MPFR Gawk bignum runtime check failed.' >&2; exit 1;
        }
        gawk_binary_hash=$(sha256 "$NATIVE_ROOT/usr/bin/gawk")
        mpfr_library_hash=$(sha256 "$NATIVE_ROOT/usr/lib64/libmpfr.so.6.2.2")
        printf 'complete %s\nGentoo repository commit=%s\nsource sha512=%s\nsource adjustment=state/63-gawk-source-patch.receipt\nGMP/MPFR provider=source-built pinned Gentoo @system dependency\nMPFR library sha256=%s\nGawk configure CPPFLAGS=-I/usr/include LDFLAGS=-L/usr/lib64\nGawk upstream make check=not-run under global no-test policy\nGawk MPFR bignum runtime=passed\nGawk binary sha256=%s\nrecipe=source-built-native-gawk-5.4.1-mpfr-v1\ndriver sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$native_gawk_hash" \
            "$mpfr_library_hash" "$gawk_binary_hash" "$driver_hash" \
            > "$gawk_mpfr_marker.tmp"
        sync "$gawk_mpfr_marker.tmp"
        mv "$gawk_mpfr_marker.tmp" "$gawk_mpfr_marker"
        sync "$STATE"
    else
        grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$gawk_mpfr_marker" &&
            grep -Fxq 'recipe=source-built-native-gawk-5.4.1-mpfr-v1' "$gawk_mpfr_marker" &&
            marker_driver_recorded "$gawk_mpfr_marker" || {
            echo 'MPFR-enabled Gawk marker does not match the pinned source or driver.' >&2; exit 1;
        }
        native_gawk_archive=gawk-5.4.1.tar.xz
        native_gawk_record=$(lock_entry "$native_gawk_archive" "$SCRIPT_DIR/sources/native-gawk-5.4.1.lock")
        native_gawk_hash=${native_gawk_record##*sha512=}
        grep -Fxq "source sha512=$native_gawk_hash" "$gawk_mpfr_marker" &&
        grep -Fxq "source sha512=$native_gawk_hash" "$STATE/63-gawk-source-patch.receipt" || {
            echo 'MPFR Gawk completion evidence does not match the locked patched source.' >&2; exit 1;
        }
        gawk_vdb=$(find "$NATIVE_ROOT/var/db/pkg/sys-apps" -mindepth 1 -maxdepth 1 \
            -type d -name 'gawk-*' -print -quit 2>/dev/null || true)
        gawk_vdb_version=
        if [ -n "$gawk_vdb" ] && [ -r "$gawk_vdb/repository" ] &&
            [ "$(cat "$gawk_vdb/repository")" = gentoo ] &&
            awk '$2 == "/usr/bin/gawk" {found=1} END {exit !found}' "$gawk_vdb/CONTENTS" &&
            [ -f "$NATIVE_ROOT/usr/bin/gawk" ] && [ ! -L "$NATIVE_ROOT/usr/bin/gawk" ]; then
            gawk_vdb_version=${gawk_vdb##*/}
            [ "$(chroot_env /usr/bin/portageq best_version / sys-apps/gawk)" = \
                "sys-apps/$gawk_vdb_version" ] || {
                echo 'The installed Gentoo Gawk VDB entry is not Portage-selected.' >&2; exit 1;
            }
            expected_gawk_md5=$(awk '$1 == "obj" && $2 == "/usr/bin/gawk" {n++; value=$3} END {if (n != 1) exit 1; print value}' \
                "$gawk_vdb/CONTENTS") || { echo 'Gentoo Gawk VDB has no unique executable record.' >&2; exit 1; }
            actual_gawk_md5=$(md5sum "$NATIVE_ROOT/usr/bin/gawk" | awk '{print $1}')
            [ "$actual_gawk_md5" = "$expected_gawk_md5" ] || {
                echo 'Installed Gawk executable does not match the Gentoo package VDB.' >&2; exit 1;
            }
            gawk_retired_marker="$STATE/native-gentoo-gawk-mpfr-retired.done"
            if [ -s "$gawk_retired_marker" ]; then
                grep -Fxq "Gentoo Gawk package=$gawk_vdb_version" "$gawk_retired_marker" &&
                    grep -Fxq "source-built MPFR Gawk marker sha256=$(sha256 "$gawk_mpfr_marker")" \
                        "$gawk_retired_marker" && marker_driver_recorded "$gawk_retired_marker" || {
                    echo 'Gawk retirement marker no longer matches package ownership.' >&2; exit 1;
                }
                recorded_gawk_contents_hash=$(awk -F= '$1 == "Gentoo Gawk CONTENTS sha256" {n++; value=$2} END {if(n!=1) exit 1; print value}' \
                    "$gawk_retired_marker") || {
                    echo 'Gawk retirement marker has no unique Gentoo VDB digest.' >&2; exit 1;
                }
                current_gawk_contents_hash=$(sha256 "$gawk_vdb/CONTENTS")
                if [ "$recorded_gawk_contents_hash" != "$current_gawk_contents_hash" ]; then
                    refresh_stamp=$(date -u +%Y%m%dT%H%M%SZ)
                    cp -a "$gawk_retired_marker" "$gawk_retired_marker.before-vdb-refresh-$refresh_stamp"
                    recorded_gawk_md5=$(awk -F= '$1 == "installed executable md5" {n++; value=$2} END {if(n!=1) exit 1; print value}' \
                        "$gawk_retired_marker") || {
                        echo 'Gawk retirement marker has no unique executable digest.' >&2; exit 1;
                    }
                    printf 'complete %s\nsource-built MPFR Gawk marker sha256=%s\nGentoo Gawk package=%s\nGentoo Gawk repository=gentoo\nGentoo Gawk CONTENTS sha256=%s\nprevious Gentoo Gawk CONTENTS sha256=%s\ninstalled executable md5=%s\nprevious installed executable md5=%s\npolicy=source-built MPFR Gawk completed its GCC configure role; pinned Gentoo Gawk VDB and executable ownership revalidated after @system merge\ndriver sha256=%s\n' \
                        "$(date -u +%FT%TZ)" "$(sha256 "$gawk_mpfr_marker")" "$gawk_vdb_version" \
                        "$current_gawk_contents_hash" "$recorded_gawk_contents_hash" \
                        "$actual_gawk_md5" "$recorded_gawk_md5" "$driver_hash" \
                        > "$gawk_retired_marker.tmp"
                    sync "$gawk_retired_marker.tmp"
                    mv "$gawk_retired_marker.tmp" "$gawk_retired_marker"
                    sync "$STATE"
                fi
            else
                printf 'complete %s\nsource-built MPFR Gawk marker sha256=%s\nGentoo Gawk package=%s\nGentoo Gawk repository=gentoo\nGentoo Gawk CONTENTS sha256=%s\ninstalled executable md5=%s\npolicy=temporary MPFR-enabled source-built Gawk completed its GCC configure role; pinned Gentoo Gawk now owns /usr/bin/gawk\ndriver sha256=%s\n' \
                    "$(date -u +%FT%TZ)" "$(sha256 "$gawk_mpfr_marker")" \
                    "$gawk_vdb_version" "$(sha256 "$gawk_vdb/CONTENTS")" \
                    "$actual_gawk_md5" "$driver_hash" > "$gawk_retired_marker.tmp"
                sync "$gawk_retired_marker.tmp"
                mv "$gawk_retired_marker.tmp" "$gawk_retired_marker"
                sync "$STATE"
            fi
        else
            [ "$(sha256 "$NATIVE_ROOT/usr/bin/gawk")" = \
                "$(awk -F= '$1 == "Gawk binary sha256" {print $2}' "$gawk_mpfr_marker")" ] || {
                echo 'The temporary MPFR Gawk changed without a Portage-owned replacement.' >&2; exit 1;
            }
            chroot_env /usr/bin/gawk -M 'BEGIN { x = 2 ^ 100; if (x + 1 == x) exit 1; print "MPFR bignum passed" }' \
                | grep -Fxq 'MPFR bignum passed' || {
                echo 'The recorded MPFR Gawk runtime check no longer passes.' >&2; exit 1;
            }
            [ ! -e "$STATE/native-gentoo-gawk-mpfr-retired.done" ] || {
                echo 'Gawk retirement marker exists but Portage no longer owns the executable.' >&2; exit 1;
            }
        fi
    fi
    if [ -s "$system_started" ]; then
        echo 'Re-resolving the interrupted Portage @system merge with the verified native glibc runtime.'
        native_gentoo_glibc_runtime_version | grep -Eq '^2\.44' || {
            echo 'Refusing to resume @system without the verified source-built native glibc runtime.' >&2; exit 1;
        }
        run_emerge --pretend --tree @system | tee "$STATE/native-gentoo-at-system-resume-plan.txt"
        run_emerge --pretend --verbose --color=n @system |
            tee "$STATE/native-gentoo-at-system-resume-remaining.txt"
        # Resolve the actual current graph; historical log counts are not
        # transaction receipts and cannot establish that a merge completed.
        run_emerge --newuse --deep @system
    else
        printf 'starting %s\nGentoo repository commit=%s\nPortage config is source-only\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" > "$system_started.tmp"
        sync "$system_started.tmp"
        mv "$system_started.tmp" "$system_started"
        sync "$STATE"
        native_gentoo_glibc_runtime_version | grep -Eq '^2\.44' || {
            echo 'Refusing to start @system without the verified source-built native glibc runtime.' >&2; exit 1;
        }
        run_emerge --pretend --tree @system | tee "$STATE/native-gentoo-at-system-resume-plan.txt"
        run_emerge --newuse --deep @system
    fi

    # Keep cycle-breaking USE overrides in force while the initial @system
    # transaction installs Python, Meson/zstd and util-linux/PAM. Then retire
    # the exact, receipted override and let Portage resolve a second normal
    # @system pass. This checks whether the profile USE settings can converge
    # once the cycle's packages have real Gentoo VDB records.
    if [ -e "$python_cycle_file" ]; then
        [ -s "$python_cycle_marker" ] &&
            [ "$(cat "$python_cycle_file")" = "$python_cycle_content" ] &&
            grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$python_cycle_marker" &&
            grep -Fxq "Python ebuild sha256=$(sha256 "$python_cycle_ebuild")" "$python_cycle_marker" &&
            grep -Fxq "util-linux ebuild sha256=$(sha256 "$util_linux_ebuild")" "$python_cycle_marker" &&
            grep -Fxq "awk alternative ebuild sha256=$(sha256 "$awk_alternative_ebuild")" "$python_cycle_marker" &&
            grep -Fxq 'setting=dev-lang/python build;sys-apps/util-linux -pam -su;app-alternatives/awk -gawk mawk; breaks Gentoo bootstrap and first-system dependency cycles' "$python_cycle_marker" &&
            marker_driver_recorded "$python_cycle_marker" &&
            native_gentoo_glibc_runtime_version | grep -Eq '^2\.44' &&
                [ "$(chroot_env /usr/bin/portageq best_version / sys-libs/glibc)" = "sys-libs/glibc-$BOOTSTRAP_GLIBC_VERSION" ] || {
            echo 'Refusing to retire an unverified bootstrap cycle-break override.' >&2; exit 1;
        }
        python_cycle_file_hash=$(sha256 "$python_cycle_file")
        rm -f "$python_cycle_file"
        sync "$(dirname "$python_cycle_file")"
        printf 'retired %s\nGentoo repository commit=%s\nPython ebuild sha256=%s\nutil-linux ebuild sha256=%s\npackage.use sha256=%s\ncycle break retired after first multilib @system merge; Gentoo glibc=sys-libs/glibc-%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$(sha256 "$python_cycle_ebuild")" "$(sha256 "$util_linux_ebuild")" \
            "$python_cycle_file_hash" "$BOOTSTRAP_GLIBC_VERSION" > "$python_cycle_marker.retired.tmp"
        sync "$python_cycle_marker.retired.tmp"
        mv "$python_cycle_marker.retired.tmp" "$python_cycle_marker.retired"
        sync "$STATE"
        run_emerge --pretend --tree --newuse --deep @system \
            | tee "$STATE/native-gentoo-at-system-profile-use-plan.txt"
        run_emerge --newuse --deep @system
    fi
    installed_system_glibc=$(chroot_env /usr/bin/portageq best_version / sys-libs/glibc)
    [ "$installed_system_glibc" = "sys-libs/glibc-$BOOTSTRAP_GLIBC_VERSION" ] || {
        echo "Unexpected Gentoo glibc after @system merge: $installed_system_glibc" >&2; exit 1;
    }
    verify_native_gentoo_glibc || {
        echo 'Gentoo glibc installed by the first @system merge failed its runtime check.' >&2; exit 1;
    }
    chroot_env /usr/bin/python3.12 -c 'import math, portage; print("Gentoo glibc and source-built Portage Python ABI passed")'
    printf 'complete %s\nGentoo repository commit=%s\ninstalled Gentoo glibc=sys-libs/glibc-%s\nebuild sha256=%s\nglibc source Manifest sha512=%s\nglibc patches Manifest sha512=%s\nkeyword marker sha256=%s\nGentoo @system installed glibc; Python math and Portage imports=passed\nsource policy=Portage strict Manifest verification; ebuild tests disabled; --usepkg=n --getbinpkg=n\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$BOOTSTRAP_GLIBC_VERSION" \
        "$glibc_ebuild_hash" "$glibc_manifest_hash" "$glibc_patches_manifest_hash" \
        "$(sha256 "$glibc_keyword_marker")" "$driver_hash" > "$glibc_runtime_marker.tmp"
    sync "$glibc_runtime_marker.tmp"
    mv "$glibc_runtime_marker.tmp" "$glibc_runtime_marker"
    sync "$STATE"
    printf 'complete %s\nGentoo repository commit=%s\ninstalled Gentoo glibc=%s\nsource-built Portage Python math import=passed\nkeyword marker sha256=%s\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$installed_system_glibc" \
        "$(sha256 "$glibc_keyword_marker")" "$driver_hash" \
        > "$STATE/native-gentoo-glibc-portage-abi.done.tmp"
    sync "$STATE/native-gentoo-glibc-portage-abi.done.tmp"
    mv "$STATE/native-gentoo-glibc-portage-abi.done.tmp" "$STATE/native-gentoo-glibc-portage-abi.done"
    sync "$STATE"
    # The split-usr coreutils ebuild owns /bin/echo. Record that ownership as
    # soon as the successful system merge returns.
    echo_vdb=$(find "$NATIVE_ROOT/var/db/pkg/sys-apps" -mindepth 1 -maxdepth 1 \
        -type d -name 'coreutils-*' -print -quit 2>/dev/null || true)
    [ -n "$echo_vdb" ] &&
        awk '$2 == "/bin/echo" {found=1} END {exit !found}' "$echo_vdb/CONTENTS" &&
        [ -x "$NATIVE_ROOT/bin/echo" ] && [ ! -L "$NATIVE_ROOT/bin/echo" ] || {
        echo 'Gentoo coreutils did not take ownership of the tested /bin/echo path.' >&2; exit 1;
    }
    run_emerge --pretend --verbose --color=n @system | tee "$STATE/native-gentoo-at-system-remaining.txt"
    if ! plan_is_empty "$STATE/native-gentoo-at-system-remaining.txt"; then
        # Portage can keep reporting already-installed @system atoms as
        # reinstalls after the source pass, even when the selected version and
        # USE configuration exactly match the VDB. Accept only that narrow
        # case. New, updated, downgraded, or changed-USE work remains a hard
        # stop so a real convergence problem is never hidden.
        plan_is_only_installed_reinstalls "$STATE/native-gentoo-at-system-remaining.txt" || {
            echo 'Portage reports unresolved @system work beyond exact-version installed reinstalls.' >&2; exit 1;
        }
        pending_reinstalls=$(grep -c '^\[ebuild   R    \] ' "$STATE/native-gentoo-at-system-remaining.txt")
        echo "Portage retained $pending_reinstalls @system reinstall entries, all matching installed VDB versions with no USE delta; accepting the completed source pass."
    fi
    package_names=$(
        find "$NATIVE_ROOT/var/db/pkg" -mindepth 2 -maxdepth 2 -type d -printf '%P\n' |
            sort
    )
    package_count=$(printf '%s\n' "$package_names" | wc -l)
    package_digest=$(printf '%s\n' "$package_names" | sha256sum | awk '{print $1}')
    printf 'complete %s\nGentoo repository commit=%s\nPortage archive sha512=%s\nGentoo repository archive sha512=%s\nsystem lock sha256=%s\npackage database entries=%s\npackage names sha256=%s\nsource policy=strict Manifest verification; --usepkg=n --getbinpkg=n\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$portage_archive_hash" "$repo_archive_hash" \
        "$system_lock_hash" "$package_count" "$package_digest" > "$system_marker.tmp"
    sync "$system_marker.tmp"
    mv "$system_marker.tmp" "$system_marker"
    sync "$STATE"
fi
