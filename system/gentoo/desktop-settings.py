#!/usr/bin/env python3
"""Preserve operator settings and bind desktop resume to effective build inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def atomic(path, data, mode):
    if path.is_symlink():
        raise ValueError('refusing symlink: ' + str(path))
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def configure(root, mode, locale, sysfs=Path('/sys/bus/pci/devices')):
    path = root / 'etc/portage/make.conf'
    if path.is_symlink() or not path.is_file():
        raise ValueError('make.conf must be a regular file')
    data = path.read_text()
    defaults = {}
    if mode == 'hardware' and not re.search(r'^\s*(?:export\s+)?VIDEO_CARDS\s*=', data, re.M):
        graphics = [device for device in sysfs.iterdir()
                    if (device / 'class').read_text().strip().startswith('0x03')]
        if not graphics or any((device / 'vendor').read_text().strip() != '0x8086' for device in graphics):
            raise ValueError('set VIDEO_CARDS explicitly for the discovered non-Intel/unknown graphics')
        defaults['VIDEO_CARDS'] = 'intel'
    language = locale.split('_', 1)[0].split('.', 1)[0]
    if not re.fullmatch(r'[a-z]{2,3}', language):
        raise ValueError('cannot derive language from configured locale: ' + locale)
    for key in ('L10N', 'LINGUAS'):
        if not re.search(r'^\s*(?:export\s+)?' + key + r'\s*=', data, re.M):
            defaults[key] = language
    if not defaults:
        return False
    backup = path.with_name('make.conf.before-desktop-defaults')
    if backup.is_symlink() or (backup.exists() and backup.read_text() != data):
        raise ValueError('existing desktop-defaults backup differs; inspect before changing settings')
    if not backup.exists():
        atomic(backup, data.encode(), path.stat().st_mode & 0o777)
    updated = data.rstrip('\n') + '\n' + ''.join(f'{key}="{value}"\n' for key, value in defaults.items())
    atomic(path, updated.encode(), path.stat().st_mode & 0o777)
    return True


def semantic_inputs(root, settings, generation=None):
    keys = ('CHOST', 'CFLAGS', 'CXXFLAGS', 'ABI_X86', 'CPU_FLAGS_X86', 'VIDEO_CARDS',
            'L10N', 'LINGUAS', 'USE', 'FEATURES', 'ACCEPT_KEYWORDS', 'ACCEPT_LICENSE',
            'PYTHON_TARGETS', 'PYTHON_SINGLE_TARGET', 'LLVM_TARGETS')
    result = {'settings': {key: settings.get(key, '') for key in keys}, 'files': {}}
    base = root / 'etc/portage'
    for name in ('package.use', 'package.accept_keywords', 'package.mask', 'package.unmask',
                 'package.env', 'package.license', 'bashrc', 'env', 'repos.conf', 'native-cpu-policy.json'):
        path = base / name
        entries = sorted(path.rglob('*')) if path.is_dir() else [path]
        for item in entries:
            if item.is_symlink():
                raise ValueError('indirect package policy requires review: ' + str(item))
            if item.is_file():
                result['files'][str(item.relative_to(base))] = hashlib.sha256(item.read_bytes()).hexdigest()
    profile = base / 'make.profile'
    result['profile'] = str(profile.resolve(strict=True))
    if generation:
        receipt = Path(generation)
        if receipt.is_symlink() or not receipt.is_file():
            raise ValueError('missing/unsafe pending generation receipt')
        result['generation'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    return result


def bind(path, inputs):
    data = (json.dumps(inputs, sort_keys=True, indent=2) + '\n').encode()
    if path.is_symlink():
        raise ValueError('desktop input receipt must not be a symlink')
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('effective desktop inputs changed; preserve receipts and review before resuming')
        return False
    atomic(path, data, 0o600)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    actions = parser.add_subparsers(dest='action', required=True)
    defaults = actions.add_parser('configure')
    defaults.add_argument('--mode', choices=('vm', 'hardware'), required=True)
    defaults.add_argument('--locale', required=True)
    record = actions.add_parser('record')
    record.add_argument('--receipt', type=Path, required=True)
    record.add_argument('--generation', type=Path)
    args = parser.parse_args()
    if args.action == 'configure':
        changed = configure(args.root, args.mode, args.locale)
        print('Desktop defaults ' + ('configured; existing assignments preserved.' if changed else 'already configured.'))
    else:
        import portage
        settings = portage.config()
        bind(args.receipt, semantic_inputs(args.root, settings, args.generation))
        print('Effective desktop build inputs recorded/verified.')
        for key in ('VIDEO_CARDS', 'L10N', 'LINGUAS'):
            print(key + '=' + settings.get(key, ''))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit('desktop-settings: ' + str(exc))
