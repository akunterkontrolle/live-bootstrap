"""Verify and activate code releases without touching operator policy or build journals."""
import base64
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
spec = importlib.util.spec_from_file_location('installed_update', GENTOO / 'installed-update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class InstalledUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prefix = self.root / 'share'
        self.sbin = self.root / 'sbin'
        self.sbin.mkdir()
        self.active = self.prefix / 'gentoo'
        (self.active / 'sources').mkdir(parents=True)
        (self.active / 'policy').mkdir()
        self.config = b"export GENTOO_MODE=hardware\nexport GENTOO_PACKAGES_DESKTOP_USE=policy/custom\nexport GENTOO_BUILD_SOURCE_JOBS=6\n"
        (self.active / 'config.sh').write_bytes(self.config)
        (self.active / 'config.sh').chmod(0o644)
        (self.active / 'policy/custom').write_bytes(b'operator custom policy\n')
        (self.active / 'policy/custom').chmod(0o600)
        (self.active / 'setup-extra.sh').write_text('#!/bin/bash\n')
        (self.active / 'sources/gentoo-repository.lock').write_bytes(b'unchanged source lock\n')
        for entry in ('ms1504-extra', 'complete-desktop'):
            (self.sbin / entry).symlink_to(self.active / ('setup-extra.sh' if entry == 'ms1504-extra' else 'complete-desktop.sh'))
        self.data = {name: b'# reviewed source fixture\n' for name in update.REQUIRED}
        self.data['sources/gentoo-repository.lock'] = b'unchanged source lock\n'
        self.archive = self.root / 'update.tar.gz'
        self.pack()
        for name, value in {'PREFIX': self.prefix, 'SBIN': self.sbin, 'STATE': self.root / 'state',
                            'EXTRA_LOCK': self.root / 'extra/lock', 'DESKTOP_LOCK': self.root / 'run/desktop.lock',
                            'DESKTOP_STATE': self.root / 'desktop'}.items():
            patch = mock.patch.object(update, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(update.os, 'geteuid', return_value=0)
        patch.start()
        self.addCleanup(patch.stop)
        # Simulate root ownership only; production archive validation, locking,
        # release contents, policy preservation and atomic entrypoint changes run.
        def current_fixture():
            return (self.sbin / 'ms1504-extra').resolve().parent
        patch = mock.patch.object(update, 'current', side_effect=current_fixture)
        patch.start()
        self.addCleanup(patch.stop)

    def pack(self, extra=None, corrupt=False):
        manifest = {'format': 1, 'source_commit': 'a' * 40,
                    'files': {n: update.digest(v) for n, v in self.data.items()}}
        values = {'release.json': update.encode(manifest), **{'system/gentoo/' + n: v for n, v in self.data.items()}}
        if corrupt:
            values['system/gentoo/setup-extra.sh'] += b'# altered\n'
        if extra:
            values.update(extra)
        with tarfile.open(self.archive, 'w:gz') as packed:
            for name, value in values.items():
                info = tarfile.TarInfo(name)
                info.size = len(value)
                packed.addfile(info, io.BytesIO(value))

    def test_activation_preserves_policy_and_journals_and_verifies_active_code(self):
        update.activate(self.archive, True)
        destination = (self.sbin / 'ms1504-extra').resolve().parent
        self.assertNotEqual(destination, self.active)
        self.assertEqual((destination / 'config.sh').read_bytes(), self.config)
        self.assertEqual((destination / 'policy/custom').read_bytes(), b'operator custom policy\n')
        self.assertEqual((destination / 'policy/custom').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.active / 'config.sh').read_bytes(), self.config)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o755)
        update.verify_active()
        history = (update.STATE / (destination.name + '.json')).read_bytes()
        update.activate(self.archive, True)
        self.assertEqual((update.STATE / (destination.name + '.json')).read_bytes(), history)
        self.assertFalse((update.STATE / 'pending.json').exists())

    def test_interrupted_entrypoint_activation_resumes_same_bundle(self):
        replace = os.replace
        failed = False
        def interrupted(source, destination):
            nonlocal failed
            if Path(destination) == self.sbin / 'complete-desktop' and not failed:
                failed = True
                raise OSError('simulated activation interruption')
            return replace(source, destination)
        with mock.patch.object(update.os, 'replace', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interruption'):
                update.activate(self.archive, True)
        self.assertTrue((update.STATE / 'pending.json').exists())
        update.activate(self.archive, True)
        update.verify_active()
        self.assertFalse((update.STATE / 'pending.json').exists())
        self.assertEqual((self.active / 'config.sh').read_bytes(), self.config)

    def test_plan_does_not_activate_or_write_state(self):
        update.activate(self.archive)
        self.assertFalse(update.STATE.exists())
        self.assertEqual((self.sbin / 'ms1504-extra').resolve().parent, self.active)

    def test_tampering_unsafe_archive_and_repository_upgrade_refuse(self):
        for options in ({'corrupt': True}, {'extra': {'../escape': b'bad'}}):
            self.pack(**options)
            with self.assertRaises(ValueError):
                update.activate(self.archive, True)
        self.data['sources/gentoo-repository.lock'] = b'different repository\n'
        self.pack()
        with self.assertRaisesRegex(ValueError, 'repository snapshot changed'):
            update.activate(self.archive, True)
        self.assertEqual((self.sbin / 'ms1504-extra').resolve().parent, self.active)

    def test_active_operation_and_started_desktop_refuse(self):
        update.EXTRA_LOCK.parent.mkdir()
        with update.EXTRA_LOCK.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'operation is running'):
                update.activate(self.archive, True)
        update.DESKTOP_STATE.mkdir()
        (update.DESKTOP_STATE / 'desktop-plan.done').write_text('keep receipt\n')
        with self.assertRaisesRegex(ValueError, 'desktop has started'):
            update.activate(self.archive, True)
        self.assertEqual((update.DESKTOP_STATE / 'desktop-plan.done').read_text(), 'keep receipt\n')

    def test_verification_detects_changed_active_code(self):
        update.activate(self.archive, True)
        destination = (self.sbin / 'ms1504-extra').resolve().parent
        (destination / 'setup-extra.sh').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'deployed file changed'):
            update.verify_active()
