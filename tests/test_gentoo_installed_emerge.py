"""The installed policy launcher follows Gentoo's Python selection, not the seed."""
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = importlib.util.spec_from_file_location('installed_guard', GENTOO / 'source-only-emerge.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class InstalledEmergeTests(unittest.TestCase):
    def test_dispatcher_is_executed_and_python_source_uses_the_guard_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'dispatcher'
            script = Path(temporary) / 'emerge.py'
            binary.write_bytes(b'\x7fELFfixture')
            script.write_text('#!/usr/bin/python3\n')
            self.assertEqual(guard.emerge_command(str(binary)), [str(binary)])
            self.assertEqual(guard.emerge_command(str(script))[-1], str(script))
            self.assertEqual(len(guard.emerge_command(str(script))), 2)

    def test_launcher_selects_314_from_stock_emerge_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usr = root / 'usr'
            (usr / 'bin').mkdir(parents=True)
            (usr / 'lib/python-exec/python3.14').mkdir(parents=True)
            dispatcher = usr / 'lib/python-exec/python-exec2'
            dispatcher.write_text('#!/bin/sh\n[ "$*" = --version ] || exit 91\nprintf "Portage 3.0.82.2 (python 3.14.0-final-0, profile)\\n"\n')
            dispatcher.chmod(0o755)
            (usr / 'bin/emerge').symlink_to(dispatcher)
            (usr / 'lib/python-exec/python3.14/emerge').write_text('package entry\n')
            python = usr / 'bin/python3.14'
            python.write_text('#!/usr/bin/python3\nimport json,os,sys\nopen(os.environ["RESULT"],"w").write(json.dumps(sys.argv[1:]))\n')
            python.chmod(0o755)
            launcher = root / 'launcher.sh'
            launcher.write_text((GENTOO / 'source-only-emerge.sh').read_text().replace('/usr/', str(usr) + '/'))
            result = root / 'result'
            process = subprocess.run(['sh', str(launcher), '--pretend', 'cat/a b'],
                                     env=dict(os.environ, RESULT=str(result)), text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            args = json.loads(result.read_text())
            self.assertTrue(args[0].endswith('source-only-emerge.py'))
            self.assertEqual(args[1:], [str(usr / 'lib/python-exec/python3.14/emerge'), '--pretend', 'cat/a b'])
            dispatcher.write_text('#!/bin/sh\nprintf "invalid version\\n"\n')
            result.unlink()
            refused = subprocess.run(['sh', str(launcher)], env=dict(os.environ, RESULT=str(result)), capture_output=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse(result.exists())
