"""Host lifecycle, kernel handoff and disposable image regression checks."""
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.gentoo_config import load
from system.gentoo import vm
from system.gentoo.kernel_source import verify_kernel_source

spec = importlib.util.spec_from_file_location('gentoo_prepare_vm_tests', ROOT / 'system/gentoo/prepare.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def manifest(directory):
    lines = [f'{vm.digest(path)}  {path.relative_to(directory).as_posix()}\n'
             for path in sorted(directory.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS']
    (directory / 'SHA256SUMS').write_text(''.join(lines))


def fake_kernel(path):
    data = bytearray(1024)
    data[510:512] = b'\x55\xaa'
    data[514:518] = b'HdrS'
    data[566] = 1
    path.write_bytes(data)


class VMTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config = self.directory / 'config.toml'
        self.config.write_text('')
        self.settings = load(self.config, 'vm')
        self.bootstrap = self.directory / 'bootstrap with spaces.img'
        self.bootstrap.write_bytes(bytes(1024))
        self.argv = ['qemu-system-x86_64', '-enable-kvm', '-m', '49152M', '-smp', '18',
                     '-drive', f'file={self.bootstrap},format=raw', '-nic', 'user,ipv6=off,model=e1000',
                     '-machine', 'kernel-irqchip=split', '-no-reboot', '-nographic']
        self.run_dir = self.directory / 'run'
        self.run_dir.mkdir()

    def record(self):
        return vm.new_record(self.settings, self.run_dir, self.argv, self.directory)

    def test_resource_ownership_generated_ids_and_root_growth(self):
        record = self.record()
        self.assertEqual((record['cpus'], record['ram_mib']), (18, 49152))
        self.assertEqual(record['images']['workspace']['bytes'], 128 * vm.GIB)
        self.assertEqual(record['images']['scratch']['bytes'], 128 * vm.GIB)
        self.assertEqual(record['images']['root']['bytes'], 500 * vm.GIB)
        self.assertEqual(record['images']['extra']['bytes'], 128 * vm.GIB)
        self.assertEqual(len({item['serial'] for item in vm.all_images(record)}), 5)
        self.settings['layout']['root_gib'] = 600
        self.assertEqual(self.record()['images']['root']['bytes'], 812 * vm.GIB)
        self.assertNotEqual(record['images']['workspace']['uuid'], record['images']['scratch']['uuid'])

    def test_command_rejects_physical_symlink_and_ambiguous_inputs(self):
        with self.assertRaises(ValueError):
            vm.parse_command(self.argv + ['-smp', '2'], self.directory)
        with self.assertRaises(ValueError):
            vm.parse_command(self.argv + ['-bios', 'unreviewed.rom'], self.directory)
        link = self.directory / 'alias.img'
        link.symlink_to(self.bootstrap)
        for image in ('/dev/null', str(link)):
            argv = list(self.argv)
            argv[argv.index('-drive') + 1] = f'file={image},format=raw'
            with self.assertRaises(ValueError):
                vm.parse_command(argv, self.directory)

    def test_recorded_command_controls_continuation_without_resource_arguments(self):
        record = self.record()
        vm.atomic_json(self.run_dir / vm.RECORD, record)
        restored = vm.read_record(self.run_dir)
        command = vm.command(restored, self.run_dir, self.run_dir / 'kernel')
        self.assertEqual(command[command.index('-smp') + 1], '18')
        self.assertEqual(command[command.index('-m') + 1], '49152M')
        self.assertIn(f'gentoo_scratch_uuid={record["images"]["scratch"]["uuid"]}', command[command.index('-append') + 1])
        args = command[command.index('-append') + 1]
        self.assertIn(f'gentoo_root_serial={record["images"]["root"]["serial"]}', args)
        self.assertIn(f'gentoo_extra_serial={record["images"]["extra"]["serial"]}', args)
        self.assertIn(f'gentoo_boot_serial={record["bootstrap_disks"][0]["serial"]}', args)
        self.assertIn('init=/bin/bash', command[command.index('-append') + 1])
        self.assertNotIn('-bios', command)
        self.assertNotIn('-no-shutdown', command)
        self.assertEqual(record['original_argv'], self.argv)
        restored['images']['root']['path'] = '/dev/sda'
        vm.atomic_json(self.run_dir / vm.RECORD, restored)
        with self.assertRaises(ValueError):
            vm.read_record(self.run_dir)

    def test_installed_base_boot_requires_encrypted_root_arguments(self):
        append = ('rd.luks.uuid=luks-11111111-1111-4111-8111-111111111111 '
                  'rd.lvm.lv=gentoo/root rd.lvm.lv=gentoo/home rd.lvm.lv=gentoo/swap '
                  'root=/dev/mapper/gentoo-root resume=/dev/mapper/gentoo-swap ro '
                  'console=tty0 console=ttyS0,115200n8')
        self.assertIn('root=/dev/mapper/gentoo-root', vm.verify_installed_cmdline(append))
        for invalid in (append.replace('rd.luks.uuid=', 'luks.uuid='),
                        append.replace('rd.lvm.lv=gentoo/home ', ''),
                        append + ' init=/bin/bash',
                        append.replace('ro ', 'rw ')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                vm.verify_installed_cmdline(invalid)

    def test_installed_base_qemu_arguments_use_the_saved_kernel_initramfs_and_cmdline(self):
        record = self.record()
        kernel = self.directory / 'installed-kernel'
        initrd = self.directory / 'installed-initramfs'
        kernel.write_bytes(b'kernel')
        initrd.write_bytes(b'initrd')
        append = ('rd.luks.uuid=luks-11111111-1111-4111-8111-111111111111 '
                  'rd.lvm.lv=gentoo/root rd.lvm.lv=gentoo/home rd.lvm.lv=gentoo/swap '
                  'root=/dev/mapper/gentoo-root resume=/dev/mapper/gentoo-swap ro console=ttyS0')
        command = vm.command(record, self.run_dir, kernel, initrd, append)
        self.assertEqual(command[command.index('-kernel') + 1], str(kernel))
        self.assertEqual(command[command.index('-initrd') + 1], str(initrd))
        self.assertEqual(command[command.index('-append') + 1], append)

    def test_record_rejects_separate_resource_and_disk_identity_edits(self):
        for edit in ('ram', 'serial'):
            record = self.record()
            if edit == 'ram':
                record['ram_mib'] = 4096
            else:
                record['images']['root']['serial'] = record['images']['extra']['serial']
            vm.atomic_json(self.run_dir / vm.RECORD, record)
            with self.assertRaises(ValueError):
                vm.read_record(self.run_dir)

    def test_default_prepare_plan_does_not_create_run_directory(self):
        command_file = self.directory / 'qemu.json'
        command_file.write_text(json.dumps(self.argv))
        proposed = self.directory / 'proposed'
        result = subprocess.run([sys.executable, str(ROOT / 'system/gentoo/vm.py'), 'prepare',
                                 '--run-dir', str(proposed), '--config', str(self.config), '--qemu-command', str(command_file)],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(proposed.exists())

    def test_prepare_resume_retains_ids_and_does_not_repeat_creation(self):
        with patch.object(vm, 'check_idle'), patch.object(vm, 'inspect_image'), patch.object(vm, 'create_image') as create:
            # Bootstrap metadata is bound once, before publishing the record.
            with patch.object(vm, 'run', return_value='{"virtual-size":1024}'):
                first = vm.prepare(self.settings, self.run_dir, self.argv, self.directory)
            self.assertEqual(create.call_count, 4)
            create.reset_mock()
            second = vm.prepare(self.settings, self.run_dir, self.argv, self.directory)
            self.assertEqual(first, second)
            self.assertEqual(create.call_count, 4)  # create_image validates existing files itself
            self.settings['images']['root_gib'] = 501
            with self.assertRaises(ValueError):
                vm.prepare(self.settings, self.run_dir, self.argv, self.directory)

    def test_image_in_use_loop_and_inode_alias_rejection(self):
        items = [{'path': str(self.bootstrap)}]
        with patch.object(vm, 'run', return_value='/dev/loop1: active'):
            with self.assertRaises(ValueError):
                vm.check_idle(items)
        with patch.object(vm, 'run', return_value=''), patch.object(vm.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'', b'')):
            with self.assertRaises(ValueError):
                vm.check_idle(items)
        hardlink = self.directory / 'hardlink.img'
        os.link(self.bootstrap, hardlink)
        with patch.object(vm, 'run', return_value=''), patch.object(vm.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'')):
            with self.assertRaises(ValueError):
                vm.check_idle(items + [{'path': str(hardlink)}])

    def test_unknown_existing_filesystems_are_adopted_only_after_validation(self):
        images = self.run_dir / 'images'
        images.mkdir()
        for role in ('workspace', 'scratch'):
            (images / (role + '.img')).write_bytes(b'fixture, not a filesystem')
        identities = {'workspace': '11111111-1111-4111-8111-111111111111',
                      'scratch': '22222222-2222-4222-8222-222222222222'}

        def output(*args):
            if args[0] == 'qemu-img':
                return '{"virtual-size":1024}'
            role = Path(args[-1]).stem
            field = args[args.index('-s') + 1]
            return {'TYPE': 'ext4', 'LABEL': 'GENTOO_BUILD' if role == 'workspace' else 'GENTOO_SCRATCH',
                    'UUID': identities[role]}[field]

        with patch.object(vm, 'check_idle'), patch.object(vm, 'inspect_image'), patch.object(vm, 'create_image'), patch.object(vm, 'run', side_effect=output):
            record = vm.prepare(self.settings, self.run_dir, self.argv, self.directory)
        for role, identity in identities.items():
            self.assertEqual(record['images'][role]['uuid'], identity)
            self.assertEqual((images / (role + '.img')).read_bytes(), b'fixture, not a filesystem')

    def test_qcow_external_data_file_is_rejected(self):
        item = {'path': str(self.bootstrap), 'format': 'qcow2'}
        info = {'format': 'qcow2', 'virtual-size': 1024,
                'format-specific': {'data': {'data-file': '/dev/sda'}}}
        with patch.object(vm, 'run', return_value=json.dumps(info)):
            with self.assertRaises(ValueError):
                vm.inspect_image(item)

    def test_run_lock_is_process_bound(self):
        with vm.run_lock(self.run_dir):
            with self.assertRaises(BlockingIOError):
                with vm.run_lock(self.run_dir):
                    self.fail('concurrent lock accepted')
        with vm.run_lock(self.run_dir):
            pass

    def test_failed_image_creation_never_publishes_target(self):
        item = self.record()['images']['scratch']
        with patch.object(vm, 'run', side_effect=RuntimeError('interrupted mkfs')):
            with self.assertRaises(RuntimeError):
                vm.create_image(item)
        self.assertFalse(Path(item['path']).exists())
        self.assertEqual(list(Path(item['path']).parent.iterdir()), [])

    def test_real_disposable_ext4_qcow2_reuse_and_size_refusal(self):
        if not shutil.which('qemu-img') or not Path('/sbin/mkfs.ext4').exists():
            self.skipTest('qemu-img/e2fsprogs unavailable')
        record = self.record()
        for role, capacity in (('workspace', 16 * 1024**2), ('root', 8 * 1024**2)):
            item = dict(record['images'][role], bytes=capacity)
            vm.create_image(item)
            path = Path(item['path'])
            before = vm.digest(path)
            vm.create_image(item)
            self.assertEqual(vm.digest(path), before)
            wrong = dict(item, bytes=capacity * 2)
            with self.assertRaises(ValueError):
                vm.create_image(wrong)
            self.assertEqual(vm.digest(path), before)

    def test_qemu_tcg_accepts_recorded_storage_and_console_arguments(self):
        if not shutil.which('qemu-system-x86_64') or not Path('/sbin/mkfs.ext4').exists():
            self.skipTest('QEMU/e2fsprogs unavailable')
        argv = ['qemu-system-x86_64', '-accel', 'tcg', '-m', '64M', '-smp', '1', '-nic', 'none', '-nographic',
                '-drive', f'file={self.bootstrap},format=raw']
        record = vm.new_record(self.settings, self.run_dir, argv, self.directory)
        for item in record['images'].values():
            item['bytes'] = 16 * 1024**2
            vm.create_image(item)
        command = vm.command(record, self.run_dir) + ['-S']
        # -S pauses the CPU before firmware/guest execution. This checks actual
        # QEMU option/device acceptance, not a source-built guest cold boot.
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(5)
        try:
            deadline = time.monotonic() + 5
            while True:
                if process.poll() is not None:
                    self.fail(process.stderr.read().decode())
                try:
                    connection.connect(str(self.run_dir / 'qmp.sock'))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() >= deadline:
                        self.fail('QEMU did not publish its QMP socket')
                    time.sleep(0.02)
                except PermissionError:
                    self.skipTest('sandbox denies Unix socket connections; run this smoke outside that restriction')
            stream = connection.makefile('r')
            try:
                self.assertIn('QMP', json.loads(stream.readline()))
                for identifier, execute in enumerate(('qmp_capabilities', 'query-block')):
                    connection.sendall((json.dumps({'execute': execute, 'id': identifier}) + '\n').encode())
                    while True:
                        message = json.loads(stream.readline())
                        if message.get('id') == identifier:
                            break
                    self.assertNotIn('error', message)
                inserted = {entry['inserted']['file'] for entry in message['return'] if 'inserted' in entry}
                self.assertEqual(inserted, {item['path'] for item in vm.all_images(record)})
                connection.sendall(b'{"execute":"quit"}\n')
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                stream.close()
        finally:
            connection.close()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stderr.close()

    def handoff_fixture(self):
        build = self.directory / 'build'
        source_bundle = self.directory / 'bundle'
        bundle.prepare(self.config, 'vm', source_bundle)
        inputs = vm.digest(source_bundle / 'SHA256SUMS')
        snapshot = build / 'inputs' / inputs
        snapshot.parent.mkdir(parents=True)
        source_bundle.rename(snapshot)
        handoff = build / 'handoff' / inputs
        handoff.mkdir(parents=True)
        shutil.copyfile(snapshot / 'post-reboot.sh', handoff / 'resume.sh')
        shutil.copyfile(snapshot / 'shutdown.sh', handoff / 'shutdown.sh')
        artifacts = build / 'artifacts' / ('kernel-' + vm.RELEASE)
        artifacts.mkdir(parents=True)
        fake_kernel(artifacts / 'bzImage')
        for name, content in {'config': 'CONFIG_64BIT=y\n', 'System.map': 'symbol\n', 'kernelrelease': vm.RELEASE + '\n',
                              'build-boot-id': '11111111-1111-4111-8111-111111111111\n', 'build-id': 'a' * 40 + '\n',
                              'kernel.SHA256SUMS': 'fixture\n'}.items():
            (artifacts / name).write_text(content)
        modules = artifacts / 'modules/lib/modules' / vm.RELEASE / 'kernel'
        modules.mkdir(parents=True)
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        header[18] = 62
        (modules / 'fixture.ko').write_bytes(header)  # header fixture, not a loadable module
        manifest(artifacts)
        state = build / 'state'
        state.mkdir()
        for stage in ('30-kernel', '31-kernel-modules'):
            (state / (stage + '.done')).write_text('0' * 64 + '\n')
        metadata = {'version': 1, 'release': vm.RELEASE, 'inputs': inputs, 'workspace_uuid': 'workspace-uuid',
                    'boot_id': (artifacts / 'build-boot-id').read_text().strip(),
                    'kernel_manifest_sha256': vm.digest(artifacts / 'SHA256SUMS'),
                    'kernel_receipt_sha256': vm.digest(state / '30-kernel.done'),
                    'modules_receipt_sha256': vm.digest(state / '31-kernel-modules.done')}
        (handoff / 'handoff.json').write_text(json.dumps(metadata))
        (build / 'current-handoff').write_text(inputs + '\n')
        return build, artifacts, handoff, snapshot

    def test_handoff_verifies_content_and_rejects_kernel_or_module_tampering(self):
        build, artifacts, handoff, snapshot = self.handoff_fixture()
        vm.verify_handoff(build, 'workspace-uuid')
        module = next(artifacts.rglob('*.ko'))
        module.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            vm.verify_handoff(build, 'workspace-uuid')
        # Manifest path traversal must fail before opening any outside path.
        (snapshot / 'SHA256SUMS').write_text('0' * 64 + '  ../outside\n')
        with self.assertRaises(ValueError):
            vm.verify_manifest(snapshot)

    def test_handoff_rejects_changed_resume_inputs_and_receipts(self):
        build, artifacts, handoff, snapshot = self.handoff_fixture()
        (handoff / 'resume.sh').write_text('changed script')
        with self.assertRaises(ValueError):
            vm.verify_handoff(build, 'workspace-uuid')
        shutil.copyfile(snapshot / 'post-reboot.sh', handoff / 'resume.sh')
        (build / 'state/31-kernel-modules.done').unlink()
        with self.assertRaises(OSError):
            vm.verify_handoff(build, 'workspace-uuid')

    def test_mount_has_read_only_noload_options_and_unmounts_after_failure(self):
        item = self.record()['images']['workspace']
        with patch.object(vm.os, 'geteuid', return_value=0), patch.object(vm, 'inspect_image'), patch.object(vm, 'run') as run:
            with self.assertRaises(RuntimeError):
                with vm.workspace_mount(item):
                    raise RuntimeError('extraction failed')
            self.assertEqual(run.call_args_list[0].args[:5], ('mount', '-t', 'ext4', '-o', 'loop,ro,noload,nosuid,nodev,noexec'))
            self.assertEqual(run.call_args_list[-1].args[0], 'umount')

    def test_kernel_protocol_rejects_32_bit_and_bad_headers(self):
        kernel = self.directory / 'bzImage'
        fake_kernel(kernel)
        vm.check_kernel(kernel)
        data = bytearray(kernel.read_bytes())
        data[566] = 0
        kernel.write_bytes(data)
        with self.assertRaises(ValueError):
            vm.check_kernel(kernel)
        kernel.write_bytes(b'not a kernel')
        with self.assertRaises(ValueError):
            vm.check_kernel(kernel)

    def test_guest_build_id_parser_ignores_other_notes(self):
        note = self.directory / 'kernel.notes'
        note.write_bytes(bytes.fromhex('040000001400000003000000474e5500' + 'ab' * 20))
        helper = ROOT / 'system/gentoo/lib/kernel.sh'
        script = f'''set -Eeuo pipefail
WORK_ROOT="$PWD"
BUILDS="$PWD"
SCRIPT_DIR="$PWD"
. "{helper}"
kernel_running_build_id "{note}"
'''
        result = subprocess.run(['bash', '-c', script], cwd=self.directory, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ab' * 20)
        note.write_bytes(b'no GNU note')
        result = subprocess.run(['bash', '-c', script], cwd=self.directory, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_guest_handoff_resumes_after_publication_and_later_bundle_change(self):
        build = self.directory / 'guest-build'
        artifacts = build / 'artifacts/kernel-6.12.111'
        artifacts.mkdir(parents=True)
        boot_id = '11111111-1111-4111-8111-111111111111'
        (artifacts / 'build-boot-id').write_text(boot_id + '\n')
        (artifacts / 'build-id').write_text('a' * 40 + '\n')
        (artifacts / 'SHA256SUMS').write_text('artifact manifest fixture\n')
        (build / 'state').mkdir()
        for stage in ('30-kernel', '31-kernel-modules'):
            (build / 'state' / (stage + '.done')).write_text('0' * 64 + '\n')
        for revision in range(2):
            incoming = self.directory / ('incoming' + str(revision))
            bundle.prepare(self.config, 'vm', incoming)
            if revision:
                with (incoming / 'gentoo.sh').open('a') as stream:
                    stream.write('# later-stage bundle fixture\n')
                manifest(incoming)
            identity = vm.digest(incoming / 'SHA256SUMS')
            snapshot = build / 'inputs' / identity
            snapshot.parent.mkdir(exist_ok=True)
            incoming.rename(snapshot)
            script = f'''set -Eeuo pipefail
WORK_ROOT="{build}"
SCRIPT_DIR="{snapshot}"
KERNEL_ARTIFACTS="{artifacts}"
KERNEL_RELEASE=6.12.111
GENTOO_DISCOVERED_WORKSPACE_UUID=22222222-2222-4222-8222-222222222222
cd "$SCRIPT_DIR"
. lib/common.sh
. lib/state.sh
uname() {{ case "$1" in -r) echo 6.12.111 ;; -m) echo x86_64 ;; esac; }}
kernel_running_build_id() {{ printf '%s\\n' {'a' * 40}; }}
. stages/32-handoff.sh
'''
            # Repeat with its completion receipt removed to model an exit after
            # durable handoff publication, before atomic completion publication.
            for attempt in range(2):
                result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('Modern-kernel cold boot verified', result.stdout)
                if not attempt:
                    (build / 'state' / ('32-handoff-' + identity + '.done')).unlink()
            # A caller-supplied flag cannot bypass the actual old-kernel gate.
            old_kernel = script.replace('echo 6.12.111', 'echo 2.6.32').replace(
                '. stages/32-handoff.sh',
                'GENTOO_MODERN_KERNEL_READY=1\n. stages/32-handoff.sh\n[ "$GENTOO_MODERN_KERNEL_READY" = 0 ]')
            result = subprocess.run(['bash', '-c', old_kernel], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('SHUTDOWN MILESTONE', result.stdout)
            wrong_build = script.replace("printf '%s\\n' " + 'a' * 40, "printf '%s\\n' " + 'b' * 40)
            self.assertNotEqual(wrong_build, script)
            result = subprocess.run(['bash', '-c', wrong_build], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('running kernel build ID differs', result.stderr)
            metadata = json.loads((build / 'handoff' / identity / 'handoff.json').read_text())
            self.assertEqual(metadata['boot_id'], boot_id)
            self.assertEqual((build / 'current-handoff').read_text().strip(), identity)

    def test_source_verifier_refuses_corrupt_archives_before_gpg(self):
        snapshot = ROOT / 'system/gentoo'
        build = self.directory / 'build'
        (build / 'distfiles').mkdir(parents=True)
        (build / 'distfiles/linux-6.12.111.tar.xz').write_bytes(b'corrupted')
        with patch('system.gentoo.kernel_source.subprocess.Popen') as unpack:
            with self.assertRaises(ValueError):
                verify_kernel_source(build, snapshot)
            unpack.assert_not_called()


if __name__ == '__main__':
    unittest.main()
