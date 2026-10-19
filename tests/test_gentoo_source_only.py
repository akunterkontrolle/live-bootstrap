"""Source-only enforcement checks actual pinned Portage parsing and settings."""
# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = importlib.util.spec_from_file_location('source_only', GENTOO / 'source-only-emerge.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
SOURCE = os.environ.get('GENTOO_TEST_PORTAGE_SOURCE')


class EmptyCacheTests(unittest.TestCase):
    def test_missing_or_empty_directories_pass_and_every_file_or_link_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / 'cache'
            guard.check_cache(cache)
            (cache / 'empty').mkdir(parents=True)
            guard.check_cache(cache)
            entry = cache / 'empty/Packages'
            entry.write_text('binpkg index')
            with self.assertRaisesRegex(ValueError, 'contains data'):
                guard.check_cache(cache)
            entry.unlink()
            entry.symlink_to(root / 'missing')
            with self.assertRaises(ValueError):
                guard.check_cache(cache)
            entry.unlink()
            alias = root / 'alias'
            alias.symlink_to(cache)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                guard.check_cache(alias)

    def test_unreadable_cache_is_not_assumed_empty(self):
        if os.geteuid() == 0:
            self.skipTest('root bypasses ordinary directory permissions')
        with tempfile.TemporaryDirectory() as temporary:
            locked = Path(temporary) / 'cache/locked'
            locked.mkdir(parents=True)
            locked.chmod(0)
            try:
                with self.assertRaises(PermissionError):
                    guard.check_cache(locked.parent)
            finally:
                locked.chmod(0o755)


