#!/usr/bin/env python3
"""Order the packaged XDG runtime provider before OpenRC's PAM user session."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import os
from pathlib import Path
import re
import tempfile

RULE = 'session optional pam_xdg.so runtime\n'


def configure(root):
    path = root / 'etc/pam.d/system-login'
    if path.is_symlink() or not path.is_file():
        raise ValueError('expected a regular system-login PAM policy')
    modules = ('lib64/security/pam_xdg.so', 'usr/lib64/security/pam_xdg.so',
               'lib/security/pam_xdg.so', 'usr/lib/security/pam_xdg.so')
    if not any((root / name).is_file() for name in modules):
        raise ValueError('install the pinned sys-auth/pam_xdg package before configuring PAM')
    data = path.read_text()
    lines = data.splitlines(keepends=True)
    hooks = [i for i, line in enumerate(lines)
             if re.match(r'^\s*-?session\s+\S+\s+pam_openrc\.so(?:\s|$)', line)]
    if len(hooks) != 1:
        raise ValueError('expected exactly one OpenRC session hook; inspect PAM policy')
    providers = [i for i, line in enumerate(lines)
                 if not line.lstrip().startswith('#') and 'pam_xdg.so' in line]
    if providers:
        if providers != [hooks[0] - 1] or lines[providers[0]] != RULE:
            raise ValueError('existing pam_xdg configuration differs; preserve it for review')
        return False
    if any(re.match(r'^\s*-?session\s+\S+\s+pam_(elogind|systemd)\.so(?:\s|$)', line)
           for line in lines):
        raise ValueError('another runtime/session provider is configured; inspect before combining')
    lines.insert(hooks[0], RULE)
    info = path.stat()
    backup = path.with_name('system-login.before-gentoo-xdg')
    if backup.is_symlink() or (backup.exists() and backup.read_text() != data):
        raise ValueError('PAM backup differs; refusing to overwrite evidence')
    if not backup.exists():
        with backup.open('x') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        backup.chmod(info.st_mode & 0o777)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.system-login-', delete=False) as out:
        pending = Path(out.name)
        out.write(''.join(lines))
        out.flush()
        os.fsync(out.fileno())
    pending.chmod(info.st_mode & 0o777)
    if os.geteuid() == 0:
        os.chown(pending, info.st_uid, info.st_gid)
    os.replace(pending, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    args = parser.parse_args()
    changed = configure(args.root.resolve(strict=True))
    print('OpenRC PAM runtime provider ' + ('configured; log out and log in again.' if changed else 'already configured.'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('gentoo-user-runtime: ' + str(error))
