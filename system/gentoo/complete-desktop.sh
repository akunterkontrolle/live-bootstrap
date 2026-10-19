#!/bin/bash
set -Eeuo pipefail
umask 077

# Run manually from the encrypted installed Gentoo system, after its encrypted
# base generation is booted and encrypted /extra has been unlocked. Portage
# keeps distfiles, temporary files, and logs on /extra so an interrupted fetch
# or build can resume without consuming the root rollback reserve.

SCRIPT_PATH=$(readlink -f -- "$0")
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd)
. "$SCRIPT_DIR/config.sh"
. "$SCRIPT_DIR/lib/repository-policy.sh"
STATE_DIR=/var/lib/ms1504-gentoo
STATE="$STATE_DIR/desktop"
LOCK=/run/lock/ms1504-complete-desktop.lock
REPO=/usr/portage
PROFILE=/etc/portage/make.profile
PINNED_PROFILE=/usr/portage/profiles/$GENTOO_BASE_PROFILE
DESKTOP_PROFILE=/usr/portage/profiles/$GENTOO_DESKTOP_PROFILE
SOURCE_RECEIPT=/usr/share/gentoo-continuation/gentoo-source-receipt
LOG_DIR=/extra/portage/logs
DISTDIR=/extra/portage/distfiles
PORTAGE_TMPDIR=/extra/portage/tmp

die() { printf 'complete-desktop: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required command is missing: $1"; }
sha256() { sha256sum "$1" | awk '{print $1}'; }
tree_hash() {
    (
        cd "$1"
        {
            find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
            LC_ALL=C find . -type f -printf 'F %m %p\n' | LC_ALL=C sort
            LC_ALL=C find . -type d -printf 'D %m %p\n' | LC_ALL=C sort
            LC_ALL=C find . -type l -printf 'L %m %p -> %l\n' | LC_ALL=C sort
        } | sha256sum | awk '{print $1}'
    )
}

[ "$(id -u)" -eq 0 ] || die 'run as root (use sudo).'
for command in awk bash chmod cryptsetup date emerge find findmnt flock install lvs mountpoint portageq python3 readlink rc-update sed sha256sum sleep sort sync tee xargs; do need "$command"; done
python3 "$SCRIPT_DIR/cpu-policy.py" --receipt /etc/portage/native-cpu-policy.json \
    --detector /usr/bin/cpuid2cpuflags --compiler /usr/bin/gcc
[ -x /usr/bin/gcc ] && [ -x /usr/bin/g++ ] || die 'source-built multilib GCC is missing.'
[ -x /usr/bin/emerge ] && [ -d "$REPO/profiles" ] || die 'installed pinned Gentoo tree or Portage is missing.'
[ -s "$SOURCE_RECEIPT" ] || die "pinned Gentoo source receipt is missing: $SOURCE_RECEIPT."
pinned_commit=$(gentoo_repository_commit "$SCRIPT_DIR/sources/gentoo-repository.lock") || die 'invalid repository lock'
grep -Fxq "repository commit=$pinned_commit" "$SOURCE_RECEIPT" || die 'source receipt has the wrong Gentoo commit.'
grep -Fxq 'repository tree sha256='"$(tree_hash "$REPO")" "$SOURCE_RECEIPT" ||
    die 'installed Gentoo tree identity differs from its source receipt.'
[ -L "$PROFILE" ] || die 'Portage profile is not a symlink; inspect it before proceeding.'
current_profile=$(readlink "$PROFILE")
case "$current_profile" in
    "$PINNED_PROFILE"|"$DESKTOP_PROFILE") ;;
    *) die "unexpected starting profile: $current_profile" ;;
esac
[ -d "$DESKTOP_PROFILE" ] || die 'pinned desktop profile is missing.'
grep -Fxq "profile=$GENTOO_MODE" /etc/ms1504-root-identity || die 'installed mode identity differs from the input bundle.'
GENERATION_STATE=
if [ "$GENTOO_MODE" = hardware ]; then
    need ms1504-signed-generation
    [ -r /etc/ms1504-signed-generation.toml ] || die 'hardware desktop updates require the signed-generation configuration.'
    GENERATION_STATE=$(python3 -c 'import pathlib, sys, tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())["state"]["directory"])' \
        /etc/ms1504-signed-generation.toml) || die 'cannot read the signed-generation state directory.'
    [ -d "$GENERATION_STATE/pending" ] || die 'run ms1504-signed-generation begin before changing hardware packages.'
    ms1504-signed-generation status || die 'signed-generation status check failed.'
