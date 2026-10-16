#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
portage_render_config() {
    local jobs=$GENTOO_PORTAGE_JOBS load=$GENTOO_PORTAGE_LOAD_AVERAGE make_jobs=$GENTOO_PORTAGE_MAKE_JOBS
    [[ "$jobs" =~ ^[1-9][0-9]*$ && "$make_jobs" =~ ^[1-9][0-9]*$ &&
        "$load" =~ ^([0-9]+([.][0-9]+)?|[.][0-9]+)$ ]] &&
        awk -v value="$load" 'BEGIN {exit !(value > 0)}' || gentoo_die 'invalid Portage concurrency settings'
    [ -d "$GENTOO_REPO_TREE/profiles/default/linux/amd64/23.0/desktop" ] ||
        gentoo_die 'pinned Gentoo snapshot lacks the reviewed amd64 desktop profile'
    local directory="$PORTAGE_CONFIG/etc/portage"
    mkdir -p "$directory/repos.conf"
    local temporary
    temporary=$(mktemp "$directory/.gentoo-conf.XXXXXX")
    cat > "$temporary" <<EOF_CONF
[DEFAULT]
main-repo = gentoo

[gentoo]
location = $GENTOO_REPO_TREE
EOF_CONF
    chmod 0644 "$temporary"
    mv "$temporary" "$directory/repos.conf/gentoo.conf"
    ln -sfn "$GENTOO_REPO_TREE/profiles/default/linux/amd64/23.0/desktop" "$directory/make.profile"
    temporary=$(mktemp "$directory/.make-conf.XXXXXX")
    cat > "$temporary" <<EOF_CONF
CHOST="x86_64-pc-linux-gnu"
DISTDIR="$WORK_ROOT/distfiles"
FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"
EMERGE_DEFAULT_OPTS="--jobs=$jobs --load-average=$load --usepkg=n --getbinpkg=n --buildpkg=n"
MAKEOPTS="-j$make_jobs"
PORTAGE_BINHOST=""
EOF_CONF
    chmod 0644 "$temporary"
    mv "$temporary" "$directory/make.conf"
    mkdir -p "$WORK_ROOT/tmp/portage"
}

portage_host_environment() {
    export PYTHONPATH="$PORTAGE_SOURCE/lib"
    export PORTAGE_CONFIGROOT="$PORTAGE_CONFIG"
    export PORTAGE_TMPDIR="$WORK_ROOT/tmp/portage"
    export DISTDIR="$WORK_ROOT/distfiles"
    export FEATURES='-test -getbinpkg -buildpkg -assume-digests -digest strict'
    export EMERGE_DEFAULT_OPTS="--jobs=$GENTOO_PORTAGE_JOBS --load-average=$GENTOO_PORTAGE_LOAD_AVERAGE --usepkg=n --getbinpkg=n --buildpkg=n"
    export MAKEOPTS="-j$GENTOO_PORTAGE_MAKE_JOBS"
    export PORTAGE_BINHOST=
}

portage_verify_guest_account() {
    local group_file=${1:-/etc/group} passwd_file=${2:-/etc/passwd} false_path=${3:-/bin/false}
    local groups users
    groups=$(awk -F: '$1=="portage" {n++; line=$0} END {if(n>1) exit 1; print line}' "$group_file") || return 1
    users=$(awk -F: '$1=="portage" {n++; line=$0} END {if(n>1) exit 1; print line}' "$passwd_file") || return 1
    [ -n "$groups" ] && [ "$(awk -F: '{print $3}' <<< "$groups")" = 250 ] || return 1
    [ "$(awk -F: '{print $3 ":" $4 ":" $7}' <<< "$users")" = "250:250:$false_path" ] || return 1
}

