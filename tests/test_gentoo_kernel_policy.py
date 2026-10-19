"""Hardening requests must survive Kconfig and the selected compiler."""
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'


class KernelPolicyTests(unittest.TestCase):
    def shell(self, root, command):
        prefix = ('set -Eeuo pipefail\nBUILDS=' + shlex.quote(str(root)) + '\nWORK_ROOT=$BUILDS\nSCRIPT_DIR='
                  + shlex.quote(str(GENTOO)) + '\n. "$SCRIPT_DIR/lib/kernel.sh"\n')
        return subprocess.run(['bash', '-c', prefix + command], text=True, capture_output=True, timeout=5)

    def test_generated_configuration_must_keep_each_requested_hardening_setting(self):
        fragment = (GENTOO / 'kernel-6.12.111.config.fragment').read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config'
            config.write_text(fragment)
            self.assertEqual(self.shell(root, 'kernel_check_config ' + shlex.quote(str(config))).returncode, 0)
            for symbol in ('CONFIG_GENERIC_CPU', 'CONFIG_FORTIFY_SOURCE', 'CONFIG_STACKPROTECTOR_STRONG',
                           'CONFIG_HARDENED_USERCOPY', 'CONFIG_STRICT_KERNEL_RWX', 'CONFIG_RANDOMIZE_BASE'):
                config.write_text(fragment.replace(symbol + '=y', '# ' + symbol + ' is not set'))
                result = self.shell(root, 'kernel_check_config ' + shlex.quote(str(config)))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Missing kernel setting', result.stderr)

    def test_compiler_probe_builds_portable_elf_with_stack_canary(self):
        gcc = shutil.which('gcc')
        readelf = shutil.which('readelf')
        if not gcc or not readelf:
            self.skipTest('requires GCC and readelf')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bin').mkdir()
            (root / 'bin/target-gcc').symlink_to(gcc)
            command = ('PREFIX=' + shlex.quote(str(root)) + '\nTARGET=target\nmkdir -p "$KERNEL_BUILD"\n'
                       'kernel_check_compiler\n')
            result = self.shell(root, command)
            self.assertEqual(result.returncode, 0, result.stderr)
            obj = root / 'linux-continuation/hardening-probe.o'
            relocations = subprocess.check_output([readelf, '-r', str(obj)], text=True)
            self.assertIn('__stack_chk_fail', relocations)
            # An unsupported compiler option must stop before the kernel build.
            (root / 'bin/target-gcc').unlink()
            (root / 'bin/target-gcc').write_text('#!/bin/sh\nexit 7\n')
            (root / 'bin/target-gcc').chmod(0o755)
            self.assertEqual(self.shell(root, command).returncode, 7)
