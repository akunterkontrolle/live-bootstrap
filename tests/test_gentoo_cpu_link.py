"""Exercise the split-lib startup binding and narrow failed-attempt recovery."""
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = importlib.util.spec_from_file_location('cpu_link_repair', GENTOO / 'repair-cpu-prerequisite-link.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class CpuLinkTests(unittest.TestCase):
    def test_actual_action_binds_split_lib64_before_detection(self):
        recipe = (GENTOO / repair.STAGE).read_text()
        action = recipe[recipe.index('cpu_policy_action() {'):recipe.index('\ncpu_policy_verify()')]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tool/bin').mkdir(parents=True)
            (root / 'sysroot/usr/lib64').mkdir(parents=True)
            (root / 'state').mkdir()
            compiler = root / 'tool/bin/target-gcc'
            compiler.write_text('''#!/bin/bash
set -eu
seen_b=0; seen_l=0; seen_64=0; seen_static=0; output=
for arg; do
    case "$arg" in
        -B"$FIXTURE/sysroot/usr/lib64/") seen_b=1;;
        -L"$FIXTURE/sysroot/usr/lib64") seen_l=1;;
        -m64) seen_64=1;; -static) seen_static=1;;
    esac
done
[ "$seen_b$seen_l$seen_64$seen_static" = 1111 ] || { echo 'missing startup binding' >&2; exit 17; }
while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then output=$2; break; fi
    shift
done
printf '#!/bin/sh\\nprintf "cpuid2cpuflags 18\\\\n"\\n' > "$output"
chmod 755 "$output"
''')
            compiler.chmod(0o755)
            (root / 'cpu-policy.py').write_text('import pathlib, sys\npathlib.Path(sys.argv[sys.argv.index("--receipt")+1]).write_text("detected")\n')
            variables = ('set -Eeuo pipefail\nWORK_ROOT=' + shlex.quote(temporary) + '\n'
                         'SCRIPT_DIR=$WORK_ROOT\nPREFIX=$WORK_ROOT/tool\nTARGET=target\n'
                         'SYSROOT=$WORK_ROOT/sysroot\nCPU_RECEIPT=$WORK_ROOT/state/cpu.json\n'
                         'CPU_PREREQUISITE=$WORK_ROOT/native-build-tools/bin/cpuid2cpuflags\n'
                         'gentoo_prepare_source() { :; }\n')
            env = dict(os.environ, FIXTURE=temporary)
            old = action.replace(repair.NEW_LINK, repair.OLD_LINK)
            broken = subprocess.run(['bash', '-c', variables + old + '\ncpu_policy_action'],
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(broken.returncode, 17)
            self.assertFalse((root / 'state/cpu.json').exists())
            fixed = subprocess.run(['bash', '-c', variables + action + '\ncpu_policy_action'],
                                   env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            self.assertEqual((root / 'state/cpu.json').read_text(), 'detected')
            self.assertTrue((root / 'state/cpu-detector.sha256').is_file())


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        (self.work / 'state').mkdir()
        (self.work / 'inputs').mkdir()
        self.entries = {name: (GENTOO / name).read_bytes() for name in (
            repair.STAGE, 'lib/cpu-policy.sh', 'cpu-policy.py',
            'sources/cpuid2cpuflags.lock', 'policy/00-cpu-flags.accept_keywords', 'gentoo.sh')}
        self.entries[repair.STAGE] = self.entries[repair.STAGE].replace(
            repair.NEW_LINK.encode(), repair.OLD_LINK.encode())
        self.assertEqual(repair.sha(self.entries[repair.STAGE]), repair.OLD_SHA256)
        manifest = ''.join(repair.sha(data) + '  ' + name + '\n'
                           for name, data in sorted(self.entries.items())).encode()
        self.identity = repair.sha(manifest)
        self.source = self.work / 'inputs' / self.identity
        self.source.mkdir()
        for name, data in self.entries.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.source / 'SHA256SUMS').write_bytes(manifest)
        (self.work / 'current-inputs').write_text(self.identity + '\n')
        (self.work / 'state/71-native-portage.done').write_text('verified earlier stage\n')
        self.before = repair.fingerprint(self.entries, self.work)
        self.started = self.work / 'state/72-native-cpu-policy.inputs'
        self.started.write_bytes(self.before)
        # Only these bytes may change: current selector and failed-attempt marker.
        self.original_done = (self.work / 'state/71-native-portage.done').read_bytes()

    def test_publication_repeat_and_fingerprint_match_the_real_shell(self):
        script = ('. ' + shlex.quote(str(GENTOO / 'lib/state.sh')) + '\ngentoo_fingerprint '
                  + ' '.join(shlex.quote(name) for name in [repair.STAGE, 'lib/cpu-policy.sh', 'cpu-policy.py',
                     'sources/cpuid2cpuflags.lock', 'policy/00-cpu-flags.accept_keywords',
                     str(self.work / 'state/71-native-portage.done')]))
        actual = subprocess.check_output(['bash', '-c', script], cwd=self.source)
        self.assertEqual(actual, self.before)
        driver = repair.repair(self.work)
        self.assertEqual(driver.read_bytes(), self.entries['gentoo.sh'])
        after = repair.snapshot(self.work, driver.parent.name)
        self.assertEqual(repair.sha(after[repair.STAGE]), repair.NEW_SHA256)
        self.assertEqual(self.started.read_bytes(), repair.fingerprint(after, self.work))
        self.assertEqual(repair.repair(self.work), driver)
        self.assertEqual(repair.snapshot(self.work, self.identity), self.entries)
        self.assertEqual((self.work / 'state/71-native-portage.done').read_bytes(), self.original_done)
        self.assertFalse((self.work / 'state/72-native-cpu-policy.done').exists())

    def test_unknown_attempt_completed_cpu_or_started_system_refuses_without_mutation(self):
        for name in ('72-native-cpu-policy.done', 'native-cpu-policy.json', 'cpu-detector.sha256',
                     '80-native-gentoo-system.inputs'):
            marker = self.work / 'state' / name
            marker.write_text('must preserve\n')
            with self.assertRaises(ValueError):
                repair.repair(self.work)
            self.assertEqual(self.started.read_bytes(), self.before)
            self.assertEqual((self.work / 'current-inputs').read_text(), self.identity + '\n')
            marker.unlink()
        self.started.write_text('unknown\n')
        with self.assertRaises(ValueError):
            repair.repair(self.work)
        self.assertEqual(self.started.read_text(), 'unknown\n')

    def test_interrupted_selector_publication_can_finish(self):
        original = repair.atomic
        def interrupt(path, data):
            if path.name == 'current-inputs':
                raise OSError('simulated interruption')
            original(path, data)
        with patch.object(repair, 'atomic', side_effect=interrupt):
            with self.assertRaises(OSError):
                repair.repair(self.work)
        driver = repair.repair(self.work)
        self.assertEqual((self.work / 'current-inputs').read_text(), driver.parent.name + '\n')
        self.assertEqual((self.work / 'state/71-native-portage.done').read_bytes(), self.original_done)

    def test_whole_paste_publishes_and_repeats_without_changing_completed_receipts(self):
        paste = (GENTOO / 'repair-vm-cpu-link.paste.txt').read_text()
        paste = paste.replace("default=Path('/build/gentoo-bootstrap')", 'default=Path(' + repr(str(self.work)) + ')')
        for _ in range(2):
            result = subprocess.run(['bash', '-eu', '-s'], input=paste + '\necho PARENT_ALIVE\n',
                                    text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Resume: bash ', result.stdout)
            self.assertIn('PARENT_ALIVE', result.stdout)
            self.assertEqual((self.work / 'state/71-native-portage.done').read_bytes(), self.original_done)
        self.assertFalse((self.work / 'state/72-native-cpu-policy.done').exists())

    def test_changed_snapshot_refuses_before_state_changes(self):
        (self.source / repair.STAGE).write_text('unknown guest edit\n')
        with self.assertRaises(ValueError):
            repair.repair(self.work)
        self.assertEqual(self.started.read_bytes(), self.before)
        self.assertEqual((self.work / 'current-inputs').read_text(), self.identity + '\n')

    def test_paste_failure_preserves_the_strict_parent_shell(self):
        paste = (GENTOO / 'repair-vm-cpu-link.paste.txt').read_text()
        paste = paste.replace("default=Path('/build/gentoo-bootstrap')", 'default=Path(' + repr(str(self.work)) + ')')
        self.started.write_text('unknown\n')
        result = subprocess.run(['bash', '-eu', '-s'], input=paste + '\necho PARENT_ALIVE\n',
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PARENT_ALIVE', result.stdout)
        self.assertNotIn('Resume:', result.stdout)
