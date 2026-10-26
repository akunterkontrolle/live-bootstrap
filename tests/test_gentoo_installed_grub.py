"""Generate an encrypted-root menu and verify the boot route before extra erasure."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = importlib.util.spec_from_file_location('installed_grub', GENTOO / 'installed-grub.py')
grub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grub)


class InstalledGrubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for path in ('etc/kernel', 'boot/grub'):
            (self.root / path).mkdir(parents=True)
        self.uuid = '12345678-1234-1234-1234-123456789abc'
        (self.root / 'etc/ms1504-root-identity').write_text('profile=hardware\nvolume_group=ms1504\nroot_lv=root\nhome_lv=home\nswap_lv=swap\n')
        (self.root / 'etc/fstab').write_text('UUID=' + self.uuid + ' / ext4 defaults 0 1\n')
        self.cmdline = ('rd.luks.uuid=luks-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa '
                        'rd.lvm.lv=ms1504/root rd.lvm.lv=ms1504/home rd.lvm.lv=ms1504/swap '
                        'root=/dev/mapper/ms1504-root resume=/dev/mapper/ms1504-swap '
                        'ms1504.generation=base-6.12.111 ro loglevel=4 console=tty0')
        (self.root / 'etc/kernel/cmdline').write_text(self.cmdline + '\n')
        (self.root / 'boot/vmlinuz-6.12.111').write_bytes(b'verified kernel fixture')
        (self.root / 'boot/initramfs-6.12.111.img').write_bytes(b'verified initramfs fixture')

    def test_menu_uses_installed_fs_paths_matching_initramfs_and_exact_activation(self):
        text, record = grub.plan(self.root, '6.12.111', self.uuid)
        self.assertIn('search --no-floppy --fs-uuid --set=root ' + self.uuid, text)
        self.assertIn('linux /boot/vmlinuz-6.12.111 ' + self.cmdline, text)
        self.assertIn('initrd /boot/initramfs-6.12.111.img', text)
        self.assertIn(grub.BOOT_MARKER, text)
        self.assertNotIn('/gentoo-bootstrap/', text)
        self.assertEqual(record['root_uuid'], self.uuid)

    def test_publish_checks_real_grub_syntax_backs_up_and_is_idempotent(self):
        if not shutil.which('grub-script-check'):
            self.skipTest('requires GRUB parser')
        menu = self.root / 'boot/grub/grub.cfg'
        previous = "menuentry 'old wrong bootstrap' { linux /wrong init=/bin/bash }\n"
        menu.write_text(previous)
        record = grub.publish(self.root, '6.12.111', self.uuid)
        self.assertEqual(grub.publish(self.root, '6.12.111', self.uuid), record)
        state = self.root / 'var/lib/ms1504-gentoo/boot'
        self.assertEqual(len(list(state.glob('grub.cfg.before-*'))), 1)
        self.assertEqual(next(state.glob('grub.cfg.before-*')).read_text(), previous)
        self.assertEqual((state / 'encrypted-root-grub.json').stat().st_mode & 0o777, 0o600)
        subprocess.run(['grub-script-check', str(menu)], check=True)

    def test_installer_can_use_the_target_root_grub_checker(self):
        run = subprocess.run
        commands = []
        def checker(command, **kwargs):
            commands.append(command)
            if command[0] == 'chroot':
                self.assertEqual(command[1], str(self.root))
                self.assertEqual(command[-2], 'grub-script-check')
                self.assertTrue(command[-1].startswith('/boot/grub/.check-'))
                return run(['grub-script-check', str(self.root / command[-1].lstrip('/'))], **kwargs)
            return run(command, **kwargs)
        if not shutil.which('grub-script-check'):
            self.skipTest('requires GRUB parser')
        with mock.patch.object(grub.subprocess, 'run', side_effect=checker):
            grub.publish(self.root, '6.12.111', self.uuid, check_in_chroot=True)
        self.assertEqual(len(commands), 1)

    def test_unsigned_generator_preserves_configured_signed_boot(self):
        menu = self.root / 'boot/grub/grub.cfg'
        menu.write_text('reviewed signed menu')
        (self.root / 'etc/ms1504-signed-generation.toml').write_text('configured signing policy')
        with self.assertRaisesRegex(ValueError, 'signed-generation configuration exists'):
            grub.publish(self.root, '6.12.111')
        self.assertEqual(menu.read_text(), 'reviewed signed menu')

    def test_base_generator_refuses_manager_owned_unsigned_menu(self):
        menu = self.root / 'boot/grub/grub.cfg'
        menu.write_text('managed unsigned menu')
        (self.root / 'boot/grub/generation-policy.json').write_text('{"policy":"unsigned"}')
        with self.assertRaisesRegex(ValueError, 'generation manager owns'):
            grub.publish(self.root, '6.12.111')
        self.assertEqual(menu.read_text(), 'managed unsigned menu')

    def test_wrong_root_uuid_bootstrap_init_missing_initramfs_and_symlink_escape_refuse(self):
        with self.assertRaisesRegex(ValueError, 'UUID differs'):
            grub.plan(self.root, '6.12.111', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        (self.root / 'etc/kernel/cmdline').write_text(self.cmdline + ' init=/bin/bash')
        with self.assertRaisesRegex(ValueError, 'bootstrap'):
            grub.plan(self.root, '6.12.111')
        (self.root / 'etc/kernel/cmdline').write_text(self.cmdline)
        initrd = self.root / 'boot/initramfs-6.12.111.img'
        initrd.unlink()
        with self.assertRaisesRegex(ValueError, 'artifact'):
            grub.plan(self.root, '6.12.111')
        initrd.symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'artifact'):
            grub.plan(self.root, '6.12.111')

    def test_boot_gate_rejects_old_workspace_route_wrong_kernel_or_root(self):
        _, record = grub.plan(self.root, '6.12.111')
        running = ['BOOT_IMAGE=/boot/vmlinuz-6.12.111', *record['cmdline']]
        grub.verify_boot_arguments(record, running, '6.12.111', self.uuid)
        for args in (([t for t in running if t != grub.BOOT_MARKER], '6.12.111', self.uuid),
                     (running, '4.14.341', self.uuid),
                     (running, '6.12.111', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')):
            with self.assertRaises(ValueError):
                grub.verify_boot_arguments(record, *args)


class ExtraBootGateTests(unittest.TestCase):
    def test_hardware_initialization_requires_encrypted_boot_before_erase_confirmation(self):
        source = (GENTOO / 'setup-extra.sh').read_text()
        prefix = source.split('    initialize)\n', 1)[1].split('        extra_check_mounts "$SOURCE_MOUNT"', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = r'''set -Eeuo pipefail
STATE=$1; KEY=$STATE/key; MOUNT=/extra; MAPPER=ms1504-extra; PROFILE=hardware; SOURCE_MOUNT=/mnt/source
fail() { echo "$*" >&2; exit 19; }
validate_home_migration() { :; }; check_workspace_source() { :; }; check_retained_source() { :; }
workspace_copy_differences() { :; }; mountpoint() { return 1; }; cryptsetup() { return 1; }
readlink() { echo /fixture/setup-extra.sh; }
python3() { [[ "$*" == *'installed-grub.py --check-running'* ]]; [[ ${BOOT_VERIFIED:-0} = 1 ]]; }
shift
set -- --source /mnt/source
''' + prefix + '\necho REACHED_CONFIRMATION_GATE\n'
            for verified in ('0', '1'):
                result = subprocess.run(['bash', '-c', fixture, 'gate', str(root)],
                                        env=dict(__import__('os').environ, BOOT_VERIFIED=verified),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, verified == '1', result.stderr)
                self.assertFalse((root / 'initialize.started').exists())
                self.assertFalse((root / 'key').exists())
