"""Reviewable checks for the encrypted workspace migration helper."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


class ExtraMigrationTests(unittest.TestCase):
    def test_bundle_and_installed_identity_include_extra_workspace(self):
        import sys
        sys.path.insert(0, str(ROOT))
        from system.gentoo.prepare import BUNDLE_FILES
        self.assertIn('setup-extra.sh', BUNDLE_FILES)
        installer = (GENTOO / 'install-base.sh').read_text()
        self.assertIn('etc/ms1504-extra-identity', installer)
        self.assertIn('by_id=%s', installer)
        self.assertIn('workspace_uuid=%s', installer)
        self.assertIn('ms1504-extra', installer)

    def test_bash_and_generated_openrc_service_syntax(self):
        script = GENTOO / 'setup-extra.sh'
        result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        source = script.read_text()
        service = source.split("<<'OPENRC'\n", 1)[1].split('\nOPENRC', 1)[0]
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8') as fixture:
            fixture.write(service + '\n')
            fixture.flush()
            result = subprocess.run(['/bin/sh', '-n', fixture.name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('\n    [[ ', service, 'OpenRC runs its services through /bin/sh')
        self.assertIn('umount /build', service)

    def test_migration_and_destructive_phases_have_separate_gates(self):
        script = (GENTOO / 'setup-extra.sh').read_text()
        migrate = script[script.index('    migrate)'):script.index('    initialize)')]
        initialize = script[script.index('    initialize)'):script.index('    resume)')]
        resume = script[script.index('    resume)'):script.index('    restore)')]
        discard = script[script.index('    discard-migration)'):]
        self.assertIn('check_workspace_source', migrate)
        self.assertIn('copy_and_verify_migration', migrate)
        self.assertNotIn('sgdisk', migrate)
        self.assertLess(initialize.index('validate_home_migration'), initialize.index('sgdisk --zap-all'))
        self.assertLess(initialize.index('ERASE-EXTRA'), initialize.index('sgdisk --zap-all'))
        self.assertIn('partition.done', resume)
        self.assertIn('LUKS formatting was interrupted', resume)
        self.assertIn('filesystem formatting was interrupted', resume)
        self.assertLess(discard.index('verify.done'), discard.index('DELETE-MIGRATION'))
        self.assertLess(discard.index('DELETE-MIGRATION'), discard.index('rm -rf --one-file-system'))

    def test_restore_checks_filesystem_space_and_persistent_openrc_mount(self):
        script = (GENTOO / 'setup-extra.sh').read_text()
        restore = script[script.index('    restore)'):script.index('    verify)')]
        self.assertIn('df -B1 --output=avail "$MOUNT"', restore)
        self.assertIn('required / 20 + 536870912', restore)
        self.assertIn('sha256sum --check', restore)
        self.assertIn('rc-update add ms1504-extra default', script)
        self.assertIn('cryptsetup open --key-file "$KEY"', script)
        self.assertIn('LUKS_UUID=', script)


if __name__ == '__main__':
    unittest.main()
