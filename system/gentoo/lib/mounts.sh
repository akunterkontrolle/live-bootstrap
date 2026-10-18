#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Shared bootstrap mount operations; no util-linux recursive unmount required.
gentoo_prepare_virtual_filesystems() {
    mkdir -p /proc /sys /dev/pts /dev/shm
    if [ ! -r /proc/mounts ]; then mount -t proc proc /proc; fi
    mountpoint -q /proc || mount -t proc proc /proc
    mountpoint -q /sys || mount -t sysfs sysfs /sys
    mountpoint -q /dev/pts || mount -t devpts devpts /dev/pts
    mountpoint -q /dev/shm || mount -t tmpfs tmpfs /dev/shm
    gentoo_prepare_fd_links
}

# Bootstrap diagnostics can leave regular stdin/stdout/stderr placeholders.
# Normalize the conventional names without discarding any existing contents,
# device nodes or alternative links. Open descriptors remain unaffected.
# The directory argument permits disposable fixtures without touching /dev.
gentoo_prepare_fd_links() {
    python3 - "${1:-/dev}" <<'GENTOO_FD_LINKS_PY'
import os
import stat
import sys
import tempfile


def normalize(directory):
    for name, target in (('fd', '/proc/self/fd'), ('stdin', '/proc/self/fd/0'),
                         ('stdout', '/proc/self/fd/1'), ('stderr', '/proc/self/fd/2')):
        path = os.path.join(directory, name)
        for attempt in range(8):
            # lstat inspects the directory entry, never its descriptor target.
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                info = None
            if info is not None and stat.S_ISLNK(info.st_mode):
                try:
                    if os.readlink(path) == target:
                        break
                except FileNotFoundError:
                    continue
            if info is not None and stat.S_ISDIR(info.st_mode):
                raise RuntimeError('expected a proc-fd link, found a directory: ' + path)
            backup = None
            if info is not None:
                saved = tempfile.mkdtemp(prefix='.gentoo-fd-' + name + '.', dir=directory)
                backup = os.path.join(saved, 'original')
                try:
                    os.rename(path, backup)
                except FileNotFoundError:
                    # An entry can vanish between inspection and rename.
                    os.rmdir(saved)
                    continue
                print('Preserved previous %s (mode=%s, inode=%s, rdev=%s) at %s' %
                      (path, oct(info.st_mode), info.st_ino, info.st_rdev, backup),
                      file=sys.stderr, flush=True)
            try:
                os.symlink(target, path)
            except OSError:
                # Never overwrite an entry created by another process while
                # recovering. Retain the backup and report the original error.
                if backup is not None and not os.path.lexists(path):
                    os.rename(backup, path)
                if backup is None and os.path.islink(path) and os.readlink(path) == target:
                    break
                raise
            print('Created %s -> %s' % (path, target), file=sys.stderr, flush=True)
            break
        else:
            raise RuntimeError('descriptor entry keeps changing: ' + path)


if __name__ == '__main__':
    try:
        normalize(sys.argv[1])
    except Exception as error:
        print('gentoo: descriptor link preparation failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
GENTOO_FD_LINKS_PY
}

gentoo_unmount_tree() {
    local root=$1 mounts=${2:-/proc/mounts} points point
    case "$root" in /*) ;; *) return 1 ;; esac
    [ "$root" != / ] || return 1
    points=$(awk -v root="$root" '$2==root || index($2,root "/")==1 {print $2}' "$mounts" | LC_ALL=C sort -r) || return 1
    while IFS= read -r point; do
        [ -n "$point" ] || continue
        umount "$point" || return 1
    done <<< "$points"
}
