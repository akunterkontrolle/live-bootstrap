#!/usr/bin/env python3
"""Transition only bootstrap scratch settings to persistent installed-base caches."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import os
from pathlib import Path
import re
import tempfile

SETTINGS = {
    'PORTAGE_TMPDIR': ('/gentoo-scratch/portage-tmp', '/var/tmp/portage'),
    'DISTDIR': ('/gentoo-scratch/distfiles', '/var/cache/distfiles'),
    'PKGDIR': ('/gentoo-scratch/binpkgs', '/var/cache/binpkgs'),
}


def configure(root):
    config = root / 'etc/portage/make.conf'
    if config.is_symlink() or not config.is_file():
        raise ValueError('expected a regular Portage make.conf')
    original = config.read_text()
    corrected = original
    for name, (before, after) in SETTINGS.items():
        pattern = re.compile(r'^' + name + r'="([^"\n]*)"$', re.MULTILINE)
        values = pattern.findall(original)
        if len(values) != 1 or values[0] not in (before, after):
            raise ValueError('unexpected ' + name + '; preserve operator or desktop configuration for review')
        corrected = pattern.sub(name + '="' + after + '"', corrected)
    entries = [line.split(':') for line in (root / 'etc/passwd').read_text().splitlines()
               if line.startswith('portage:')]
    if len(entries) != 1 or len(entries[0]) != 7:
        raise ValueError('expected one installed Portage account')
    uid, gid = int(entries[0][2]), int(entries[0][3])
    if uid <= 0 or gid <= 0:
        raise ValueError('Portage account must not be root')
    if os.geteuid() != 0 and (uid, gid) != (os.geteuid(), os.getegid()):
        raise ValueError('root is required to create caches for the installed Portage account')
    paths = [root / after.lstrip('/') for _, after in SETTINGS.values()]
    for path in paths:
        # Do not follow a redirected cache or a redirected parent in the target.
        relative = path.relative_to(root)
        if any(root.joinpath(*relative.parts[:i]).is_symlink() for i in range(1, len(relative.parts) + 1)):
            raise ValueError('cache path contains a symlink: ' + str(path))
        if path.exists() and not path.is_dir():
            raise ValueError('cache path is not a directory: ' + str(path))
    backup = config.with_name('make.conf.before-installed-caches')
    if corrected != original and (backup.is_symlink() or (backup.exists() and backup.read_text() != original)):
        raise ValueError('existing make.conf backup differs')
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            os.chown(path, uid, gid)
        path.chmod(0o2775)
    if corrected == original:
        return False
    info = config.stat()
    if not backup.exists():
        with backup.open('x') as out:
            out.write(original)
            out.flush()
            os.fsync(out.fileno())
        backup.chmod(info.st_mode & 0o777)
    with tempfile.NamedTemporaryFile(mode='w', dir=config.parent, prefix='.make-conf-', delete=False) as out:
        pending = Path(out.name)
        out.write(corrected)
        out.flush()
        os.fsync(out.fileno())
    pending.chmod(info.st_mode & 0o777)
    if os.geteuid() == 0:
        os.chown(pending, info.st_uid, info.st_gid)
    os.replace(pending, config)
    fd = os.open(config.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    args = parser.parse_args()
    configure(args.root.resolve(strict=True))
    print('Installed Portage cache paths configured; CPU/source-only settings preserved.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('gentoo-installed-caches: ' + str(error))
