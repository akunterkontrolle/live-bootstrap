"""Batch 5: pinned repository, Manifest guard and Portage host policy."""
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'


class PortageHostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def shell(self, script, success=True):
        result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + self.helpers() + script],
                                cwd=self.directory, text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def helpers(self):
        return f'''
WORK_ROOT="$PWD/work"
SCRIPT_DIR=/hdd/bootstrap/final-merge/live-bootstrap/system/gentoo
SOURCES="$WORK_ROOT/sources"
GENTOO_PORTAGE_JOBS=2
GENTOO_PORTAGE_LOAD_AVERAGE=12
GENTOO_PORTAGE_MAKE_JOBS=12
mkdir -p "$WORK_ROOT/state" "$SOURCES"
export WORK_ROOT
. {shlex.quote(str(GENTOO / 'lib/common.sh'))}
. {shlex.quote(str(GENTOO / 'lib/sources.sh'))}
cd {shlex.quote(str(GENTOO))}
. {shlex.quote(str(GENTOO / 'lib/portage.sh'))}
'''

    def make_repository(self):
        tree = self.directory / 'work/sources' / 'gentoo-5548522e2c2fa10a4c009348e9ba84c44783f47d'
        (tree / 'profiles/default/linux/amd64/23.0/desktop').mkdir(parents=True)
        (tree / 'profiles/repo_name').write_text('gentoo\n')
        (tree / 'cat/pkg').mkdir(parents=True)
        (tree / 'cat/pkg/Manifest').write_text('DIST example 4 SHA512 abcd\n')
        return tree

    def test_new_seed_locks_are_unique_https_sha512_and_commit_bound(self):
        expected = {
            'gentoo-repository': 'gentoo-5548522e2c2fa10a4c009348e9ba84c44783f47d.tar.gz',
            'python': 'Python-3.12.15.tar.xz',
            'bzip2': 'bzip2-1.0.8.tar.gz',
            'portage': 'portage-3.0.82.2.tar.bz2',
        }
        for lock, archive in expected.items():
            records = [line for line in (GENTOO / 'sources' / f'{lock}.lock').read_text().splitlines()
                       if line and not line.startswith('#')]
            self.assertEqual(len(records), 1, lock)
            name, url, digest = records[0].split('|')
            self.assertEqual(name, archive)
            self.assertTrue(url.startswith('https://'))
            self.assertRegex(digest, r'^sha512=[0-9a-f]{128}$')
        self.assertIn('5548522e2c2fa10a4c009348e9ba84c44783f47d',
                      (GENTOO / 'sources/gentoo-repository.lock').read_text())

    def test_manifest_guard_records_and_refuses_added_changed_or_missing_files(self):
        self.make_repository()
        self.shell('''
portage_manifest_guard_init
portage_repository_guard_init
portage_manifest_guard_check
portage_repository_guard_check
''')
        baseline = self.directory / 'work/state/gentoo-manifests-5548522e2c2fa10a4c009348e9ba84c44783f47d.sha256'
        saved = baseline.read_bytes()
        original_manifest = b'DIST example 4 SHA512 abcd\n'
        self.assertEqual(len(saved.splitlines()), 1)
        manifest = self.directory / 'work/sources/gentoo-5548522e2c2fa10a4c009348e9ba84c44783f47d/cat/pkg/Manifest'
        manifest.write_text('DIST changed 5 SHA512 dcba\n')
        self.shell('portage_manifest_guard_check', success=False)
        manifest.write_bytes(original_manifest)
        new_manifest = manifest.parent.parent / 'other/Manifest'
        new_manifest.parent.mkdir()
        new_manifest.write_text('DIST another 3 SHA512 1234\n')
        self.shell('portage_manifest_guard_check', success=False)
        new_manifest.unlink()
        new_manifest.parent.rmdir()
        manifest.unlink()
        self.shell('portage_manifest_guard_check', success=False)
        manifest.write_bytes(original_manifest)
        self.assertEqual(baseline.read_bytes(), saved)
        self.assertTrue(list(baseline.parent.glob('.manifest-check.*.changed')))
        self.shell('portage_repository_guard_check')
        ebuild = self.directory / 'work/sources/gentoo-5548522e2c2fa10a4c009348e9ba84c44783f47d/cat/pkg/test.ebuild'
        ebuild.write_text('EAPI=8\n')
        self.shell('portage_repository_guard_check', success=False)
        self.assertTrue(list(baseline.parent.glob('.gentoo-tree-check.*.changed')))

    def test_portage_config_keeps_three_concurrency_values_and_source_policy(self):
        tree = self.make_repository()
        self.shell('''
GENTOO_PORTAGE_JOBS=2
GENTOO_PORTAGE_LOAD_AVERAGE=12.5
GENTOO_PORTAGE_MAKE_JOBS=7
portage_render_config
portage_host_environment
''')
        config = self.directory / 'work/portage-host-config/etc/portage/make.conf'
        text = config.read_text()
        self.assertIn('FEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"', text)
        self.assertIn('EMERGE_DEFAULT_OPTS="--jobs=2 --load-average=12.5 --usepkg=n --getbinpkg=n --buildpkg=n"', text)
        self.assertIn('MAKEOPTS="-j7"', text)
        self.assertIn(f'DISTDIR="{self.directory}/work/distfiles"', text)
        profile = self.directory / 'work/portage-host-config/etc/portage/make.profile'
        self.assertEqual(profile.resolve(), tree / 'profiles/default/linux/amd64/23.0/desktop')
        self.shell('''
portage_render_config
portage_host_environment
printf '%s\\n' "$EMERGE_DEFAULT_OPTS" "$MAKEOPTS" "$FEATURES"
''')
        changed = self.shell('''GENTOO_PORTAGE_JOBS=3 GENTOO_PORTAGE_LOAD_AVERAGE=5 GENTOO_PORTAGE_MAKE_JOBS=4
portage_render_config
portage_host_environment
printf '%s\\n' "$EMERGE_DEFAULT_OPTS" "$MAKEOPTS"''')
        self.assertIn('--jobs=3 --load-average=5', changed.stdout)
        self.assertIn('-j4', changed.stdout)
        self.shell('GENTOO_PORTAGE_LOAD_AVERAGE=0.5 portage_render_config')
        self.shell('GENTOO_PORTAGE_LOAD_AVERAGE=0 portage_render_config', success=False)

    def test_portage_emerge_checks_manifests_and_forces_source_only_options(self):
        self.make_repository()
        (self.directory / 'work/sources/portage-3.0.82.2/bin').mkdir(parents=True)
        self.shell('''
portage_manifest_guard_init
mkdir -p "$HOST_PREFIX/bin"
cat > "$HOST_PREFIX/bin/python3.12" <<'FAKE'
#!/bin/bash
printf '%s\\n' "$*" > "$WORK_ROOT/args"
printf '%s|%s|%s\\n' "$FEATURES" "$EMERGE_DEFAULT_OPTS" "$MAKEOPTS" > "$WORK_ROOT/env"
if [[ " $* " == *' mutate '* ]]; then printf changed > "$GENTOO_REPO_TREE/cat/pkg/Manifest"; fi
FAKE
chmod +x "$HOST_PREFIX/bin/python3.12"
portage_emerge --pretend @world
''')
        args = (self.directory / 'work/args').read_text()
        self.assertIn('--usepkg=n --getbinpkg=n --buildpkg=n', args)
        self.assertIn('strict', (self.directory / 'work/env').read_text())
        self.shell('portage_emerge --usepkg=y @world', success=False)
        (self.directory / 'work/sources/gentoo-5548522e2c2fa10a4c009348e9ba84c44783f47d/cat/pkg/Manifest').write_text('DIST example 4 SHA512 abcd\n')
        self.shell('portage_emerge --pretend mutate', success=False)

    def test_portage_account_creation_is_collision_safe_and_resumable(self):
        root = self.directory / 'accounts'
        root.mkdir()
        group = root / 'group'
        passwd = root / 'passwd'
        false_path = root / 'false'
        group.write_text('root:x:0:\nusers:x:100:\n')
        passwd.write_text('root:x:0:0:root:/root:/bin/sh\n')
        false_path.write_text('')
        false_path.chmod(0o755)
        common = f'portage_ensure_guest_account {shlex.quote(str(group))} {shlex.quote(str(passwd))} {shlex.quote(str(false_path))}\n'
        self.shell(common + common + f'portage_verify_guest_account {group} {passwd} {false_path}')
        self.assertIn('portage::250:portage', group.read_text())
        self.assertIn('portage:x:250:250:Portage:/var/tmp/portage:', passwd.read_text())
        original = group.read_text()
        collision = root / 'collision'
        collision.write_text('root:x:0:\nother:x:250:\n')
        result = self.shell(f'portage_ensure_guest_account {collision} {passwd} {false_path}', success=False)
        self.assertIn('GID 250 is already assigned', result.stderr)
        self.assertEqual(collision.read_text(), 'root:x:0:\nother:x:250:\n')
        self.assertEqual(group.read_text(), original)
        uid_collision = root / 'uid-collision'
        uid_collision.write_text('root:x:0:0:root:/root:/bin/sh\nother:x:250:250:Other:/tmp:/bin/sh\n')
        group_before = group.read_text()
        group_collision = root / 'group-collision'
        group_collision.write_text('root:x:0:\nusers:x:100:\n')
        result = self.shell(f'portage_ensure_guest_account {group_collision} {uid_collision} {false_path}', success=False)
        self.assertIn('UID 250 is already assigned', result.stderr)
        self.assertNotIn('portage', group_collision.read_text())
        self.assertEqual(group.read_text(), group_before)

    def test_driver_stage_order_and_bundle_include_batch_five_inputs(self):
        driver = (GENTOO / 'gentoo.sh').read_text()
        stages = [line.split('/stages/', 1)[1].split('"', 1)[0]
                  for line in driver.splitlines() if '/stages/' in line and '. ' in line]
        self.assertEqual(stages, sorted(stages))
        self.assertEqual(stages[-6:], ['50-gentoo-sources.sh', '51-host-python.sh', '52-portage-host.sh', '60-native-root.sh', '61-native-shell-tools.sh', '62-native-compiler.sh'])
        prepare = (GENTOO / 'prepare.py').read_text()
        for name in ('lib/portage.sh', 'lib/manifests.sh', 'lib/portage-host.sh', 'lib/python.sh', 'probes/portage-integrity.py', 'sources/gentoo-repository.lock',
                     'sources/python.lock', 'sources/bzip2.lock', 'sources/portage.lock',
                     'stages/50-gentoo-sources.sh', 'stages/51-host-python.sh', 'stages/52-portage-host.sh',
                     'lib/native.sh', 'stages/60-native-root.sh', 'stages/61-native-shell-tools.sh', 'stages/62-native-compiler.sh',
                     'sources/native-bash.lock', 'sources/native-coreutils.lock'):
            self.assertIn(repr(name), prepare)


if __name__ == '__main__':
    unittest.main()
