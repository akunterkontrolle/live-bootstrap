#!/usr/bin/env python3
"""Escape an unmanaged userspace governor and configure adaptive laptop scaling."""
import argparse
from pathlib import Path
import re
from importlib.util import module_from_spec, spec_from_file_location

spec = spec_from_file_location('desktop_settings', Path(__file__).with_name('desktop-settings.py'))
settings = module_from_spec(spec)
spec.loader.exec_module(settings)


def policies(sysfs):
    return sorted(sysfs.glob('policy*'))


def governor(paths):
    available = set.intersection(*(set((path / 'scaling_available_governors').read_text().split()) for path in paths)) if paths else set()
    for name in ('schedutil', 'ondemand'):
        if name in available:
            return name
    raise ValueError('no reviewed common CPU governor is available')


def prepare(sysfs):
    paths = policies(sysfs)
    stuck = [path for path in paths if (path / 'scaling_governor').read_text().strip() == 'userspace']
    if not stuck:
        return False
    selected = governor(paths)
    changed = []
    try:
        for path in stuck:
            (path / 'scaling_governor').write_text(selected + '\n')
            changed.append(path)
            if (path / 'scaling_governor').read_text().strip() != selected:
                raise ValueError('CPU governor did not change: ' + str(path))
    except (OSError, ValueError):
        for path in changed:
            (path / 'scaling_governor').write_text('userspace\n')
        raise
    print('Replaced unmanaged userspace CPU governor with ' + selected)
    return True


def configure(root, sysfs, check=False):
    selected = governor(policies(sysfs))
    path = root / 'etc/conf.d/cpupower'
    if path.is_symlink() or not path.is_file():
        raise ValueError('install packaged cpupower before configuring adaptive scaling')
    data = path.read_text()
    lines = re.findall(r'^START_OPTS=(.*)$', data, re.M)
    desired = '"--governor ' + selected + '"'
    if lines == [desired]:
        return False
    if check:
        raise ValueError('adaptive cpupower boot configuration differs')
    if lines != ['"--governor ondemand"']:
        raise ValueError('custom cpupower START_OPTS requires review; configuration preserved')
    backup = path.with_name('cpupower.before-ms1504-desktop')
    if backup.is_symlink() or (backup.exists() and backup.read_text() != data):
        raise ValueError('cpupower backup differs; inspect before replacing policy')
    if not backup.exists():
        settings.atomic(backup, data.encode(), path.stat().st_mode & 0o777)
    updated = re.sub(r'^START_OPTS=.*$', 'START_OPTS=' + desired, data, flags=re.M)
    settings.atomic(path, updated.encode(), path.stat().st_mode & 0o777)
    print('Configured cpupower boot governor: ' + selected)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--sysfs', type=Path, default=Path('/sys/devices/system/cpu/cpufreq'))
    parser.add_argument('action', choices=('prepare', 'configure'))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.sysfs)
    else:
        configure(args.root, args.sysfs, check=args.check)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit('desktop-power: ' + str(exc))
