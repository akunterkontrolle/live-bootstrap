# Seed scanelf and the Gentoo 32-bit startup layout before compiler bootstrap.
# Sourced by stages/80-native-gentoo-system.sh; shares its checked state.
# ncurses' gen_usr_ldscript and Portage's estrip require scanelf before the
# normal pax-utils/Meson/Python dependency graph can be installed. Seed the
# real ELF reader from the exact pinned pax-utils archive, then allow Gentoo
# to replace it with its complete package during @system.
PHASE=seed-source-built-scanelf
scanelf_marker="$STATE/native-gentoo-bootstrap-scanelf.done"
scanelf_helper="$SCRIPT_DIR/build-bootstrap-scanelf.sh"
scanelf_helper_hash=$(sha256 "$scanelf_helper")
scanelf_ebuild="$GENTOO_TREE/app-misc/pax-utils/pax-utils-1.3.11.ebuild"
[ -f "$scanelf_ebuild" ] || { echo 'Missing pinned pax-utils 1.3.11 ebuild.' >&2; exit 1; }
scanelf_ebuild_hash=$(sha256 "$scanelf_ebuild")
scanelf_source_hash=$(awk '$1 == "DIST" && $2 == "pax-utils-1.3.11.tar.xz" {
    n++; for(i=1;i<=NF;i++) if($i == "SHA512") digest=$(i+1)
} END {if(n != 1 || digest == "") exit 1; print digest}' \
    "$GENTOO_TREE/app-misc/pax-utils/Manifest") || {
    echo 'Pinned pax-utils Manifest lacks a unique source SHA-512.' >&2; exit 1;
}
scanelf_installed=$(chroot_env /usr/bin/portageq best_version / app-misc/pax-utils)
if [ "$scanelf_installed" = app-misc/pax-utils-1.3.11 ]; then
    scanelf_vdb="$NATIVE_ROOT/var/db/pkg/app-misc/pax-utils-1.3.11"
    scanelf_md5=$(awk '$1 == "obj" && $2 == "/usr/bin/scanelf" {n++; value=$3}
        END {if(n != 1) exit 1; print value}' "$scanelf_vdb/CONTENTS") || {
        echo 'Installed pax-utils lacks a unique scanelf ownership record.' >&2; exit 1;
    }
    [ "$(md5sum "$NATIVE_ROOT/usr/bin/scanelf" | awk '{print $1}')" = "$scanelf_md5" ] || {
        echo 'Installed scanelf differs from the Gentoo package ownership record.' >&2; exit 1;
    }
elif [ -s "$scanelf_marker" ]; then
    grep -Fxq "Gentoo repository commit=$REPO_COMMIT" "$scanelf_marker" &&
        grep -Fxq "source SHA-512=$scanelf_source_hash" "$scanelf_marker" &&
        grep -Fxq "ebuild sha256=$scanelf_ebuild_hash" "$scanelf_marker" &&
        grep -Fxq "helper sha256=$scanelf_helper_hash" "$scanelf_marker" &&
        grep -Fxq "scanelf binary sha256=$(sha256 "$NATIVE_ROOT/usr/bin/scanelf")" "$scanelf_marker" &&
        marker_driver_recorded "$scanelf_marker" || {
        echo 'Bootstrap scanelf does not match its verified source receipt.' >&2; exit 1;
    }
else
    run_emerge --fetchonly --nodeps =app-misc/pax-utils-1.3.11
    printf '%s  %s\n' "$scanelf_source_hash" "$SCRATCH/distfiles/pax-utils-1.3.11.tar.xz" | \
        sha512sum --check --status || {
        echo 'pax-utils source differs from the pinned Gentoo Manifest.' >&2; exit 1;
    }
    scanelf_source="$NATIVE_ROOT/usr/src/pax-utils-1.3.11-bootstrap-scanelf"
    mkdir -p "$scanelf_source"
    tar -xJf "$SCRATCH/distfiles/pax-utils-1.3.11.tar.xz" --strip-components=1 -C "$scanelf_source"
    install -m 0755 "$scanelf_helper" "$NATIVE_ROOT/tmp/build-bootstrap-scanelf.sh"
    chroot_env /bin/bash /tmp/build-bootstrap-scanelf.sh \
        /usr/src/pax-utils-1.3.11-bootstrap-scanelf /usr/src/bootstrap-scanelf-build
    install -m 0755 "$NATIVE_ROOT/usr/src/bootstrap-scanelf-build/scanelf" "$NATIVE_ROOT/usr/bin/scanelf"
    printf 'complete %s\nGentoo repository commit=%s\nsource SHA-512=%s\nebuild sha256=%s\nhelper sha256=%s\nscanelf binary sha256=%s\nverification=ELF32 and ELF64 SONAME probes passed\npolicy=source-built scanelf only; complete Gentoo pax-utils remains in normal system graph\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$REPO_COMMIT" "$scanelf_source_hash" "$scanelf_ebuild_hash" \
        "$scanelf_helper_hash" "$(sha256 "$NATIVE_ROOT/usr/bin/scanelf")" "$driver_hash" \
        > "$scanelf_marker.tmp"
    sync "$NATIVE_ROOT/usr/bin/scanelf" "$scanelf_marker.tmp"
    mv "$scanelf_marker.tmp" "$scanelf_marker"
    sync "$STATE"
