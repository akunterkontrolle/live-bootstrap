"""Reviewable checks for the encrypted workspace migration helper."""
from pathlib import Path
import os
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


class ExtraRootResolutionTests(unittest.TestCase):
    def test_lvs_receives_lv_name_even_when_device_resolves_to_dm_node(self):
        source = (GENTOO / 'setup-extra.sh').read_text()
        function = 'resolve_devices() {' + source.split('resolve_devices() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        # Fixture paths represent devices; all discovery/LVM commands remain real shell calls to stubs.
        function = function.replace('[[ -b ', '[[ -n ').replace(' && -b ', ' && -n ')
        fixture = r'''set -Eeuo pipefail
VG=gentoo; ROOT_LV=root; HOME_LV=home; ROOT_PROFILE=vm; PROFILE=vm
ROOT_SERIAL=ROOT; ROOT_BYTES=1000; EXTRA_SERIAL=EXTRA; EXTRA_BYTES=2000
fail() { echo "$*" >&2; exit 19; }
[() { if builtin [ "$1" = -b ]; then return 0; fi; builtin [ "$@"; }
readlink() {
    case "${!#}" in /dev/gentoo/root) echo /dev/dm-1;; /dev/gentoo/home) echo /dev/dm-2;; *) echo "${!#}";; esac
}
mountpoint() { return 0; }
findmnt() { echo /dev/dm-2; }
lvs() { [[ ! -e /proc/self/fd/9 ]] || { echo "inherited lock fd" >&2; return 78; }; [ "${!#}" = gentoo/root ] || { echo 'dm node parsed as VG' >&2; return 79; }; echo '/dev/mapper/gentoo-crypt(0)'; }
lsblk() {
    case "$*" in
      *NAME,TYPE*) echo '/dev/sdc disk';;
      *TYPE*) echo disk;;
      *SIZE*' /dev/sdc') echo 1000;;
      *SIZE*) echo 2000;;
      *MAJ:MIN*) echo 8:16;;
      *) return 99;;
    esac
}
disk_serial() { case "$1" in /dev/sdc) echo ROOT;; /dev/sdb) echo EXTRA;; esac; }
find_by_serial() { echo /dev/sdb; }
'''
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, TEST_LOCK=str(Path(temporary) / 'lock'))
            locked = fixture + 'exec 9> "$TEST_LOCK"\nflock -n 9\n'
            check_lock = r'''[[ -e /proc/self/fd/9 ]]
if flock -n "$TEST_LOCK" true 9>&-; then echo 'parent lock lost' >&2; exit 79; fi
exec 9>&-
flock -n "$TEST_LOCK" true
'''
            fixed = subprocess.run(['bash', '-c', locked + function + 'resolve_devices\n' + check_lock],
                                   env=env, capture_output=True, text=True)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            broken = function.replace('"$VG/$ROOT_LV"', '"$ROOT_DEVICE"')
            result = subprocess.run(['bash', '-c', locked + broken + 'resolve_devices\n'],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('dm node parsed as VG', result.stderr)
            leaky = function.replace('"$VG/$ROOT_LV" 9>&-', '"$VG/$ROOT_LV"')
            result = subprocess.run(['bash', '-c', locked + leaky + 'resolve_devices\n'],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('inherited lock fd', result.stderr)



class ExtraSerialDiscoveryTests(unittest.TestCase):
    def test_extra_reader_uses_shared_vpd_fallback_and_refuses_missing_serial(self):
        import shlex
        source = (GENTOO / 'setup-extra.sh').read_text()
        function = 'disk_serial() {' + source.split('disk_serial() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = b'GUEST-ROOT '
            (root / 'vpd_pg80').write_bytes(b'\0\x80' + len(payload).to_bytes(2, 'big') + payload)
            fixture = ('. ' + shlex.quote(str(GENTOO / 'lib/devices.sh')) + '\n'
                       'lsblk() { return 0; }\n'
                       'readlink() { echo ' + shlex.quote(str(root)) + '; }\n'
                       'fail() { echo "$*" >&2; exit 19; }\n')
            run = lambda: subprocess.run(['bash', '-c', fixture + function + 'disk_serial /dev/sdc\n'],
                                         text=True, capture_output=True)
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'GUEST-ROOT\n')
            (root / 'vpd_pg80').unlink()
            result = run()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('cannot read disk serial', result.stderr)
