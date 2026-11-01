"""Opt-in real device-mapper test: only a newly allocated file-backed loop device.

On a Linux test host/T440p with LVM2, util-linux and e2fsprogs:
  sudo env MS1504_TEST_LVM=1 python3 /path/to/gentoo/probes/generation-lvm.py -v
No installation PV/VG is accepted as an input. The private VG is removed afterward.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

GENTOO = Path(__file__).resolve().parents[1]
spec = spec_from_file_location('generation_lvm', GENTOO / 'signed-generation.py')
m = module_from_spec(spec)
spec.loader.exec_module(m)


def run(args):
    # Keep numeric reports machine-readable and warnings out of stdout.
    try:
        return subprocess.check_output(args, stdin=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, timeout=90,
                                       env={**os.environ, "LC_ALL": "C"})
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = (exc.stderr or b"").decode(errors="replace").strip()
        raise RuntimeError(f"probe command failed: {' '.join(args)}\n{detail or str(exc)}") from exc


@unittest.skipUnless(os.environ.get('MS1504_TEST_LVM') == '1', 'opt-in disposable LVM test; MS1504_TEST_LVM=1 required')
class RealSnapshotLifecycle(unittest.TestCase):
    def test_file_backed_snapshot_preserves_data_and_merges_offline(self):
        self.assertEqual(os.geteuid(), 0, 'requires root and loop/mount/device-mapper access')
        for command in ('losetup', 'pvcreate', 'vgcreate', 'lvcreate', 'lvs', 'lvchange', 'lvconvert',
                        'vgremove', 'pvremove', 'mkfs.ext4', 'mount', 'umount'):
            self.assertIsNotNone(shutil.which(command), command + ' is missing')
        with tempfile.TemporaryDirectory(prefix='ms1504-lvm-') as tmp:
            directory = Path(tmp)
            image = directory / 'disposable.img'
            with image.open('xb') as stream:
                stream.truncate(4 * 1024**3)
            loop = run(['losetup', '--find', '--show', str(image)]).decode().strip()
            vg = 'ms1504_test_' + os.urandom(6).hex()
            root = directory / 'root'
            snap = directory / 'snapshot'
            root.mkdir()
            snap.mkdir()
            created = False
            def lvm(args, **kwargs):
                # Restrict every LVM operation to this loop device, including cleanup.
                return run([args[0], '--devices', loop, *args[1:]])
            try:
                listing = json.loads(run(['losetup', '--json', '--output', 'NAME,BACK-FILE', loop]))['loopdevices']
                self.assertEqual(len(listing), 1)
                self.assertEqual(Path(listing[0]['back-file']).resolve(), image.resolve())
                lvm(['pvcreate', '--yes', loop])
                lvm(['vgcreate', vg, loop])
                created = True
                # Keep origin larger than its 1 GiB COW request: LVM caps
                # oversized snapshot allocations at origin size plus metadata.
                lvm(['lvcreate', '--size', '2G', '--name', 'root', vg])
                origin = '/dev/' + vg + '/root'
                snapshot = '/dev/' + vg + '/root_rollback_base'
                run(['mkfs.ext4', '-F', origin])
                run(['mount', origin, str(root)])
                (root / 'checkpoint').write_bytes(b'before snapshot')
                os.sync()
                cfg = {'vg': vg, 'root': origin, 'system': {'root_lv': 'root'}}
                with patch.object(m, 'run', side_effect=lvm):
                    m.create_snapshot(cfg, 'root_rollback_base', 1)
                    percent, size = m.snapshot_health(cfg, 'root_rollback_base')
                self.assertLess(percent, 80)
                self.assertGreaterEqual(size, 1024**3)
                (root / 'checkpoint').write_bytes(b'after snapshot')
                os.sync()
                run(['mount', '-o', 'ro,noload', snapshot, str(snap)])
                self.assertEqual((snap / 'checkpoint').read_bytes(), b'before snapshot')
                run(['umount', str(snap)])
                run(['umount', str(root)])
                # Origin deactivation also deactivates its classic snapshots.
                lvm(['lvchange', '--activate', 'n', origin])
                lvm(['lvconvert', '--merge', snapshot])
                lvm(['lvchange', '--activate', 'y', origin])
                with patch.object(m, 'run', side_effect=lvm):
                    m.wait_snapshot_merge(cfg, 'root_rollback_base', '', timeout=30)
                    self.assertNotIn('root_rollback_base', m.actual_root_snapshots(cfg))
                self.assertFalse(Path(snapshot).exists(), 'snapshot merge did not finish')
                run(['mount', origin, str(root)])
                self.assertEqual((root / 'checkpoint').read_bytes(), b'before snapshot')
            finally:
                for mount in (snap, root):
                    if os.path.ismount(mount):
                        run(['umount', str(mount)])
                # Any failed cleanup stops before detaching/reusing the loop device.
                if created:
                    lvm(['vgremove', '--force', '--yes', vg])
                lvm(['pvremove', '--yes', loop])
                run(['losetup', '--detach', loop])


if __name__ == "__main__":
    unittest.main()