fi
chroot_env /usr/bin/scanelf --version

# The source-built seed uses the older lib32 layout. Profile 23.0 GCC uses
# /usr/lib for its 32-bit startup objects even before Gentoo glibc is merged.
# Copy only verified seed startup/linker inputs, never replace existing files.
PHASE=prepare-seed-glibc32-startup-layout
if [ -z "$(chroot_env /usr/bin/portageq best_version / sys-libs/glibc)" ]; then
    seed_glibc32_dir="$WORK_ROOT/sysroot-x86_64/usr/lib32"
    seed_glibc32_receipt="$STATE/native-gentoo-seed-glibc32-startup-layout.done"
    for name in crt1.o crti.o crtn.o libc.so libc_nonshared.a; do
        [ -f "$seed_glibc32_dir/$name" ] || {
            echo "Verified seed glibc32 input is missing: $name" >&2; exit 1;
        }
    done
    for name in crt1.o crti.o crtn.o Scrt1.o rcrt1.o gcrt1.o libc.so libc_nonshared.a; do
        [ -f "$seed_glibc32_dir/$name" ] || continue
        seed_input="$seed_glibc32_dir/$name"
        native_input="$NATIVE_ROOT/usr/lib32/$name"
        target_input="$NATIVE_ROOT/usr/lib/$name"
        cmp -s "$seed_input" "$native_input" || {
            echo "Native glibc32 input differs from the source-built sysroot: $name" >&2; exit 1;
        }
        if [ -e "$target_input" ] || [ -L "$target_input" ]; then
            [ ! -L "$target_input" ] && cmp -s "$seed_input" "$target_input" || {
                echo "Refusing to replace an unexpected 32-bit startup input: $target_input" >&2; exit 1;
            }
        else
            install -m 0644 "$seed_input" "$target_input"
        fi
    done
    chroot_env /usr/bin/readelf -h /usr/lib/crti.o | grep -Eq 'Class:[[:space:]]+ELF32' || {
        echo 'Canonical seed crti.o is not ELF32.' >&2; exit 1;
    }
    printf 'complete %s\nseed sysroot marker sha256=%s\ncrti.o sha256=%s\nlayout=/usr/lib32 seed startup inputs exposed in /usr/lib until Gentoo glibc replaces them\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$(sha256 "$STATE/19-glibc-64.done")" \
        "$(sha256 "$NATIVE_ROOT/usr/lib/crti.o")" "$driver_hash" > "$seed_glibc32_receipt.tmp"
    sync "$seed_glibc32_receipt.tmp"
    mv "$seed_glibc32_receipt.tmp" "$seed_glibc32_receipt"
fi

bootstrap_hash=$(sha256 "$GENTOO_TREE/scripts/bootstrap.sh")
progress="$NATIVE_ROOT/var/run/bootstrap-progress"
grep -Fxq 'ABI_X86="64 32"' "$NATIVE_ROOT/etc/portage/make.conf" || {
    echo 'Refusing Gentoo bootstrap before multilib ABI_X86=64 32 is active.' >&2; exit 1;
}
grep -Fxq "active profile=$gentoo_profile_path" "$multilib_profile_marker" || {
    echo 'Refusing Gentoo bootstrap without the pinned multilib profile marker.' >&2; exit 1;
}
# The pass-2 compiler is deliberately built with --disable-libgomp. Gettext
# must build before Gentoo GCC supplies that runtime. Scope the exception to
# gettext, keeping OpenMP enabled for the compiler that replaces the seed.
bootstrap_openmp_file="$NATIVE_ROOT/etc/portage/package.use/86-native-bootstrap-openmp"
bootstrap_openmp_content=$(printf 'sys-devel/gettext -openmp\nsys-devel/gcc openmp\n')
if [ -e "$bootstrap_openmp_file" ]; then
    [ -f "$bootstrap_openmp_file" ] && [ ! -L "$bootstrap_openmp_file" ] &&
        [ "$(cat "$bootstrap_openmp_file")" = "$bootstrap_openmp_content" ] || {
        echo 'Unexpected bootstrap OpenMP override; refusing to replace it.' >&2; exit 1;
    }
fi
