"""Real GPG signatures and failure injection around policy migration and RAM recovery."""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import tempfile

import test_gentoo_generations as fixtures

m = fixtures.m
GENTOO = fixtures.GENTOO


def load(name):
    spec = spec_from_file_location(name, GENTOO / (name + '.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


t = load('generation_transition')
r = load('generation_recovery')


@unittest.skipUnless(shutil.which('gpg'), 'GnuPG is required for real signature checks')
class SigningMigration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.home = Path(cls.keys.name)
        cls.home.chmod(0o700)
        subprocess.run(['gpg', '--homedir', str(cls.home), '--batch', '--pinentry-mode', 'loopback',
                        '--passphrase', '', '--quick-generate-key', 'Boot test <boot@example.invalid>',
                        'rsa2048', 'sign', '0'], check=True, capture_output=True)
        listing = subprocess.check_output(['gpg', '--homedir', str(cls.home), '--with-colons', '--list-keys'], stderr=subprocess.DEVNULL)
        cls.fingerprint = next(line.split(':')[9] for line in listing.decode().splitlines() if line.startswith('fpr:'))
        cls.public = subprocess.check_output(['gpg', '--homedir', str(cls.home), '--export', cls.fingerprint])

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['gpgconf', '--homedir', str(cls.home), '--kill', 'gpg-agent'], check=False)
        cls.keys.cleanup()

    fake_run = fixtures.GenerationTransactions.fake_run
    initialize = fixtures.GenerationTransactions.initialize
    begin = fixtures.GenerationTransactions.begin
    offline_fixture = fixtures.GenerationTransactions.offline_fixture

    def setUp(self):
        fixtures.GenerationTransactions.setUp(self)
        self.addCleanup(setattr, m, '_SIGNER', None)
        m._SIGNER = None
        fixture_run = self.fake_run

        def run(command, **kwargs):
            if command[0] == 'gpg':
                return subprocess.check_output(command, stderr=subprocess.PIPE)
            return fixture_run(command, **kwargs)

        patcher = patch.object(m, 'run', side_effect=run)
        patcher.start()
        self.addCleanup(patcher.stop)
        for patcher in (patch.object(m, 'protected_path'), patch.object(m, 'command_verify_running'),
                        patch.object(m, 'confirm_tty'), patch.object(t, 'Path', side_effect=m.Path),
                        patch.object(t, 'validate_interlocks')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = self.root / 'etc/ms1504-generation.toml'
        self.config.write_bytes(t.configuration(self.cfg))
        patcher = patch.object(m, 'CONFIG', self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.key = self.root / 'boot.key'
        self.key.write_bytes(self.public)
        self.args = SimpleNamespace(public_key=self.key, fingerprint=self.fingerprint,
                                    secret_key_file=None, gnupg_home=str(self.home))
        self.directory = self.cfg['state_dir'] / 'signing-migration'

    def prepare(self):
        t.prepare(m, self.cfg, self.args, self.directory)

    def test_prepare_preserves_unsigned_boot_and_signs_current_and_retained_artifacts(self):
        self.begin()
        m.command_seal(self.cfg, SimpleNamespace(generation='desktop-1', kernel=str(self.kernel), initramfs=str(self.initrd)))
        before = (self.cfg['boot_dir'] / 'grub/grub.cfg').read_bytes()
        active = os.readlink(self.cfg['boot_dir'] / 'grub/active')
        self.prepare()
        self.assertEqual((self.cfg['boot_dir'] / 'grub/grub.cfg').read_bytes(), before)
        self.assertEqual(os.readlink(self.cfg['boot_dir'] / 'grub/active'), active)
        self.assertEqual(self.config.read_bytes(), t.configuration(self.cfg))
        for generation in ('base-1', 'desktop-1'):
            for name in ('vmlinuz', 'initramfs.img'):
                self.assertTrue((self.cfg['boot_dir'] / 'generations' / generation / (name + '.sig')).is_file())
        self.percent = 0.05  # COW usage can change while writing signatures.
        self.prepare()

    def test_prepare_rejects_pending_transaction_and_wrong_key(self):
        self.begin()
        with self.assertRaisesRegex(RuntimeError, 'root update'):
            self.prepare()
        self.assertFalse(self.directory.exists())
        self.args.fingerprint = 'A' * 40
        with self.assertRaisesRegex(RuntimeError, 'fingerprint'):
            self.prepare()

    def test_tampering_after_preparation_blocks_activation_before_publication(self):
        self.initialize()
        self.prepare()
        active = os.readlink(self.cfg['boot_dir'] / 'grub/active')
        path = self.cfg['boot_dir'] / 'generations/base-1/vmlinuz'
        path.write_bytes(b'changed after signing')
        with self.assertRaisesRegex(RuntimeError, 'artifact differs|inputs changed'):
            t.activate(m, self.cfg, self.args, self.directory)
        self.assertEqual(os.readlink(self.cfg['boot_dir'] / 'grub/active'), active)

    def test_activation_resumes_after_policy_write_and_configuration_stays_signed(self):
        self.initialize()
        self.prepare()
        original = m.atomic_write
        failed = False

        def power_cut(path, data, *args):
            nonlocal failed
            if path == self.config and not failed:
                failed = True
                raise OSError('simulated power cut')
            return original(path, data, *args)

        with patch.object(m, 'atomic_write', side_effect=power_cut):
            with self.assertRaisesRegex(OSError, 'power cut'):
                t.activate(m, self.cfg, self.args, self.directory)
        self.assertEqual(json.loads((self.directory / 'manifest.json').read_text())['phase'], 'activating')
        t.activate(m, self.cfg, self.args, self.directory)
        self.assertIn('policy = "signed"', self.config.read_text())
        self.assertFalse(self.directory.exists())
        self.assertTrue((self.cfg['state_dir'] / 'signing-migrations' / self.fingerprint).exists())
        target = t.target_config(self.cfg, self.fingerprint, self.root / 'etc/ms1504-boot/boot.key')
        m.verify_signature(self.cfg['boot_dir'] / 'grub/grub.cfg', self.args, target)
        with self.assertRaisesRegex(RuntimeError, 'downgraded'):
            t.command(m, target, SimpleNamespace(action='abort-signing'))

    def test_interrupted_preparation_resumes_and_abort_never_removes_boot_artifacts(self):
        self.initialize()
        with patch.object(m, 'sign', side_effect=RuntimeError('signer unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'signer unavailable'):
                self.prepare()
        self.assertEqual(json.loads((self.directory / 'manifest.json').read_text())['phase'], 'preparing')
        self.prepare()
        t.command(m, self.cfg, SimpleNamespace(action='abort-signing'))
        self.assertFalse(self.directory.exists())
        self.assertTrue((self.cfg['boot_dir'] / 'generations/base-1/vmlinuz').exists())
        m.check_policy(self.cfg)

    def test_valid_signature_under_another_configured_fingerprint_is_rejected(self):
        self.initialize()
        self.prepare()
        target = t.target_config(self.cfg, 'A' * 40, self.key)
        with self.assertRaisesRegex(RuntimeError, 'configured key'):
            m.verify_signature(self.cfg['boot_dir'] / 'generations/base-1/vmlinuz', self.args, target)

    def test_signed_merge_to_pre_signing_checkpoint_restores_signed_policy_and_key(self):
        cfg, args, fake_run, ismount = self.offline_fixture()
        key = self.root / 'etc/ms1504-boot/boot.key'
        key.parent.mkdir()
        key.write_bytes(self.public)
        signed = t.target_config(cfg, self.fingerprint, Path('/etc/ms1504-boot/boot.key'))
        original_config = t.configuration(cfg)
        self.config.write_bytes(t.configuration(signed))
        policy = self.root / 'boot/grub/generation-policy.json'
        policy.write_bytes(m.policy_binding(signed))
        args.gnupg_home = str(self.home)

        def run(command, **kwargs):
            if command[0] == 'gpg':
                return subprocess.check_output(command, stderr=subprocess.PIPE)
            result = fake_run(command, **kwargs)
            if command[0] == 'lvconvert':
                # The selected snapshot predates policy migration.
                self.config.write_bytes(original_config)
                policy.write_bytes(m.policy_binding(cfg))
                key.unlink()
                for signature in (self.root / 'boot').rglob('*.sig'):
                    signature.unlink()
            return result

        with patch.object(m, 'run', side_effect=run), \
                patch.object(m, 'current_root_source', return_value='/dev/rescue'), \
                patch.object(m.os.path, 'ismount', side_effect=ismount):
            with patch.object(m, 'sign', side_effect=RuntimeError('signing key unavailable')):
                with self.assertRaisesRegex(RuntimeError, 'signing key unavailable'):
                    m.command_merge_rollback(signed, args)
            self.assertFalse((self.root / 'external/merge-pending-base-1').exists())
            self.assertIn('root_rollback_base-1', self.lvs)
            # The failed preflight unmounted root; the operator can remount it
            # and supply the key without an ambiguous LVM merge intent.
            fake_run(['mount', '-t', 'ext4', '-o', 'ro', cfg['root'], str(self.root)])
            m.command_merge_rollback(signed, args)
        self.assertEqual(self.config.read_bytes(), t.configuration(signed))
        self.assertEqual(key.read_bytes(), self.public)
        self.assertEqual(policy.read_bytes(), m.policy_binding(signed))
        self.assertIn('verify_detached', (self.root / 'boot/grub/active/grub.cfg').read_text())
        self.assertTrue((self.root / 'external/merge-complete-base-1').is_file())


class InternalRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.storage = {'filesystem_uuid': '0fdfccaf-1221-4a92-9033-ac17d0000721',
                        'luks_uuid': '8f14e081-a7e3-4778-9530-b290c8122717'}
        (self.root / 'etc/conf.d').mkdir(parents=True)
        (self.root / 'etc/conf.d/ms1504-extra').write_text(
            'LUKS_UUID=' + self.storage['luks_uuid'] + '\nMAPPER=ms1504-extra\nKEY=/root/.keys/ms1504-extra.key\nMOUNT=/extra\nLABEL=GENTOO_BUILD\n')
        self.key = self.root / 'root/.keys/ms1504-extra.key'
        self.key.parent.mkdir(parents=True)
        self.key.write_bytes(b'k' * 64)
        self.key.chmod(0o400)
        self.manager = SimpleNamespace(need=m.need, protected_path=lambda _: None, run=lambda _: b'')

    def test_extra_unlock_uses_root_key_descriptor_without_a_key_copy_or_prompt(self):
        key_stat = SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100400, st_size=64)
        original_stat = Path.stat
        def stat(path, *args, **kwargs):
            return key_stat if path == self.key else original_stat(path, *args, **kwargs)
        def cryptsetup(command, **kwargs):
            self.assertEqual(command[:4], ['cryptsetup', 'open', '--key-file', '-'])
            self.assertNotIn(str(self.key), command)
            self.assertEqual(kwargs['stdin'].read(), b'k' * 64)
            self.assertFalse(kwargs['stdin'].closed)
        # is_file uses st_mode; the descriptor is closed by the helper itself.
        with patch.object(Path, 'stat', stat), patch.object(r.subprocess, 'run', side_effect=cryptsetup) as run:
            r.unlock_storage(self.manager, self.root, self.storage)
        self.assertTrue(run.call_args.kwargs['stdin'].closed)
        self.assertEqual(self.key.read_bytes(), b'k' * 64)
        self.assertEqual(list(self.key.parent.iterdir()), [self.key])

    def test_extra_unlock_rejects_config_injection_and_wrong_identity_before_cryptsetup(self):
        config = self.root / 'etc/conf.d/ms1504-extra'
        for replacement in ('LUKS_UUID=wrong', 'KEY=$(touch /tmp/not-allowed)',
                            'KEY=/other/key', 'KEY=/root/.keys/ms1504-extra.key\nKEY=other'):
            original = config.read_text()
            field = replacement.split('=', 1)[0]
            config.write_text('\n'.join(replacement if line.startswith(field + '=') else line
                                         for line in original.splitlines()) + '\n')
            with patch.object(r.subprocess, 'run') as run, self.assertRaises(RuntimeError):
                r.unlock_storage(self.manager, self.root, self.storage)
            run.assert_not_called()
            config.write_text(original)

    def test_recovery_menu_is_verified_and_disables_resume(self):
        directory = self.root / ('a' * 32)
        directory.mkdir()
        for name in ('vmlinuz', 'initramfs.img'):
            (directory / name).write_bytes(name.encode())
        descriptor = self.root / 'descriptor.json'
        data = {'id': directory.name, 'cmdline': ['root=/dev/ms1504/root', 'noresume', 'rd.ms1504.recovery=1'],
                'sha256': {name: r.digest(directory / name) for name in ('vmlinuz', 'initramfs.img')}}
        descriptor.write_text(json.dumps(data))
        cfg = dict(vg='ms1504', system={'root_lv': 'root'}, policy='unsigned')
        manager = SimpleNamespace(need=m.need, protected_path=lambda _: None, signed=m.signed)
        unsigned = r.menu_entry(manager, cfg, descriptor)
        self.assertIn('noresume', unsigned)
        signed = r.menu_entry(manager, cfg | {'policy': 'signed'}, descriptor)
        self.assertEqual(signed.count('verify_detached'), 2)
        (directory / 'initramfs.img').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'artifact changed'):
            r.menu_entry(manager, cfg, descriptor)

    def test_offline_mode_refuses_to_run_outside_initramfs_before_any_mount(self):
        manager = SimpleNamespace(need=m.need)
        with self.assertRaisesRegex(RuntimeError, 'initramfs'):
            r.offline(manager, 'base-1', base=self.root)

    def test_recovery_hook_cannot_fall_through_after_shell_exit(self):
        library = self.root / 'dracut-lib.sh'
        count = self.root / 'count'
        library.write_text('getargbool() { return 0; }\n'
                           'setsid() {\n'
                           ' n=0; [ ! -f "$TEST_COUNT" ] || n=$(cat "$TEST_COUNT")\n'
                           ' n=$((n+1)); echo "$n" > "$TEST_COUNT"\n'
                           ' [ "$n" -lt 2 ] || exit 67\n'
                           ' return 1\n}\n'
                           'sync() { :; }\nreboot() { exit 68; }\n')
        hook = (GENTOO / 'dracut-ms1504-rollback/recover.sh').read_text()
        hook = hook.replace('/lib/dracut-lib.sh', str(library)).replace('/dev/console', str(self.root / 'console'))
        sentinel = self.root / 'normal-boot'
        hook += '\necho unexpected > ' + str(sentinel) + '\n'
        result = subprocess.run(['sh'], input=hook, text=True, capture_output=True,
                                env={**os.environ, 'TEST_COUNT': str(count)}, timeout=5)
        self.assertEqual(result.returncode, 67, result.stderr)
        self.assertEqual(count.read_text().strip(), '2')
        self.assertFalse(sentinel.exists())

    def test_menu_retries_invalid_choice_and_cancelled_confirmation(self):
        choices = [dict(generation='newest', eligible=True, signed=False, reason=''),
                   dict(generation='older', eligible=False, signed=False, reason='boot-only')]
        answers = iter(['wrong', '2', '1', '1'])
        with patch.object(r, 'rollback_choices', return_value=choices), \
                patch.object(r, 'offline', side_effect=[RuntimeError('confirmation did not match'), None]) as offline:
            r.recovery_menu(self.manager, read=lambda _: next(answers))
        self.assertEqual(offline.call_count, 2)
        self.assertTrue(all(call.kwargs['interactive_menu'] for call in offline.call_args_list))

    def test_hook_reboots_after_menu_success_or_cancel(self):
        library = self.root / 'dracut-lib.sh'
        log = self.root / 'actions'
        library.write_text('getargbool() { return 0; }\nsetsid() { return 0; }\n'
                           'sync() { echo sync >> "$TEST_LOG"; }\n'
                           'reboot() { echo reboot >> "$TEST_LOG"; exit 69; }\n')
        hook = (GENTOO / 'dracut-ms1504-rollback/recover.sh').read_text()
        hook = hook.replace('/lib/dracut-lib.sh', str(library)).replace('/dev/console', str(self.root / 'console'))
        result = subprocess.run(['sh'], input=hook, text=True, capture_output=True, timeout=5,
                                env={**os.environ, 'TEST_LOG': str(log)})
        self.assertEqual(result.returncode, 69, result.stderr)
        self.assertEqual(log.read_text().splitlines(), ['sync', 'reboot'])

    def test_menu_none_requests_reboot_without_a_merge(self):
        with patch.object(r, 'rollback_choices', return_value=[]), patch.object(r, 'offline') as offline:
            r.recovery_menu(self.manager, read=lambda _: 'none')
        offline.assert_not_called()

    def test_menu_does_not_retry_after_a_durable_merge_intent(self):
        choices = [dict(generation='newest', eligible=True, signed=False, reason='')]
        with patch.object(r, 'rollback_choices', return_value=choices), \
                patch.object(r, 'offline', side_effect=r.RecoveryPendingError('merge intent exists')) as offline:
            with self.assertRaises(r.RecoveryPendingError):
                r.recovery_menu(self.manager, read=lambda _: '1')
        self.assertEqual(offline.call_count, 1)

    def test_cancelled_attempt_cleans_owned_mounts_and_can_retry(self):
        state = {}
        target = self.root / 'mounted'
        source = '/dev/test-recovery'
        def run(command):
            if command[0] == 'mount':
                self.assertNotIn(str(target), state)
                state[str(target)] = (source, 'ext4', command[command.index('-o') + 1])
            elif command[0] == 'umount':
                state.pop(command[1])
            elif command[0] == 'findmnt':
                fields = dict(zip(('SOURCE', 'FSTYPE', 'OPTIONS'), state[str(target)]))
                return fields[command[-1]].encode()
            else:
                self.fail(str(command))
            return b''
        self.manager.run = run
        with patch.object(r.os.path, 'ismount', side_effect=lambda path: str(path) in state):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, 'confirmation'):
                    with r.mounted(self.manager, source, target, 'rw'):
                        raise RuntimeError('confirmation did not match')
                self.assertFalse(state)
            # Adopt the matching mount left behind by the previous image.
            state[str(target)] = (source, 'ext4', 'rw,relatime')
            with r.mounted(self.manager, source, target, 'rw'):
                self.assertTrue(state)
            self.assertFalse(state)

    def test_unrelated_existing_mount_is_never_unmounted(self):
        target = self.root / 'other'
        def run(command):
            fields = {'SOURCE': '/dev/other-disk', 'FSTYPE': 'ext4', 'OPTIONS': 'ro'}
            self.assertEqual(command[0], 'findmnt')
            return fields[command[-1]].encode()
        self.manager.run = run
        with patch.object(r.os.path, 'ismount', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'unexpected existing'):
                with r.mounted(self.manager, '/dev/expected', target):
                    self.fail('unexpected mount was accepted')

    def test_cleanup_error_after_merge_intent_is_not_treated_as_retryable(self):
        target = self.root / 'after-merge'
        state = set()
        def run(command):
            if command[0] == 'mount':
                state.add(str(target))
                return b''
            self.assertEqual(command[0], 'umount')
            raise RuntimeError('device busy')
        self.manager.run = run
        with patch.object(r.os.path, 'ismount', side_effect=lambda path: str(path) in state):
            with self.assertRaises(r.RecoveryPendingError):
                with r.mounted(self.manager, '/dev/test', target, 'rw', unsafe=lambda: True):
                    pass

    def test_recovery_storage_rejects_any_target_vg_ancestor(self):
        def run(command):
            if command[0] == 'findmnt':
                return b'/dev/mapper/other' if command[-1] == 'SOURCE' else b'ext4'
            if command[0] == 'lsblk':
                return b'/dev/mapper/other\n/dev/ms1504/home\n'
            if command[0] == 'lvs':
                return b'/dev/ms1504/root\n/dev/ms1504/home\n/dev/ms1504/swap\n'
            self.fail('unexpected probe after detecting target VG')
        manager = SimpleNamespace(need=m.need, run=run)
        with patch.object(r.os.path, 'ismount', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'entire target VG'):
                r.storage_identity(manager, {'vg': 'ms1504'}, self.root)

    def test_checkpoint_must_keep_the_key_for_unchanged_extra_and_is_unmounted_on_failure(self):
        checkpoint = self.root / 'checkpoint'
        (checkpoint / 'etc/conf.d').mkdir(parents=True)
        (checkpoint / 'root/.keys').mkdir(parents=True)
        (checkpoint / 'etc/ms1504-generation').write_text('base-1\n')
        config = 'etc/conf.d/ms1504-extra'
        (checkpoint / config).write_bytes((self.root / config).read_bytes())
        historical_key = checkpoint / 'root/.keys/ms1504-extra.key'
        historical_key.write_bytes(b'different'.ljust(64, b'!'))
        original_stat = Path.stat
        info = SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100400, st_size=64)
        def stat(path, *args, **kwargs):
            return info if path in (self.key, historical_key) else original_stat(path, *args, **kwargs)
        def paths(value):
            return checkpoint if str(value) == '/run/ms1504-checkpoint' else Path(value)
        cfg = {'vg': 'ms1504', 'system': {'root_lv': 'root'}}
        with patch.object(r, 'Path', side_effect=paths), patch.object(Path, 'stat', stat), \
                patch.object(self.manager, 'run', return_value=b'') as run:
            with self.assertRaisesRegex(RuntimeError, 'different /extra key'):
                r.verify_checkpoint_storage(self.manager, self.root, cfg, 'base-1')
            self.assertEqual(run.call_args.args[0], ['umount', str(checkpoint)])
            historical_key.write_bytes(self.key.read_bytes())
            r.verify_checkpoint_storage(self.manager, self.root, cfg, 'base-1')


class MergeCompletion(unittest.TestCase):
    cfg = {'vg': 'ms1504', 'root': '/dev/ms1504/root', 'system': {'root_lv': 'root'}}

    def report(self, merging=True, uuid='snapshot-id'):
        rows = [dict(lv_name='root', lv_uuid='root-id', lv_attr='Owi-aos---' if merging else 'owi-a-s---',
                     lv_merging='merging' if merging else '', origin='')]
        if merging:
            rows.append(dict(lv_name='[root_rollback_base]', lv_uuid=uuid,
                             lv_attr='Swi-aos---', lv_merging='merging', origin='root'))
        return json.dumps({'report': [{'lv': rows}]}).encode()

    def test_hidden_snapshot_and_merging_origin_must_disappear_before_completion(self):
        reports = iter([self.report(), self.report(), self.report(False)])
        calls = []
        def run(command):
            calls.append(command)
            return next(reports) if command[0] == 'lvs' else b''
        with patch.object(m, 'run', side_effect=run), patch.object(m.time, 'sleep') as sleep:
            m.wait_snapshot_merge(self.cfg, 'root_rollback_base', 'snapshot-id', timeout=5)
        self.assertEqual(calls[0], ['lvchange', '--poll', 'y', '/dev/ms1504/root'])
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(all('--all' in call for call in calls if call[0] == 'lvs'))

    def test_hidden_merge_times_out_even_when_device_node_has_disappeared(self):
        with patch.object(m, 'run', side_effect=lambda command: self.report() if command[0] == 'lvs' else b''), \
                patch.object(m.time, 'monotonic', side_effect=[0, 2]):
            with self.assertRaisesRegex(RuntimeError, 'cleanup did not complete'):
                m.wait_snapshot_merge(self.cfg, 'root_rollback_base', 'snapshot-id', timeout=1)

    def test_replaced_hidden_snapshot_refuses_completion(self):
        with patch.object(m, 'run', side_effect=lambda command: self.report(uuid='changed') if command[0] == 'lvs' else b''):
            with self.assertRaisesRegex(RuntimeError, 'UUID changed'):
                m.wait_snapshot_merge(self.cfg, 'root_rollback_base', 'snapshot-id')

    def test_inventory_counts_hidden_merging_snapshot(self):
        with patch.object(m, 'run', return_value=b'[root_rollback_base] root\nroot\n'):
            self.assertEqual(m.actual_root_snapshots(self.cfg), {'root_rollback_base'})


class RecoveryPublication(unittest.TestCase):
    fake_run = fixtures.GenerationTransactions.fake_run
    initialize = fixtures.GenerationTransactions.initialize
    begin = fixtures.GenerationTransactions.begin

    def setUp(self):
        fixtures.GenerationTransactions.setUp(self)
        self.config = self.root / 'etc/ms1504-generation.toml'
        self.config.write_bytes(t.configuration(self.cfg))
        self.args = SimpleNamespace(kernel=self.kernel, kernel_release='test', storage=self.root / 'extra',
                                    secret_key_file=None, gnupg_home=None)

        def paths(value='.'):
            if str(value).startswith('/usr/'):
                return self.root / str(value).lstrip('/')
            return m.Path(value)

        original_run = self.fake_run
        self.builds = 0

        def run(command, **kwargs):
            if command[0] == 'dracut':
                self.builds += 1
                Path(command[-1]).write_bytes(b'self contained recovery image')
                return b''
            if command[0] == 'lsinitrd':
                return b'ms1504-python ms1504-offline/generation_recovery.py ms1504-offline/signed-generation.py 00-recover.sh'
            return original_run(command, **kwargs)

        for patcher in (patch.object(m, 'CONFIG', self.config), patch.object(m, 'protected_path'),
                        patch.object(m, 'command_verify_running'), patch.object(m, 'run', side_effect=run),
                        patch.object(r, 'Path', side_effect=paths),
                        patch.object(r, 'storage_identity', return_value={'filesystem_uuid': 'fs', 'luks_uuid': 'luks'})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_publication_resumes_after_descriptor_write_without_rebuilding_or_losing_pending_gate(self):
        self.begin()
        with patch.object(m, 'publish', side_effect=RuntimeError('power cut before publication')):
            with self.assertRaisesRegex(RuntimeError, 'power cut'):
                r.prepare(m, self.cfg, self.args)
        self.assertTrue((self.cfg['state_dir'] / 'recovery-preparation.json').exists())
        self.assertEqual(self.builds, 1)
        r.prepare(m, self.cfg, self.args)
        self.assertEqual(self.builds, 1)
        self.assertFalse((self.cfg['state_dir'] / 'recovery-preparation.json').exists())
        m.check_pending(self.cfg)
        menu = (self.cfg['boot_dir'] / 'grub/active/grub.cfg').read_text()
        self.assertIn('Offline root rollback (RAM recovery)', menu)
        self.assertIn('set default=1', menu)
        self.assertNotIn('ms1504-extra.key', menu)

    def test_changed_recovery_image_is_rejected_on_publication_resume(self):
        self.initialize()
        with patch.object(m, 'publish', side_effect=RuntimeError('power cut')):
            with self.assertRaises(RuntimeError):
                r.prepare(m, self.cfg, self.args)
        journal = json.loads((self.cfg['state_dir'] / 'recovery-preparation.json').read_text())
        image = self.cfg['boot_dir'] / 'ms1504-recovery' / journal['descriptor']['id'] / 'initramfs.img'
        image.write_bytes(b'corruption')
        with self.assertRaisesRegex(RuntimeError, 'artifact changed'):
            r.prepare(m, self.cfg, self.args)

    def test_older_manager_pending_menu_can_protect_redeployment_after_rollback(self):
        self.begin()
        r.prepare(m, self.cfg, self.args)
        rows = [m.parse_fields(self.cfg['state_dir'] / 'pending/transaction')]
        legacy = m.make_menu(self.cfg, 'base-1', rows, default=1, include_recovery=False)
        self.assertNotIn(b'Offline root rollback', legacy)
        ident = m.publish(self.cfg, legacy, self.args)
        (self.cfg['state_dir'] / 'pending/menu').write_text(ident + '\n')
        m.check_pending(self.cfg)
        m.command_begin(self.cfg, self.args)
        self.assertEqual(len(self.lvs), 1)
        self.assertIn('Offline root rollback', (self.cfg['boot_dir'] / 'grub/active/grub.cfg').read_text())


if __name__ == '__main__':
    unittest.main()
