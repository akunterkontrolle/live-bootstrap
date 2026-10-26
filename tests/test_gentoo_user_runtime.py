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


class DesktopRuntimeTests(UserRuntimeTests):
    def setUp(self):
        super().setUp()
        self.original = self.original.replace('-session optional pam_openrc.so', helper.RULE + '-session optional pam_openrc.so')
        self.policy.write_text(self.original)
        (self.module.parent / 'pam_elogind.so').write_bytes(b'installed elogind PAM module')
        package = self.root / 'var/db/pkg/sys-auth/pambase-20251104-r1'; package.mkdir(parents=True)
        self.use = package / 'USE'; self.use.write_text('amd64 pam elogind\n')
        self.target = self.original.replace(helper.RULE, '-session\toptional\tpam_elogind.so\n')

    # The inherited base-insertion tests exercise a different starting state.
    def test_ordered_insertion_preserves_authentication_and_backup_and_is_idempotent(self):
        candidate = self.policy.with_name('._cfg0000_system-login'); candidate.write_text(self.target)
        self.assertTrue(helper.configure(self.root, 'elogind'))
        self.assertEqual(self.policy.read_text(), self.target)
        self.assertEqual(self.policy.with_name('system-login.before-ms1504-elogind').read_text(), self.original)
        self.assertEqual(self.policy.with_name('system-login.packaged-ms1504-elogind').read_text(), self.target)
        self.assertFalse(candidate.exists())
        self.assertFalse(helper.configure(self.root, 'elogind', check=True))
        self.assertFalse(helper.configure(self.root, 'elogind'))

    def test_missing_module_duplicate_hook_or_custom_provider_refuses_without_policy_changes(self):
        for text in (self.original + '-session optional pam_elogind.so\n',
                     self.original.replace(helper.RULE, 'session optional pam_xdg.so track_sessions\n')):
            self.policy.write_text(text)
            with self.assertRaises(ValueError): helper.configure(self.root, 'elogind')
            self.assertEqual(self.policy.read_text(), text)

    def test_changed_authentication_candidate_requires_review_before_any_pam_change(self):
        candidate = self.policy.with_name('._cfg0000_system-login')
        candidate.write_text(self.target.replace('auth required pam_unix.so', 'auth required pam_deny.so'))
        with self.assertRaisesRegex(ValueError, 'changes more'):
            helper.configure(self.root, 'elogind')
        self.assertEqual(self.policy.read_text(), self.original)
        self.assertTrue(candidate.exists())
        self.assertFalse(self.policy.with_name('system-login.before-ms1504-elogind').exists())

    def test_missing_packaged_provider_and_check_mode_refuse_without_mutating(self):
        with self.assertRaises(ValueError): helper.configure(self.root, 'elogind', check=True)
        self.use.write_text('amd64 pam\n')
        with self.assertRaisesRegex(ValueError, 'USE=elogind'): helper.configure(self.root, 'elogind')
        self.assertEqual(self.policy.read_text(), self.original)

    def test_base_helper_refuses_two_runtime_providers_even_if_its_rule_is_present(self):
        text = 'session optional pam_elogind.so\n' + self.original
        self.policy.write_text(text)
        with self.assertRaisesRegex(ValueError, 'another runtime'):
            helper.configure(self.root)
        self.assertEqual(self.policy.read_text(), text)
