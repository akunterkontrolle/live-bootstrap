#!/bin/bash
# Dedicated recovery image only: never copy a root unlock/signing key.
check() { return 255; }
depends() { echo 'bash crypt lvm'; }
install() {
    local python stdlib file
    # Gentoo's python3 can be a python-exec dispatcher. /proc/self/exe inside
    # Python identifies the interpreter that actually ran, independent of argv0.
    python=$(python3 -c 'import os; print(os.readlink("/proc/self/exe"))') || return 1
    inst_multiple "$python" bash setsid mount umount findmnt blkid lsblk lvs vgs lvchange lvconvert \
        cryptsetup udevadm grub-script-check sync reboot || return 1
    inst_dir /usr/bin
    ln -s "$python" "$initdir/usr/bin/ms1504-python" || return 1
    # Python and its extension modules execute entirely from the initramfs.
    # Omit packages, tests and bytecode, but install ELF dependencies normally.
    while IFS= read -r stdlib; do
        [[ -d $stdlib ]] || continue
        while IFS= read -r -d '' file; do
            case "$file" in
                *.so) inst_multiple "$file" || return 1 ;;
                *) inst_simple "$file" || return 1 ;;
            esac
        done < <(find "$stdlib" \( -name site-packages -o -name dist-packages -o -name test -o -name tests -o -name __pycache__ \) -prune \
            -o -type f \( -name '*.py' -o -name '*.so' \) -print0)
    done < <("$python" -c 'import sysconfig; print("\n".join(sorted(set(filter(None, (sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib"), sysconfig.get_config_var("DESTSHARED")))))))')
    # Signed menu repair needs a separately supplied signing key. No key or
    # agent state is taken from the installed system.
    inst_multiple -o gpg gpg-agent gpgconf pinentry pinentry-tty pinentry-curses
    for file in generation.py signed-generation.py generation_recovery.py generation_transition.py; do
        inst_simple "$moddir/$file" "/lib/ms1504-offline/$file" || return 1
    done
    inst_simple "$moddir/config.toml" /etc/ms1504-offline/config.toml || return 1
    inst_simple "$moddir/storage.json" /etc/ms1504-offline/storage.json || return 1
    if [[ ! -e $initdir/etc/initrd-release ]]; then
        printf 'NAME="ms1504 RAM recovery"\n' > "$initdir/etc/initrd-release" || return 1
    fi
    inst_hook pre-mount 00 "$moddir/recover.sh"
}
