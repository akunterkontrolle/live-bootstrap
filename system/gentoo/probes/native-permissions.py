#!/usr/bin/env python3
"""Check the native seed as Portage's user; explicitly repair the old 077 mask."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import sys
import time
import uuid

# These are public seed runtime trees. Do not walk /root, /home, /dev, /proc,
# /sys, /host-build, scratch mounts, or all of /etc (shadow and private keys).
PUBLIC_TREES = ('/bin', '/sbin', '/lib', '/lib32', '/lib64', '/usr',
                '/etc/portage', '/etc/env.d', '/etc/ssl/certs', '/var/db/pkg')
PUBLIC_FILES = ('/etc/resolv.conf', '/etc/nsswitch.conf', '/etc/hosts',
                '/etc/services', '/etc/protocols', '/etc/networks', '/etc/host.conf',
                '/etc/gai.conf', '/etc/ld.so.conf')
PUBLIC_PARENTS = ('/', '/etc', '/etc/ssl', '/var', '/var/db', '/var/cache',
                  '/var/log', '/var/tmp', '/run')


def repair_modes(root, journal):
    """Journal bounded batches durably before chmod; never follow symlink dirs."""
    changed = []
    pending = []
    scanned = 0
    last_report = time.monotonic()
    journal.parent.mkdir(parents=True, exist_ok=True)
    print('Repairing public seed permissions; journal: ' + str(journal), flush=True)
    with journal.open('a') as log:
        os.chmod(journal, 0o600)

        def flush_batch():
            if not pending:
                return
            # Every old mode reaches stable storage BEFORE its chmod. One
            # fsync per bounded batch avoids thousands of serial USB flushes.
            log.writelines(json.dumps({'path': str(path), 'before': mode, 'after': new}) + '\n'
                           for path, mode, new in pending)
            log.flush()
            os.fsync(log.fileno())
            for path, mode, new in pending:
                path.chmod(new)
                changed.append(str(path))
            pending.clear()

        def fix(path):
            nonlocal scanned, last_report
            scanned += 1
            now = time.monotonic()
            if now - last_report >= 2:
                print('Permission repair: checked %d, changed %d, queued %d; %s' %
                      (scanned, len(changed), len(pending), path), flush=True)
                last_report = now
            info = path.lstat()
            if info.st_uid != os.geteuid() or stat.S_ISLNK(info.st_mode):
                return
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode) and mode == 0o700:
                new = 0o755
            elif stat.S_ISREG(info.st_mode) and mode in (0o600, 0o700):
                new = 0o644 if mode == 0o600 else 0o755
            else:
                return
            pending.append((path, mode, new))
            if len(pending) >= 256:
                flush_batch()

        def walk_error(error):
            raise error

        for name in PUBLIC_PARENTS:
            path = root / name.lstrip('/')
            if path.exists():
                fix(path)
        for name in PUBLIC_FILES:
            path = root / name.lstrip('/')
            if path.is_file() and not path.is_symlink():
                fix(path)
        # Fix parent traversal before visiting their descendants.
        flush_batch()
        for name in PUBLIC_TREES:
            path = root / name.lstrip('/')
            if not path.exists() or path.is_symlink():
                continue
            print('Checking public seed tree: ' + name, flush=True)
            fix(path)
            for directory, dirs, files in os.walk(path, followlinks=False, onerror=walk_error):
                for child in dirs + files:
                    fix(Path(directory) / child)
            flush_batch()
        print('Permission repair complete: checked %d, changed %d.' %
              (scanned, len(changed)), flush=True)
    return changed


PROBE = '''import os, socket, subprocess
import portage
# Portage also stats optional configuration names. ENOENT is valid, while
# EACCES means the fetch subprocess cannot inspect its policy directory.
try:
    os.stat("/etc/portage/mirrors")
except FileNotFoundError:
    pass
for name in ("/etc/portage/make.conf", "/usr/portage/profiles/repo_name"):
    with open(name, "rb") as stream:
        stream.read(1)
subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c",
                "printf 'PORTAGE_USER_EXEC_OK\\\\n'"], check=True)
print("Portage user can execute Bash, import Portage and read native policy/repository.", flush=True)
socket.getaddrinfo("distfiles.gentoo.org", 443, type=socket.SOCK_STREAM)
print("Portage-user DNS resolution passed for distfiles.gentoo.org.", flush=True)
'''


def scratch_write_probe(descriptor):
    """Use only a private O_EXCL test file, under the caller's dropped identity."""
    name = 'distfiles/.gentoo-permission-probe-' + uuid.uuid4().hex
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=descriptor)
    try:
        os.write(fd, b'permission probe\n')
    finally:
        os.close(fd)
        os.unlink(name, dir_fd=descriptor)
    directory = 'portage-tmp/.gentoo-build-probe-' + uuid.uuid4().hex
    os.mkdir(directory, 0o700, dir_fd=descriptor)
    try:
        work = os.open(directory + '/work', os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                       0o600, dir_fd=descriptor)
        os.close(work)
        os.unlink(directory + '/work', dir_fd=descriptor)
    finally:
        os.rmdir(directory, dir_fd=descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--repair', action='store_true')
    parser.add_argument('--scratch', type=Path, help='also verify the already mounted scratch distfiles as Portage')
    args = parser.parse_args()
    print('Checking native seed permissions: ' + str(args.root), flush=True)
    root = args.root.resolve(strict=True)
    if root == Path('/') or root.name != 'native-root-x86_64':
        raise ValueError('expected the separate native-root-x86_64 seed directory')
    if os.geteuid() != 0:
        raise ValueError('native permission check requires root for chroot and privilege dropping')
    scratch_fd = None
    if args.scratch is not None:
        scratch = args.scratch
        if scratch != Path('/gentoo-scratch') or scratch.is_symlink():
            raise ValueError('expected the existing /gentoo-scratch mount')
        mounts = [line.split() for line in Path('/proc/mounts').read_text().splitlines()]
        if not any(len(row) >= 4 and row[1] == str(scratch) and 'rw' in row[3].split(',') for row in mounts):
            raise ValueError('scratch is not mounted read-write; no storage changes were made')
        scratch_fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    # Absolute symlinks must resolve in the native tree, never in the bootstrap.
    os.chroot(root)
    os.chdir('/')
    account = pwd.getpwnam('portage')
    if account.pw_uid != 250 or account.pw_gid != 250:
        raise ValueError('unexpected seed Portage account identity')
    if args.repair:
        journal = Path('/var/log/gentoo-seed-permissions-' + str(time.time_ns()) + '.jsonl')
        changed = repair_modes(Path('/'), journal)
        print('Repaired restrictive seed modes:', len(changed), 'journal:', str(root) + str(journal), flush=True)
    print('Checking native execution and fetch access as UID/GID 250.', flush=True)
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
            os.chdir('/')
            if scratch_fd is not None:
                scratch_write_probe(scratch_fd)
                os.close(scratch_fd)
                print('Portage-user scratch fetch and build create/remove probes passed.', flush=True)
            os.execve('/usr/bin/python3.12', ['python3.12', '-c', PROBE],
                      {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/var/tmp',
                       'PYTHONPATH': '/usr/lib/portage/lib', 'PYTHONDONTWRITEBYTECODE': '1'})
        except Exception as error:
            print('Portage-user execution failed: ' + str(error), file=sys.stderr, flush=True)
            os._exit(1)
    deadline = time.monotonic() + 30
    while True:
        finished, status = os.waitpid(pid, os.WNOHANG)
        if finished:
            break
        if time.monotonic() >= deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise ValueError('Portage-user runtime/DNS probe timed out after 30 seconds')
        time.sleep(0.1)
    if scratch_fd is not None:
        os.close(scratch_fd)
    if status != 0:
        raise ValueError('native Portage-user permission probe failed; do not run ebuilds yet')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('gentoo: ' + str(error), file=sys.stderr)
        sys.exit(1)