fi

mountpoint -q /extra || die '/extra is not mounted; unlock and mount the encrypted source/build disk first.'
extra_source=$(findmnt -rn -M /extra -o SOURCE)
[ -n "$extra_source" ] || die 'cannot identify the /extra mount source.'
case "$extra_source" in
    /dev/mapper/*) ;;
    *) die "/extra is not backed by an encrypted mapper device: $extra_source" ;;
esac
cryptsetup status "${extra_source#/dev/mapper/}" >/dev/null 2>&1 ||
    die '/extra mapper is not an active cryptsetup mapping.'
mkdir -p "$STATE" "$LOG_DIR" "$DISTDIR" "$PORTAGE_TMPDIR" /run/lock
chmod 0700 "$STATE"
# Portage drops privileges when fetching sources and reading ebuild metadata.
# Its user needs access through /extra and ownership of the build directories.
chmod 0755 /extra /extra/portage
install -d -o portage -g portage -m 2775 "$LOG_DIR" "$DISTDIR" "$PORTAGE_TMPDIR"
exec 9>"$LOCK"
flock -n 9 || die 'another desktop completion process is already running.'
exec > >(tee -a "$LOG_DIR/complete-desktop.log") 2>&1

PACKAGE_LIST=$SCRIPT_DIR/${GENTOO_PACKAGES_DESKTOP:-desktop-packages.list}
PACKAGE_USE=$SCRIPT_DIR/${GENTOO_PACKAGES_DESKTOP_USE:-desktop-package.use}
PACKAGE_ACCEPT_KEYWORDS=$SCRIPT_DIR/${GENTOO_PACKAGES_DESKTOP_KEYWORDS:-desktop-package.accept_keywords}
PACKAGE_MASK=$SCRIPT_DIR/${GENTOO_PACKAGES_DESKTOP_MASK:-desktop-package.mask}
RUST_BOOTSTRAP="$SCRIPT_DIR/desktop-rust-bootstrap.py"
LIGHTDM_CONFIG="$SCRIPT_DIR/desktop-lightdm.conf"
for input in "$PACKAGE_LIST" "$PACKAGE_USE" "$PACKAGE_ACCEPT_KEYWORDS" "$PACKAGE_MASK" "$RUST_BOOTSTRAP" "$LIGHTDM_CONFIG"; do
    [ -s "$input" ] || die "required pinned desktop input is missing: $input"
done
driver_hash=$(sha256 "$SCRIPT_DIR/complete-desktop.sh")
inputs_hash=$(sha256sum "$PACKAGE_LIST" "$PACKAGE_USE" "$PACKAGE_ACCEPT_KEYWORDS" "$PACKAGE_MASK" "$RUST_BOOTSTRAP" "$LIGHTDM_CONFIG" | sha256sum | awk '{print $1}')

features=$(awk -F= '$1 == "FEATURES" {gsub(/"/, "", $2); sub(/^[[:space:]]+/, "", $2); sub(/[[:space:]]+$/, "", $2); print $2}' /etc/portage/make.conf)
use=$(awk -F= '$1 == "USE" {gsub(/"/, "", $2); sub(/^[[:space:]]+/, "", $2); sub(/[[:space:]]+$/, "", $2); print $2}' /etc/portage/make.conf)
case " $features " in *' -test '*) ;; *) die 'global FEATURES=-test is required by the reviewed bootstrap policy.' ;; esac
for required_feature in -getbinpkg -buildpkg -assume-digests -digest strict; do
    case " $features " in *" $required_feature "*) ;; *) die "Portage must include FEATURES=$required_feature." ;; esac
done
case " $use " in *' -test '*) ;; *) die 'global USE=-test is required by the reviewed bootstrap policy.' ;; esac

managed_config() {
    local source=$1 destination=$2 expected
    expected=$(sha256 "$source")
    mkdir -p "$(dirname "$destination")"
    if [ -e "$destination" ]; then
        [ "$(sha256 "$destination")" = "$expected" ] ||
            die "refusing to replace an unexpected existing configuration: $destination"
    else
        install -m 0644 "$source" "$destination.tmp"
        sync "$destination.tmp"
        mv "$destination.tmp" "$destination"
    fi
}

profile_marker="$STATE/desktop-profile.done"
if [ ! -s "$profile_marker" ]; then
    ln -sfn "$DESKTOP_PROFILE" "$PROFILE.tmp"
    mv -Tf "$PROFILE.tmp" "$PROFILE"
    printf 'complete %s\nprofile=%s\nGentoo repository commit=%s\npackage inputs sha256=%s\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$DESKTOP_PROFILE" "$pinned_commit" "$inputs_hash" "$driver_hash" > "$profile_marker.tmp"
    sync "$profile_marker.tmp"
    mv "$profile_marker.tmp" "$profile_marker"
    sync "$STATE"
else
    grep -Fxq "profile=$DESKTOP_PROFILE" "$profile_marker" &&
        grep -Fxq "Gentoo repository commit=$pinned_commit" "$profile_marker" &&
        grep -Fxq "package inputs sha256=$inputs_hash" "$profile_marker" &&
        grep -Fxq "driver sha256=$driver_hash" "$profile_marker" || die 'desktop profile marker does not match current source inputs/driver.'
    [ "$current_profile" = "$DESKTOP_PROFILE" ] || die 'profile changed after its completion marker.'
fi

managed_config "$PACKAGE_USE" /etc/portage/package.use/80-ms1504-desktop
managed_config "$PACKAGE_ACCEPT_KEYWORDS" /etc/portage/package.accept_keywords/80-ms1504-desktop
managed_config "$PACKAGE_MASK" /etc/portage/package.mask/80-ms1504-desktop

mapfile -t atoms < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$PACKAGE_LIST")
[ "${#atoms[@]}" -gt 0 ] || die 'desktop package list is empty.'
export DISTDIR PORTAGE_TMPDIR
export USE='-test -cdr'
export FEATURES='-test -getbinpkg -buildpkg -assume-digests -digest strict'
export PORTAGE_BINHOST=
DESKTOP_MAKE_JOBS=${MS1504_DESKTOP_MAKE_JOBS:-$GENTOO_PORTAGE_MAKE_JOBS}
DESKTOP_PACKAGE_JOBS=${MS1504_DESKTOP_PACKAGE_JOBS:-$GENTOO_PORTAGE_JOBS}
DESKTOP_LOAD_AVERAGE=${MS1504_DESKTOP_LOAD_AVERAGE:-$GENTOO_PORTAGE_LOAD_AVERAGE}
for setting in DESKTOP_MAKE_JOBS DESKTOP_PACKAGE_JOBS; do
    value=${!setting}
    case "$value" in ''|*[!0-9]*|0) die "$setting must be a positive integer (got: $value)." ;; esac
done
[[ "$DESKTOP_LOAD_AVERAGE" =~ ^[1-9][0-9]*(\.[0-9]+)?$ ]] || die "DESKTOP_LOAD_AVERAGE must be positive (got: $DESKTOP_LOAD_AVERAGE)."
export MAKEOPTS="-j$DESKTOP_MAKE_JOBS"
export EMERGE_DEFAULT_OPTS="--jobs=$DESKTOP_PACKAGE_JOBS --load-average=$DESKTOP_LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n"
printf 'Desktop build parallelism: make jobs=%s; package jobs=%s; load average=%s\n' \
    "$DESKTOP_MAKE_JOBS" "$DESKTOP_PACKAGE_JOBS" "$DESKTOP_LOAD_AVERAGE"

# Use the same effective configuration and option checks as host/chroot builds.
emerge() { python3 "$SCRIPT_DIR/source-only-emerge.py" /usr/bin/emerge "$@"; }

# Retire the old selected server without removing any installed package here.
emerge --deselect media-sound/pulseaudio-daemon

# Build each minor release after its predecessor is installed. The Rust eclass
# also permits a compiler from its own slot, which can produce a self-cycle
# when an entire unbootstrapped desktop graph is resolved in one operation.
[ -z "$(portageq best_version / dev-lang/rust-bin)" ] || die 'a binary Rust compiler is installed; source bootstrap provenance needs inspection.'
rust_chain=$(python3 "$RUST_BOOTSTRAP") || die 'could not determine the visible source Rust bootstrap chain.'
[ -n "$rust_chain" ] || die 'source Rust bootstrap chain is empty.'
mapfile -t rust_atoms <<< "$rust_chain"
printf 'Source Rust bootstrap order:\n%s\n' "$rust_chain"
for atom in "${rust_atoms[@]}"; do
    if [ -n "$(portageq best_version / "$atom")" ]; then
        printf 'Source Rust slot already installed: %s\n' "$atom"
        continue
    fi
    emerge --oneshot --usepkg=n --getbinpkg=n --buildpkg=n "$atom"
    [ -n "$(portageq best_version / "$atom")" ] || die "source Rust slot was not installed: $atom"
done
printf 'complete %s\nGentoo repository commit=%s\npackage inputs sha256=%s\ndriver sha256=%s\nsource Rust build order:\n%s\n' \
    "$(date -u +%FT%TZ)" "$pinned_commit" "$inputs_hash" "$driver_hash" "$rust_chain" > "$STATE/desktop-rust-bootstrap.done.tmp"
sync "$STATE/desktop-rust-bootstrap.done.tmp"
mv "$STATE/desktop-rust-bootstrap.done.tmp" "$STATE/desktop-rust-bootstrap.done"

plan="$STATE/desktop-emerge-plan.txt"
if [ ! -s "$STATE/desktop-plan.done" ]; then
    emerge --pretend --tree --verbose --update --newuse --deep @world "${atoms[@]}" | tee "$plan.tmp"
    if grep -Eq '^\[binary' "$plan.tmp"; then die 'resolved desktop plan contains a binary package.'; fi
    mv "$plan.tmp" "$plan"
    printf 'complete %s\nGentoo repository commit=%s\npackage inputs sha256=%s\ndriver sha256=%s\n' \
        "$(date -u +%FT%TZ)" "$pinned_commit" "$inputs_hash" "$driver_hash" > "$STATE/desktop-plan.done.tmp"
    sync "$STATE/desktop-plan.done.tmp"
    mv "$STATE/desktop-plan.done.tmp" "$STATE/desktop-plan.done"
fi
grep -Fxq "Gentoo repository commit=$pinned_commit" "$STATE/desktop-plan.done" &&
    grep -Fxq "package inputs sha256=$inputs_hash" "$STATE/desktop-plan.done" &&
    grep -Fxq "driver sha256=$driver_hash" "$STATE/desktop-plan.done" || die 'desktop package plan does not match current source inputs/driver; preserve it for review.'

fetch_marker="$STATE/desktop-sources-fetched.done"
if [ ! -s "$fetch_marker" ]; then
    attempt=1
    until emerge --fetchonly --update --newuse --deep @world "${atoms[@]}"; do
        [ "$attempt" -lt 5 ] || die 'source fetch failed five times; verified downloads remain in /extra/portage/distfiles.'
        attempt=$((attempt + 1))
        printf 'Fetch attempt %s failed; retrying while retaining verified distfiles.\n' "$attempt"
        sleep $((attempt * 10))
    done
    printf 'complete %s\nGentoo repository commit=%s\npackage inputs sha256=%s\ndriver sha256=%s\nGentoo Manifests verified by Portage\n' \
        "$(date -u +%FT%TZ)" "$pinned_commit" "$inputs_hash" "$driver_hash" > "$fetch_marker.tmp"
    sync "$fetch_marker.tmp"
    mv "$fetch_marker.tmp" "$fetch_marker"
fi
grep -Fxq "Gentoo repository commit=$pinned_commit" "$fetch_marker" &&
    grep -Fxq "package inputs sha256=$inputs_hash" "$fetch_marker" &&
    grep -Fxq "driver sha256=$driver_hash" "$fetch_marker" || die 'fetch marker inputs mismatch.'

verify_desktop_audio() {
    for audio_command in pipewire pipewire-pulse wireplumber pavucontrol pactl gentoo-pipewire-launcher; do
        need "$audio_command"
    done
    [ -z "$(portageq best_version / media-sound/pulseaudio-daemon)" ] ||
        die 'the PulseAudio daemon remains installed alongside the selected PipeWire server.'
    [ -r /etc/xdg/autostart/pipewire.desktop ] &&
        grep -Fq 'gentoo-pipewire-launcher restart' /etc/xdg/autostart/pipewire.desktop ||
        die 'the pinned PipeWire Xfce session autostart entry is missing or changed.'
}

configure_desktop_login() {
    [ -r /etc/conf.d/display-manager ] || die 'shared OpenRC display-manager configuration is missing.'
    display_manager=$(awk -F= '$1 == "DISPLAYMANAGER" {gsub(/"/, "", $2); print $2}' /etc/conf.d/display-manager)
    case "$display_manager" in
        xdm|lightdm) ;;
        *) die "unexpected existing display-manager selection: $display_manager" ;;
    esac
    rc_display_manager=$(awk -F= '$1 == "DISPLAYMANAGER" {gsub(/"/, "", $2); print $2}' /etc/rc.conf)
    case "$rc_display_manager" in
        ''|lightdm) ;;
        *) die "DISPLAYMANAGER in /etc/rc.conf overrides LightDM: $rc_display_manager" ;;
    esac
    sed -i 's/^DISPLAYMANAGER=.*/DISPLAYMANAGER="lightdm"/' /etc/conf.d/display-manager
    managed_config "$LIGHTDM_CONFIG" /etc/lightdm/lightdm.conf.d/80-ms1504-desktop.conf
}

