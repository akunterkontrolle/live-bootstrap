#!/usr/bin/env python3
"""Run maintained Gentoo syntax and behavior checks with checksum-pinned Portage."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--portage-archive', type=Path, help='existing locked source archive')
    source.add_argument('--fetch-portage', action='store_true', help='download locked source with curl')
    args = parser.parse_args()
    rows = [line.split('|') for line in (GENTOO / 'sources/portage.lock').read_text().splitlines()
            if line and not line.startswith('#')]
    if len(rows) != 1 or len(rows[0]) != 3:
        raise ValueError('expected one Portage lock record')
    name, url, checksum = rows[0]
    algorithm, expected = checksum.split('=', 1)
    if algorithm != 'sha512' or Path(name).name != name:
        raise ValueError('unexpected Portage archive lock')
    with tempfile.TemporaryDirectory(prefix='gentoo-checks-') as temporary:
        work = Path(temporary)
        archive = args.portage_archive
        if args.fetch_portage:
            archive = work / name
            subprocess.run(['curl', '--fail', '--location', '--retry', '3',
                            '--connect-timeout', '30', '--max-time', '300',
                            '--output', str(archive), url], check=True)
        with archive.open('rb') as stream:
            if hashlib.file_digest(stream, algorithm).hexdigest() != expected:
                raise ValueError('Portage archive checksum mismatch')
        # Portage includes relative helper symlinks; the standard data filter
        # preserves those while refusing escaping links and special devices.
        if not hasattr(tarfile, 'data_filter'):
            raise ValueError('checks require Python with tarfile.data_filter (3.11.4+)')
        with tarfile.open(archive) as packed:
            packed.extractall(work / 'source', filter=tarfile.data_filter)
        sources = list((work / 'source').iterdir())
        if len(sources) != 1 or not (sources[0] / 'lib/portage').is_dir():
            raise ValueError('unexpected Portage archive layout')
        paths = sorted(set(GENTOO.rglob('*.py')) | set(GENTOO.rglob('*.sh')) |
                       set((ROOT / 'tests').glob('*gentoo*.py')) |
                       {ROOT / 'rootfs.py', ROOT / 'lib/gentoo_config.py'})
        shell_count = python_count = 0
        for path in paths:
            if path.suffix == '.sh':
                subprocess.run(['bash', '-n', str(path)], check=True)
                shell_count += 1
            else:
                compile(path.read_bytes(), str(path), 'exec')
                python_count += 1
        print(f'Syntax passed: {shell_count} shell and {python_count} Python files', flush=True)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', LC_ALL='C',
                   GENTOO_TEST_PORTAGE_SOURCE=str(sources[0]),
                   GENTOO_AUDIT_PORTAGE_SOURCE=str(sources[0]))
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                        '-p', 'test_gentoo*.py', '-v'], cwd=ROOT, env=env, check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'gentoo-checks: {error}')
