"""Avoid unattended userspace clocks while preserving explicit power policy."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = spec_from_file_location('desktop_power', GENTOO / 'desktop-power.py')
helper = module_from_spec(spec); spec.loader.exec_module(helper)


class DesktopPowerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sysfs = self.root / 'cpufreq'
        for number in range(2):
            policy = self.sysfs / f'policy{number}'; policy.mkdir(parents=True)
            (policy / 'scaling_available_governors').write_text('userspace performance ondemand schedutil\n')
            (policy / 'scaling_governor').write_text('userspace\n')
        self.config = self.root / 'etc/conf.d/cpupower'; self.config.parent.mkdir(parents=True)
        self.config.write_text('# packaged defaults\nSTART_OPTS="--governor ondemand"\nSTOP_OPTS=""\n')
        self.config.chmod(0o644)

    def test_prepare_replaces_userspace_and_preserves_explicit_performance(self):
        (self.sysfs / 'policy1/scaling_governor').write_text('performance\n')
        self.assertTrue(helper.prepare(self.sysfs))
        self.assertEqual((self.sysfs / 'policy0/scaling_governor').read_text().strip(), 'schedutil')
        self.assertEqual((self.sysfs / 'policy1/scaling_governor').read_text().strip(), 'performance')
        self.assertFalse(helper.prepare(self.sysfs))

    def test_adaptive_boot_policy_is_backed_up_idempotent_and_checkable(self):
        original = self.config.read_text()
        with self.assertRaises(ValueError): helper.configure(self.root, self.sysfs, check=True)
        self.assertEqual(self.config.read_text(), original)
        self.assertTrue(helper.configure(self.root, self.sysfs))
        self.assertIn('START_OPTS="--governor schedutil"', self.config.read_text())
        self.assertEqual(self.config.with_name('cpupower.before-ms1504-desktop').read_text(), original)
        self.assertFalse(helper.configure(self.root, self.sysfs, check=True))

    def test_custom_boot_policy_is_not_replaced(self):
        custom = 'START_OPTS="--governor performance --min 2GHz"\n'
        self.config.write_text(custom)
        with self.assertRaisesRegex(ValueError, 'custom'):
            helper.configure(self.root, self.sysfs)
        self.assertEqual(self.config.read_text(), custom)
