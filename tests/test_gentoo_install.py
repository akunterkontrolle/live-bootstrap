import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


class InstallStorageTests(unittest.TestCase):
    def test_partition_path_handles_device_number_suffixes(self):
        script = f'. {GENTOO / "lib/storage.sh"}; gentoo_partition_path /dev/sda 1; gentoo_partition_path /dev/nvme0n1 2; gentoo_partition_path /dev/mmcblk0 3'
        result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['/dev/sda1', '/dev/nvme0n1p2', '/dev/mmcblk0p3'])

    def test_resume_gate_requires_all_destructive_phase_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            script = f'. {GENTOO / "lib/storage.sh"}; gentoo_storage_resume_gate {state}'
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            for name in ('storage.started', 'target.identity', 'partition.done', 'luks.done'):
                (state / name).write_text('verified\n')
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_atomic_receipt_writer_accepts_dotted_phase_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = f'. {GENTOO / "lib/storage.sh"}; printf "phase data\\n" | gentoo_storage_receipt {temporary} target.identity'
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((Path(temporary) / 'target.identity').read_text(), 'phase data\n')

    def test_installer_keeps_destructive_operations_behind_explicit_mode(self):
        script = (GENTOO / 'install-base.sh').read_text()
        self.assertLess(script.index('Type the exact line above'), script.index('sgdisk --zap-all'))
        self.assertIn('[ "$MODE" = --initialize-storage ]', script)
        self.assertIn('gentoo_storage_resume_gate "$STATE"', script)
        self.assertIn('--exclude=/dev/***', script)
        self.assertIn('root-install.done', script)
        self.assertIn('installed-boot.receipt', script)
        result = subprocess.run(['bash', '-n', str(GENTOO / 'install-base.sh')], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
