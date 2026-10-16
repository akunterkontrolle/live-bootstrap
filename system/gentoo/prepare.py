#!/usr/bin/env python3
"""Prepare an explicit, self-contained input bundle on the host; no VM changes."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.gentoo_config import load, render, root_image_gib

# Extend this list in the batch that adds each runtime artifact.
BUNDLE_FILES = (
    'gentoo.sh', 'lib/common.sh', 'lib/devices.sh', 'lib/state.sh',
    'lib/sources.sh', 'stages/00-inputs.sh',
)


def prepare(config, mode, output):
    settings = load(config, mode)
    output = Path(output).absolute()
    if output.exists():
        raise ValueError(f'Output already exists (never overwritten): {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='.gentoo-inputs-', dir=output.parent) as temporary:
        bundle = Path(temporary) / 'bundle'
        bundle.mkdir()
        for name in BUNDLE_FILES:
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        for key, value in settings['packages'].items():
            if value:
                (bundle / 'policy').mkdir(exist_ok=True)
                shutil.copyfile(value, bundle / 'policy' / key)
        (bundle / 'config.sh').write_text(render(settings, mode), encoding='utf-8')
        (bundle / 'config.inputs.sh').write_text(render(settings, mode, semantic=True), encoding='utf-8')
        files = sorted(p for p in bundle.rglob('*') if p.is_file())
        manifest = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(bundle).as_posix()}\n'
                           for p in files)
        (bundle / 'SHA256SUMS').write_text(manifest, encoding='utf-8')
        bundle.rename(output)
    return root_image_gib(settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--mode', required=True, choices=('vm', 'hardware'))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        capacity = prepare(args.config, args.mode, args.output)
    except (ValueError, OSError) as error:
        parser.exit(2, f'{error}\n')
    print(f'Prepared {args.output}; planned installation-root capacity: {capacity} GiB')


if __name__ == '__main__':
    main()
