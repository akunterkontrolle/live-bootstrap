"""Public Portage copy roots must survive private extraction roots and resumption."""
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
RECIPE = (GENTOO / 'stages/71-native-portage.sh').read_text()
HELPER = 'native_portage_tree_hash() {' + RECIPE.split('native_portage_tree_hash() {', 1)[1].split('\nnative_portage_configure()', 1)[0]


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
