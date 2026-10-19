"""Failure-path and tool-floor regressions found during the continuation audit."""
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def shell(self, body, success=True):
        result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + body], cwd=self.directory,
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def driver_fixture(self, failure):
        bundle = self.directory / 'bundle'
        (bundle / 'lib').mkdir(parents=True)
        (bundle / 'stages').mkdir()
        driver = (GENTOO / 'gentoo.sh').read_text().replace('WORK_ROOT=/build/gentoo-bootstrap', 'WORK_ROOT="$SCRIPT_DIR/work"')
        (bundle / 'gentoo.sh').write_text(driver)
        (bundle / 'config.sh').write_text('GENTOO_MODE=vm\nGENTOO_DISCOVERED_WORKSPACE_UUID=fixture\nGENTOO_MODERN_KERNEL_READY=0\n')
        (bundle / 'lib/common.sh').write_text('''gentoo_die() { printf 'gentoo: %s\\n' "$*" >&2; exit 1; }
gentoo_verify_bundle() { :; }
gentoo_lock() { :; }
gentoo_persist_bundle() { :; }
''')
        (bundle / 'lib/devices.sh').write_text('gentoo_mount_workspace() { :; }\n')
        (bundle / 'lib/mounts.sh').write_text('gentoo_prepare_virtual_filesystems() { :; }\n')
        (bundle / 'lib/cpu-policy.sh').write_text('gentoo_verify_recorded_cpu() { :; }\n')
        for name in ('state', 'sources', 'toolchain'):
            (bundle / 'lib' / (name + '.sh')).write_text('')
        for line in driver.splitlines():
            if line.startswith('. "$SCRIPT_DIR/stages/'):
                name = line.split('/stages/', 1)[1].split('"', 1)[0]
                (bundle / 'stages' / name).write_text('')
        if failure:
            (bundle / 'stages/00-inputs.sh').write_text('''broken() {
    { printf partial; gentoo_die 'redirected prerequisite failed'; } > "$WORK_ROOT/partial-inputs"
}
broken
''')
        else:
            (bundle / 'stages/00-inputs.sh').write_text('printf "fixture stage finished\\n"\n')
        fake = self.directory / 'bin'
        fake.mkdir()
        (fake / 'id').write_text('#!/bin/sh\nprintf "0\\n"\n')
        (fake / 'id').chmod(0o755)
        return bundle, dict(os.environ, PATH=str(fake) + ':' + os.defpath, GENTOO_CONSOLE_SESSION='1')

    def test_redirected_stage_failure_drains_log_and_returns_without_deadlock(self):
        bundle, env = self.driver_fixture(failure=True)
        process = subprocess.Popen(['bash', str(bundle / 'gentoo.sh'), 'build'], env=env,
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            self.fail('driver deadlocked waiting for its logger after a redirected failure')
        self.assertEqual(process.returncode, 1, stdout + stderr)
        self.assertIn('redirected prerequisite failed', stdout + stderr)
        self.assertIn('Continuation exited 1', stdout)
        logs = list((bundle / 'work/logs').glob('run-*'))
        self.assertEqual(len(logs), 1)
        self.assertIn('Continuation exited 1', logs[0].read_text())
        self.assertEqual((bundle / 'work/partial-inputs').read_text(), 'partial')

    def test_successful_driver_also_waits_for_complete_log(self):
        bundle, env = self.driver_fixture(failure=False)
        result = subprocess.run(['bash', str(bundle / 'gentoo.sh'), 'build'], env=env,
                                text=True, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        log = next((bundle / 'work/logs').glob('run-*')).read_text()
        self.assertIn('fixture stage finished', log)
        self.assertIn('Continuation exited 0', log)

    def test_build_mask_keeps_public_runtime_readable_and_workspace_private(self):
        bundle, env = self.driver_fixture(failure=False)
        (bundle / 'stages/10-host-preflight.sh').write_text(
            'mkdir "$WORK_ROOT/public-runtime"\n'
            'printf runtime > "$WORK_ROOT/public-runtime/file"\n')
        result = subprocess.run(['bash', str(bundle / 'gentoo.sh'), 'build'], env=env,
                                text=True, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((bundle / 'work').stat().st_mode & 0o777, 0o700)
        self.assertEqual(next((bundle / 'work/logs').glob('run-*')).stat().st_mode & 0o777, 0o600)
        self.assertEqual((bundle / 'work/public-runtime').stat().st_mode & 0o777, 0o755)
        self.assertEqual((bundle / 'work/public-runtime/file').stat().st_mode & 0o777, 0o644)

    def test_recursive_unmount_uses_old_tool_compatible_leaf_first_calls(self):
        mounts = self.directory / 'mounts'
        mounts.write_text('dev /native/dev devtmpfs rw 0 0\npts /native/dev/pts devpts rw 0 0\nshm /native/dev/shm tmpfs rw 0 0\nother /native/dev2 tmpfs rw 0 0\n')
        result = self.shell(f'''. {shlex.quote(str(GENTOO / 'lib/mounts.sh'))}
umount() {{ printf '%s\\n' "$*"; }}
gentoo_unmount_tree /native/dev {shlex.quote(str(mounts))}
''')
        self.assertEqual(result.stdout.splitlines(), ['/native/dev/shm', '/native/dev/pts', '/native/dev'])
        result = self.shell(f'''. {shlex.quote(str(GENTOO / 'lib/mounts.sh'))}
umount() {{ printf '%s\\n' "$*"; return 1; }}
gentoo_unmount_tree /native/dev {shlex.quote(str(mounts))}
''', success=False)
        self.assertEqual(result.stdout.splitlines(), ['/native/dev/shm'])

    def test_native_storage_inspectors_do_not_need_host_executables_or_mounts(self):
        native = self.directory / 'native'
        (native / 'usr/bin').mkdir(parents=True)
        (native / 'bin').mkdir()
        (native / 'lib64').mkdir()
        loader = native / 'lib64/ld-linux-x86-64.so.2'
        loader.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        loader.chmod(0o755)
        for name in ('lsblk', 'findmnt', 'wipefs', 'rsync'):
            binary = native / 'bin' / name
            binary.write_text('native executable fixture\n')
            binary.chmod(0o755)
            (native / 'usr/bin' / name).symlink_to('/bin/' + name)
        result = self.shell(f'''. {shlex.quote(str(GENTOO / 'lib/bootstrap-tools.sh'))}
NATIVE_ROOT={shlex.quote(str(native))}
gentoo_die() {{ printf '%s\\n' "$*" >&2; exit 1; }}
gentoo_use_native_storage_tools
lsblk -dnpo NAME,TYPE
findmnt -rn -T /build -o SOURCE
wipefs --no-act --noheadings --output TYPE /dev/fixture
rsync --version
''')
        for name in ('lsblk', 'findmnt', 'wipefs', 'rsync'):
            self.assertIn(str(native / 'bin' / name), result.stdout)
        self.assertNotIn('\n/bin/lsblk\n', result.stdout)
        self.assertIn('--inhibit-cache', result.stdout)

    def test_native_gcc_target_wrappers_select_each_abi(self):
        recipe = (GENTOO / 'stages/62-native-compiler.sh').read_text()
        start = recipe.index('    local language compiler wrapper\n')
        end = recipe.index('    cd "$gcc_build"', start)
        # Execute the actual wrapper-generation commands, then use a fake
        # existing cross compiler to observe the exact forwarded arguments.
        block = recipe[start:end].replace('    local language compiler wrapper\n', '')
        prefix = self.directory / 'cross'
        (prefix / 'bin').mkdir(parents=True)
        build = self.directory / 'build'
        build.mkdir()
        for language in ('gcc', 'g++'):
            path = prefix / 'bin' / ('target-' + language)
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            path.chmod(0o755)
        self.shell('PREFIX=' + shlex.quote(str(prefix)) + '\nTARGET=target\n'
                   + 'NATIVE_ROOT=/fixture-native\ngcc_build=' + shlex.quote(str(build)) + '\n' + block)
        for language in ('gcc', 'g++'):
            for abi in (32, 64):
                result = subprocess.run([str(build / ('cross-' + language)), '-m' + str(abi), 'input.cc'],
                                        text=True, capture_output=True, check=True)
                self.assertIn('-B/fixture-native/usr/lib' + str(abi) + '/', result.stdout)
                self.assertIn('-L/fixture-native/usr/lib' + str(abi), result.stdout)
                self.assertIn('--sysroot=/fixture-native', result.stdout)
        self.assertIn('CC_FOR_TARGET="$gcc_build/cross-gcc"', recipe)
        self.assertIn('CXX_FOR_TARGET="$gcc_build/cross-g++ -nostdinc++"', recipe)

    def test_console_session_acquires_serial_tty_and_ctrl_c_stops_child(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        command = ['python3', str(GENTOO / 'console-session.py'), 'bash', '-c',
                   'test -t 0 && test -r /dev/tty || exit 3; printf "console ready\\n"; sleep 300']
        process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        output = b''
        deadline = time.monotonic() + 3
        while b'console ready' not in output and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                output += os.read(master, 4096)
        self.assertIn(b'console ready', output)
        os.write(master, b'\x03')
        self.assertEqual(process.wait(timeout=3), 130)

    def test_real_static_openssl_zlib_prerequisite_probe(self):
        value = os.environ.get('GENTOO_AUDIT_STATIC_PREFIX')
        if not value:
            self.skipTest('set GENTOO_AUDIT_STATIC_PREFIX to the source-built static OpenSSL/zlib prefix')
        prefix = Path(value)
        compiler = self.directory / 'fixture-gcc'
        compiler.write_text('#!/bin/sh\ncase "$1" in -print-file-name=*) printf "%s/%s\\n" ' +
                            shlex.quote(str(prefix / 'lib')) + ' "${1#-print-file-name=}";; *) exec /usr/bin/gcc "$@";; esac\n')
        compiler.chmod(0o755)
        result = self.shell(f'''WORK_ROOT="$PWD/work"
mkdir -p "$WORK_ROOT/state"
printf original-toolchain > "$WORK_ROOT/state/host-toolchain.inputs"
. {shlex.quote(str(GENTOO / 'lib/common.sh'))}
. {shlex.quote(str(GENTOO / 'lib/python.sh'))}
gentoo_python_host_inputs {shlex.quote(str(compiler))} {shlex.quote(str(prefix / 'include'))}
cp "$WORK_ROOT/state/host-python.inputs" first-inputs
gentoo_python_host_inputs {shlex.quote(str(compiler))} {shlex.quote(str(prefix / 'include'))}
cmp first-inputs "$WORK_ROOT/state/host-python.inputs"
''')
        self.assertIn('static OpenSSL/zlib link and runtime probe passed', result.stdout)
        inputs = (self.directory / 'work/state/host-python.inputs').read_text()
        self.assertIn('libssl.a', inputs)
        self.assertIn('libcrypto.a', inputs)
        self.assertNotIn('libssl.so', inputs)

    def test_native_compiler_verifier_rejects_failed_compile_even_with_old_output(self):
        recipe = (GENTOO / 'stages/62-native-compiler.sh').read_text()
        probe = recipe[recipe.index('native_compiler_probe() {'):recipe.index('\nnative_compiler_verify()')]
        self.shell(probe + '''
NATIVE_ROOT="$PWD/native"
mkdir -p "$NATIVE_ROOT/tmp"
printf stale > "$NATIVE_ROOT/tmp/native-probe-64"
chroot() { return 1; }
native_verify_elf() { :; }
gentoo_die() { exit 1; }
# This is the same conditional context used by the receipt verifier.
if native_compiler_probe 64; then exit 9; fi
''')


if __name__ == '__main__':
    unittest.main()
