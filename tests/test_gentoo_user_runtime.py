"""Configure PAM runtime ordering while preserving login policy and operator changes."""
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
from pathlib import Path
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = importlib.util.spec_from_file_location('user_runtime', GENTOO / 'configure-user-runtime.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class UserRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.policy = self.root / 'etc/pam.d/system-login'
        self.policy.parent.mkdir(parents=True)
        self.original = 'auth required pam_unix.so\n-session optional pam_openrc.so\nsession required pam_limits.so\n'
        self.policy.write_text(self.original)
        self.policy.chmod(0o644)
        self.module = self.root / 'lib64/security/pam_xdg.so'
        self.module.parent.mkdir(parents=True)
        self.module.write_bytes(b'package-owned module fixture')

    def test_ordered_insertion_preserves_authentication_and_backup_and_is_idempotent(self):
        self.assertTrue(helper.configure(self.root))
        expected = self.original.replace('-session optional pam_openrc.so',
                                         helper.RULE + '-session optional pam_openrc.so')
        self.assertEqual(self.policy.read_text(), expected)
        self.assertEqual(self.policy.with_name('system-login.before-gentoo-xdg').read_text(), self.original)
        self.assertEqual(self.policy.stat().st_mode & 0o777, 0o644)
        self.assertFalse(helper.configure(self.root))

    def test_missing_module_duplicate_hook_or_custom_provider_refuses_without_policy_changes(self):
        self.module.unlink()
        with self.assertRaises(ValueError):
            helper.configure(self.root)
        self.assertEqual(self.policy.read_text(), self.original)
        self.module.write_bytes(b'installed module')
        for policy in (self.original + '-session optional pam_openrc.so\n',
                       'session optional pam_xdg.so track_sessions\n' + self.original,
                       'session optional pam_elogind.so\n' + self.original):
            self.policy.write_text(policy)
            with self.assertRaises(ValueError):
                helper.configure(self.root)
            self.assertEqual(self.policy.read_text(), policy)
