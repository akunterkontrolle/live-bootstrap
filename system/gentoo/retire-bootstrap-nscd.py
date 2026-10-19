#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Back up and retire only an unowned nscd copied from the bootstrap sysroot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def inspect(root, seed, expected=None):
    if root.is_symlink() or seed.is_symlink():
        raise ValueError('root and sysroot must not be symlinks')
    candidate = root / 'usr/sbin/nscd'
    # Never follow a symlink out of either tree or retire a replacement daemon.
    if candidate.is_symlink():
        raise ValueError('unexpected nscd symlink; inspect before retirement')
    if not candidate.exists():
        return {'status': 'absent', 'root': str(root)}
    if not candidate.is_file() or root.resolve() not in candidate.resolve().parents:
        raise ValueError('unexpected nscd path')
    vdb = root / 'var/db/pkg'
    if not vdb.is_dir() or not list((vdb / 'sys-libs').glob('glibc-*/CONTENTS')):
        raise ValueError('Gentoo glibc package ownership records are missing')
    for contents in sorted(vdb.glob('*/*/CONTENTS')):
        if contents.is_symlink():
            raise ValueError('unexpected symlink in package ownership records')
        for line in contents.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1] == '/usr/sbin/nscd':
                return {'status': 'package-owned', 'root': str(root), 'owner': str(contents.parent)}
    reference = seed / 'usr/sbin/nscd'
    if reference.is_symlink() or not reference.is_file() or seed.resolve() not in reference.resolve().parents:
        raise ValueError('bootstrap nscd reference is missing or unsafe')
    checksum = digest(reference)
    if expected is not None and checksum != expected:
        raise ValueError('bootstrap reference differs from the inspected VM hash')
    if digest(candidate) != checksum:
        raise ValueError('unowned nscd differs from bootstrap; inspect before retirement')
    if list((root / 'etc/runlevels').glob('**/nscd')) or (root / 'etc/init.d/nscd').exists():
        raise ValueError('nscd service configuration exists; inspect before retirement')
    return {'status': 'bootstrap-unowned', 'root': str(root), 'sha256': checksum}


def retire(root, seed, backup, expected=None, apply=False):
    result = inspect(root, seed, expected)
    if result['status'] != 'bootstrap-unowned' or not apply:
        return result
    if backup.is_symlink():
        raise ValueError('backup directory must not be a symlink')
    backup.mkdir(parents=True, exist_ok=True)
    os.chmod(backup, 0o700)
    saved = backup / 'nscd'
    candidate = root / 'usr/sbin/nscd'
    if saved.is_symlink() or (saved.exists() and digest(saved) != result['sha256']):
        raise ValueError('existing nscd backup differs')
    if not saved.exists():
        atomic(saved, candidate.read_bytes())
    # Recheck provenance immediately before unlinking, including package ownership.
    if inspect(root, seed, expected) != result:
        raise ValueError('nscd provenance changed during backup')
    record = dict(result, status='retirement-prepared', backup=str(saved))
    atomic(backup / 'receipt.json', (json.dumps(record, sort_keys=True) + '\n').encode())
    candidate.unlink()
    sync_directory(candidate.parent)
    record['status'] = 'retired'
    atomic(backup / 'receipt.json', (json.dumps(record, sort_keys=True) + '\n').encode())
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--sysroot', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--expected-seed')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(retire(args.root, args.sysroot, args.backup, args.expected_seed, args.apply), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('nscd retirement: ' + str(error))
