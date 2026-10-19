# Expose locked seed runtimes temporarily; retire their claims after genuine Gentoo merges.
# Sourced by stages/80-native-gentoo-system.sh; shares its checked state.
# The pinned 2026 profile forces ELIBC=glibc, so its first GCC/glibc and
# Perl/libxcrypt graphs contain cycles while the Gentoo package database is
# empty. Portage's package.provided supports source-built host prerequisites.
# We expose only the exact glibc 2.44 and Perl 5.42.2 already built from locked
# source in this root. Drop the glibc claim after bootstrap so normal @system
# can install its genuine Gentoo package; keep the working seed libraries.
# Retire the Perl claim after @system. The installed system inherits no claims.
bootstrap_marker="$STATE/native-gentoo-bootstrap.done"
provider_file="$NATIVE_ROOT/etc/portage/profile/package.provided"
provider_marker="$STATE/native-gentoo-bootstrap-providers.done"
glibc_provider_removed_marker="$STATE/native-gentoo-bootstrap-glibc-provider.removed"
perl_provider_removed_marker="$STATE/native-gentoo-bootstrap-perl-provider.removed"
system_marker="$STATE/native-gentoo-at-system-source-built.done"
glibc_provider_entry='sys-libs/glibc-2.44'
perl_provider_entry='dev-lang/perl-5.42.2'
perl_only_provider_hash=$(printf '# Temporary source-built Perl provider for @system dependency ordering.\n%s\n' \
    "$perl_provider_entry" | sha256sum | awk '{print $1}')
native_glibc_marker="$STATE/19-glibc-64.done"
perl_marker="$STATE/70-perl.done"
runtime_lock_hash=$(sha256 "$RUNTIME_LOCK")
glibc_source_hash=$(lock_entry glibc-2.44.tar.xz "$BASE_SOURCES_LOCK")
glibc_source_hash=${glibc_source_hash##*sha512=}
perl_source_hash=$(lock_entry perl-5.42.2.tar.xz "$RUNTIME_LOCK")
perl_source_hash=${perl_source_hash##*sha512=}
 [ -s "$native_glibc_marker" ] && [ -s "$STATE/20-sysroot-links.done" ] || {
    echo 'Native glibc provider lacks its completed, source-locked sysroot lineage.' >&2; exit 1;
}
grep -Fxq "source sha512=$perl_source_hash" "$perl_marker" || {
    echo 'Native Perl provider lacks its source-locked seed lineage.' >&2; exit 1;
}
verify_locked glibc-2.44.tar.xz "$BASE_SOURCES_LOCK"
verify_locked perl-5.42.2.tar.xz "$RUNTIME_LOCK"
native_runtime=$(native_gentoo_glibc_runtime_version) &&
    chroot_env /usr/bin/python3.12 -c 'import math, portage; print("Native Portage Python imports passed")' &&
    case "$native_runtime" in 2.44*) printf 'Native glibc runtime=%s\n' "$native_runtime";; *) false;; esac || {
    echo 'Native-root glibc does not match the source-locked 2.44 provider.' >&2; exit 1;
}
# Gentoo may have replaced the seed during an interrupted @system merge.
# Keep checking historical source receipts above, but validate the current
# package-owned executable rather than demanding the retired seed runtime.
provider_gentoo_perl=$(chroot_env /usr/bin/portageq best_version / dev-lang/perl 2>/dev/null || true)
if [ -n "$provider_gentoo_perl" ]; then
    [[ "$provider_gentoo_perl" =~ ^dev-lang/perl-([0-9]+[.][0-9]+[.][0-9]+)(-r[0-9]+)?$ ]] || {
        echo 'Unexpected installed Gentoo Perl identity.' >&2; exit 1;
    }
    provider_perl_version=${BASH_REMATCH[1]}
    provider_perl_vdb="$NATIVE_ROOT/var/db/pkg/$provider_gentoo_perl"
    [ "$(cat "$provider_perl_vdb/repository" 2>/dev/null)" = gentoo ] || {
        echo 'Installed Perl is not owned by the pinned Gentoo repository.' >&2; exit 1;
    }
    provider_perl_md5=$(awk '$1=="obj" && $2=="/usr/bin/perl" {count++; hash=$3}
        END {if(count!=1) exit 1; print hash}' "$provider_perl_vdb/CONTENTS") || {
        echo 'Gentoo Perl lacks a unique executable ownership record.' >&2; exit 1;
    }
    [ "$(md5sum "$NATIVE_ROOT/usr/bin/perl" | awk '{print $1}')" = "$provider_perl_md5" ] &&
        chroot_env /usr/bin/perl -MConfig -e 'printf "%vd\n", $^V' |
            grep -Fxq "$provider_perl_version" || {
        echo 'Installed Perl does not match its Gentoo package/runtime.' >&2; exit 1;
    }
