"""Public Portage copy roots must survive private extraction roots and resumption."""
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
spec = importlib.util.spec_from_file_location('public_roots_repair', GENTOO / 'repair-portage-public-roots.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
ADDITION = '''    # Extraction's private temporary root is copied by cp -a too. Publish
    # these two runtime roots, then bind their final modes in the tree receipt.
    chmod 0755 "$repository" "$portage_dir"
    repo_hash=$(native_portage_tree_hash "$repository")
    portage_hash=$(native_portage_tree_hash "$portage_dir")
'''
RECIPE = (GENTOO / 'stages/71-native-portage.sh').read_text()
OLD = RECIPE.replace(ADDITION, '')
HELPER = 'native_portage_tree_hash() {' + OLD.split('native_portage_tree_hash() {', 1)[1].split('\nnative_portage_configure()', 1)[0]


def tree_hash(path):
    return subprocess.check_output(['bash', '-c', HELPER + '\nnative_portage_tree_hash "$1"',
                                    'fixture', str(path)]).decode().strip()


class PublicCopyTests(unittest.TestCase):
    def test_actual_copy_phase_keeps_sources_private_and_publishes_verifiable_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = root / 'sources'
            native = root / 'native'
            for name in ('repo', 'portage'):
                path = sources / name
                path.mkdir(parents=True, mode=0o700)
                (path / 'public-file').write_text('pinned bytes\n')
            for name in ('usr/portage', 'usr/lib/portage'):
                (native / name).mkdir(parents=True)
            (sources / 'repo/profiles').mkdir()
            (sources / 'repo/profiles/repo_name').write_text('gentoo\n')
            (sources / 'portage/bin').mkdir()
            (sources / 'portage/bin/emerge').write_text('pinned executable\n')
            action = 'native_portage_action() {' + RECIPE.split('native_portage_action() {', 1)[1].split('    mkdir -p "$NATIVE_ROOT/usr/bin"', 1)[0] + '''
[ "$repo_hash" = "$(native_portage_tree_hash "$repository")" ]
[ "$portage_hash" = "$(native_portage_tree_hash "$portage_dir")" ]
}
'''
            variables = ('set -Eeuo pipefail\nNATIVE_ROOT=' + shlex.quote(str(native)) +
                         '\nPORTAGE_SOURCE=' + shlex.quote(str(sources / 'portage')) +
                         '\nGENTOO_REPO_TREE=' + shlex.quote(str(sources / 'repo')) + '\n')
            subprocess.run(['bash', '-c', variables + HELPER + '\n' + action + '''
portage_manifest_guard_check() { :; }
portage_repository_guard_check() { :; }
gentoo_die() { echo "$*" >&2; exit 1; }
native_portage_action
'''], check=True, capture_output=True)
            for name in ('repo', 'portage'):
                self.assertEqual((sources / name).stat().st_mode & 0o777, 0o700)
            for name in ('usr/portage', 'usr/lib/portage'):
                self.assertEqual((native / name).stat().st_mode & 0o777, 0o755)

    @unittest.skipIf(os.geteuid() == 0, 'root bypasses directory permission denial')
    def test_missing_module_error_can_mean_an_inaccessible_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / 'private-copy'
            (parent / 'lib/portage').mkdir(parents=True)
            (parent / 'lib/portage/__init__.py').write_text('IDENTITY = "pinned"\n')
            code = 'import portage; assert portage.IDENTITY == "pinned"'
            parent.chmod(0)
            try:
                env = dict(os.environ, PYTHONPATH=str(parent / 'lib'), PYTHONDONTWRITEBYTECODE='1')
                broken = subprocess.run(['python3', '-c', code], env=env, capture_output=True, text=True)
                self.assertIn("No module named 'portage'", broken.stderr)
                parent.chmod(0o755)
                fixed = subprocess.run(['python3', '-c', code], env=env, capture_output=True, text=True)
                self.assertEqual(fixed.returncode, 0, fixed.stderr)
            finally:
                parent.chmod(0o755)


class RepairTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        state = self.work / 'state'
        state.mkdir()
        for name in ('71-native-portage.done', '72-native-cpu-policy.done', 'native-cpu-policy.json'):
            (state / name).write_text('immutable previous output\n')
        self.protected = {p: p.read_bytes() for p in state.iterdir()}
        self.assertEqual(repair.sha(OLD.encode()), repair.OLD_RECIPE)
        entries = {'stages/71-native-portage.sh': OLD.encode(), 'gentoo.sh': b'original driver\n'}
        manifest = ''.join(repair.sha(data) + '  ' + name + '\n' for name, data in sorted(entries.items())).encode()
        identity = repair.sha(manifest)
        self.source = self.work / 'inputs' / identity
        for name, data in entries.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.source / 'SHA256SUMS').write_bytes(manifest)
        (self.work / 'current-inputs').write_text(identity + '\n')
        self.roots = [self.work / 'native-root-x86_64' / name for name in ('usr/portage', 'usr/lib/portage')]
        for path in self.roots:
            path.mkdir(parents=True, mode=0o700)
            (path / 'pinned-content').write_text('must preserve\n')
        self.receipt = state / '71-native-portage-trees.receipt'
        self.before = ('repository tree sha256=' + tree_hash(self.roots[0]) + '\nportage tree sha256=' + tree_hash(self.roots[1]) + '\n').encode()
        self.receipt.write_bytes(self.before)

    def test_complete_paste_repeats_and_preserves_all_completed_and_cpu_receipts(self):
        paste = (GENTOO / 'repair-vm-portage-public-roots.paste.txt').read_text()
        paste = paste.replace("default=Path('/build/gentoo-bootstrap')", 'default=Path(' + repr(str(self.work)) + ')')
        for _ in range(2):
            result = subprocess.run(['bash', '-eu', '-s'], input=paste + '\necho PARENT_ALIVE\n',
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Resume: bash ', result.stdout)
            self.assertIn('PARENT_ALIVE', result.stdout)
        for path, data in self.protected.items():
            self.assertEqual(path.read_bytes(), data)
        for path in self.roots:
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual((path / 'pinned-content').read_text(), 'must preserve\n')
        self.assertNotEqual(self.receipt.read_bytes(), self.before)

    def test_changed_content_or_started_system_refuses_without_receipt_changes(self):
        (self.roots[1] / 'pinned-content').write_text('unknown edit\n')
        with self.assertRaises(ValueError):
            repair.repair(self.work)
        self.assertEqual(self.receipt.read_bytes(), self.before)
        (self.work / 'state/80-native-gentoo-system.inputs').write_text('started\n')
        with self.assertRaises(ValueError):
            repair.repair(self.work)
        self.assertEqual(self.receipt.read_bytes(), self.before)

    def test_interrupted_metadata_publication_recovers_from_the_saved_original(self):
        atomic = repair.atomic
        def interrupt(path, data):
            if path == self.receipt:
                raise OSError('interrupted publication')
            atomic(path, data)
        with patch.object(repair, 'atomic', side_effect=interrupt):
            with self.assertRaises(OSError):
                repair.repair(self.work)
        repair.repair(self.work)
        self.assertNotEqual(self.receipt.read_bytes(), self.before)
        for path, data in self.protected.items():
            self.assertEqual(path.read_bytes(), data)
