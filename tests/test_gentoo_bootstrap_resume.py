"""A verified root entry restores the workspace without depending on its mount."""
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'


class BootstrapResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'work'
        self.source = self.work / 'inputs/pending'
        (self.source / 'lib').mkdir(parents=True)
        for name in ('lib/common.sh', 'lib/hardware-discovery.sh'):
            shutil.copyfile(GENTOO / name, self.source / name)
        # Mount operations are fixture observations; all publication, digest,
        # bundle and handoff verification executes the ordinary runtime code.
        events = shlex.quote(str(self.root / 'events'))
        (self.source / 'lib/mounts.sh').write_text('gentoo_prepare_virtual_filesystems() { echo mounts >> ' + events + '; }\n')
        (self.source / 'lib/devices.sh').write_text(
            'gentoo_mount_workspace() { echo "workspace $GENTOO_WORKSPACE_UUID" >> ' + events + '; }\n')
        (self.source / 'config.sh').write_text('GENTOO_WORKSPACE_UUID=wrong-config-default\n')
        (self.source / 'post-reboot.sh').write_text('printf "RESUMED\\n"\n')
        launcher = (GENTOO / 'bootstrap-resume.sh').read_text().replace('/build/gentoo-bootstrap', str(self.work))
        (self.source / 'bootstrap-resume.sh').write_text(launcher)
        rows = ''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + str(p.relative_to(self.source)) + '\n'
                       for p in sorted(self.source.rglob('*')) if p.is_file())
        (self.source / 'SHA256SUMS').write_text(rows)
        self.digest = hashlib.sha256(rows.encode()).hexdigest()
        destination = self.source.with_name(self.digest)
        self.source.rename(destination)
        self.source = destination
        self.handoff = self.work / 'handoff' / self.digest
        self.handoff.mkdir(parents=True)
        shutil.copyfile(self.source / 'post-reboot.sh', self.handoff / 'resume.sh')
        self.bootstrap = self.root / 'bootstrap-root'
        fake = self.root / 'bin'
        fake.mkdir()
        (fake / 'id').write_text('#!/bin/sh\necho 0\n')
        (fake / 'id').chmod(0o755)
        self.env = os.environ | {'PATH': str(fake) + ':' + os.defpath}
        self.entry = self.bootstrap / 'etc/gentoo-bootstrap-resume' / self.digest / 'bootstrap-resume.sh'

    def publish(self):
        script = ('set -Eeuo pipefail\n. ' + shlex.quote(str(GENTOO / 'lib/common.sh')) + '\n. '
                  + shlex.quote(str(GENTOO / 'lib/handoff.sh')) + '\nSCRIPT_DIR=' + shlex.quote(str(self.source))
                  + '\ninput_digest=' + self.digest + '\nGENTOO_DISCOVERED_WORKSPACE_UUID=22222222-2222-4222-8222-222222222222\n'
                  + 'gentoo_publish_bootstrap_resume ' + shlex.quote(str(self.bootstrap)))
        return subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=5)

    def resume(self):
        return subprocess.run(['bash', str(self.entry)], env=self.env, text=True, capture_output=True, timeout=3)

    def test_independent_root_entry_uses_recorded_uuid_and_exact_snapshot_after_repeat(self):
        for _ in range(2):
            result = self.publish()
            self.assertEqual(result.returncode, 0, result.stderr)
        link = self.bootstrap / 'usr/local/sbin/gentoo-resume'
        self.assertEqual(os.readlink(link), '/etc/gentoo-bootstrap-resume/' + self.digest + '/bootstrap-resume.sh')
        # No current-inputs or current-handoff pointer is needed to locate it.
        result = self.resume()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('RESUMED', result.stdout)
        self.assertEqual((self.root / 'events').read_text().splitlines(),
                         ['mounts', 'workspace 22222222-2222-4222-8222-222222222222'])

    def test_changed_root_helpers_refuse_before_mounts_and_are_not_replaced(self):
        self.assertEqual(self.publish().returncode, 0)
        helper = self.entry.parent / 'lib/mounts.sh'
        helper.write_text('unexpected root code\n')
        self.assertNotEqual(self.resume().returncode, 0)
        self.assertFalse((self.root / 'events').exists())
        self.assertNotEqual(self.publish().returncode, 0)
        self.assertEqual(helper.read_text(), 'unexpected root code\n')

    def test_changed_durable_inputs_or_handoff_cannot_execute(self):
        self.assertEqual(self.publish().returncode, 0)
        original = (self.source / 'config.sh').read_bytes()
        (self.source / 'config.sh').write_text('unexpected snapshot code\n')
        result = self.resume()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('RESUMED', result.stdout)
        (self.source / 'config.sh').write_bytes(original)
        (self.handoff / 'resume.sh').write_text('echo UNVERIFIED_EXECUTION\n')
        result = self.resume()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('UNVERIFIED_EXECUTION', result.stdout)
