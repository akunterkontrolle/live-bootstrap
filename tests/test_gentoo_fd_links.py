"""Bootstrap /dev placeholders must not prevent continuation startup."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import types
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


class DescriptorLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.dev = self.directory / 'dev'
        self.dev.mkdir()

    def run_helper(self, extra='', success=True):
        body = ('set -Eeuo pipefail\n'
                '. ' + shlex.quote(str(GENTOO / 'lib/mounts.sh')) + '\n'
                'gentoo_die() { printf "%s\\n" "$*" >&2; exit 1; }\n'
                + extra + '\ngentoo_prepare_fd_links ' + shlex.quote(str(self.dev)))
        result = subprocess.run(['bash', '-c', body], text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_empty_nonempty_fifo_and_alternate_link_preserved_and_normalized(self):
        (self.dev / 'stdin').touch()
        (self.dev / 'stdout').write_text('preserve diagnostic output\n')
        os.mkfifo(str(self.dev / 'stderr'))
        (self.dev / 'fd').symlink_to('/dev/nonstandard-fd')
        result = self.run_helper()
        for name, target in {'fd': '/proc/self/fd', 'stdin': '/proc/self/fd/0',
                             'stdout': '/proc/self/fd/1', 'stderr': '/proc/self/fd/2'}.items():
            self.assertEqual(os.readlink(str(self.dev / name)), target)
            saved = list(self.dev.glob('.gentoo-fd-' + name + '.*/original'))
            self.assertEqual(len(saved), 1)
        self.assertEqual(next(self.dev.glob('.gentoo-fd-stdout.*/original')).read_text(),
                         'preserve diagnostic output\n')
        self.assertTrue(next(self.dev.glob('.gentoo-fd-stderr.*/original')).is_fifo())
        self.assertEqual(os.readlink(str(next(self.dev.glob('.gentoo-fd-fd.*/original')))),
                         '/dev/nonstandard-fd')
        self.assertIn('Preserved previous', result.stderr)
        self.run_helper()
        self.assertEqual(len(list(self.dev.glob('.gentoo-fd-*'))), 4)

    def test_correct_links_and_missing_entries_are_repeatable(self):
        self.run_helper()
        before = {p.name: p.lstat().st_ino for p in self.dev.iterdir()}
        self.run_helper()
        self.assertEqual(before, {p.name: p.lstat().st_ino for p in self.dev.iterdir()})
        result = subprocess.run(['bash', '-c', 'cat < ' + shlex.quote(str(self.dev / 'stdin'))],
                                input='actual pipe input\n', text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout, 'actual pipe input\n')

    def test_directory_is_not_moved_or_deleted(self):
        (self.dev / 'fd').mkdir()
        (self.dev / 'fd/owned').write_text('retain')
        self.run_helper(success=False)
        self.assertEqual((self.dev / 'fd/owned').read_text(), 'retain')

    def python_helper(self):
        script = (GENTOO / 'lib/mounts.sh').read_text()
        code = script.split("<<'GENTOO_FD_LINKS_PY'\n", 1)[1].split('\nGENTOO_FD_LINKS_PY', 1)[0]
        module = types.ModuleType('fd_links_fixture')
        exec(compile(code, 'mounts.sh:inline-python', 'exec'), module.__dict__)
        return module

    def test_failed_link_creation_restores_original(self):
        self.run_helper()
        (self.dev / 'stdin').unlink()
        (self.dev / 'stdin').write_text('retain on failure')
        module = self.python_helper()
        symlink = os.symlink
        def failing(target, path):
            if str(path) == str(self.dev / 'stdin'):
                raise PermissionError('fixture link creation refused')
            return symlink(target, path)
        with mock.patch.object(module.os, 'symlink', side_effect=failing):
            with self.assertRaises(PermissionError):
                module.normalize(str(self.dev))
        self.assertEqual((self.dev / 'stdin').read_text(), 'retain on failure')

    def test_entry_vanishing_before_rename_is_rechecked_and_created(self):
        self.run_helper()
        (self.dev / 'stdin').unlink()
        (self.dev / 'stdin').touch()
        module = self.python_helper()
        rename = os.rename
        vanished = []
        def changing(source, target):
            if str(source) == str(self.dev / 'stdin') and not vanished:
                os.unlink(source)
                vanished.append(source)
                raise FileNotFoundError(source)
            return rename(source, target)
        with mock.patch.object(module.os, 'rename', side_effect=changing):
            module.normalize(str(self.dev))
        self.assertEqual(len(vanished), 1)
        self.assertEqual(os.readlink(str(self.dev / 'stdin')), '/proc/self/fd/0')

    def test_shell_file_utilities_are_not_used_for_descriptor_entries(self):
        (self.dev / 'stdin').write_text('preserve')
        self.run_helper('mv() { return 1; }; ln() { return 1; }; readlink() { return 1; }')
        self.assertEqual(next(self.dev.glob('.gentoo-fd-stdin.*/original')).read_text(), 'preserve')
