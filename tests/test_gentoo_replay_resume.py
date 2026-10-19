"""Behavioral regressions for the ordinary Gentoo replay path.

These fixtures execute maintained recipes and helpers, independently of the
historical snapshot repair generators and paste payloads.
"""
# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import importlib.util
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'



STAGES = ('60-native-root', '61-native-shell-tools', '62-native-compiler',
          '63-native-tool-floor', '70-native-runtimes', '71-native-portage')

class NativeResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.source = self.work / 'inputs/pending'
        self.source.mkdir(parents=True)
        tree = ast.parse((GENTOO / 'prepare.py').read_text())
        files = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'BUNDLE_FILES' for target in node.targets))
        for name in set(files):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(GENTOO / name, path)
        (self.source / 'config.sh').write_text('GENTOO_BUILD_SOURCE_JOBS=1\n')
        (self.source / 'config.inputs.sh').write_text('semantic fixture settings\n')
        (self.work / 'state').mkdir()
        for stage in ('20-sysroot-links', '42-libstdcxx', '43-runtime-acceptance',
                      '50-gentoo-sources', '51-host-python', '52-portage-host'):
            (self.work / 'state' / (stage + '.done')).write_text('a' * 64 + '\n')
        for stage in STAGES:
            identity = self.identity('stages/' + stage + '.sh')
            for suffix in ('inputs', 'done'):
                (self.work / 'state' / (stage + '.' + suffix)).write_text(identity + '\n')
        self.owner = self.identity('stages/80-native-gentoo-system.sh')
        (self.work / 'state/80-native-gentoo-system.inputs').write_text(self.owner + '\n')
        (self.work / 'state/native-gentoo-multilib-profile-enabled.done').write_text('old recipe driver marker\n')
        portage = self.work / 'native-root-x86_64/etc/portage'
        portage.mkdir(parents=True)
        (portage / 'make.conf').write_text('ABI_X86="64 32"\nUSE="-test -nls"\n')
        (portage / 'make.profile').symlink_to('/usr/portage/profiles/default/linux/amd64/23.0/split-usr')
        cache = self.work / 'native-root-x86_64/usr/lib/portage/lib/portage/__pycache__/later-import.pyc'
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b'bytecode created by later Portage imports')

    def shell(self, command, helper=None):
        script = '''set -Eeuo pipefail
SCRIPT_DIR={source}
WORK_ROOT={work}
. "$SCRIPT_DIR/lib/common.sh"
. "$SCRIPT_DIR/config.sh"
. "$SCRIPT_DIR/lib/state.sh"
. "$SCRIPT_DIR/lib/toolchain.sh"
. "$SCRIPT_DIR/lib/native.sh"
. {helper}
{command}
'''.format(source=shlex.quote(str(self.source)), work=shlex.quote(str(self.work)),
           helper=shlex.quote(str(helper or GENTOO / 'lib/native-resume.sh')), command=command)
        return subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=10)

    def identity(self, recipe):
        result = self.shell('gentoo_native_recipe_identity ' + shlex.quote(recipe))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), r'^[0-9a-f]{64}$')
        return result.stdout.strip()

    def test_started_80_accepts_mutated_seed_outputs_without_changing_policy_or_state(self):
        before = {str(p.relative_to(self.work)): p.read_bytes() for p in self.work.rglob('*') if p.is_file() and not p.is_symlink()}
        result = self.shell('gentoo_native_resume_handoff')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('stage 80 owns', result.stdout)
        for name, content in before.items():
            self.assertEqual((self.work / name).read_bytes(), content)
        self.assertEqual((self.work / 'native-root-x86_64/etc/portage/make.profile').readlink(),
                         Path('/usr/portage/profiles/default/linux/amd64/23.0/split-usr'))

    def test_absent_owner_does_not_skip_seed_stages(self):
        (self.work / 'state/80-native-gentoo-system.inputs').unlink()
        result = self.shell('if gentoo_native_resume_handoff; then exit 99; else printf "RUN_SEED\\n"; fi')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('RUN_SEED', result.stdout)

    def test_changed_recipe_cannot_bypass_checks(self):
        lock = self.source / 'sources/native-bash.lock'
        lock.write_text(lock.read_text() + '# changed source input\n')
        result = self.shell('if gentoo_native_resume_handoff; then :; else printf "UNSAFE_FALLBACK"; fi')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('receipt mismatch: 61-native-shell-tools', result.stderr)
        self.assertNotIn('UNSAFE_FALLBACK', result.stdout)

    def test_missing_seed_completion_cannot_bypass_checks(self):
        (self.work / 'state/71-native-portage.done').unlink()
        result = self.shell('gentoo_native_resume_handoff')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('receipt mismatch: 71-native-portage', result.stderr)

    def test_changed_owner_recipe_rejected(self):
        (self.source / 'stages/system/10-prepare.sh').write_text('# changed driver\n')
        result = self.shell('gentoo_native_resume_handoff')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ownership receipt does not match', result.stderr)

    def test_fingerprint_extraction_does_not_execute_stage_configuration(self):
        stage = self.source / 'stages/71-native-portage.sh'
        sentinel = self.work / 'UNWANTED_CONFIGURATION'
        stage.write_text('touch ' + shlex.quote(str(sentinel)) + '\n' + stage.read_text())
        identity = self.identity('stages/71-native-portage.sh')
        for suffix in ('inputs', 'done'):
            (self.work / 'state' / ('71-native-portage.' + suffix)).write_text(identity + '\n')
        owner = self.identity('stages/80-native-gentoo-system.sh')
        (self.work / 'state/80-native-gentoo-system.inputs').write_text(owner + '\n')
        self.assertEqual(self.shell('gentoo_native_resume_handoff').returncode, 0)
        self.assertFalse(sentinel.exists())


