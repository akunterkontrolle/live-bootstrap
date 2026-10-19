"""Pinned source Portage reports HEAD without Git metadata, including on --version."""
import importlib.util
import os
from pathlib import Path
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