else
    [ -s "$NATIVE_ROOT/usr/lib/perl5/5.42.2/x86_64-linux/CORE/libperl.so" ] &&
        chroot_env /usr/bin/perl -e 'printf "%vd\n", $^V' | grep -Fxq '5.42.2' || {
        echo 'The source-built native Perl 5.42.2 runtime did not execute with its verified shared library.' >&2; exit 1;
    }
fi
retire_bootstrap_glibc_provider() {
    [ -s "$provider_marker" ] || return 0
    # Remove the temporary package.provided claim, not the working libraries.
    # Keeping the claim until Gentoo glibc is installed hides that package from
    # @system. The locked seed runtime is checked above; phase 70 verifies the
    # genuine Gentoo glibc after its merge, before accepting the system.
    [ -s "$bootstrap_marker" ] || {
        echo 'Refusing to retire the glibc provider before bootstrap completion.' >&2
        return 1
    }
    expected_provider_hash=$(awk -F= '$1 == "provider file sha256" {n++; value=$2} END {if(n!=1) exit 1; print value}' \
        "$provider_marker") || { echo 'Malformed bootstrap-provider marker.' >&2; return 1; }
    if [ -s "$glibc_provider_removed_marker" ]; then
        remaining_hash=$(awk -F= '$1 == "remaining provider file sha256" {n++; value=$2} END {if(n!=1) exit 1; print value}' \
            "$glibc_provider_removed_marker") || { echo 'Malformed glibc-provider retirement marker.' >&2; return 1; }
        if [ -s "$perl_provider_removed_marker" ]; then
            grep -Fxq "remaining provider file sha256=$remaining_hash" "$perl_provider_removed_marker" &&
                grep -Fxq "Gentoo @system marker sha256=$(sha256 "$system_marker")" \
                    "$perl_provider_removed_marker" &&
                [ ! -e "$provider_file" ] || {
                echo 'Completed Perl-provider retirement does not match the glibc-provider record.' >&2; return 1;
            }
            return 0
        fi
        [ -f "$provider_file" ] && [ "$(sha256 "$provider_file")" = "$remaining_hash" ] &&
            ! grep -Fxq "$glibc_provider_entry" "$provider_file" &&
            grep -Fxq "$perl_provider_entry" "$provider_file" || {
            echo 'Remaining Perl provider does not match glibc-provider retirement state.' >&2; return 1;
        }
        return 0
    fi
    if [ -f "$provider_file" ]; then
        current_provider_hash=$(sha256 "$provider_file")
        if [ "$current_provider_hash" = "$expected_provider_hash" ]; then
            grep -Fxq "$glibc_provider_entry" "$provider_file" &&
                grep -Fxq "$perl_provider_entry" "$provider_file" || {
                echo 'Refusing to remove a changed bootstrap-provider file.' >&2; return 1;
            }
            printf '# Temporary source-built Perl provider for @system dependency ordering.\n%s\n' \
                "$perl_provider_entry" > "$provider_file.retiring"
            chmod 0644 "$provider_file.retiring"
            sync "$provider_file.retiring"
            mv "$provider_file.retiring" "$provider_file"
        elif [ "$current_provider_hash" != "$perl_only_provider_hash" ] ||
            grep -Fxq "$glibc_provider_entry" "$provider_file" ||
            ! grep -Fxq "$perl_provider_entry" "$provider_file"; then
            echo 'Refusing to transition a changed bootstrap-provider file.' >&2; return 1
        fi
    elif [ -e "$provider_file" ]; then
        echo 'Refusing to remove a non-file bootstrap-provider path.' >&2; return 1
    else
        echo 'Full bootstrap provider file is missing before Gentoo glibc installation.' >&2; return 1
    fi
    printf 'retired %s\nfull provider file sha256=%s\nremaining provider file sha256=%s\nbootstrap marker sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$expected_provider_hash" "$(sha256 "$provider_file")" \
        "$(sha256 "$bootstrap_marker")" > "$glibc_provider_removed_marker.tmp"
    sync "$glibc_provider_removed_marker.tmp"
    mv "$glibc_provider_removed_marker.tmp" "$glibc_provider_removed_marker"
    sync "$STATE"
}
retire_bootstrap_perl_provider() {
    [ -s "$provider_marker" ] || return 0
    remaining_hash=$(awk -F= '$1 == "remaining provider file sha256" {n++; value=$2} END {if(n!=1) exit 1; print value}' \
        "$glibc_provider_removed_marker") || { echo 'Missing glibc-provider retirement record.' >&2; return 1; }
    if [ -s "$perl_provider_removed_marker" ]; then
        grep -Fxq "remaining provider file sha256=$remaining_hash" "$perl_provider_removed_marker" &&
            grep -Fxq "Gentoo @system marker sha256=$(sha256 "$system_marker")" "$perl_provider_removed_marker" &&
            [ ! -e "$provider_file" ] || {
            echo 'Perl-provider removal marker is inconsistent.' >&2; return 1;
        }
        return 0
    fi
    if [ -f "$provider_file" ]; then
        [ "$(sha256 "$provider_file")" = "$remaining_hash" ] &&
            grep -Fxq "$perl_provider_entry" "$provider_file" || {
            echo 'Refusing to remove a modified Perl provider.' >&2; return 1;
        }
        rm -f "$provider_file"
    elif [ -e "$provider_file" ]; then
        echo 'Refusing to remove a non-file Perl provider path.' >&2; return 1
    fi
    printf 'removed %s\nremaining provider file sha256=%s\nGentoo @system marker sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$remaining_hash" "$(sha256 "$system_marker")" \
        > "$perl_provider_removed_marker.tmp"
    sync "$perl_provider_removed_marker.tmp"
    mv "$perl_provider_removed_marker.tmp" "$perl_provider_removed_marker"
    sync "$STATE"
}