class NativeVerifierTests(unittest.TestCase):
    def test_absolute_compiler_links_checked_in_native_namespace_and_failures_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            state = work / 'state'
            state.mkdir()
            for name in ('native-gentoo-at-system-source-built.done', 'native-gentoo-multilib-gcc-full.done',
                         'native-gentoo-multilib-glibc.done'):
                (state / name).write_text('complete\n')
            receipt = state / 'native-gentoo-system-ready.receipt'
            receipt.write_text('Gentoo repository commit=pinned\n')
            native = work / 'native-root-x86_64'
            portage = native / 'etc/portage'
            portage.mkdir(parents=True)
            (portage / 'make.profile').symlink_to('/usr/portage/profiles/default/linux/amd64/23.0/split-usr')
            (portage / 'make.conf').write_text('ABI_X86="64 32"\nFEATURES="-test -getbinpkg -buildpkg -assume-digests -digest strict"\n')
            (native / 'usr/bin').mkdir(parents=True)
            # The host cannot resolve this legitimate native absolute link.
            (native / 'usr/bin/gcc').symlink_to('/usr/native-fixture/gcc-bin/16/gcc')
            self.assertFalse((native / 'usr/bin/gcc').exists())
            recipe = (GENTOO / 'stages/80-native-gentoo-system.sh').read_text()
            verifier = 'native_gentoo_system_verify() {' + recipe.split(
                'native_gentoo_system_verify() {', 1)[1].split('\nnative_system_fingerprint=', 1)[0]
            body = ('set -Eeuo pipefail\nWORK_ROOT=' + shlex.quote(str(work)) + '\n'
                    'GENTOO_REPO_COMMIT=pinned\nNATIVE_ROOT=/wrong-inherited-root\n'
                    'chroot() {\n'
                    '  [ "$1" = "$WORK_ROOT/native-root-x86_64" ] || return 8\n'
                    '  [ "$2" = /bin/bash ] && [ "$3" = --noprofile ] && [ "$4" = --norc ] && [ "$5" = -c ] || return 9\n'
                    '  printf "%s\\n" "$6" > "$WORK_ROOT/native-command"\n'
                    '  return "$CHROOT_STATUS"\n}\n' + verifier + '\nnative_gentoo_system_verify\n')
            for status in (0, 1):
                result = subprocess.run(['bash', '-c', 'CHROOT_STATUS=' + str(status) + '\n' + body],
                                        text=True, capture_output=True, timeout=3)
                self.assertEqual(result.returncode == 0, status == 0, result.stderr)
            command = (work / 'native-command').read_text()
            for path in ('/usr/bin/gcc', '/usr/bin/g++', '/usr/bin/emerge', '/sbin/openrc'):
                self.assertIn(path, command)
            # Exercise the actual inner loop: missing executables must fail.
            missing = command.replace('/usr/bin/gcc', '/nonexistent-native-gcc-for-test')
            result = subprocess.run(['bash', '-c', missing], text=True, capture_output=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Missing native executable:', result.stderr)
            # Missing receipt must still reject before reaching native checks.
            receipt.unlink()
            (work / 'native-command').unlink()
            result = subprocess.run(['bash', '-c', 'CHROOT_STATUS=0\n' + body],
                                    text=True, capture_output=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((work / 'native-command').exists())
