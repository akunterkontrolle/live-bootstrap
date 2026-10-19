#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Native stages 60-71 build a seed in a root which stage 80 then mutates.
# Verify their historical input identities after that handoff, not pristine
# seed output bytes or seed policy which Gentoo is supposed to replace.
gentoo_native_recipe_identity() (
    local recipe=$1 expression value
    expression=$(awk '
        /^(fingerprint|native_system_fingerprint)=[$][(](gentoo_fingerprint|native_recipe_fingerprint)[[:space:]]/ {
            count++; collecting=1
        }
        collecting {
            line=$0
            if (line ~ /[)][[:space:]]*([|][|][[:space:]]*)?$/) {
                sub(/[[:space:]]*[|][|][[:space:]]*$/, "", line)
                text=text line "\n"; collecting=0; complete++
            } else text=text line "\n"
        }
        END {if(count!=1 || complete!=1 || collecting) exit 1; printf "%s", text}
    ' "$SCRIPT_DIR/$recipe") || return 1
    # The driver has verified this recipe as bundle code. Evaluate only its
    # original fingerprint assignment; do not source configure/build commands.
    cd "$SCRIPT_DIR" || return 1
    unset fingerprint native_system_fingerprint
    eval "$expression" || return 1
    value=${fingerprint:-${native_system_fingerprint:-}}
    [[ "$value" =~ ^[0-9a-f]{64}$ ]] || return 1
    printf '%s\n' "$value"
)

gentoo_native_resume_handoff() {
    local owner="$WORK_ROOT/state/80-native-gentoo-system.inputs" stage recipe expected
    if [ ! -e "$owner" ]; then
        [ ! -e "$WORK_ROOT/state/80-native-gentoo-system.done" ] ||
            gentoo_die 'stage 80 completion exists without its input receipt'
        return 1
    fi
    for stage in 60-native-root 61-native-shell-tools 62-native-compiler \
        63-native-tool-floor 70-native-runtimes 71-native-portage; do
        case "$stage" in 60-native-root) recipe=stages/60-native-root.sh ;;
            *) recipe="stages/$stage.sh" ;; esac
        expected=$(gentoo_native_recipe_identity "$recipe") || gentoo_die "cannot compute historical native recipe: $stage"
        [ -f "$WORK_ROOT/state/$stage.done" ] && [ -f "$WORK_ROOT/state/$stage.inputs" ] &&
            [ "$(cat "$WORK_ROOT/state/$stage.done")" = "$expected" ] &&
            [ "$(cat "$WORK_ROOT/state/$stage.inputs")" = "$expected" ] ||
            gentoo_die "native seed input/completion receipt mismatch: $stage"
    done
    expected=$(gentoo_native_recipe_identity stages/80-native-gentoo-system.sh) ||
        gentoo_die 'cannot compute the owning Gentoo system recipe'
    [ "$(cat "$owner")" = "$expected" ] || gentoo_die 'stage 80 ownership receipt does not match its recipe/prerequisites'
    if [ -e "$WORK_ROOT/state/80-native-gentoo-system.done" ]; then
        [ "$(cat "$WORK_ROOT/state/80-native-gentoo-system.done")" = "$expected" ] ||
            gentoo_die 'stage 80 completion receipt differs from its ownership receipt'
    fi
    [ -d "$NATIVE_ROOT/etc/portage" ] || gentoo_die 'owning Gentoo stage has no native root configuration'
    printf 'Native seed input receipts verified; stage 80 owns native policy and runtime outputs.\n'
    printf 'Resuming the Gentoo system stage without restoring or rechecking pristine seed outputs.\n'
}
