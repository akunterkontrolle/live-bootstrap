"""Native CPU decisions are guest observations and immutable semantic inputs."""
# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = importlib.util.spec_from_file_location('cpu_policy', GENTOO / 'cpu-policy.py')
cpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu)


class NativeCpuPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.detector = self.root / 'cpuid2cpuflags'
        self.detector.write_text('#!/bin/sh\nprintf "*/* CPU_FLAGS_X86: sse2 avx2 aes\\n"\n')
        self.detector.chmod(0o755)
        self.compiler = self.root / 'gcc'
        self.compiler.write_text('#!/bin/sh\n[ "$LC_ALL" = C ] || exit 8\n'
                                 '[ "$*" = "-march=native -mtune=native -Q --help=target" ] || exit 9\n'
                                 'printf "  -march= haswell\\n  -mtune= haswell\\n  -mavx2 [enabled]\\n"\n')
        self.compiler.chmod(0o755)
        self.cpuinfo = self.root / 'cpuinfo'
        self.cpuinfo.write_text('processor : 0\nflags : sse sse2 avx2 aes\nprocessor : 1\nflags : sse2 avx2 aes\n')

    def test_detect_executes_the_selected_guest_tools_and_intersects_all_vcpus(self):
        result = cpu.detect(self.detector, self.compiler, self.cpuinfo)
        self.assertEqual(result['cpu_flags_x86'], ['aes', 'avx2', 'sse2'])
        self.assertEqual(result['kernel_flags'], ['aes', 'avx2', 'sse2'])
        self.assertEqual(result['compiler_target'], {'march': 'haswell', 'mtune': 'haswell'})
        self.assertIn('-march=native -mtune=native', result['cflags'])
        cpu.verify(result, result)

    def test_lost_feature_or_changed_compiler_target_stops_resumption(self):
        record = cpu.detect(self.detector, self.compiler, self.cpuinfo)
        for field in ('cpu_flags_x86', 'kernel_flags', 'compiler_target', 'cflags'):
            observed = copy.deepcopy(record)
            if field == 'compiler_target':
                observed[field]['march'] = 'x86-64'
            elif field == 'cflags':
                # A changed recorded build policy must require a fresh lineage.
                changed = copy.deepcopy(record)
                changed[field] = '-O3'
                with self.assertRaises(ValueError):
                    cpu.verify(changed, observed)
                continue
            else:
                observed[field].remove('avx2')
            with self.subTest(field=field), self.assertRaises(ValueError):
                cpu.verify(record, observed)
        observed = copy.deepcopy(record)
        observed['kernel_flags'].append('additional_feature')
        with self.assertRaises(ValueError):
            cpu.verify(record, observed)

    def test_malformed_detector_output_or_unresolved_native_target_refuses(self):
        self.detector.write_text('#!/bin/sh\necho CPU_FLAGS_X86=avx2\n')
        with self.assertRaises(ValueError):
            cpu.detect(self.detector, self.compiler, self.cpuinfo)
        self.detector.write_text('#!/bin/sh\necho "*/* CPU_FLAGS_X86: sse2"\n')
        self.compiler.write_text('#!/bin/sh\necho " -march= native"\n')
        with self.assertRaises(ValueError):
            cpu.detect(self.detector, self.compiler, self.cpuinfo)

    def test_record_and_global_package_use_resume_without_recomputing_policy(self):
        receipt = self.root / 'native-cpu-policy.json'
        policy = self.root / 'package.use/00-cpu-flags'
        command = ['python3', str(GENTOO / 'cpu-policy.py'), '--initialize', '--receipt', str(receipt),
                   '--detector', str(self.detector), '--compiler', str(self.compiler), '--package-use', str(policy)]
        for _ in range(2):
            result = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(policy.read_text(), '*/* CPU_FLAGS_X86: aes avx2 sse2\n')
        before = receipt.read_bytes()
        policy.write_text('operator override\n')
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt.read_bytes(), before)
        self.assertEqual(policy.read_text(), 'operator override\n')

    def test_legacy_gentoo_root_without_cpu_receipt_cannot_resume_as_native(self):
        state = self.root / 'state'
        state.mkdir()
        script = ('set -Eeuo pipefail\nWORK_ROOT=' + shlex.quote(str(self.root)) + '\n'
                  'gentoo_die() { echo "$*" >&2; exit 19; }\n. '
                  + shlex.quote(str(GENTOO / 'lib/cpu-policy.sh')) + '\ngentoo_verify_recorded_cpu\n')
        result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        (state / '80-native-gentoo-system.inputs').write_text('previous generic build\n')
        result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 19)
        self.assertIn('start a fresh lineage', result.stderr)
