"""Automatic hardware migration resolves receipts before mounting or copying."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'


class HardwareMigrationTests(unittest.TestCase):
    def run_fixture(self, **changes):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'mnt').mkdir()
            if changes.get('SYMLINK'):
                (root / 'mnt/gentoo-workspace').symlink_to(root)
            source = (GENTOO / 'setup-extra.sh').read_text()
            function = 'mount_recorded_hardware_workspace() {' + source.split('mount_recorded_hardware_workspace() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
            function = function.replace('/mnt', str(root / 'mnt')).replace('[[ -b $device ]]', '[[ -n $device ]]')
            branch = source.split('    migrate)\n', 1)[1].split('        ;;', 1)[0]
            fixture = r'''set -Eeuo pipefail
PROFILE=${PROFILE:-hardware}; STATE=$FIXTURE/state
WORKSPACE_UUID=fixture-uuid; WORKSPACE_LABEL=GENTOO_BUILD; EXTRA_REAL=/dev/sdb
fail() { echo "$*" >&2; exit 19; }
blkid() {
    case "$*" in
      *'-t UUID='*) printf '%b\n' "${CANDIDATES:-/dev/sdb1}";;
      *TYPE*) echo "${TYPE:-ext4}";;
      *LABEL*) echo GENTOO_BUILD;;
      *) return 99;;
    esac
}
readlink() { echo "${!#}"; }
lsblk() { echo "${PARENT:-sdb}"; }
findmnt() {
    if [[ $* == *'-S '* ]]; then
        if [[ -n ${EXISTING:-} ]]; then echo "$EXISTING"; return 0; fi
        return "${DISCOVERY_STATUS:-1}"
    fi
    echo "${OPTIONS:-ro,noload}"
}
mountpoint() { return 1; }
mount() { echo "mount $*"; }
check_workspace_source() { echo "check $1"; }
check_installed_continuation() { :; }
select_migration_policy() { :; }
copy_and_verify_migration() { echo copy-and-verify; }
'''
            command = fixture + function + branch
            env = dict(os.environ, FIXTURE=str(root), **{k: str(v) for k, v in changes.items()})
            if env.get('EXISTING') == 'target':
                env['EXISTING'] = str(root / 'mnt/gentoo-workspace')
            return subprocess.run(['bash', '-c', command], text=True, capture_output=True, env=env)

    def test_hardware_command_mounts_readonly_without_journal_replay_then_copies(self):
        result = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mount -t ext4 -o ro,noload /dev/sdb1 ', result.stdout)
        self.assertLess(result.stdout.index('mount -t'), result.stdout.index('check '))
        self.assertLess(result.stdout.index('check '), result.stdout.index('copy-and-verify'))

    def test_ambiguous_uuid_wrong_disk_filesystem_and_vm_refuse_before_mount(self):
        for changes in ({'CANDIDATES': '/dev/sdb1\n/dev/sdz1'}, {'PARENT': 'sdc'},
                        {'TYPE': 'xfs'}, {'PROFILE': 'vm'}, {'SYMLINK': '1'},
                        {'DISCOVERY_STATUS': '2'}, {'EXISTING': '/build'}):
            with self.subTest(changes=changes):
                result = self.run_fixture(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('mount -t', result.stdout)
                self.assertNotIn('copy-and-verify', result.stdout)

    def test_existing_readonly_mount_is_reused_but_readwrite_mount_refuses(self):
        result = self.run_fixture(EXISTING='target')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('mount -t', result.stdout)
        self.assertIn('copy-and-verify', result.stdout)
        result = self.run_fixture(EXISTING='target', OPTIONS='rw,relatime')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('copy-and-verify', result.stdout)