if [ -s "$provider_marker" ]; then
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$provider_marker" &&
        grep -Fxq "provided packages=$glibc_provider_entry,$perl_provider_entry" "$provider_marker" &&
        grep -Fxq "source glibc SHA-512=$glibc_source_hash" "$provider_marker" &&
        grep -Fxq "source Perl SHA-512=$perl_source_hash" "$provider_marker" &&
        grep -Fxq "native glibc marker sha256=$(sha256 "$native_glibc_marker")" "$provider_marker" &&
        grep -Fxq "native Perl marker sha256=$(sha256 "$perl_marker")" "$provider_marker" &&
        marker_driver_recorded "$provider_marker" || {
        echo 'Bootstrap providers do not match their source-built runtime markers.' >&2; exit 1;
    }
    expected_provider_hash=$(awk -F= '$1 == "provider file sha256" {n++; value=$2} END {if(n!=1) exit 1; print value}' \
        "$provider_marker") || { echo 'Malformed bootstrap-provider file digest.' >&2; exit 1; }
    if [ -s "$system_marker" ]; then
        if [ -s "$perl_provider_removed_marker" ]; then
            grep -Fxq "Gentoo @system marker sha256=$(sha256 "$system_marker")" "$perl_provider_removed_marker" &&
                [ ! -e "$provider_file" ] || {
                echo 'Removed Perl provider does not match the completed @system marker.' >&2; exit 1;
            }
        elif [ -e "$provider_file" ]; then
            [ -f "$provider_file" ] &&
                [ "$(sha256 "$provider_file")" = "$perl_only_provider_hash" ] &&
                grep -Fxq "$perl_provider_entry" "$provider_file" || {
                echo 'Interrupted Perl-provider retirement left an unexpected provider file.' >&2; exit 1;
            }
        fi
    elif [ -s "$bootstrap_marker" ]; then
        if [ -s "$glibc_provider_removed_marker" ]; then
            remaining_hash=$(awk -F= '$1 == "remaining provider file sha256" {n++; value=$2} END {if(n!=1) exit 1; print value}' \
                "$glibc_provider_removed_marker") || { echo 'Malformed bootstrap glibc-provider retirement record.' >&2; exit 1; }
            [ -f "$provider_file" ] && [ "$(sha256 "$provider_file")" = "$remaining_hash" ] &&
                ! grep -Fxq "$glibc_provider_entry" "$provider_file" &&
                grep -Fxq "$perl_provider_entry" "$provider_file" || {
                echo 'Perl-only provider state does not match the completed bootstrap.' >&2; exit 1;
            }
        elif [ -f "$provider_file" ] && [ "$(sha256 "$provider_file")" = "$perl_only_provider_hash" ]; then
            # The process may have stopped after the atomic provider-file
            # replacement and before recording its retirement marker.
            grep -Fxq "$perl_provider_entry" "$provider_file" || {
                echo 'Interrupted provider transition has no verified Perl entry.' >&2; exit 1;
            }
        else
            [ -f "$provider_file" ] &&
                [ "$(sha256 "$provider_file")" = "$expected_provider_hash" ] &&
                grep -Fxq "$glibc_provider_entry" "$provider_file" &&
                grep -Fxq "$perl_provider_entry" "$provider_file" || {
                echo 'Full provider state is inconsistent with the completed bootstrap.' >&2; exit 1;
            }
        fi
    else
        [ -f "$provider_file" ] &&
            [ "$(sha256 "$provider_file")" = "$expected_provider_hash" ] &&
            grep -Fxq "$glibc_provider_entry" "$provider_file" &&
            grep -Fxq "$perl_provider_entry" "$provider_file" || {
            echo 'Temporary bootstrap providers are missing or were modified.' >&2; exit 1;
        }
    fi
