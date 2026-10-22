#!/usr/bin/env python3
"""Describe and fingerprint the workspace needed after installed-base boot."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

POLICY_NAME = 'installed-continuation-v1'
# These are managed bootstrap work/output directories, not installed runtime inputs.
OMIT = tuple('gentoo-bootstrap/' + name for name in (
    'build', 'sources', 'toolchain', 'linux-target-binutils', 'linux-toolchain',
    'linux-target-tools', 'host-tools', 'sysroot-x86_64', 'native-root-x86_64',
    'native-build-tools', 'portage-host-config', 'tmp',
))
POLICY = json.dumps({'name': POLICY_NAME, 'omit': OMIT, 'unknown': 'retain',
                     'bootstrap-resume': False}, sort_keys=True, separators=(',', ':')) + '\n'


def entries(root, strict=False):
    root = root.resolve(strict=True)
    bootstrap = root / 'gentoo-bootstrap'
    if bootstrap.is_symlink() or not bootstrap.is_dir():
        raise ValueError('expected a real gentoo-bootstrap directory')
    device = root.stat().st_dev
    stack = [root]
    while stack:
        directory = stack.pop()
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if relative in OMIT:
                if strict:
                    raise ValueError('omitted path unexpectedly present in staging: ' + relative)
                # Do not follow or enumerate omitted bootstrap roots.
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError('expected managed bootstrap directory: ' + relative)
                continue
            if info.st_dev != device:
                raise ValueError('nested filesystem in retained tree: ' + relative)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                raise ValueError('special file in retained tree: ' + relative)
            yield path, relative, info
            if stat.S_ISDIR(info.st_mode):
                stack.append(path)


def fingerprint(root, strict=False):
    result = []
    hardlinks = {}
    for path, relative, info in entries(root, strict):
        record = {'path': relative, 'mode': stat.S_IMODE(info.st_mode),
                  'uid': info.st_uid, 'gid': info.st_gid, 'mtime_ns': info.st_mtime_ns}
        if stat.S_ISREG(info.st_mode):
            with path.open('rb') as stream:
                record['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = path.stat()
            if (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                    info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                raise ValueError('file changed while fingerprinting: ' + relative)
            record['kind'] = 'file'
            hardlinks.setdefault((info.st_dev, info.st_ino), []).append(record)
        elif stat.S_ISLNK(info.st_mode):
            record.update(kind='symlink', target=os.readlink(path))
        else:
            record['kind'] = 'directory'
        # ACLs are exposed as xattrs too. Fail rather than silently losing them.
        record['xattrs'] = {name: base64.b64encode(os.getxattr(path, name, follow_symlinks=False)).decode()
                            for name in sorted(os.listxattr(path, follow_symlinks=False))}
        result.append(record)
    for group in hardlinks.values():
        if len(group) > 1:
            names = sorted(record['path'] for record in group)
            for record in group:
                record['hardlinks'] = names
    return json.dumps(sorted(result, key=lambda record: record['path']),
                      sort_keys=True, separators=(',', ':')) + '\n'


def allocated(root):
    seen = set()
    total = 0
    for _, _, info in entries(root):
        identity = info.st_dev, info.st_ino
        if identity not in seen:
            total += info.st_blocks * 512
            seen.add(identity)
    return total


def check_installed(root):
    shared = root / 'usr/local/share/ms1504-bootstrap/gentoo'
    required = [root / 'usr/share/gentoo-continuation/gentoo-source-receipt',
                root / 'etc/portage/native-cpu-policy.json']
    required += [shared / name for name in (
        'complete-desktop.sh', 'desktop-rust-bootstrap.py', 'source-only-emerge.py',
        'config.sh', 'cpu-policy.py', 'lib/repository-policy.sh',
        'sources/gentoo-repository.lock')]
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError('installed continuation input is missing: ' + str(path))
    if not (root / 'usr/portage/profiles').is_dir():
        raise ValueError('installed Gentoo repository is missing')
    for name in ('gcc', 'g++', 'cpuid2cpuflags'):
        if not os.access(root / 'usr/bin' / name, os.X_OK):
            raise ValueError('installed tool is missing: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('policy', 'excludes', 'bytes', 'report', 'snapshot', 'installed'))
    parser.add_argument('source', nargs='?', type=Path)
    parser.add_argument('--strict', action='store_true', help='reject excluded paths in staging')
    args = parser.parse_args()
    if args.command == 'policy':
        print(POLICY, end='')
    elif args.command == 'installed':
        check_installed(args.source or Path('/'))
    elif args.command == 'excludes':
        print('\n'.join('/' + name + '/***' for name in OMIT))
    else:
        if args.source is None:
            parser.error('source is required')
        if args.command == 'snapshot':
            print(fingerprint(args.source, args.strict), end='')
        elif args.command == 'bytes':
            print(allocated(args.source))
        else:
            print('Policy:', POLICY_NAME)
            print('Retained allocated bytes:', allocated(args.source))
            print('Retain: inputs, receipts, logs, handoff, artifacts, source archives and unclassified files outside the omitted work directories.')
            print('Omit managed bootstrap directories:')
            for name in OMIT:
                if (args.source / name).exists():
                    print(' ', name)
            print('Bootstrap resumption is not supported from this reduced copy; preserve the original checkpoint separately.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit('workspace-retention: ' + str(error))
