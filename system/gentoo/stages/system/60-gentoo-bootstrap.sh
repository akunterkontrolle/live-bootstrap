# Fetch and run the pinned Gentoo bootstrap, preserving upstream progress on interruption.
# Sourced by stages/80-native-gentoo-system.sh; shares its checked state.
if [ ! -s "$bootstrap_marker" ]; then
    mkdir -p "$(dirname "$bootstrap_openmp_file")"
    printf '%s\n' "$bootstrap_openmp_content" > "$bootstrap_openmp_file.tmp"
    chmod 0644 "$bootstrap_openmp_file.tmp"
    sync "$bootstrap_openmp_file.tmp"
    mv "$bootstrap_openmp_file.tmp" "$bootstrap_openmp_file"
fi
PHASE=run-pinned-gentoo-bootstrap
if [ -s "$bootstrap_marker" ]; then
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$bootstrap_marker" &&
        grep -Fxq "upstream script sha256=$bootstrap_hash" "$bootstrap_marker" &&
        grep -Fxq "system lock sha256=$system_lock_hash" "$bootstrap_marker" &&
        grep -Fxq "bootstrap providers marker sha256=$(sha256 "$provider_marker")" "$bootstrap_marker" || {
        echo 'Bootstrap marker belongs to different pinned source inputs.' >&2; exit 1;
    }
else
    bootstrap_fetch_marker="$STATE/native-gentoo-bootstrap-sources-fetched.done"
    if [ ! -s "$bootstrap_fetch_marker" ]; then
        PHASE=prefetch-pinned-gentoo-bootstrap
        run_gentoo_bootstrap --fetchonly
        printf 'complete %s\nGentoo repository commit=%s\nupstream script sha256=%s\nsystem lock sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$bootstrap_hash" "$system_lock_hash" \
            > "$bootstrap_fetch_marker.tmp"
        sync "$bootstrap_fetch_marker.tmp"
        mv "$bootstrap_fetch_marker.tmp" "$bootstrap_fetch_marker"
        sync "$STATE"
    fi
    ensure_gentoo_mpfr_gawk
    bootstrap_resume=started-by-driver
    if [ -s "$progress" ]; then
        progress_value=$(cat "$progress")
        case "$progress_value" in
            BOOTSTRAP_STAGE=1) ;;
            # Input bundle is immutable, so upstream's stored resume graph
            # still belongs to these settings and can be resumed unchanged.
            BOOTSTRAP_STAGE=2) ;;
            BOOTSTRAP_STAGE=3)
                # Gentoo writes stage 3 only after its package emerge succeeds.
                # The following cleanup is small; the regular @system merge
                # applies the final base-profile USE settings afterward.
                echo 'Adopting upstream bootstrap stage 3 after a wrapper interruption.'
                bootstrap_resume=adopted-upstream-stage-3
                ;;
            *) echo "Unexpected bootstrap progress state: $progress_value" >&2; exit 1 ;;
        esac
    fi
    if [ "$bootstrap_resume" = started-by-driver ]; then
        printf 'starting %s\nGentoo repository commit=%s\nupstream script sha256=%s\nsystem lock sha256=%s\n' \
            "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$bootstrap_hash" "$system_lock_hash" \
            > "$STATE/native-gentoo-bootstrap.started.tmp"
        sync "$STATE/native-gentoo-bootstrap.started.tmp"
        mv "$STATE/native-gentoo-bootstrap.started.tmp" "$STATE/native-gentoo-bootstrap.started"
        sync "$STATE"
        run_gentoo_bootstrap
    fi
    [ "$(cat "$progress" 2>/dev/null || true)" = BOOTSTRAP_STAGE=3 ] || {
        echo 'Pinned Gentoo bootstrap exited without reaching its upstream stage-3 state.' >&2; exit 1;
    }
    if grep -q '^BOOTSTRAP_USE=' "$NATIVE_ROOT/etc/portage/make.conf"; then
        [ -n "$bootstrap_use_conf_line" ] &&
            grep -Fxq "$bootstrap_use_conf_line" "$NATIVE_ROOT/etc/portage/make.conf" || {
            echo 'Refusing to remove an unexpected bootstrap USE override.' >&2; exit 1;
        }
        sed -i '/^BOOTSTRAP_USE=/d' "$NATIVE_ROOT/etc/portage/make.conf"
    fi
    if grep -Fxq 'USE="-test -nls"' "$NATIVE_ROOT/etc/portage/make.conf"; then
        sed -i 's/^USE="-test -nls"$/USE="-test"/' "$NATIVE_ROOT/etc/portage/make.conf"
    fi
    grep -Fxq 'USE="-test"' "$NATIVE_ROOT/etc/portage/make.conf" || {
        echo 'Could not restore normal profile USE before Gentoo @system.' >&2; exit 1;
    }
    if grep -Fxq 'ABI_X86="64"' "$NATIVE_ROOT/etc/portage/make.conf"; then
        sed -i 's/^ABI_X86="64"$/ABI_X86="64 32"/' "$NATIVE_ROOT/etc/portage/make.conf"
    fi
    grep -Fxq 'ABI_X86="64 32"' "$NATIVE_ROOT/etc/portage/make.conf" || {
        echo 'Could not restore the selected amd64 multilib ABI before Gentoo @system.' >&2; exit 1;
    }
    printf 'complete %s\nGentoo repository commit=%s\nGentoo source archive sha512=%s\nPortage archive sha512=%s\nupstream script sha256=%s\nsystem lock sha256=%s\nruntime source lock sha256=%s\nbootstrap completion evidence=%s\nbootstrap USE policy=global -nls only until upstream stage 3; restored to base-profile USE before @system\nbootstrap ABI policy=ABI_X86=64 32 enabled from first bootstrap merge through @system\nbootstrap providers marker sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$repo_archive_hash" "$portage_archive_hash" \
        "$bootstrap_hash" "$system_lock_hash" "$runtime_lock_hash" "$bootstrap_resume" \
        "$(sha256 "$provider_marker")" \
        > "$bootstrap_marker.tmp"
    sync "$bootstrap_marker.tmp"
    mv "$bootstrap_marker.tmp" "$bootstrap_marker"
    sync "$STATE"
fi
