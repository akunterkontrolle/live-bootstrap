"""Exercise selective migration using real rsync, snapshots and legacy journals."""
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = importlib.util.spec_from_file_location('retention', GENTOO / 'workspace-retention.py')
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


def function(name):
    source = (GENTOO / 'setup-extra.sh').read_text()
    start = name + '() {'
    return start + source.split(start, 1)[1].split('\n}\n', 1)[0] + '\n}\n'


class SelectedMigrationTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which('rsync'):
            self.skipTest('rsync is required')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.bootstrap = self.source / 'gentoo-bootstrap'
        self.bootstrap.mkdir(parents=True)
        for name in ('build', 'sources', 'toolchain', 'native-root-x86_64', 'inputs', 'state', 'artifacts', 'distfiles'):
            (self.bootstrap / name).mkdir()
            (self.bootstrap / name / 'file').write_text(name + '\n')
        (self.source / 'operator-data').write_text('keep unknown files\n')
        (self.bootstrap / 'artifacts/link').symlink_to('../state/file')
        (self.bootstrap / 'artifacts/external-reference').symlink_to('/unavailable/provenance-reference')
        os.link(self.bootstrap / 'state/file', self.bootstrap / 'artifacts/shared')
        # Cross-boundary hard links must retain bytes without depending on omitted paths.
        os.link(self.bootstrap / 'build/file', self.bootstrap / 'artifacts/cross-boundary')
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.state / 'migration.started').write_text('old interrupted full copy\n')
        self.legacy = self.root / 'migration/data'
        self.legacy.mkdir(parents=True)
        (self.legacy / 'preserve').write_text('old failed copy\n')
        self.selected = self.root / 'migration/installed-continuation-v1/data'
        names = ('use_selected_migration', 'check_retention_policy', 'select_migration_policy',
                 'check_retained_source', 'workspace_copy_differences', 'tree_manifest',
                 'copy_and_verify_migration', 'validate_home_migration')
        self.shell = '''set -Eeuo pipefail
umask 077
STATE_BASE={state}; MIGRATION_BASE={migration}; STATE=$STATE_BASE; MIGRATION=$MIGRATION_BASE
DATA=$MIGRATION/data; RETENTION_POLICY=full; RSYNC_SELECTION=()
RETENTION_TOOL={tool}; SOURCE_MOUNT={source}; SOURCE_DEVICE=/dev/fixture
WORKSPACE_UUID=fixture-uuid; EXTRA_SERIAL=fixture-extra; EXTRA_BYTES=10000000000
fail() {{ echo "$*" >&2; exit 19; }}
findmnt() {{ echo /home; }}
# Fixture trees belong to the test user; emulate root's ownership checks only.
chown() {{ :; }}
stat() {{
    if [[ $1 == -c && $2 == '%u:%g:%a' ]]; then
        printf '0:0:%s\\n' "$(command stat -c %a "$3")"
    elif [[ $1 == -c && $2 == '%u' ]]; then echo 0
    else command stat "$@"; fi
}}
'''.format(state=shlex.quote(str(self.state)), migration=shlex.quote(str(self.root / 'migration')),
           tool=shlex.quote(str(GENTOO / 'workspace-retention.py')), source=shlex.quote(str(self.source)))
        self.shell += ''.join(function(name) for name in names)

    def run_shell(self, command):
        return subprocess.run(['bash', '-c', self.shell + command], text=True, capture_output=True)

    def migrate(self):
        result = self.run_shell('select_migration_policy\ncopy_and_verify_migration\nvalidate_home_migration\n')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_selection_copy_metadata_hardlinks_and_repeat_preserve_legacy_and_unknown_files(self):
        self.migrate()
        self.assertEqual((self.selected / 'operator-data').read_text(), 'keep unknown files\n')
        for name in ('build', 'sources', 'toolchain', 'native-root-x86_64'):
            self.assertFalse((self.selected / 'gentoo-bootstrap' / name).exists())
        self.assertEqual((self.selected / 'gentoo-bootstrap/artifacts/cross-boundary').read_text(), 'build\n')
        self.assertEqual((self.selected / 'gentoo-bootstrap/state/file').stat().st_ino,
                         (self.selected / 'gentoo-bootstrap/artifacts/shared').stat().st_ino)
        self.assertEqual(os.readlink(self.selected / 'gentoo-bootstrap/artifacts/external-reference'),
                         '/unavailable/provenance-reference')
        self.assertEqual((self.legacy / 'preserve').read_text(), 'old failed copy\n')
        self.assertEqual((self.state / 'migration.started').read_text(), 'old interrupted full copy\n')
        self.migrate()

    def test_changed_omitted_output_does_not_block_but_changed_retained_file_does(self):
        self.migrate()
        (self.bootstrap / 'build/file').unlink()  # Break the cross-boundary hard link first.
        (self.bootstrap / 'build/file').write_text('new disposable build output')
        self.migrate()
        (self.source / 'operator-data').write_text('changed retained content')
        result = self.run_shell('select_migration_policy\ncopy_and_verify_migration\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('retained source changed', result.stderr)
        self.assertEqual((self.selected / 'operator-data').read_text(), 'keep unknown files\n')

    def test_policy_change_and_started_legacy_initialization_refuse(self):
        (self.state / 'initialize.started').write_text('do not reinterpret legacy initialization')
        result = self.run_shell('select_migration_policy\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state / 'active-migration').exists())
        (self.state / 'initialize.started').unlink()
        self.migrate()
        policy = self.state / 'installed-continuation-v1/retention-policy.json'
        policy.write_text('operator-modified policy\n')
        result = self.run_shell('select_migration_policy\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('policy changed', result.stderr)

    def test_interrupted_copy_reuses_source_snapshot_and_repairs_selected_staging(self):
        # Fail after policy/snapshot publication but before the first actual copy.
        command = 'select_migration_policy\nrsync() { return 71; }\ncopy_and_verify_migration\n'
        result = self.run_shell(command)
        self.assertEqual(result.returncode, 71)
        journal = self.state / 'installed-continuation-v1'
        before = (journal / 'source.snapshot').read_bytes()
        self.assertFalse((journal / 'migration.done').exists())
        self.migrate()
        self.assertEqual((journal / 'source.snapshot').read_bytes(), before)
        # Metadata damage in staging is detected, not excused by a content-only hash.
        (self.selected / 'operator-data').chmod(0o600)
        result = self.run_shell('use_selected_migration\nvalidate_home_migration\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('staging metadata or content changed', result.stderr)

    def test_pre_erasure_comparison_and_restore_use_the_selected_tree(self):
        self.migrate()
        (self.bootstrap / 'build/new-file').write_text('not retained')
        result = self.run_shell('use_selected_migration\ncheck_retained_source\nworkspace_copy_differences\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        restored = self.root / 'restored'
        restored.mkdir()
        subprocess.run(['rsync', '-aHAX', '--modify-window=-1', str(self.selected) + '/', str(restored) + '/'], check=True)
        self.assertEqual(retention.fingerprint(self.selected, strict=True), retention.fingerprint(restored, strict=True))
        (self.bootstrap / 'inputs/file').write_text('changed after migration')
        result = self.run_shell('use_selected_migration\ncheck_retained_source\n')
        self.assertNotEqual(result.returncode, 0)

    def test_source_mount_must_be_readonly_and_have_no_other_alias(self):
        actual = function('check_workspace_source').replace('[[ -b $device ]]', '[[ -n $device ]]')
        fixture = r'''
PROFILE=vm; WORKSPACE_UUID=fixture-uuid; WORKSPACE_LABEL=GENTOO_BUILD
ROOT_DEVICE=/dev/root; HOME_DEVICE=/dev/home; EXTRA_REAL=/dev/extra; MOUNT=/extra
readlink() { echo "${!#}"; }
mountpoint() { return 0; }
findmnt() {
    case "$*" in
      *'-o SOURCE'*) echo /dev/workspace;;
      *'-o OPTIONS'*) echo "$OPTIONS";;
      *'-S '*) printf '%b\n' "$ALIASES";;
      *) echo /mnt/gentoo-workspace;;
    esac
}
blkid() {
    case "$*" in *TYPE*) echo ext4;; *UUID*) echo fixture-uuid;; *LABEL*) echo GENTOO_BUILD;; esac
}
'''
        for options, aliases, accepted in (('ro,noload', '/mnt/gentoo-workspace', True),
                                           ('rw', '/mnt/gentoo-workspace', False),
                                           ('ro', '/mnt/gentoo-workspace\n/build', False)):
            command = (self.shell + fixture + 'OPTIONS=' + shlex.quote(options) + '\nALIASES='
                       + shlex.quote(aliases) + '\n' + actual + 'check_workspace_source /mnt/gentoo-workspace\n')
            result = subprocess.run(['bash', '-c', command], capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_installed_prerequisite_check_refuses_before_bootstrap_outputs_are_omitted(self):
        with self.assertRaisesRegex(ValueError, 'installed continuation input is missing'):
            retention.check_installed(self.root)

    def test_added_excluded_directory_in_staging_is_rejected(self):
        self.migrate()
        (self.selected / 'gentoo-bootstrap/build').mkdir()
        result = self.run_shell('use_selected_migration\nvalidate_home_migration\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('omitted path unexpectedly present', result.stderr)

    def test_omitted_symlink_or_special_retained_file_refuses(self):
        shutil.rmtree(self.bootstrap / 'sources')
        (self.bootstrap / 'sources').symlink_to('/unrelated')
        with self.assertRaises(ValueError):
            retention.allocated(self.source)
        (self.bootstrap / 'sources').unlink()
        os.mkfifo(self.source / 'unexpected-fifo')
        with self.assertRaises(ValueError):
            retention.allocated(self.source)