verify_desktop_login() {
    need lightdm
    need lightdm-gtk-greeter
    need startxfce4
    [ -r /usr/share/xsessions/xfce.desktop ] || die 'Xfce graphical session entry is missing.'
    grep -Fxq 'DISPLAYMANAGER="lightdm"' /etc/conf.d/display-manager || die 'shared display-manager service is not configured for LightDM.'
    [ -r /etc/lightdm/lightdm.conf.d/80-ms1504-desktop.conf ] &&
        [ "$(sha256 /etc/lightdm/lightdm.conf.d/80-ms1504-desktop.conf)" = "$(sha256 "$LIGHTDM_CONFIG")" ] ||
        die 'LightDM greeter and Xfce session configuration differs from the desktop inputs.'
}

build_marker="$STATE/desktop-build.done"
if [ ! -s "$build_marker" ]; then
    emerge --update --newuse --deep @world "${atoms[@]}"
    configure_desktop_login
    verify_desktop_login
    verify_desktop_audio
    for service in dbus elogind display-manager bluetooth; do
        [ -e "/etc/init.d/$service" ] || die "expected OpenRC desktop service is missing: $service"
    done
    rc-update add dbus default
    rc-update add elogind boot
    rc-update add bluetooth default
    rc-update add display-manager default
    for atom in "${atoms[@]}"; do
        best=$(portageq best_version / "$atom")
        case "$best" in */*) ;; *) die "desktop atom is not registered as installed: $atom" ;; esac
    done
    printf 'complete %s\nGentoo repository commit=%s\npackage inputs sha256=%s\ndriver sha256=%s\nprofile=%s\nmake jobs=%s\npackage jobs=%s\nload average=%s\nsource policy=Portage Manifest digest verification enabled; no binary packages; package tests disabled\naudio server=PipeWire; PulseAudio compatibility; WirePlumber; pavucontrol; Xfce session autostart\n' \
        "$(date -u +%FT%TZ)" "$pinned_commit" "$inputs_hash" "$driver_hash" \
        "$DESKTOP_PROFILE" "$DESKTOP_MAKE_JOBS" "$DESKTOP_PACKAGE_JOBS" \
        "$DESKTOP_LOAD_AVERAGE" > "$build_marker.tmp"
    sync "$build_marker.tmp"
    mv "$build_marker.tmp" "$build_marker"
    sync "$STATE"
fi
grep -Fxq "Gentoo repository commit=$pinned_commit" "$build_marker" &&
    grep -Fxq "package inputs sha256=$inputs_hash" "$build_marker" &&
    grep -Fxq "driver sha256=$driver_hash" "$build_marker" || die 'desktop build marker does not match current source inputs/driver.'

# Revalidate the installed package and OpenRC state on every invocation,
# including after a completion marker exists. A receipt must never mask a
# package removal or disabled login/audio/Bluetooth service.
for atom in "${atoms[@]}"; do
    best=$(portageq best_version / "$atom")
    case "$best" in */*) ;; *) die "desktop atom is not registered as installed: $atom" ;; esac
done
verify_desktop_audio
verify_desktop_login
for service_runlevel in 'dbus default' 'elogind boot' 'bluetooth default' 'display-manager default'; do
    read -r service runlevel <<< "$service_runlevel"
    init_script="/etc/init.d/$service"
    runlevel_link="/etc/runlevels/$runlevel/$service"
    [ -x "$init_script" ] || die "expected OpenRC service is missing: $init_script"
    [ -L "$runlevel_link" ] &&
        [ "$(readlink -f "$runlevel_link")" = "$(readlink -f "$init_script")" ] ||
        die "OpenRC service is not enabled in the expected runlevel: $service/$runlevel"
done

printf 'Desktop package build and OpenRC service registration completed. Review the signed system-generation procedure before activating it as the default.\n'
if [ "$GENTOO_MODE" = hardware ]; then
    [ -d "$GENERATION_STATE/pending" ] || die 'signed-generation transaction disappeared during desktop build.'
    printf 'Hardware update remains protected by its pending rollback transaction. Seal a new signed generation before rebooting.\n'
fi
