"""Execute hibernation marker and initramfs failure paths without sleeping."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import os
import stat
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = spec_from_file_location('hibernate_test', GENTOO / 'hibernate.py')
h = module_from_spec(spec)
spec.loader.exec_module(h)


class Markers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = h.Hibernation({}, self.root)
        self.fields = dict(format='1', generation='desktop-3', kernel_release='6.12.111',
                           root_uuid='root', home_uuid='home', swap_uuid='swap', boot_id='first-boot')
        for obj, name, value in [(self.tool, 'context', self.fields), (h.m, 'protected_path', None), (h.m, 'run', b'')]:
            p = patch.object(obj, name, return_value=value)
            setattr(self, name + '_mock', p.start())
            self.addCleanup(p.stop)

    def test_pre_is_durable_private_and_checkpoints_only_home(self):
        self.tool.pre()
        self.assertEqual(self.tool.read_marker(), self.fields)
        self.assertEqual(self.tool.marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.tool.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual([x.args[0] for x in self.run_mock.call_args_list],
                         [['fsfreeze', '--freeze', '/home'], ['fsfreeze', '--unfreeze', '/home']])

    def test_duplicate_pre_preserves_marker(self):
        self.tool.pre()
        before = self.tool.marker.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            self.tool.pre()
        self.assertEqual(before, self.tool.marker.read_bytes())

    def test_post_archives_matching_identity(self):
        self.tool.pre()
        self.tool.post()
        self.assertFalse(self.tool.marker.exists())
        self.assertEqual(len(list((self.tool.directory / 'history').iterdir())), 1)

    def test_wrong_post_identity_retains_marker(self):
        self.tool.pre()
        self.context_mock.return_value = self.fields | {'generation': 'wrong'}
        with self.assertRaisesRegex(RuntimeError, 'identity differs'):
            self.tool.post()
        self.assertTrue(self.tool.marker.exists())

    def test_remaining_swap_image_retains_marker(self):
        self.tool.pre()
        self.context_mock.side_effect = RuntimeError('swap contains an image')
        with self.assertRaisesRegex(RuntimeError, 'image'):
            self.tool.post()
        self.assertTrue(self.tool.marker.exists())

    def test_clear_requires_cold_boot_and_archives_evidence(self):
        self.tool.pre()
        with self.assertRaisesRegex(RuntimeError, 'same-boot'):
            self.tool.clear_stale()
        self.context_mock.return_value = self.fields | {'boot_id': 'next-boot'}
        self.tool.clear_stale()
        self.assertFalse(self.tool.marker.exists())

    def test_checkpoint_failure_keeps_marker_and_unfreezes_on_read_error(self):
        with patch.object(self.tool, 'read_marker', side_effect=RuntimeError('read failed')):
            with self.assertRaisesRegex(RuntimeError, 'read failed'):
                self.tool.pre()
        self.assertTrue(self.tool.marker.exists())
        self.assertEqual(self.run_mock.call_args.args[0], ['fsfreeze', '--unfreeze', '/home'])

    def test_corrupt_or_duplicate_marker_is_not_retired(self):
        self.tool.pre()
        self.tool.marker.write_text(self.tool.marker.read_text() + 'generation=other\n')
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            self.tool.post()
        self.assertTrue(self.tool.marker.exists())

    def test_failed_context_never_creates_a_marker(self):
        self.context_mock.side_effect = RuntimeError('pending update')
        with self.assertRaisesRegex(RuntimeError, 'pending update'):
            self.tool.pre()
        self.assertFalse(self.tool.marker.exists())

    def test_conflicting_hook_veto_policy_is_rejected(self):
        p = self.root / 'etc/elogind/sleep.conf.d/custom.conf'
        p.parent.mkdir(parents=True)
        p.write_text('[Sleep]\nAllowSuspendInterrupts=no\n')
        with self.assertRaisesRegex(RuntimeError, 'conflicting'):
            self.tool.validate_policy()

    def test_installer_is_idempotent_and_does_not_write_runtime_power_state(self):
        self.tool.cfg = {'system': {'home_lv': 'home', 'swap_lv': 'swap'}}
        (self.root / 'usr/local/sbin').mkdir(parents=True)
        with patch.object(h.m, 'check_pending'):
            self.tool.install()
            self.tool.install()
        self.assertTrue((self.root / 'usr/local/sbin/ms1504-hibernate').is_symlink())
        self.assertTrue((self.root / 'usr/local/sbin/ms1504-generation').resolve().is_file())
        self.assertEqual((self.root / 'etc/ms1504-hibernate-ready').read_bytes(), h.READY)
        self.assertFalse((self.root / 'sys/power/resume').exists())
        self.run_mock.assert_not_called()

    def test_installer_refuses_to_replace_custom_regular_entrypoint(self):
        self.tool.cfg = {'system': {'home_lv': 'home', 'swap_lv': 'swap'}}
        target = self.root / 'usr/local/sbin/ms1504-hibernate'
        target.parent.mkdir(parents=True)
        target.write_text('operator command')
        with patch.object(h.m, 'check_pending'), self.assertRaisesRegex(RuntimeError, 'custom regular'):
            self.tool.install()
        self.assertEqual(target.read_text(), 'operator command')
        self.assertFalse((self.root / 'etc/ms1504-hibernate-ready').exists())

    def test_generation_mutations_reject_pending_marker_and_untracked_image(self):
        original_path = Path
        def paths(value):
            return self.tool.marker if str(value) == '/home/.ms1504-hibernate/hibernation-pending' else original_path(value)
        cfg = {'vg': 'vg', 'system': {'swap_lv': 'swap'}}
        with patch.object(h.m, 'Path', side_effect=paths):
            self.run_mock.return_value = b'swsuspend\n'
            with self.assertRaisesRegex(RuntimeError, 'image'):
                h.m.hibernation_guard(cfg)
            self.run_mock.return_value = b'swap\n'
            h.m.hibernation_guard(cfg)
            self.tool.pre()
            with self.assertRaisesRegex(RuntimeError, 'pending hibernation'):
                h.m.hibernation_guard(cfg)

    def test_rollback_entries_disable_older_unguarded_resume_paths(self):
        cfg = {'vg': 'vg', 'policy': 'unsigned',
               'system': {'root_lv': 'root', 'home_lv': 'home', 'swap_lv': 'swap'}}
        menu = h.m.make_menu(cfg, 'desktop-3', [{'old': 'desktop-2', 'snapshot': 'root_rollback_desktop-2'}],
                             base_cmdline=['resume=/dev/mapper/vg-swap', 'noresume', 'ro']).decode()
        kernels = [line.strip() for line in menu.splitlines() if line.strip().startswith('linux ')]
        self.assertNotIn('noresume', kernels[0].split())
        self.assertIn('noresume', kernels[1].split())


class Context(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = {'vg': 'vg', 'state_dir': self.root / 'state',
                    'system': {'root_lv': 'root', 'home_lv': 'home', 'swap_lv': 'swap'}}
        self.tool = h.Hibernation(self.cfg, self.root)
        for name, data in {
            'etc/ms1504-generation': 'desktop-3\n',
            'proc/sys/kernel/random/boot_id': '00000000-0000-0000-0000-000000000099\n',
            'proc/swaps': 'Filename Type Size Used Priority\n/dev/vg/swap partition 999999 0 -2\n',
            'sys/power/resume': '253:1\n', 'sys/power/resume_offset': '0\n',
            'etc/ms1504-hibernate-ready': h.READY.decode(),
            'etc/elogind/sleep.conf.d/90-ms1504-hibernate.conf': (GENTOO / 'hibernate-sleep.conf').read_text(),
        }.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data)
        self.stat_original = os.stat
        def device_stat(path, *args, **kwargs):
            devices = {'/dev/vg/root': 0, '/dev/vg/swap': 1, '/dev/vg/home': 2, '/dev/other': 3}
            if str(path) in devices:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(253, devices[str(path)]))
            return self.stat_original(path, *args, **kwargs)
        def run(args):
            if args[0] == 'findmnt': return b'ext4\n' if args[-1] == 'FSTYPE' else b'/dev/vg/home\n'
            if args[args.index('-s')+1] == 'TYPE': return b'swap\n'
            return b'00000000-0000-0000-0000-000000000001\n'
        for obj, key, value in [(h.m, 'root_is_live', None), (h.m, 'read_root_identity', {}),
                                 (h.m, 'command_verify_running', None), (h.m, 'protected_path', None)]:
            p = patch.object(obj, key, return_value=value)
            p.start(); self.addCleanup(p.stop)
        for obj, key, fn in [(h.os, 'stat', device_stat), (h.m, 'run', run)]:
            p = patch.object(obj, key, side_effect=fn)
            p.start(); self.addCleanup(p.stop)

    def test_recorded_swap_and_generation_are_accepted(self):
        self.assertEqual(self.tool.context()['generation'], 'desktop-3')

    def test_pending_update_is_rejected(self):
        (self.root / 'state/pending').mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, 'pending root update'):
            self.tool.context()

    def test_wrong_runtime_resume_device_and_offset_are_rejected(self):
        (self.root / 'sys/power/resume').write_text('0:0\n')
        with self.assertRaisesRegex(RuntimeError, 'runtime resume'):
            self.tool.context()
        (self.root / 'sys/power/resume').write_text('253:1\n')
        (self.root / 'sys/power/resume_offset').write_text('1\n')
        with self.assertRaisesRegex(RuntimeError, 'zero resume offset'):
            self.tool.context()

    def test_inactive_recorded_swap_is_rejected(self):
        (self.root / 'proc/swaps').write_text('Filename Type Size Used Priority\n/dev/other partition 999999 0 -2\n')
        with self.assertRaisesRegex(RuntimeError, 'not active'):
            self.tool.context()

    def test_wrong_home_and_image_signatures_are_rejected(self):
        with patch.object(h.m, 'run', return_value=b'/dev/other\n'), patch.object(self.tool, 'ordinary_swap'):
            with self.assertRaisesRegex(RuntimeError, 'persistent /home'):
                self.tool.context()
        with patch.object(self.tool, 'probe', return_value='swsuspend'):
            with self.assertRaisesRegex(RuntimeError, 'image'):
                self.tool.context()

    def test_first_installation_cannot_enable_an_already_available_sleep_path(self):
        (self.root / 'etc/ms1504-hibernate-ready').unlink()
        with self.assertRaisesRegex(RuntimeError, 'first installation'):
            self.tool.context(installing=True)
        (self.root / 'sys/power/resume').write_text('0:0\n')
        self.assertEqual(self.tool.context(installing=True)['generation'], 'desktop-3')

    def test_unreviewed_home_filesystem_is_rejected(self):
        original = h.m.run.side_effect
        def run(args):
            return b'btrfs\n' if args[0] == 'findmnt' and args[-1] == 'FSTYPE' else original(args)
        with patch.object(h.m, 'run', side_effect=run), self.assertRaisesRegex(RuntimeError, 'ext4 marker'):
            self.tool.context()


class Initramfs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for path in ['power', 'blocks/dm-1', 'mnt/.ms1504-hibernate']:
            (self.root / path).mkdir(parents=True)
        (self.root / 'power/resume').write_text('0:0\n')
        (self.root / 'power/resume_offset').write_text('0\n')
        (self.root / 'blocks/dm-1/dev').write_text('253:1\n')
        self.cmdline = 'root=/dev/vg/root rd.lvm.lv=vg/root rd.lvm.lv=vg/home rd.lvm.lv=vg/swap resume=/dev/mapper/vg-swap ms1504.generation=desktop-3'
        self.marker = self.root / 'mnt/.ms1504-hibernate/hibernation-pending'
        self.fields = dict(format='1', generation='desktop-3', kernel_release='6.12.111',
                           root_uuid='root', home_uuid='home', swap_uuid='swap')

    def run_guard(self, signature='swap', ready=True, marker=True, commands=''):
        (self.root / 'cmdline').write_text(self.cmdline)
        if ready:
            (self.root / 'ready').write_text('generation-aware-v1\n')
        if marker:
            self.marker.write_text(''.join(f'{k}={v}\n' for k, v in self.fields.items()))
        script = f'''
. {shlex.quote(str(GENTOO / 'dracut-ms1504-resume-guard/resume-lib.sh'))}
cd {shlex.quote(str(self.root))}
ms1504_cmdline="$PWD/cmdline"
ms1504_power="$PWD/power"
ms1504_blocks="$PWD/blocks"
ms1504_mount="$PWD/mnt"
ms1504_ready="$PWD/ready"
ms1504_stock_rules="$PWD/stock-rules"
ms1504_stock_resume="$PWD/stock-resume"
ms1504_stock_parse="$PWD/stock-parse"
signature={shlex.quote(signature)}
[() {{ if builtin [ "$1" = -b ]; then return 0; else builtin [ "$@"; fi; }}
readlink() {{ echo /dev/dm-1; }}
uname() {{ echo 6.12.111; }}
stat() {{ case "$3" in *hibernation-pending) echo 0:600 ;; *) echo 0:700 ;; esac; }}
blkid() {{
    case "$*" in
        *'-s TYPE'*) echo "$signature" ;;
        *'/dev/vg/root') echo root ;;
        *'/dev/vg/home') echo home ;;
        *'/dev/vg/swap') echo swap ;;
        *) return 1 ;;
    esac
}}
mount() {{ echo mount >> calls; }}
umount() {{ [ "$(cat power/resume)" = 0:0 ] || exit 99; echo umount >> calls; }}
{commands}
ms1504_resume
'''
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=10)

    def assert_not_resumed(self, result):
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'power/resume').read_text().strip(), '0:0')

    def test_normal_boot_sets_runtime_resume_without_mounting_home(self):
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'power/resume').read_text().strip(), '253:1')
        self.assertFalse((self.root / 'calls').exists())

    def test_base_without_installed_lifecycle_does_not_enable_hibernation(self):
        self.assertEqual(self.run_guard(ready=False).returncode, 0)
        self.assertEqual((self.root / 'power/resume').read_text().strip(), '0:0')

    def test_recovery_noresume_never_restores_image(self):
        self.cmdline += ' noresume'
        self.assert_not_resumed(self.run_guard(signature='swsuspend'))

    def test_recovery_normal_boot_leaves_hibernation_unconfigured(self):
        self.cmdline += ' noresume'
        self.assertEqual(self.run_guard().returncode, 0)
        self.assertEqual((self.root / 'power/resume').read_text().strip(), '0:0')

    def test_operator_disable_arguments_are_respected(self):
        original = self.cmdline
        for argument in ('noresume=1', 'nohibernate', 'hibernate=no', 'hibernate=noresume'):
            with self.subTest(argument=argument):
                self.cmdline = original + ' ' + argument
                self.assertEqual(self.run_guard().returncode, 0)
                self.assertEqual((self.root / 'power/resume').read_text().strip(), '0:0')

    def test_valid_image_is_checked_and_unmounted_before_resume_and_failed_restore_halts(self):
        result = self.run_guard(signature='swsuspend')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'power/resume').read_text().strip(), '253:1')
        self.assertEqual((self.root / 'calls').read_text(), 'mount\numount\n')

    def test_untracked_image_never_resumes(self):
        self.assert_not_resumed(self.run_guard(signature='swsuspend', marker=False))

    def test_mismatched_generation_kernel_uuid_and_format_never_resume(self):
        for key in self.fields:
            with self.subTest(key=key):
                saved = self.fields[key]
                self.fields[key] = 'wrong'
                self.assert_not_resumed(self.run_guard(signature='swsuspend'))
                self.fields[key] = saved

    def test_competing_stock_path_never_resumes(self):
        self.assert_not_resumed(self.run_guard(commands='touch stock-rules'))

    def test_probe_failure_and_unknown_signature_never_resume(self):
        self.assert_not_resumed(self.run_guard(signature='unknown'))
        self.assert_not_resumed(self.run_guard(signature='swsuspend', commands='blkid() { return 1; }'))

    def test_duplicate_generation_and_unmount_failure_never_resume(self):
        self.cmdline += ' ms1504.generation=desktop-3'
        self.assert_not_resumed(self.run_guard())
        self.cmdline = self.cmdline.rsplit(' ', 1)[0]
        self.assert_not_resumed(self.run_guard(signature='swsuspend', commands='umount() { return 1; }'))

    def test_writable_marker_is_rejected(self):
        self.assert_not_resumed(self.run_guard(signature='swsuspend', commands='stat() { echo 0:666; }'))

    def test_duplicate_marker_field_is_rejected(self):
        self.assert_not_resumed(self.run_guard(signature='swsuspend', commands="echo generation=desktop-3 >> mnt/.ms1504-hibernate/hibernation-pending"))

    def test_symlink_marker_is_rejected(self):
        self.assert_not_resumed(self.run_guard(signature='swsuspend', commands='mv mnt/.ms1504-hibernate/hibernation-pending actual-marker; ln -s "$PWD/actual-marker" mnt/.ms1504-hibernate/hibernation-pending'))

    def test_module_install_rejects_stock_resume(self):
        script = '. ' + shlex.quote(str(GENTOO / 'dracut-ms1504-resume-guard/module-setup.sh')) + '\ndracutmodules=resume\ndfatal() { :; }\ninstall\n'
        self.assertNotEqual(subprocess.run(['bash', '-c', script], capture_output=True).returncode, 0)

    def test_hook_leaves_suspend_to_ram_alone_and_rejects_other_operations(self):
        hook = GENTOO / 'hibernate-hook.sh'
        self.assertEqual(subprocess.run(['sh', str(hook), 'pre', 'suspend']).returncode, 0)
        self.assertNotEqual(subprocess.run(['sh', str(hook), 'pre', 'hybrid-sleep'], capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
