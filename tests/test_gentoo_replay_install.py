"""Behavioral regressions for the ordinary Gentoo replay path.

These fixtures execute maintained recipes and helpers, independently of the
historical snapshot repair generators and paste payloads.
"""
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'



class InstallerDispatchTests(unittest.TestCase):
    def dispatch(self, *arguments):
        recipe = (GENTOO / 'install-base.sh').read_text()
        parser = 'MODE=--plan;' + recipe.split('MODE=--plan;', 1)[1].split('[ "$(id -u)"', 1)[0]
        dispatch = 'case "$MODE" in' + recipe.rsplit('case "$MODE" in', 1)[1]
        with tempfile.TemporaryDirectory() as directory:
            script = ('set -Eeuo pipefail\nSTATE=' + shlex.quote(directory) + '\n'
                      'fail() { echo "$*" >&2; exit 19; }\n'
                      'gentoo_die() { fail "$@"; }\n'
                      'native_mounts() { echo UNEXPECTED_MOUNT >&2; exit 99; }\n'
                      'native_run() { echo UNEXPECTED_WRITE >&2; exit 99; }\n'
                      'install_root() { echo UNEXPECTED_INSTALL >&2; exit 99; }\n'
                      + parser + '\n' + dispatch)
            return subprocess.run(['bash', '-c', script, 'installer-fixture', *arguments],
                                  input='', text=True, capture_output=True, timeout=3)

    def test_default_and_explicit_plan_do_not_mount_or_write_target(self):
        for arguments in ((), ('--plan',)):
            result = self.dispatch(*arguments)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Plan only; no storage writes or mounts performed.', result.stdout)

    def test_mutating_modes_require_terminal_before_mount_or_write(self):
        for mode in ('--initialize-storage', '--resume-storage', '--install-root'):
            result = self.dispatch(mode)
            self.assertEqual(result.returncode, 19, result.stderr)
            self.assertIn('interactive terminal required', result.stderr)
            self.assertNotIn('UNEXPECTED_', result.stderr)

    def test_unknown_modes_and_extra_arguments_refuse(self):
        for arguments in (('--erase',), ('--plan', '--initialize-storage')):
            result = self.dispatch(*arguments)
            self.assertEqual(result.returncode, 19, result.stderr)
            self.assertNotIn('UNEXPECTED_', result.stderr)


class FilesystemLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        for fs in ('root', 'home', 'swap'):
            (self.state / ('lv-' + fs + '.done')).write_text('filesystem preparation=not started\n')
        (self.root / 'root').write_text('ext4')
        for fs in ('home', 'swap'):
            (self.root / fs).write_text('')
        installer = (GENTOO / 'install-base.sh').read_text()
        self.loop = installer.split('    for fs in root home swap; do\n', 1)[1].split('\n    [ "$(lvm_run lvs', 1)[0]
        self.loop = 'for fs in root home swap; do\n' + self.loop
        self.script = '''set -Eeuo pipefail
STATE=$1; FIXTURE=$2; VG=gentoo; MODE=--resume-storage; luks_uuid=existing
fail() { echo "$*" >&2; exit 19; }
sha256() { sha256sum < "$1" | awk '{print $1}'; }
phase() { local name=$1; shift; printf '%s\\n' "$@" > "$STATE/$name"; }
lvm_run() { case " $* " in *' lv_uuid '*) echo lv-identity;; *) echo 1073741824;; esac; }
blkid() {
 local device=${!#} fs type; fs=${device##*/}; type=$(cat "$FIXTURE/$fs")
 [ -n "$type" ] || return 2
 case " $* " in *' UUID '*) echo existing-root-uuid;; *) echo "$type";; esac
}
dumpe2fs() { echo 'Filesystem state:         clean'; }
native_run() {
 local device=${!#} fs=${!#}; fs=${fs##*/}
 echo "$1 $fs" >> "$FIXTURE/formats"
 case "$1" in mkfs.ext4) echo ext4 > "$FIXTURE/$fs";; mkswap) echo swap > "$FIXTURE/$fs";; *) exit 9;; esac
}
. ''' + shlex.quote(str(GENTOO / 'lib/installer-filesystems.sh')) + '\n'

    def run_loop(self, extra=''):
        return subprocess.run(['bash', '-c', self.script + extra + '\n' + self.loop,
                               'format-fixture', str(self.state), str(self.root)],
                              text=True, capture_output=True, timeout=5)

    def test_resume_preserves_root_and_formats_only_blank_home_swap_once(self):
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'formats').read_text(), 'mkfs.ext4 home\nmkswap swap\n')
        self.assertEqual(self.run_loop().returncode, 0)
        self.assertEqual((self.root / 'formats').read_text().count('\n'), 2)
        self.assertTrue((self.state / 'fs-root.done').exists())

    def test_probe_error_never_infers_blank_or_formats(self):
        result = self.run_loop('blkid() { return 4; }')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refusing to infer an empty LV', result.stderr)
        self.assertFalse((self.root / 'formats').exists())

    def test_interrupted_blank_format_requires_inspection(self):
        (self.state / 'fs-home.started').write_text('interrupted\n')
        result = self.run_loop()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('format was interrupted', result.stderr)
        self.assertFalse((self.root / 'formats').exists())

    def test_completed_clean_format_without_final_receipt_is_adopted(self):
        (self.state / 'fs-home.started').write_text('interrupted after mkfs\n')
        (self.root / 'home').write_text('ext4')
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'formats').read_text(), 'mkswap swap\n')

    def test_legacy_blank_without_identity_bound_approval_refuses(self):
        (self.state / 'lv-home.done').write_text('LV=gentoo/home\n')
        result = self.run_loop()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no verified first-format intent', result.stderr)
        self.assertFalse((self.root / 'formats').exists())

    def test_matching_legacy_approval_allows_format_changed_lv_identity_refuses(self):
        (self.state / 'target.identity').write_text('target\n')
        import hashlib
        target = hashlib.sha256(b'target\n').hexdigest()
        (self.state / 'lv-home.done').write_text('LV=gentoo/home\n')
        approval = self.state / 'fs-home.first-format-approved'
        approval.write_text('policy=verified first format after legacy root-verifier failure\n'
                           'target identity sha256=' + target + '\nLUKS UUID=existing\nLV=gentoo/home\n'
                           'LV UUID=wrong\nLV bytes=1073741824\nverified root UUID=existing-root-uuid\n')
        self.assertNotEqual(self.run_loop().returncode, 0)
        self.assertFalse((self.root / 'formats').exists())
        approval.write_text(approval.read_text().replace('LV UUID=wrong', 'LV UUID=lv-identity'))
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_ext4_inspection_uses_native_wrappers_despite_broken_host_tools(self):
        mkfs, blkid, dumpe = (shutil.which(n, path=os.environ.get('PATH', '') + ':/usr/sbin:/sbin') for n in ('mkfs.ext4', 'blkid', 'dumpe2fs'))
        if not all((mkfs, blkid, dumpe)):
            self.skipTest('ext4 tools unavailable')
        image = self.root / 'root.ext4'
        with image.open('wb') as stream:
            stream.truncate(32 * 1024 * 1024)
        subprocess.run([mkfs, '-q', '-F', str(image)], check=True, capture_output=True)
        script = ('set -Eeuo pipefail\nfail() { exit 19; }\n'
                  'blkid() { return 4; }; dumpe2fs() { return 9; }\n'
                  'gentoo_native_tool_path() { echo native; }\n'
                  'gentoo_bootstrap_tool() { local tool=$1; shift; case "$tool" in '
                  'blkid) ' + shlex.quote(blkid) + ' "$@";; dumpe2fs) ' + shlex.quote(dumpe) + ' "$@";; esac; }\n'
                  '. ' + shlex.quote(str(GENTOO / 'lib/installer-filesystems.sh')) + '\n'
                  'gentoo_use_native_filesystem_tools\nclean_ext4 "$1"\n')
        result = subprocess.run(['bash', '-c', script, 'inspect', str(image)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class NativeAtaSerialTests(unittest.TestCase):
    def test_real_loader_finds_private_systemd_library_without_rpath(self):
        import tempfile
        cc = shutil.which('cc')
        loader = Path('/lib64/ld-linux-x86-64.so.2')
        if not cc or not loader.is_file():
            self.skipTest('requires a C compiler and the x86-64 GNU loader')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / 'usr/lib64/systemd'
            private.mkdir(parents=True)
            library = root / 'serial.c'
            library.write_text('const char *serial(void) { return "GT413958f294BOOT"; }\n')
            subprocess.run([cc, '-shared', '-fPIC', str(library), '-Wl,-soname,libsystemd-shared-260.so',
                '-o', str(private / 'libsystemd-shared-260.so')], check=True, capture_output=True)
            source = root / 'ata.c'
            source.write_text('#include <stdio.h>\nextern const char *serial(void);\n'
                              'int main(void) { printf("ID_SERIAL_SHORT=%s\\n", serial()); return 0; }\n')
            tool = root / 'usr/lib/udev/ata_id'
            tool.parent.mkdir(parents=True)
            subprocess.run([cc, str(source), '-L' + str(private), '-l:libsystemd-shared-260.so', '-o', str(tool)],
                           check=True, capture_output=True)
            (root / 'lib64').mkdir()
            (root / 'lib64/ld-linux-x86-64.so.2').symlink_to(loader)
            # Reproduce the original failure with the real dynamic loader.
            previous = subprocess.run([str(loader), '--inhibit-cache', '--library-path',
                str(root / 'lib64') + ':' + str(root / 'usr/lib64') + ':' + str(root / 'usr/lib'), str(tool)],
                text=True, capture_output=True, timeout=3)
            self.assertNotEqual(previous.returncode, 0)
            self.assertIn('libsystemd-shared-260.so', previous.stderr)
            script = ('set -Eeuo pipefail\nNATIVE_ROOT=' + shlex.quote(str(root)) + '\n. ' +
                      shlex.quote(str(GENTOO / 'lib/bootstrap-tools.sh')) + '\ngentoo_native_ata_serial /dev/sda\n')
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'GT413958f294BOOT\n')

    def test_native_udev_helper_runs_through_loader_without_host_udev(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = root / 'usr/lib/udev/ata_id'
            tool.parent.mkdir(parents=True)
            tool.write_text('#!/bin/bash\n[ "$*" = "--export /dev/sda" ] || exit 9\n'
                            'printf "ID_MODEL=QEMU_HARDDISK\\nID_SERIAL_SHORT=GT413958f294BOOT\\n"\n')
            tool.chmod(0o755)
            loader = root / 'lib64/ld-linux-x86-64.so.2'
            loader.parent.mkdir()
            loader.write_text('#!/bin/bash\nset -eu\n[ "$1" = --inhibit-cache ]\n'
                              '[ "$2" = --library-path ]\nshift 3\nexec "$@"\n')
            loader.chmod(0o755)
            script = ('set -Eeuo pipefail\nNATIVE_ROOT=' + shlex.quote(str(root)) + '\n'
                      '. ' + shlex.quote(str(GENTOO / 'lib/devices.sh')) + '\n'
                      '. ' + shlex.quote(str(GENTOO / 'lib/bootstrap-tools.sh')) + '\n'
                      'lsblk() { return 0; }\n'
                      'readlink() { if [ "$*" = "-f /sys/class/block/sda/device" ]; then '
                      'echo /no-sysfs-serial; else command readlink "$@"; fi; }\n'
                      'gentoo_disk_serial /dev/sda\n')
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'GT413958f294BOOT\n')
            # Multiple exported identities must not be accepted.
            tool.write_text('#!/bin/bash\nprintf "ID_SERIAL_SHORT=one\\nID_SERIAL_SHORT=two\\n"\n')
            result = subprocess.run(['bash', '-c', script.replace('gentoo_disk_serial /dev/sda', 'gentoo_native_ata_serial /dev/sda')],
                                    text=True, capture_output=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)

    def test_existing_lsblk_serial_retains_priority(self):
        script = ('set -Eeuo pipefail\n. ' + shlex.quote(str(GENTOO / 'lib/devices.sh')) + '\n'
                  'lsblk() { echo ORIGINAL_SERIAL; }\n'
                  'gentoo_native_ata_serial() { echo UNEXPECTED_FALLBACK >&2; return 9; }\n'
                  'gentoo_disk_serial /dev/sda\n')
        result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'ORIGINAL_SERIAL\n')
        self.assertNotIn('UNEXPECTED_FALLBACK', result.stderr)
