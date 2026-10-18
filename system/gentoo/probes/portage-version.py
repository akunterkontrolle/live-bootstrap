#!/usr/bin/env python3
"""Verify a pinned, uninstalled Portage tree and its actual emerge banner."""
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import re
import sys

EXPECTED = '3.0.82.2'


def verify(source, banner, portage):
    source = Path(source).resolve()
    if Path(portage.__file__).resolve() != source / 'lib/portage/__init__.py':
        raise ValueError('Portage imported from an unexpected source tree')
    # Meson itself uses this project version when source VERSION is HEAD.
    project = (source / 'meson.build').read_text().split('\n)', 1)[0]
    if not re.match(r"\s*project\(\s*'portage'\s*,", project):
        raise ValueError('unrecognized Portage Meson project declaration')
    versions = re.findall(r"\bversion\s*:\s*'([^']+)'", project)
    if versions != [EXPECTED]:
        raise ValueError('Portage source project version differs from ' + EXPECTED)
    from portage.installation import TYPE, TYPES
    if TYPE != TYPES.SOURCE:
        raise ValueError('expected the uninstalled Portage source runtime')
    runtime = str(portage.VERSION)
    if runtime not in ('HEAD', EXPECTED):
        raise ValueError('unexpected source Portage runtime version: ' + runtime)
    rows = [line for line in banner.splitlines() if line.startswith('Portage ')]
    if len(rows) != 1 or not rows[0].startswith('Portage ' + runtime + ' ('):
        raise ValueError('emerge banner disagrees with the verified source runtime')
    print('Verified pinned Portage source %s (uninstalled runtime banner: %s)' % (EXPECTED, runtime))


def main():
    import portage
    if len(sys.argv) != 3:
        raise ValueError('usage: portage-version.py SOURCE VERSION-OUTPUT')
    verify(sys.argv[1], Path(sys.argv[2]).read_text(), portage)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('gentoo: Portage source version verification failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
