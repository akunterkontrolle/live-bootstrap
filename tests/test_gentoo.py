"""Focused host and disposable-directory checks; no downloads or block writes."""
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import gentoo_config as config

spec = importlib.util.spec_from_file_location('gentoo_prepare', ROOT / 'system/gentoo/prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class GentooTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.toml = self.directory / 'config.toml'
        self.toml.write_text('')

    def load(self, text='', mode='vm'):
        self.toml.write_text(text)
        return config.load(self.toml, mode)

    def shell(self, script, *, success=True):
        result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + script],
                                cwd=self.directory, text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def helpers(self, *names):
        return ''.join(f'. "{ROOT}/system/gentoo/lib/{name}.sh"\n' for name in names)

    def test_defaults_partial_and_capacity(self):
        settings = self.load('[portage]\nload_average = 4.5\n')
        self.assertEqual(settings['portage']['jobs'], 2)
        self.assertEqual(settings['portage']['load_average'], 4.5)
        self.assertEqual(config.root_image_gib(settings), 500)
        settings['layout']['root_gib'] = 600
        self.assertEqual(config.root_image_gib(settings), 812)

    def test_invalid_schema_and_values(self):
        invalid = ('[build]\nprofile="vm"', '[vm]\ncpus=18',
                   '[workspace]\nscratch_uuid="x"', '[build]\nsource_jobs=true',
                   '[build]\nsource_jobs=0', '[portage]\nload_average=nan',
                   '[portage]\nload_average=inf', '[system]\nusername="root"',
                   '[system]\ntimezone="../etc"', '[workspace]\nlabel="a b"',
                   '[workspace]\nuuid="not-a-uuid"', '[packages]\nbase="missing"',
                   '[hardware_disks]\nroot_serial="a\\nb"',
                   '[hardware_disks]\nroot_by_id="/dev/sda"',
                   '[hardware_disks]\nroot_by_id="/dev/disk/by-id/ata-example-part10"',
                   '[hardware_disks]\nroot_max_bytes=1')
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load(text)

    def test_hardware_required_and_distinct(self):
        with self.assertRaises(ValueError):
            self.load(mode='hardware')
        text = '[hardware_disks]\n' + '\n'.join(
            f'{role}_by_id="/dev/disk/by-id/ata-{role}"\n{role}_serial="{role}"'
            for role in ('root', 'boot', 'extra'))
        self.load(text, 'hardware')
        with self.assertRaises(ValueError):
            self.load(text.replace('extra_serial="extra"', 'extra_serial="root"'), 'hardware')

    def test_shell_quoting(self):
        settings = self.load()
        payload = "space ' quote ; $(touch INJECTED) `touch INJECTED` $HOME " + chr(92)
        settings['hardware_disks']['root_serial'] = payload
        rendered = config.render(settings, 'vm')
        self.shell(rendered + '\nprintf %s "$GENTOO_HARDWARE_DISKS_ROOT_SERIAL" > result')
        self.assertEqual((self.directory / 'result').read_text(), payload)
        self.assertFalse((self.directory / 'INJECTED').exists())

    def test_concurrency_does_not_change_semantics(self):
        first = self.load()
        second = self.load('[build]\nsource_jobs=3\n[portage]\njobs=4\nload_average=7.5\nmake_jobs=8')
        self.assertEqual(config.render(first, 'vm', semantic=True), config.render(second, 'vm', semantic=True))
        self.assertNotEqual(config.render(first, 'vm'), config.render(second, 'vm'))
        second['system']['locale'] = 'de_DE.UTF-8'
        self.assertNotEqual(config.render(first, 'vm', semantic=True), config.render(second, 'vm', semantic=True))

    def make_bundle(self, name='bundle'):
        destination = self.directory / name
        prepare.prepare(self.toml, 'vm', destination)
        return destination

    def test_bundle_copies_relative_policy_and_is_reproducible(self):
        (self.directory / 'packages with spaces').write_text('sys-apps/example\n')
        self.toml.write_text('[packages]\nbase="packages with spaces"')
        first, second = self.make_bundle(), self.make_bundle('second')
        self.assertEqual((first / 'policy/base').read_text(), 'sys-apps/example\n')
        self.assertEqual((first / 'SHA256SUMS').read_bytes(), (second / 'SHA256SUMS').read_bytes())
        self.assertNotIn(str(self.directory), (first / 'config.sh').read_text())
        with self.assertRaises(ValueError):
            self.make_bundle()
        self.shell(f'cd "{first}"; sha256sum -c SHA256SUMS')

    def test_snapshots_survive_original_input_loss(self):
        bundle = self.make_bundle()
        script = self.helpers('common', 'state') + f'''
WORK_ROOT="$PWD/work"
mkdir -p "$WORK_ROOT/state"
SCRIPT_DIR="{bundle}"
gentoo_persist_bundle "$SCRIPT_DIR"
. "$SCRIPT_DIR/stages/00-inputs.sh"
printf '%s' "$SCRIPT_DIR" > saved-path
'''
        self.shell(script)
        saved = Path((self.directory / 'saved-path').read_text())
        self.assertTrue(saved.is_dir())
        (bundle / 'config.sh').unlink()
        self.shell(self.helpers('common', 'state') + f'''
WORK_ROOT="$PWD/work"
SCRIPT_DIR="{saved}"
gentoo_verify_bundle "$SCRIPT_DIR"
. "$SCRIPT_DIR/stages/00-inputs.sh"
''')
        self.assertEqual(len(list((self.directory / 'work/state').glob('*.done'))), 1)
        (saved / 'config.sh').write_text('corrupted')
        self.shell(self.helpers('common') + f'gentoo_verify_bundle "{saved}"', success=False)

    def test_stage_receipts_verify_outputs_and_refuse_changed_inputs(self):
        prefix = self.helpers('common', 'state') + '''
WORK_ROOT="$PWD/work"
mkdir -p "$WORK_ROOT/state"
action() { echo built >> output; }
verify() { test -s output; }
echo recipe > recipe
fingerprint=$(gentoo_fingerprint recipe)
'''
        self.shell(prefix + 'gentoo_run_stage 10-test "$fingerprint" action verify')
        self.shell(prefix + 'gentoo_run_stage 10-test "$fingerprint" action verify')
        self.assertEqual((self.directory / 'output').read_text(), 'built\n')
        self.shell(prefix + '''echo changed > recipe
fingerprint=$(gentoo_fingerprint recipe)
gentoo_run_stage 10-test "$fingerprint" action verify''', success=False)
        (self.directory / 'output').unlink()
        self.shell(prefix + 'gentoo_run_stage 10-test "$fingerprint" action verify', success=False)

    def test_failed_stage_has_no_receipt_and_resumes(self):
        prefix = self.helpers('common', 'state') + '''
WORK_ROOT="$PWD/work"
mkdir -p "$WORK_ROOT/state"
echo recipe > recipe
fingerprint=$(gentoo_fingerprint recipe)
verify() { test -s output; }
'''
        self.shell(prefix + '''action() { false; echo incorrectly-continued > output; }
gentoo_run_stage 10-test "$fingerprint" action verify''', success=False)
        self.assertFalse((self.directory / 'work/state/10-test.done').exists())
        self.assertFalse((self.directory / 'output').exists())
        self.shell(prefix + '''action() { echo resumed > output; }
gentoo_run_stage 10-test "$fingerprint" action verify''')
        self.assertTrue((self.directory / 'work/state/10-test.done').is_file())

    def test_process_lock_released_after_kill(self):
        fake_bin = self.directory / 'fake-bin'
        fake_bin.mkdir()
        (fake_bin / 'perl').symlink_to(shutil.which('perl'))
        (fake_bin / 'awk').symlink_to(shutil.which('awk'))
        script = self.helpers('common') + f'PATH="$PWD/fake-bin"\nmountpoint -q /proc\nWORK_ROOT="$PWD"\ngentoo_lock\necho ready\nread -r wait_for_input'
        process = subprocess.Popen(['bash', '-c', script], cwd=self.directory,
                                   text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.shell(self.helpers('common') + f'PATH="$PWD/fake-bin"\nWORK_ROOT="$PWD"\ngentoo_lock', success=False)
            process.kill()
            process.wait(timeout=5)
            # The Perl guardian releases its kernel lock when the killed shell
            # closes the coprocess pipe.
            for _ in range(50):
                result = subprocess.run(
                    ['bash', '-c', 'set -Eeuo pipefail\n' + self.helpers('common') +
                     'PATH="$PWD/fake-bin"\nWORK_ROOT="$PWD"\ngentoo_lock'],
                    cwd=self.directory, text=True, capture_output=True)
                if result.returncode == 0:
                    break
                import time
                time.sleep(0.02)
            else:
                self.fail('Perl process-bound lock remained held after its parent died')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_fingerprint_ignores_unrelated_files_and_snapshot_path(self):
        prefix = self.helpers('common', 'state')
        self.shell(prefix + '''
mkdir first second
echo recipe > first/recipe
echo source-lock > first/sources.lock
cp first/* second/
echo docs > first/README.md
echo future-stage > second/99-future.sh
(cd first; gentoo_fingerprint recipe sources.lock) > first-hash
(cd second; gentoo_fingerprint recipe sources.lock) > second-hash
cmp first-hash second-hash
echo changed-source > second/sources.lock
(cd second; gentoo_fingerprint recipe sources.lock) > second-hash
if cmp -s first-hash second-hash; then exit 1; fi
''')

    def test_download_publication_requires_success_and_matching_digest(self):
        digest = hashlib.sha256(b'pinned').hexdigest()
        prefix = self.helpers('common', 'sources')
        self.shell(prefix + f'''
curl() {{ while [ "$1" != --output ]; do shift; done; printf pinned > "$2"; }}
gentoo_fetch_source https://invalid.example/source {digest} archive
gentoo_verify_source archive {digest}
''')
        self.shell(prefix + f'''
curl() {{ while [ "$1" != --output ]; do shift; done; printf wrong > "$2"; }}
gentoo_fetch_source https://invalid.example/source {digest} bad
''', success=False)
        self.assertFalse((self.directory / 'bad').exists())
        self.shell(prefix + f'''
curl() {{ while [ "$1" != --output ]; do shift; done; printf partial > "$2"; return 1; }}
gentoo_fetch_source https://invalid.example/source {digest} interrupted
''', success=False)
        self.assertFalse((self.directory / 'interrupted').exists())
        self.assertEqual(list(self.directory.glob('*.partial.*')), [])

    def test_source_integrity_and_cache_refusal(self):
        source = self.directory / 'archive'
        source.write_bytes(b'locked source')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        prefix = self.helpers('common', 'sources')
        self.shell(prefix + f'gentoo_fetch_source https://invalid.example/source {digest} archive')
        source.write_bytes(b'corrupted')
        self.shell(prefix + f'gentoo_fetch_source https://invalid.example/source {digest} archive', success=False)
        self.shell(prefix + f'gentoo_fetch_source http://invalid.example/source {digest} new', success=False)

    def test_workspace_discovery_stops_on_missing_or_ambiguous_identity(self):
        prefix = self.helpers('common', 'devices') + '''
GENTOO_WORKSPACE_LABEL=GENTOO_BUILD
GENTOO_WORKSPACE_UUID=
'''
        self.shell(prefix + 'blkid() { return 2; }; gentoo_mount_workspace', success=False)
        self.shell(prefix + 'blkid() { printf "/dev/a\\n/dev/b\\n"; }; gentoo_mount_workspace', success=False)


if __name__ == '__main__':
    unittest.main()
