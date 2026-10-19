#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Use pinned Portage's parser and effective settings before and after emerge."""
import os
import configparser
from pathlib import Path
import shlex
import stat
import subprocess
import sys

BINARY_OPTIONS = {'--usepkg', '--usepkgonly', '--getbinpkg', '--getbinpkgonly',
                  '--buildpkg', '--buildpkgonly', '--quickpkg-direct',
                  '--useoldpkg-atoms', '--rebuilt-binaries'}


def check_options(arguments, parse_opts):
    # Long-option abbreviations and combined short aliases are interpreted by
    # the same parser as emerge. Check default options independently too.
    _, options, _ = parse_opts(arguments, silent=True)
    for option in BINARY_OPTIONS:
        if options.get(option) not in (None, False, 'n'):
            raise ValueError('source-only Portage forbids ' + option)
    if options.get('--digest'):
        raise ValueError('source-only Portage forbids Manifest regeneration')
    for option in ('--root', '--sysroot', '--prefix', '--config-root', '--quickpkg-direct-root'):
        if option in options:
            raise ValueError('source-only settings must be selected by the caller: ' + option)


def check_cache(directory):
    path = Path(directory)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise ValueError('binary package cache must not be a symlink: ' + str(path))
    if not stat.S_ISDIR(mode):
        raise ValueError('binary package cache is not a directory: ' + str(path))
    def unreadable(error):
        raise error
    # Permit empty directories; refuse files, links and special nodes,
    # including indexes, rather than guessing binpkg filename extensions.
    for base, directories, files in os.walk(path, onerror=unreadable):
        if files or any((Path(base) / name).is_symlink() for name in directories):
            raise ValueError('binary package cache contains data: ' + str(path))


def check_settings(settings, binrepo_loader, portage):
    features = set(settings.features)
    if 'strict' not in features or features & {'assume-digests', 'digest', 'buildpkg',
                                              'buildsyspkg', 'getbinpkg'}:
        raise ValueError('effective FEATURES violates source-only strict digest policy')
    if settings.get('PORTAGE_BINHOST', '').strip():
        raise ValueError('effective PORTAGE_BINHOST is configured')
    from portage.const import BINREPOS_CONF_FILE, PORTAGE_BASE_PATH
    global_path = (Path(PORTAGE_BASE_PATH) / 'cnf' if portage._not_installed
                   else Path(settings.global_config_path))
    paths = [global_path / 'binrepos.conf',
             Path(settings['PORTAGE_CONFIGROOT']) / BINREPOS_CONF_FILE]
    for path in paths:
        files = sorted(path.rglob('*')) if path.is_dir() else [path]
        for file in files:
            if not file.is_file():
                continue
            parser = configparser.ConfigParser()
            with file.open() as stream:
                parser.read_file(stream)
            if parser.sections():
                raise ValueError('binary repository section configured: ' + str(file))
    # Match Portage's effective search paths, including its built-in defaults.
    if binrepo_loader([str(path) for path in paths], settings):
        raise ValueError('effective binary repository is configured')
    pkgdir = settings.get('PKGDIR', '')
    if not pkgdir or not os.path.isabs(pkgdir):
        raise ValueError('effective PKGDIR must be an absolute empty cache')
    check_cache(pkgdir)
    check_cache(str(Path(settings.get('EROOT', '/')) / 'var/cache/binpkgs'))


def run(emerge, arguments):
    import portage
    from _emerge.main import parse_opts
    from portage.binrepo.config import BinRepoConfigLoader

    def check():
        settings = portage.config()
        check_options(shlex.split(settings.get('EMERGE_DEFAULT_OPTS', '')), parse_opts)
        check_options(arguments, parse_opts)
        check_settings(settings, BinRepoConfigLoader, portage)

    check()
    try:
        result = subprocess.run([sys.executable, emerge, '--usepkg=n', '--getbinpkg=n',
                                 '--buildpkg=n', *arguments]).returncode
    finally:
        check()
    return 128 - result if result < 0 else result


if __name__ == '__main__':
    try:
        if len(sys.argv) < 2:
            raise ValueError('usage: source-only-emerge.py EMERGE [arguments]')
        sys.exit(run(sys.argv[1], sys.argv[2:]))
    except (OSError, ValueError, configparser.Error) as error:
        sys.exit('gentoo: ' + str(error))