elif [ ! -s "$glibc_provider_removed_marker" ] && [ ! -s "$system_marker" ]; then
    mkdir -p "$(dirname "$provider_file")"
    # A power loss can leave the canonical file before its receipt rename.
    # Adopt only those exact bytes, after the locked runtime checks above.
    printf '# Temporary providers for the source-built Gentoo bootstrap only.\n%s\n%s\n' \
        "$glibc_provider_entry" "$perl_provider_entry" > "$provider_file.tmp"
    if [ -e "$provider_file" ]; then
        [ -f "$provider_file" ] && cmp -s "$provider_file.tmp" "$provider_file" || {
            echo 'Unexpected Portage package.provided contents.' >&2; exit 1;
        }
    fi
    chmod 0644 "$provider_file.tmp"
    mv "$provider_file.tmp" "$provider_file"
    printf 'complete %s\nGentoo repository commit=%s\nprovided packages=%s,%s\nsource glibc SHA-512=%s\nsource Perl SHA-512=%s\nnative glibc marker sha256=%s\nnative Perl marker sha256=%s\nprovider file sha256=%s\npolicy=temporary package.provided entries for source-built host runtimes; removed after Gentoo @system\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$glibc_provider_entry" "$perl_provider_entry" \
        "$glibc_source_hash" "$perl_source_hash" "$(sha256 "$native_glibc_marker")" \
        "$(sha256 "$perl_marker")" "$(sha256 "$provider_file")" "$driver_hash" \
        > "$provider_marker.tmp"
    sync "$provider_file" "$provider_marker.tmp"
    mv "$provider_marker.tmp" "$provider_marker"
    sync "$STATE"
fi
if [ -s "$bootstrap_marker" ]; then
    retire_bootstrap_glibc_provider
fi
if [ -s "$system_marker" ]; then
    retire_bootstrap_perl_provider
elif [ -s "$perl_provider_removed_marker" ]; then
    echo 'The Perl bootstrap provider was removed before the Gentoo @system completion marker.' >&2; exit 1
fi

PHASE=prepare-source-built-grep-test-support
# The seed installs tools under /usr/bin; the split-usr Gentoo environment
# also needs the standard /bin entry points. Gentoo takes ownership at merge.
for tool in grep echo; do
    path="$NATIVE_ROOT/bin/$tool"
    if [ -e "$path" ] || [ -L "$path" ]; then
        [ -x "$path" ] || { echo "Unusable bootstrap tool: $path" >&2; exit 1; }
    else
        [ -x "$NATIVE_ROOT/usr/bin/$tool" ] || {
            echo "Missing source-built bootstrap tool: $tool" >&2; exit 1;
        }
        ln -s "../usr/bin/$tool" "$path"
    fi
    chroot_env "/bin/$tool" --version >/dev/null
 done