@unittest.skipUnless(SOURCE, 'set GENTOO_TEST_PORTAGE_SOURCE to the verified pinned Portage source')
class PinnedPortagePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(SOURCE) / 'lib'))
        import portage
        from _emerge.main import parse_opts
        from portage.binrepo.config import BinRepoConfigLoader
        cls.portage = portage
        cls.parse_opts = staticmethod(parse_opts)
        cls.loader = staticmethod(BinRepoConfigLoader)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = self.root / 'config'
        portage_dir = config / 'etc/portage'
        portage_dir.mkdir(parents=True)
        self.makeconf = portage_dir / 'make.conf'
        self.makeconf.write_text('FEATURES="-assume-digests -digest strict -buildpkg -getbinpkg"\n'
                                 'PKGDIR="' + str(self.root / 'packages') + '"\n')
        repo = self.root / 'repo'
        (repo / 'profiles').mkdir(parents=True)
        (repo / 'profiles/repo_name').write_text('gentoo\n')
        (repo / 'profiles/eapi').write_text('8\n')
        (repo / 'profiles/make.defaults').write_text('ARCH="amd64"\n')
        (portage_dir / 'make.profile').symlink_to(repo / 'profiles')
        (portage_dir / 'repos.conf').write_text('[gentoo]\nlocation = ' + str(repo) + '\nauto-sync = no\n')
        self.config = config

    def settings(self):
        return self.portage.config(config_root=str(self.config), target_root=str(self.root / 'target'),
                                   env={'PATH': os.defpath})

    def test_all_binary_aliases_combined_options_and_supported_abbreviations_refuse(self):
        for arguments in (['-k'], ['-K'], ['-g'], ['-G'], ['-b'], ['-B'], ['-pvK'],
                          ['--usepkg=y'], ['--usepkgonly'], ['--getbinpkg=y'],
                          ['--getbinpkgonly=y'], ['--buildpkg'], ['--buildpkgonly'],
                          ['--getbinpkgo=y'], ['--useoldpkg-atoms', 'cat/pkg'],
                          ['--quickpkg-direct=y'], ['--fetch-binary'], ['--pkgdir=/tmp/other']):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises((ValueError, SystemExit)):
                    guard.check_options(arguments, self.parse_opts)
        guard.check_options(['--usepkg=n', '--getbinpkg=n', '--buildpkg=n', '--pretend', '@system'], self.parse_opts)

    def test_target_configuration_overrides_refuse(self):
        for name in ('--root', '--config-root', '--sysroot', '--prefix'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                guard.check_options([name + '=/other'], self.parse_opts)

    def test_effective_included_features_and_binhost_are_rejected(self):
        guard.check_settings(self.settings(), self.loader, self.portage)
        for line in ('FEATURES="strict buildpkg"\n', 'FEATURES="strict assume-digests"\n',
                     'PORTAGE_BINHOST="https://binary.invalid"\n'):
            with self.subTest(line=line):
                included = self.makeconf.with_name('binary-policy.conf')
                included.write_text(line)
                original = self.makeconf.read_text()
                self.makeconf.write_text(original + 'source ' + str(included) + '\n')
                with self.assertRaises(ValueError):
                    guard.check_settings(self.settings(), self.loader, self.portage)
                self.makeconf.write_text(original)

    def test_binrepos_directory_and_malformed_configuration_fail_closed(self):
        path = self.config / 'etc/portage/binrepos.conf'
        path.mkdir()
        file = path / 'repo.conf'
        for content in ('[remote]\nsync-uri = https://binary.invalid\n', '[broken\n',
                        '[remote-without-uri]\nlocation = /tmp/packages\n'):
            file.write_text(content)
            with self.subTest(content=content), self.assertRaises((ValueError, guard.configparser.Error)):
                guard.check_settings(self.settings(), self.loader, self.portage)

    def test_cache_inspection_after_failed_emerge_prevents_silent_binary_output(self):
        settings = self.settings()
        def merge(*args, **kwargs):
            cache = Path(settings['PKGDIR'])
            cache.mkdir()
            (cache / 'unexpected.gpkg.tar').write_bytes(b'binary output')
            return subprocess.CompletedProcess(args[0], 7)
        with patch.object(self.portage, 'config', return_value=settings), \
                patch.object(guard.subprocess, 'run', side_effect=merge):
            with self.assertRaisesRegex(ValueError, 'contains data'):
                guard.run('/fixture/emerge', ['--pretend', '@system'])

    def test_exit_status_preserved_and_unsafe_defaults_cannot_run_emerge(self):
        settings = self.settings()
        with patch.object(self.portage, 'config', return_value=settings), \
                patch.object(guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)) as merge:
            self.assertEqual(guard.run('/fixture/emerge', ['--pretend', '@system']), 7)
            self.assertEqual(merge.call_args.args[0][2:5], ['--usepkg=n', '--getbinpkg=n', '--buildpkg=n'])
        with patch.object(self.portage, 'config', return_value=settings), \
                patch.object(guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], -2)):
            self.assertEqual(guard.run('/fixture/emerge', ['--pretend', '@system']), 130)
        self.makeconf.write_text(self.makeconf.read_text() + 'EMERGE_DEFAULT_OPTS="-G"\n')
        settings = self.settings()
        with patch.object(self.portage, 'config', return_value=settings), patch.object(guard.subprocess, 'run') as merge:
            with self.assertRaises(ValueError):
                guard.run('/fixture/emerge', ['--pretend', '@system'])
            merge.assert_not_called()

    def test_generated_perl_environment_and_base_atoms_use_pinned_portage_syntax(self):
        from portage.dep import Atom
        from portage.exception import InvalidAtom
        from portage.util import getconfig
        native = self.root / 'native'
        helper = GENTOO / 'lib/perl-locale.sh'
        script = ('set -Eeuo pipefail\nNATIVE_ROOT=' + shlex.quote(str(native)) +
                  '\n. ' + shlex.quote(str(helper)) + '\n'
                  'chroot_env() { :; }\n'
                  'gentoo_die() { echo "$*" >&2; exit 1; }\n'
                  'perl_locale_install_policy\n')
        subprocess.run(['bash', '-c', script], check=True, capture_output=True, timeout=5)
        values = getconfig(str(native / 'etc/portage/env/90-gentoo-perl-locale.conf'),
                           tolerant=False, expand={'EXTRA_ECONF': '-Dprevious=define'})
        self.assertEqual(values['EXTRA_ECONF'].split(), [
            '-Dprevious=define', '-Dd_perl_lc_all_uses_name_value_pairs=define',
            '-Ud_perl_lc_all_separator', '-Ud_perl_lc_all_category_positions_init'])
        with self.assertRaises(InvalidAtom):
            Atom('sys-kernel/linux-firmware-20260916')
        for line in (GENTOO / 'policy/defaults/base-packages.list').read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                Atom(line.strip())


@unittest.skipUnless(os.environ.get('GENTOO_TEST_REPOSITORY'),
                     'set GENTOO_TEST_REPOSITORY to the extracted pinned repository')
class PinnedRepositoryAtomsTests(unittest.TestCase):
    def test_default_base_atoms_have_ebuilds_in_the_supplied_locked_repository(self):
        root = Path(os.environ['GENTOO_TEST_REPOSITORY'])
        lock = (GENTOO / 'sources/gentoo-repository.lock').read_text()
        self.assertIn(root.name + '.tar.gz|', lock)
        for line in (GENTOO / 'policy/defaults/base-packages.list').read_text().splitlines():
            atom = line.strip()
            if not atom or atom.startswith('#'):
                continue
            plain = atom.lstrip('=').split('[')[0]
            package = re.sub(r'-[0-9].*$', '', plain)
            candidates = list((root / package).glob('*.ebuild'))
            if atom.startswith('='):
                candidates = [p for p in candidates if p.stem == plain.split('/')[1]]
            self.assertTrue(candidates, atom)
