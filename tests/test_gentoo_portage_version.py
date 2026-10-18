"""Pinned source Portage reports HEAD without Git metadata, including on --version."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SourceVersionTests(unittest.TestCase):
    def test_head_banner_requires_correct_tree_project_and_runtime(self):
        probe = load('version_probe', GENTOO / 'probes/portage-version.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'meson.build').write_text("project(\n 'portage',\n 'c',\n version : '3.0.82.2'\n)\n")
            module = types.SimpleNamespace(__file__=str(root / 'lib/portage/__init__.py'), VERSION='HEAD')
            source_type = object()
            installation = types.SimpleNamespace(TYPE=source_type, TYPES=types.SimpleNamespace(SOURCE=source_type))
            with mock.patch.dict('sys.modules', {'portage.installation': installation}):
                probe.verify(root, 'Portage HEAD (python 3.12.15)\n', module)
                for banner in ('Portage 3.0.82.2 (python 3.12)\n', 'Portage HEAD-evil (python 3.12)\n',
                               'Portage 3.0.99 (python 3.12)\n', '', 'Portage HEAD ()\nPortage HEAD ()\n'):
                    with self.assertRaises(ValueError):
                        probe.verify(root, banner, module)
                module.__file__ = '/unexpected/portage/__init__.py'
                with self.assertRaises(ValueError):
                    probe.verify(root, 'Portage HEAD ()\n', module)
                module.__file__ = str(root / 'lib/portage/__init__.py')
                (root / 'meson.build').write_text("project(\n 'portage',\n version : '3.0.99'\n)\n")
                with self.assertRaises(ValueError):
                    probe.verify(root, 'Portage HEAD ()\n', module)

    def test_real_pinned_source_emerge_version(self):
        source = os.environ.get('GENTOO_AUDIT_PORTAGE_SOURCE')
        prefix = os.environ.get('GENTOO_AUDIT_STATIC_PREFIX')
        if not source or not prefix:
            self.skipTest('set pinned Portage source and audit static Python prefix for real source execution')
        source = Path(source).resolve()
        python = Path(prefix).parent / 'python-build/python'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config/etc/portage').mkdir(parents=True)
            (root / 'config/etc/portage/make.conf').write_text('FEATURES="-test strict"\n')
            (root / 'root').mkdir()
            env = dict(os.environ, PYTHONPATH=str(source / 'lib'), ROOT=str(root / 'root'),
                       PORTAGE_CONFIGROOT=str(root / 'config'), PORTAGE_TMPDIR=str(root / 'tmp'),
                       PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([str(python), str(source / 'bin/emerge'), '--version'],
                                    env=env, text=True, capture_output=True, check=True)
            self.assertIn('Portage HEAD (', result.stdout)
            banner = root / 'version.txt'
            banner.write_text(result.stdout + result.stderr)
            subprocess.run([str(python), str(GENTOO / 'probes/portage-version.py'), str(source), str(banner)],
                           env=env, check=True, capture_output=True)
            banner.write_text('Portage 9.9 (wrong)\n')
            result = subprocess.run([str(python), str(GENTOO / 'probes/portage-version.py'), str(source), str(banner)],
                                    env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)


class VersionRepairTests(unittest.TestCase):
    def setUp(self):
        fixture = load('audit_fixture_for_portage', ROOT / 'tests/test_gentoo_audit_repair.py')
        self.fixture = fixture.AuditRepairTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.work = self.fixture.work
        source = self.fixture.source
        extras = ['stages/52-portage-host.sh', 'lib/portage-host.sh', 'lib/manifests.sh', 'lib/sources.sh',
                  'probes/portage-integrity.py', 'sources/portage.lock', 'stages/51-host-python.sh']
        for name in extras:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('old ' + name + '\n')
        files = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*')
                 if p.is_file() and p.name != 'SHA256SUMS'}
        manifest = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n' for name, data in sorted(files.items()))
        (source / 'SHA256SUMS').write_text(manifest)
        self.old = hashlib.sha256(manifest.encode()).hexdigest()
        self.source = source.with_name(self.old)
        source.rename(self.source)
        (self.work / 'current-inputs').write_text(self.old + '\n')
        for suffix in ('inputs', 'done'):
            (self.work / 'state' / ('51-host-python.' + suffix)).write_text('b' * 64 + '\n')
        names = extras[:6] + [str(self.work / 'state/50-gentoo-sources.done'), str(self.work / 'state/51-host-python.done')]
        # Use the actual Bash fingerprint implementation to check compatibility
        # with the transaction's independently implemented prerequisite check.
        result = subprocess.run(['bash', '-c', '. ' + shlex.quote(str(GENTOO / 'lib/state.sh'))
                                 + '; gentoo_fingerprint ' + ' '.join(shlex.quote(n) for n in names)],
                                cwd=self.source, text=True, capture_output=True, check=True)
        (self.work / 'state/52-portage-host.inputs').write_text(result.stdout)

    def test_repairs_only_failed_52_and_future_native_check(self):
        before = {p.name: p.read_bytes() for p in (self.work / 'state').iterdir()}
        old_files = {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        result = self.fixture.run_script(GENTOO / 'repair-vm-portage-version.paste.txt')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Published audited snapshot:', result.stdout)
        self.assertIn('PARENT_ALIVE', result.stdout)
        new = self.work / 'inputs' / (self.work / 'current-inputs').read_text().strip()
        expected = {'stages/52-portage-host.sh', 'lib/portage-host.sh', 'stages/71-native-portage.sh', 'probes/portage-version.py'}
        for name, data in old_files.items():
            self.assertEqual((self.source / name).read_bytes(), data)
            if name not in expected | {'SHA256SUMS'}:
                self.assertEqual((new / name).read_bytes(), data)
        for name in expected:
            self.assertEqual((new / name).read_bytes(), (GENTOO / name).read_bytes())
        for name, data in before.items():
            if name == '52-portage-host.inputs':
                self.assertFalse((self.work / 'state' / name).exists())
                self.assertEqual((self.work / 'repair-state' / (name + '.' + self.old)).read_bytes(), data)
            else:
                self.assertEqual((self.work / 'state' / name).read_bytes(), data)

    def test_mismatched_failed_fingerprint_refused(self):
        (self.work / 'state/52-portage-host.inputs').write_text('c' * 64 + '\n')
        result = self.fixture.run_script(GENTOO / 'repair-vm-portage-version.paste.txt')
        self.assertIn('fingerprint does not match', result.stderr)
        self.assertIn('PARENT_ALIVE', result.stdout)
        self.assertEqual((self.work / 'current-inputs').read_text().strip(), self.old)

    def test_completed_52_refused_without_reset(self):
        receipt = self.work / 'state/52-portage-host.done'
        receipt.write_text('c' * 64 + '\n')
        result = self.fixture.run_script(GENTOO / 'repair-vm-portage-version.paste.txt')
        self.assertIn('later stage has already started', result.stderr)
        self.assertEqual(receipt.read_text(), 'c' * 64 + '\n')
        self.assertEqual((self.work / 'current-inputs').read_text().strip(), self.old)
