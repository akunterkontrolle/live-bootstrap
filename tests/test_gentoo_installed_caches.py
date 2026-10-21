"""Installed base caches must not depend on bootstrap scratch mounts."""
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = importlib.util.spec_from_file_location('installed_caches', GENTOO / 'configure-installed-portage.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class InstalledCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / 'etc/portage/make.conf'
        self.config.parent.mkdir(parents=True)
        self.before = ('CFLAGS="-O2 -pipe -march=native -mtune=native"\n'
                       'FEATURES="-assume-digests -digest strict -buildpkg -getbinpkg"\n' +
                       ''.join(name + '="' + paths[0] + '"\n' for name, paths in helper.SETTINGS.items()))
        self.config.write_text(self.before)
        self.config.chmod(0o644)
        # A non-root fixture owns its account; production reads the target's
        # packaged account rather than the bootstrap host's passwd database.
        uid = os.getuid() or 250
        gid = os.getgid() or 250
        (self.root / 'etc/passwd').write_text('portage:x:%s:%s:Portage:/var/tmp:/bin/false\n' % (uid, gid))

    def test_transition_preserves_cpu_and_source_policy_data_and_repeats(self):
        cache = self.root / 'var/cache/distfiles'
        cache.mkdir(parents=True)
        (cache / 'cached-source.tar.xz').write_text('keep existing source\n')
        self.assertTrue(helper.configure(self.root))
        after = self.config.read_text()
        self.assertIn('CFLAGS="-O2 -pipe -march=native -mtune=native"', after)
        self.assertIn('FEATURES="-assume-digests -digest strict -buildpkg -getbinpkg"', after)
        self.assertNotIn('/gentoo-scratch/', after)
        for name, (_, destination) in helper.SETTINGS.items():
            self.assertIn(name + '="' + destination + '"', after)
            self.assertEqual((self.root / destination.lstrip('/')).stat().st_mode & 0o7777, 0o2775)
        self.assertEqual((cache / 'cached-source.tar.xz').read_text(), 'keep existing source\n')
        self.assertEqual(self.config.with_name('make.conf.before-installed-caches').read_text(), self.before)
        self.assertFalse(helper.configure(self.root))
        self.assertEqual(self.config.read_text(), after)

    def test_operator_desktop_path_or_duplicate_assignment_is_not_overwritten(self):
        for data in (self.before.replace('/gentoo-scratch/distfiles', '/extra/portage/distfiles'),
                     self.before + 'PKGDIR="/other"\n'):
            self.config.write_text(data)
            with self.assertRaises(ValueError):
                helper.configure(self.root)
            self.assertEqual(self.config.read_text(), data)
            self.assertFalse((self.root / 'var').exists())

    def test_redirected_cache_parent_is_refused_without_configuration_changes(self):
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (self.root / 'var').symlink_to(elsewhere)
        with self.assertRaises(ValueError):
            helper.configure(self.root)
        self.assertEqual(self.config.read_text(), self.before)
        self.assertEqual(list(elsewhere.iterdir()), [])
