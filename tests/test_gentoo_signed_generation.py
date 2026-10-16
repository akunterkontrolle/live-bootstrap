"""Focused, host-only checks for the signed generation manager."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
SPEC = spec_from_file_location('signed_generation', GENTOO / 'signed-generation.py')
manager = module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


class SignedGenerationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            'vg': 'gentoo',
            'root': '/dev/gentoo/root',
            'system': {'volume_group': 'gentoo', 'root_lv': 'root', 'home_lv': 'home', 'swap_lv': 'swap'},
            'snapshots': {'size_gib': 32, 'maximum_data_percent': 80, 'retain_generations': 2},
        }

    def test_menu_preserves_home_swap_and_changes_root_for_each_generation(self):
        menu = manager.make_menu(self.cfg, 'new-2', [
            {'old': 'old-1', 'snapshot': 'root_rollback_old-1'},
            {'old': 'base-6.12', 'snapshot': 'root_rollback_base-6.12'},
        ], base_cmdline=['rd.luks.uuid=luks-abc', 'root=/wrong', 'rd.lvm.lv=wrong/root',
                         'rd.lvm.lv=wrong/home', 'rd.lvm.lv=wrong/swap', 'resume=/dev/mapper/gentoo-swap',
                         'ms1504.generation=wrong', 'ro'])
        text = menu.decode()
        self.assertIn('root=/dev/gentoo/root', text)
        self.assertIn('root=/dev/gentoo/root_rollback_old-1', text)
        self.assertEqual(text.count('rd.lvm.lv=gentoo/home'), 3)
        self.assertEqual(text.count('rd.lvm.lv=gentoo/swap'), 3)
        self.assertEqual(text.count('resume=/dev/mapper/gentoo-swap'), 3)
        self.assertIn('ms1504.generation=old-1', text)

    def test_hardware_identity_requires_matching_layout_and_full_reserve(self):
        valid = {'profile': 'hardware', 'volume_group': 'gentoo', 'root_lv': 'root',
                 'home_lv': 'home', 'swap_lv': 'swap', 'reserve_gib': '64'}
        self.assertEqual(manager.validate_root_identity(valid, self.cfg), valid)
        for key, value in [('profile', 'vm'), ('volume_group', 'other'), ('reserve_gib', '63')]:
            altered = valid | {key: value}
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                manager.validate_root_identity(altered, self.cfg)

    def test_receipt_parser_rejects_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'transaction'
            path.write_text('old=base\nold=other\n')
            with self.assertRaisesRegex(RuntimeError, 'duplicate'):
                manager.parse_fields(path)

    def test_snapshot_creation_always_thaws_after_lvcreate_failure(self):
        with patch.object(manager, 'run', side_effect=[b'', RuntimeError('lvcreate failed'), b'']) as run:
            with self.assertRaisesRegex(RuntimeError, 'lvcreate failed'):
                manager.create_snapshot(self.cfg, 'root_rollback_base', 32)
        self.assertEqual(run.call_args_list[0].args[0], ['fsfreeze', '-f', '/'])
        self.assertEqual(run.call_args_list[1].args[0][0], 'lvcreate')
        self.assertEqual(run.call_args_list[2].args[0], ['fsfreeze', '-u', '/'])

    def test_batch_scripts_parse_and_flash_has_explicit_gates(self):
        for path in [GENTOO / 'install-signed-generation-tools.sh', GENTOO / 'export-boot-signing-key.sh',
                     GENTOO / 'harden-libreboot-payload.sh', GENTOO / 'firmware-build-receipt.sh',
                     GENTOO / 'firmware-flash.sh', GENTOO / 'dracut-ms1504-resume-guard/module-setup.sh',
                     GENTOO / 'dracut-ms1504-resume-guard/check-generation.sh']:
            shell = '/bin/sh' if path.suffix == '.sh' and path.name == 'check-generation.sh' else 'bash'
            with self.subTest(path=path.name):
                result = subprocess.run([shell, '-n', str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        flash = (GENTOO / 'firmware-flash.sh').read_text()
        self.assertIn('flashrom -p internal -r', flash)
        self.assertIn('flashrom -p internal -w "$IMAGE"', flash)
        self.assertIn('cmp -s "$BACKUP_DIR/readback.bin" "$IMAGE"', flash)
        self.assertIn('FLASH T440P', flash)
        self.assertNotIn('--force', flash)
        self.assertNotIn('-p internal:ichspi', flash)


if __name__ == '__main__':
    unittest.main()
