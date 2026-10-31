"""Run generation transactions against real files with a simulated LVM boundary."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = spec_from_file_location('generation_transactions', GENTOO / 'signed-generation.py')
m = module_from_spec(spec)
spec.loader.exec_module(m)


class GenerationTransactions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {'policy': 'unsigned', 'vg': 'ms1504', 'root': '/dev/ms1504/root',
                    'system': {'volume_group': 'ms1504', 'root_lv': 'root', 'home_lv': 'home', 'swap_lv': 'swap'},
                    'boot': {'directory': '/boot', 'policy': 'unsigned'},
                    'snapshots': {'size_gib': 32, 'maximum_data_percent': 80, 'retain_generations': 2},
                    'state_dir': self.root / 'state', 'boot_dir': self.root / 'boot'}
        self.cfg['state_dir'].mkdir()
        (self.root / 'etc/kernel').mkdir(parents=True)
        (self.root / 'dev/ms1504').mkdir(parents=True)
        (self.root / 'boot/grub').mkdir(parents=True)
        (self.root / 'proc').mkdir()
        self.marker = self.root / 'etc/ms1504-generation'
        self.marker.write_text('base-1\n')
        self.cmdline = self.root / 'etc/kernel/cmdline'
        self.cmdline.write_text('rd.luks.uuid=luks-abc rd.lvm.lv=ms1504/root rd.lvm.lv=ms1504/home rd.lvm.lv=ms1504/swap root=/dev/ms1504/root resume=/dev/mapper/ms1504-swap ms1504.generation=base-1 ro\n')
        (self.root / 'proc/cmdline').write_text(self.cmdline.read_text())
        self.kernel = self.root / 'boot/vmlinuz-test'
        self.initrd = self.root / 'boot/initramfs-test.img'
        self.kernel.write_bytes(b'kernel')
        self.initrd.write_bytes(b'initramfs')
        (self.root / 'boot/grub/grub.cfg').write_text('original usable menu\n')
        self.lvs = {}
        self.percent = 0
        self.calls = []
        self.args = SimpleNamespace(secret_key_file=None, gnupg_home=None)
        real_path = Path
        def paths(value='.'):
            value = str(value)
            if any(value == prefix or value.startswith(prefix + '/') for prefix in ('/etc', '/dev/ms1504', '/boot', '/proc', '/home')):
                return self.root / value.lstrip('/')
            return real_path(value)
        patches = [patch.object(m, 'Path', side_effect=paths),
                   patch.object(m, 'root_is_live'),
                   patch.object(m, 'read_root_identity', return_value={'reserve_gib': '100'}),
                   patch.object(m, 'run', side_effect=self.fake_run),
                   patch.object(m.os, 'uname', return_value=SimpleNamespace(release='test')),
                   patch.object(m.shutil, 'which', return_value='/usr/bin/grub-script-check')]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def fake_run(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == 'lvcreate':
            name = args[args.index('--name') + 1]
            self.lvs[name] = 'uuid-' + name
            (self.root / 'dev/ms1504' / name).touch()
            return b''
        if args[0] == 'vgs':
            return f'{100 * 1024**3} {4 * 1024**2}'.encode()
        if args[0] == 'lvs':
            if 'origin' in args:
                return b'root'
            if args[-1] == 'ms1504':
                return ''.join(f'{name} root\n' for name in self.lvs).encode()
            name = args[-1].split('/')[-1]
            if name not in self.lvs:
                raise RuntimeError('LV missing')
            if 'lv_uuid' in args:
                return self.lvs[name].encode()
            return f'swi-a-s--- root {self.percent} {32 * 1024**3}'.encode()
        if args[0].endswith('grub-script-check'):
            return b''
        raise AssertionError(args)

    def initialize(self):
        m.command_initialize(self.cfg, self.args)

    def begin(self):
        self.initialize()
        m.command_begin(self.cfg, self.args)

    def test_initialize_begin_seal_and_retain_bootable_base(self):
        self.begin()
        m.check_pending(self.cfg)
        self.assertEqual((self.cfg['state_dir'] / 'pre-generation-grub.cfg').read_text(), 'original usable menu\n')
        self.assertEqual(len(self.lvs), 1)
        pending_menu = self.cfg['boot_dir'] / 'grub/active/grub.cfg'
        self.assertIn('set default=1', pending_menu.read_text())
        self.assertIn('root=/dev/ms1504/root_rollback_base-1', pending_menu.read_text())
        self.assertNotIn('verify_detached', pending_menu.read_text())
        self.assertIn('set root=(lvm/ms1504-root)', pending_menu.read_text())
        self.assertFalse(list(self.cfg['boot_dir'].rglob('*.sig')))
        m.command_begin(self.cfg, self.args)
        self.assertEqual(sum(args[0] == 'lvcreate' for args in self.calls), 1)
        m.command_seal(self.cfg, SimpleNamespace(generation='desktop-1', kernel=str(self.kernel), initramfs=str(self.initrd)))
        self.assertEqual(self.marker.read_text().strip(), 'desktop-1')
        self.assertFalse((self.cfg['state_dir'] / 'pending').exists())
        self.assertTrue((self.cfg['state_dir'] / 'completed-base-1/transaction').exists())
        self.assertIn('set default=0', pending_menu.read_text())
        self.assertIn('root_rollback_base-1', pending_menu.read_text())
        self.assertEqual((self.cfg['boot_dir'] / 'generations/base-1/vmlinuz').read_bytes(), b'kernel')

    def test_pending_gate_refuses_missing_altered_and_replaced_recovery(self):
        self.begin()
        m.check_pending(self.cfg)
        for percent in (80, 100):
            self.percent = percent
            with self.assertRaises(RuntimeError):
                m.check_pending(self.cfg)
        self.percent = 0
        self.lvs['root_rollback_base-1'] = 'replaced-uuid'
        with self.assertRaisesRegex(RuntimeError, 'UUID'):
            m.check_pending(self.cfg)
        self.lvs.clear()
        with self.assertRaises(RuntimeError):
            m.check_pending(self.cfg)

    def test_pending_gate_refuses_menu_and_kernel_tampering(self):
        self.begin()
        menu = self.cfg['boot_dir'] / 'grub/active/grub.cfg'
        original = menu.read_bytes()
        menu.write_bytes(b'other menu')
        with self.assertRaisesRegex(RuntimeError, 'menu differs'):
            m.check_pending(self.cfg)
        menu.write_bytes(original)
        (self.cfg['boot_dir'] / 'generations/base-1/vmlinuz').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'artifact differs'):
            m.check_pending(self.cfg)

    def test_interrupted_initialize_and_begin_resume_without_recreating_snapshot(self):
        publish = m.publish
        with patch.object(m, 'publish', side_effect=RuntimeError('power loss')):
            with self.assertRaisesRegex(RuntimeError, 'power loss'):
                self.initialize()
        self.assertEqual((self.cfg['boot_dir'] / 'grub/grub.cfg').read_text(), 'original usable menu\n')
        self.initialize()
        with self.assertRaisesRegex(RuntimeError, 'already initialized'):
            self.initialize()
        with patch.object(m, 'publish', side_effect=RuntimeError('power loss')):
            with self.assertRaisesRegex(RuntimeError, 'power loss'):
                m.command_begin(self.cfg, self.args)
        self.assertEqual(len(self.lvs), 1)
        with self.assertRaises((OSError, RuntimeError)):
            m.check_pending(self.cfg)
        m.command_begin(self.cfg, self.args)
        m.check_pending(self.cfg)
        self.assertEqual(sum(args[0] == 'lvcreate' for args in self.calls), 1)

    def test_interrupted_seal_rejects_changed_inputs_and_resumes_original(self):
        self.begin()
        args = SimpleNamespace(generation='desktop-1', kernel=str(self.kernel), initramfs=str(self.initrd))
        with patch.object(m, 'publish', side_effect=RuntimeError('power loss')):
            with self.assertRaisesRegex(RuntimeError, 'power loss'):
                m.command_seal(self.cfg, args)
        with self.assertRaises(RuntimeError):
            m.check_pending(self.cfg)
        self.kernel.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
            m.command_seal(self.cfg, args)
        self.kernel.write_bytes(b'kernel')
        m.command_seal(self.cfg, args)
        self.assertTrue((self.cfg['state_dir'] / 'completed-base-1').is_dir())

    def test_policy_cannot_downgrade_or_rebind_existing_boot(self):
        self.initialize()
        for changed in ({'policy': 'signed'}, {'state_dir': self.root / 'different'},
                        {'system': self.cfg['system'] | {'root_lv': 'other'}}):
            with self.assertRaisesRegex(RuntimeError, 'policy/layout'):
                m.check_policy(self.cfg | changed)
        (self.cfg['boot_dir'] / 'grub/grub.cfg.sig').write_bytes(b'signed')
        with self.assertRaisesRegex(RuntimeError, 'signed'):
            m.command_begin(self.cfg, self.args)

    def test_monitor_stops_command_when_checkpoint_becomes_unsafe(self):
        checks = 0
        def check(cfg):
            nonlocal checks
            checks += 1
            if checks > 1:
                raise RuntimeError('COW threshold')
        child = SimpleNamespace(pid=123, wait=None, poll=lambda: None)
        from unittest.mock import Mock
        child.wait = Mock(side_effect=[subprocess.TimeoutExpired('build', 5), 0])
        with patch.object(m, 'check_pending', side_effect=check), patch.object(m.subprocess, 'Popen', return_value=child), patch.object(m.os, 'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError, 'COW threshold'):
                m.command_run(self.cfg, SimpleNamespace(command=['--', 'build']))
        self.assertEqual(kill.call_args.args[0], 123)
        self.assertEqual(checks, 2)

    def test_monitor_refuses_to_start_without_checkpoint(self):
        with patch.object(m.subprocess, 'Popen') as spawn:
            with self.assertRaises((OSError, RuntimeError)):
                m.command_run(self.cfg, SimpleNamespace(command=['--', 'build']))
        spawn.assert_not_called()

    def test_status_fails_at_threshold_and_includes_pending_snapshot(self):
        self.begin()
        m.command_status(self.cfg)
        self.percent = 80
        with self.assertRaises(RuntimeError):
            m.command_status(self.cfg)

    def test_verify_running_checks_generation_and_actual_root_device(self):
        self.initialize()
        with patch.object(m, 'current_root_source', return_value='/dev/ms1504/root'):
            m.command_verify_running(self.cfg)
            (self.root / 'proc/cmdline').write_text(self.cmdline.read_text().replace('base-1', 'wrong'))
            with self.assertRaisesRegex(RuntimeError, 'generation differs'):
                m.command_verify_running(self.cfg)

    def test_prune_requires_confirmation_and_removes_menu_before_lv(self):
        self.begin()
        m.command_seal(self.cfg, SimpleNamespace(generation='desktop-1', kernel=str(self.kernel), initramfs=str(self.initrd)))
        args = SimpleNamespace(generation='base-1')
        ordinary_run = self.fake_run
        def prune_run(command, **kwargs):
            if command[0] == 'findmnt':
                return b'/dev/ms1504/root\n'
            if command[0] == 'lvremove':
                self.assertNotIn('root_rollback_base-1', (self.cfg['boot_dir'] / 'grub/active/grub.cfg').read_text())
                self.lvs.pop('root_rollback_base-1')
                (self.root / 'dev/ms1504/root_rollback_base-1').unlink()
                return b''
            return ordinary_run(command)
        with patch.object(m, 'run', side_effect=prune_run), patch.object(m, 'confirm_tty', side_effect=RuntimeError('confirmation mismatch')):
            with self.assertRaisesRegex(RuntimeError, 'confirmation mismatch'):
                m.command_prune(self.cfg, args)
        self.assertEqual(len(self.lvs), 1)
        with patch.object(m, 'run', side_effect=prune_run), patch.object(m, 'confirm_tty'):
            m.command_prune(self.cfg, args)
        self.assertEqual(len(self.lvs), 0)
        self.assertFalse((self.cfg['state_dir'] / 'completed-base-1').exists())

    def offline_fixture(self):
        self.begin()
        # The real recovery mount is root-owned. This fixture runs as the host
        # user and models the privileged mount/LVM boundary separately.
        protection = patch.object(m, 'protected_path')
        protection.start()
        self.addCleanup(protection.stop)
        receipt_dir = self.root / 'external'
        receipt_dir.mkdir()
        (self.root / 'proc/swaps').write_text('Filename Type Size Used Priority\n')
        identity = dict(self.cfg['system'], profile='hardware', reserve_gib='100')
        (self.root / 'etc/ms1504-root-identity').write_text(''.join(f'{k}={v}\n' for k, v in identity.items()))
        # Production config paths are relative to the installed filesystem root,
        # not the test's host temporary directory.
        cfg = self.cfg | {'state_dir': Path('/state'), 'boot_dir': Path('/boot')}
        (self.root / 'boot/grub/generation-policy.json').write_bytes(m.policy_binding(cfg))
        args = SimpleNamespace(generation='base-1', recovery_root=str(self.root), recovery_receipts=str(receipt_dir),
                               secret_key_file=None, gnupg_home=None)
        mounted = True
        ordinary_run = self.fake_run
        def recovery_run(command, **kwargs):
            nonlocal mounted
            if command[0] == 'findmnt':
                if command[-1] == 'SOURCE,TARGET':
                    return b'/dev/rescue /\n/dev/external /recovery\n'
                if command[-1] == 'FSTYPE':
                    return b'ext4'
                return b'/dev/external' if command[command.index('-M') + 1] == str(receipt_dir) else b'/dev/ms1504/root'
            if command[0] == 'umount':
                mounted = False
                return b''
            if command[0] == 'mount':
                mounted = True
                return b''
            if command[0] in ('blkid', 'lsblk'):
                return b'swap' if command[0] == 'blkid' else b'/dev/external'
            if command[0] == 'lvs' and 'lv_path' in command:
                return b'/dev/ms1504/root /dev/ms1504/home /dev/ms1504/swap'
            if command[0] == 'lvchange':
                self.assertEqual(command, ['lvchange', '--activate', command[2], '/dev/ms1504/root'])
                self.assertFalse(mounted)
                return b''
            if command[0] == 'lvconvert':
                self.assertTrue((receipt_dir / 'merge-pending-base-1').exists())
                self.assertTrue((receipt_dir / 'merge-transaction-base-1').exists())
                self.lvs.clear()
                (self.root / 'dev/ms1504/root_rollback_base-1').unlink()
                return b''
            return ordinary_run(command)
        return cfg, args, recovery_run, lambda target: mounted if str(target) == str(self.root) else str(target) == str(receipt_dir)

    def test_offline_merge_accepts_pending_base_and_repairs_consumed_snapshot_menu(self):
        cfg, args, fake_run, ismount = self.offline_fixture()
        with patch.object(m, 'run', side_effect=fake_run), patch.object(m, 'current_root_source', return_value='/dev/rescue'), patch.object(m.os.path, 'ismount', side_effect=ismount), patch.object(m, 'confirm_tty'):
            m.command_merge_rollback(cfg, args)
        self.assertFalse((self.cfg['state_dir'] / 'pending').exists())
        self.assertNotIn('root_rollback_base-1', (self.root / 'boot/grub/active/grub.cfg').read_text())
        self.assertTrue((self.root / 'external/merge-complete-base-1').exists())
        self.assertFalse((self.root / 'external/merge-pending-base-1').exists())

    def test_offline_merge_refuses_live_origin_before_unmount_or_lvm_mutation(self):
        cfg, args, fake_run, ismount = self.offline_fixture()
        commands = []
        def checked(command, **kwargs):
            commands.append(command)
            return fake_run(command, **kwargs)
        with patch.object(m, 'run', side_effect=checked), patch.object(m, 'current_root_source', return_value='/dev/ms1504/root'), patch.object(m.os.path, 'ismount', side_effect=ismount), patch.object(m, 'confirm_tty') as confirm:
            with self.assertRaisesRegex(RuntimeError, 'recovery environment'):
                m.command_merge_rollback(cfg, args)
        confirm.assert_not_called()
        self.assertFalse(any(command[0] in ('umount', 'lvchange', 'lvconvert') for command in commands))

    def test_offline_merge_preserves_ram_recovery_when_checkpoint_predates_it(self):
        import json
        import hashlib
        cfg, args, fake_run, ismount = self.offline_fixture()
        recovery = self.root / 'boot/ms1504-recovery'
        ident = 'a' * 32
        (recovery / ident).mkdir(parents=True)
        content = {'vmlinuz': b'recovery kernel', 'initramfs.img': b'RAM recovery image'}
        for name, data in content.items():
            (recovery / ident / name).write_bytes(data)
        descriptor = {'id': ident, 'cmdline': ['noresume', 'rd.ms1504.recovery=1'],
                      'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}}
        (recovery / 'descriptor.json').write_text(json.dumps(descriptor))

        def unmount_root(command, **kwargs):
            if command[0] == 'umount' and '-R' in command and recovery.exists():
                recovery.rename(self.root / 'unmounted-origin-recovery')
            return fake_run(command, **kwargs)

        with patch.object(m, 'run', side_effect=unmount_root), \
                patch.object(m, 'current_root_source', return_value='/dev/rescue'), \
                patch.object(m.os.path, 'ismount', side_effect=ismount), patch.object(m, 'confirm_tty'):
            m.command_merge_rollback(cfg, args)
        for name, data in content.items():
            self.assertEqual((recovery / ident / name).read_bytes(), data)
        self.assertTrue((self.root / 'external/merge-recovery-base-1/descriptor.json').exists())

    def test_verify_running_accepts_the_existing_mapper_alias(self):
        self.initialize()
        (self.root / 'proc/cmdline').write_text(self.cmdline.read_text().replace('root=/dev/ms1504/root', 'root=/dev/mapper/ms1504-root'))
        realpath = os.path.realpath
        def aliases(value):
            return '/dev/ms1504/root' if str(value) == '/dev/mapper/ms1504-root' else realpath(value)
        with patch.object(m, 'current_root_source', return_value='/dev/ms1504/root'), patch.object(m.os.path, 'realpath', side_effect=aliases):
            m.command_verify_running(self.cfg)

    def test_legacy_command_cannot_select_unsigned_policy(self):
        with patch.object(sys, 'argv', ['ms1504-signed-generation', 'status']), patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'read_config', return_value=self.cfg), patch.object(m, 'protected_path'), patch.object(m, 'command_status') as status:
            self.assertEqual(m.main(require_signed=True), 1)
        status.assert_not_called()

    def test_unsigned_command_refuses_signing_credentials(self):
        self.initialize()
        with patch.object(sys, 'argv', ['ms1504-generation', '--gnupg-home', '/keys', 'status']), patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'read_config', return_value=self.cfg), patch.object(m, 'protected_path'), patch.object(m, 'command_status') as status:
            self.assertEqual(m.main(require_signed=False), 1)
        status.assert_not_called()

    def test_merge_archives_restored_receipts_for_previously_pruned_lvs(self):
        cfg, args, fake_run, ismount = self.offline_fixture()
        def restoring(command, **kwargs):
            result = fake_run(command, **kwargs)
            if command[0] == 'lvconvert':
                stale = self.cfg['state_dir'] / 'completed-pruned-0'
                stale.mkdir()
                (stale / 'transaction').write_text('old=pruned-0\nsnapshot=root_rollback_pruned-0\nsize_gib=32\n')
            return result
        with patch.object(m, 'run', side_effect=restoring), patch.object(m, 'current_root_source', return_value='/dev/rescue'), patch.object(m.os.path, 'ismount', side_effect=ismount), patch.object(m, 'confirm_tty'):
            m.command_merge_rollback(cfg, args)
        self.assertTrue((self.cfg['state_dir'] / 'recovered-receipts/completed-pruned-0/transaction').exists())
        self.assertNotIn('pruned-0', (self.root / 'boot/grub/active/grub.cfg').read_text())

    def test_monitor_interrupts_a_real_build_process_on_validation_failure(self):
        spawn = subprocess.Popen
        children = []
        def tracked(*args, **kwargs):
            child = spawn(*args, **kwargs)
            children.append(child)
            return child
        command = [sys.executable, '-c', 'import signal,sys,time; signal.signal(signal.SIGINT, lambda *args: sys.exit(0)); time.sleep(30)']
        with patch.object(m, 'check_pending', side_effect=[None, RuntimeError('unsafe snapshot')]), patch.object(m.subprocess, 'Popen', side_effect=tracked):
            with self.assertRaisesRegex(RuntimeError, 'unsafe snapshot'):
                m.command_run(self.cfg, SimpleNamespace(command=command))
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_unsigned_and_signed_menus_parse_with_real_grub(self):
        for policy in ('signed', 'unsigned'):
            cfg = self.cfg | {'policy': policy}
            menu = m.make_menu(cfg, 'base-1', [{'old': 'base-1', 'snapshot': 'root_rollback_base-1'}],
                               default=1, base_cmdline=self.cmdline.read_text().split())
            path = self.root / ('check-' + policy + '.cfg')
            path.write_bytes(menu)
            try:
                subprocess.run(['grub-script-check', str(path)], check=True, capture_output=True)
            except FileNotFoundError:
                self.skipTest('requires GRUB parser')

    def test_unsigned_configuration_requires_explicit_policy_and_no_signer(self):
        config = self.root / 'config.toml'
        config.write_text((GENTOO / 'generation.example.toml').read_text())
        config.chmod(0o644)
        with patch.object(m, 'CONFIG', config), patch.object(m.os, 'geteuid', return_value=0):
            # Permission requirement is real on production; this suite may run without root.
            from unittest.mock import Mock
            stat = config.stat()
            with patch.object(type(config), 'stat', return_value=SimpleNamespace(st_mode=stat.st_mode, st_uid=0)):
                loaded = m.read_config()
        self.assertFalse(m.signed(loaded))
        self.assertNotIn('signing_fingerprint', loaded['boot'])

    def test_auto_and_numeric_retention_configuration(self):
        config = self.root / 'capacity.toml'
        template = (GENTOO / 'generation.example.toml').read_text()
        config.write_text(template)
        config.chmod(0o600)
        metadata = config.stat()
        with patch.object(m, 'CONFIG', config), patch.object(type(config), 'stat', return_value=SimpleNamespace(st_mode=metadata.st_mode, st_uid=0)):
            loaded = m.read_config()
            self.assertEqual(loaded['snapshots']['retain_generations'], 'auto')
            identity = loaded['system'] | {'profile': 'hardware', 'reserve_gib': '100'}
            m.validate_root_identity(identity, loaded)
            with self.assertRaisesRegex(RuntimeError, 'reserve'):
                m.validate_root_identity(identity | {'reserve_gib': '31'}, loaded)
            for value in ('3', '8'):
                config.write_text(template.replace('retain_generations = "auto"', 'retain_generations = ' + value))
                self.assertEqual(m.read_config()['snapshots']['retain_generations'], int(value))
            for value in ('0', '-1', 'true', '1.5', '"other"'):
                config.write_text(template.replace('retain_generations = "auto"', 'retain_generations = ' + value))
                with self.assertRaisesRegex(RuntimeError, 'positive integer'):
                    m.read_config()

    def test_third_transaction_preserves_two_existing_checkpoints_and_stops_at_three(self):
        self.cfg['snapshots']['retain_generations'] = 'auto'
        self.initialize()
        for generation in ('desktop-1', 'desktop-2', 'desktop-3'):
            m.command_begin(self.cfg, self.args)
            m.command_seal(self.cfg, SimpleNamespace(generation=generation, kernel=str(self.kernel), initramfs=str(self.initrd)))
        self.assertEqual(set(self.lvs), {'root_rollback_base-1', 'root_rollback_desktop-1', 'root_rollback_desktop-2'})
        with self.assertRaisesRegex(RuntimeError, 'no snapshot slot'):
            m.command_begin(self.cfg, self.args)
        self.assertEqual(len(self.lvs), 3)


class SnapshotCapacity(unittest.TestCase):
    def setUp(self):
        self.gib = 1024**3
        self.cfg = {'vg': 'vg', 'snapshots': {'size_gib': 32, 'retain_generations': 'auto'}}
        self.rows = [{'snapshot': 'one'}, {'snapshot': 'two'}]
        self.sizes = {'one': 32 * self.gib, 'two': 32 * self.gib}

    def capacity(self, free=36, extent=None):
        extent = extent if extent is not None else 4 * 1024**2
        with patch.object(m, 'snapshot_health', side_effect=lambda cfg, name: (0, self.sizes[name])), \
             patch.object(m, 'run', return_value=f'{free * self.gib} {extent}'.encode()):
            return m.snapshot_capacity(self.cfg, self.rows, {'reserve_gib': '100'})

    def test_auto_uses_the_lower_of_free_extents_and_remaining_reserve(self):
        self.assertEqual(self.capacity()['additional_slots'], 1)
        self.assertEqual(self.capacity()['allowed_slots'], 3)
        self.assertEqual(self.capacity(free=31)['additional_slots'], 0)
        self.assertEqual(self.capacity(free=1000)['allowed_slots'], 3)

    def test_grown_cow_allocation_is_counted_even_when_almost_empty(self):
        self.sizes['one'] = 48 * self.gib
        self.assertEqual(self.capacity()['allocated_bytes'], 80 * self.gib)
        self.assertEqual(self.capacity()['additional_slots'], 0)

    def test_optional_explicit_limit_remains_a_ceiling(self):
        self.cfg['snapshots']['retain_generations'] = 2
        self.assertEqual(self.capacity()['additional_slots'], 0)

    def test_rounding_to_physical_extents_is_included(self):
        self.cfg['snapshots']['size_gib'] = 25
        result = self.capacity(free=31, extent=16 * self.gib)
        self.assertEqual(result['next_snapshot_bytes'], 32 * self.gib)
        self.assertEqual(result['additional_slots'], 0)
        self.assertEqual(self.capacity(free=32, extent=16 * self.gib)['additional_slots'], 1)

    def test_invalid_lvm_report_fails_closed(self):
        for report in (b'unknown 4194304', b'1', b'1 0'):
            with self.subTest(report=report), patch.object(m, 'snapshot_health', return_value=(0, 32 * self.gib)), \
                 patch.object(m, 'run', return_value=report), self.assertRaises(RuntimeError):
                m.snapshot_capacity(self.cfg, self.rows, {'reserve_gib': '100'})


if __name__ == '__main__':
    unittest.main()
