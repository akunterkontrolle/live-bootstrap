"""Effective desktop policy survives configuration and rejects incompatible resume."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = spec_from_file_location('desktop_settings', GENTOO / 'desktop-settings.py')
helper = module_from_spec(spec); spec.loader.exec_module(helper)


class DesktopSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'etc/portage'; self.base.mkdir(parents=True)
        self.config = self.base / 'make.conf'; self.config.write_text('CFLAGS="native flags"\nMAKEOPTS="-j4"\n')
        self.config.chmod(0o644)
        self.profile = self.root / 'profile'; self.profile.mkdir()
        (self.base / 'make.profile').symlink_to(self.profile)
        self.sysfs = self.root / 'pci'; gpu = self.sysfs / '0000:00:02.0'; gpu.mkdir(parents=True)
        (gpu / 'class').write_text('0x030000\n'); (gpu / 'vendor').write_text('0x8086\n')

    def test_hardware_defaults_preserve_native_flags_and_existing_operator_values(self):
        original = self.config.read_text()
        self.assertTrue(helper.configure(self.root, 'hardware', 'de_DE.UTF-8', self.sysfs))
        self.assertIn(original, self.config.read_text())
        self.assertIn('VIDEO_CARDS="intel"', self.config.read_text())
        self.assertIn('L10N="de"', self.config.read_text())
        self.assertEqual((self.base / 'make.conf.before-desktop-defaults').read_text(), original)
        self.assertFalse(helper.configure(self.root, 'hardware', 'de_DE.UTF-8', self.sysfs))
        custom = 'VIDEO_CARDS="custom"\nL10N="fr"\nLINGUAS="fr"\n'
        self.config.write_text(custom)
        self.assertFalse(helper.configure(self.root, 'hardware', 'de_DE.UTF-8', self.sysfs))
        self.assertEqual(self.config.read_text(), custom)

    def test_unknown_graphics_refuses_without_changing_make_conf(self):
        (self.sysfs / '0000:00:02.0/vendor').write_text('0x10de\n')
        original = self.config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'VIDEO_CARDS explicitly'):
            helper.configure(self.root, 'hardware', 'de_DE.UTF-8', self.sysfs)
        self.assertEqual(self.config.read_bytes(), original)

    def test_resume_binds_effective_settings_and_package_policy_but_allows_job_tuning(self):
        policy = self.base / 'package.use'; policy.mkdir(); use = policy / 'custom'; use.write_text('package flag\n')
        generation = self.root / 'pending'; generation.write_text('snapshot_uuid=abc\n')
        settings = {'CFLAGS': '-march=native', 'VIDEO_CARDS': 'intel', 'MAKEOPTS': '-j4'}
        receipt = self.root / 'inputs.json'
        self.assertTrue(helper.bind(receipt, helper.semantic_inputs(self.root, settings, generation)))
        self.assertFalse(helper.bind(receipt, helper.semantic_inputs(self.root, settings | {'MAKEOPTS': '-j6'}, generation)))
        for changed in (settings | {'CFLAGS': '-march=generic'}, settings | {'VIDEO_CARDS': 'nouveau'}):
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                helper.bind(receipt, helper.semantic_inputs(self.root, changed, generation))
        use.write_text('package other-flag\n')
        with self.assertRaisesRegex(ValueError, 'inputs changed'):
            helper.bind(receipt, helper.semantic_inputs(self.root, settings, generation))

    def test_vm_does_not_invent_hardware_graphics(self):
        self.assertTrue(helper.configure(self.root, 'vm', 'en_US.UTF-8', self.root / 'missing'))
        self.assertNotIn('VIDEO_CARDS', self.config.read_text())