portage_ensure_guest_account() {
    local group_file=${1:-/etc/group} passwd_file=${2:-/etc/passwd} false_path=${3:-/bin/false}
    local group_line user_line group_id temporary mode owner
    [ -s "$group_file" ] && [ -s "$passwd_file" ] && [ -x "$false_path" ] &&
        [ -f "$group_file" ] && [ ! -L "$group_file" ] && [ -f "$passwd_file" ] && [ ! -L "$passwd_file" ] ||
        gentoo_die 'bootstrap account databases or /bin/false are unavailable'
    group_line=$(awk -F: '$1=="portage" {n++; line=$0} END {if(n>1) exit 1; print line}' "$group_file") || gentoo_die 'duplicate portage groups'
    user_line=$(awk -F: '$1=="portage" {n++; line=$0} END {if(n>1) exit 1; print line}' "$passwd_file") || gentoo_die 'duplicate portage accounts'
    if [ -n "$group_line" ]; then
        [ "$(awk -F: '{print $3}' <<< "$group_line")" = 250 ] ||
            gentoo_die 'existing portage group has an unexpected GID'
    elif awk -F: '$3==250 && $1!="portage" {found=1} END {exit !found}' "$group_file"; then
        gentoo_die 'GID 250 is already assigned'
    fi
    if [ -n "$user_line" ]; then
        [ "$(awk -F: '{print $3 ":" $4 ":" $7}' <<< "$user_line")" = "250:250:$false_path" ] ||
            gentoo_die 'existing portage account does not match the reviewed UID/GID/shell'
    elif awk -F: '$3==250 && $1!="portage" {found=1} END {exit !found}' "$passwd_file"; then
        gentoo_die 'UID 250 is already assigned'
    fi
    # Check both IDs before either file changes. A power loss between the two
    # atomic publications is safe: the next invocation recognizes the group.
    if [ -z "$group_line" ]; then
        mode=$(stat -c %a "$group_file")
        owner=$(stat -c %u:%g "$group_file")
        temporary=$(mktemp "$(dirname "$group_file")/.group.portage.XXXXXX")
        cat "$group_file" > "$temporary"
        printf 'portage::250:portage\n' >> "$temporary"
        chmod "$mode" "$temporary"
        chown "$owner" "$temporary"
        mv "$temporary" "$group_file"
    fi
    if [ -z "$user_line" ]; then
        mode=$(stat -c %a "$passwd_file")
        owner=$(stat -c %u:%g "$passwd_file")
        temporary=$(mktemp "$(dirname "$passwd_file")/.passwd.portage.XXXXXX")
        cat "$passwd_file" > "$temporary"
        printf 'portage:x:250:250:Portage:/var/tmp/portage:%s\n' "$false_path" >> "$temporary"
        chmod "$mode" "$temporary"
        chown "$owner" "$temporary"
        mv "$temporary" "$passwd_file"
    fi
    portage_verify_guest_account "$group_file" "$passwd_file" "$false_path" || gentoo_die 'Portage UID/GID setup did not verify'
}

portage_emerge() {
    local result=0 argument
    portage_manifest_guard_check || gentoo_die 'refusing Portage operation with changed repository Manifests'
    for argument in "$@"; do
        case "$argument" in
            --usepkg|--usepkg=*|--usepkg-yes|--usepkgonly|--getbinpkg|--getbinpkg=*|--getbinpkg-yes|--getbinpkgonly|--buildpkg|--buildpkg=*|--buildpkg-yes|--buildpkgonly|-k|-g|-b|-B)
                gentoo_die "source-only Portage forbids $argument" ;;
            --jobs|--jobs=*|--load-average|--load-average=*|-j|-j[0-9]*|-j[0-9]*.*)
                gentoo_die "Portage concurrency comes from the reviewed TOML settings, not $argument" ;;
        esac
    done
    if (
        portage_host_environment
        "$HOST_PREFIX/bin/python3.12" "$PORTAGE_SOURCE/bin/emerge" \
            --usepkg=n --getbinpkg=n --buildpkg=n "$@"
    ); then
        result=0
    else
        result=$?
    fi
    portage_manifest_guard_check || gentoo_die 'Portage operation changed pinned repository Manifests'
    return "$result"
}
