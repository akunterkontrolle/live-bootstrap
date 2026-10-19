"""The saved cold-boot handoff restores networking before entering the driver."""
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'


class RebootNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.events = self.directory / 'events'
        work = self.directory / 'work'
        digest = 'a' * 64
        handoff = work / 'handoff' / digest
        handoff.mkdir(parents=True)
        self.resume = handoff / 'resume.sh'
        shutil.copyfile(GENTOO / 'post-reboot.sh', self.resume)
        common = work / 'inputs' / digest / 'lib/common.sh'
        common.parent.mkdir(parents=True)
        shutil.copyfile(GENTOO / 'lib/common.sh', common)
        shutil.copyfile(GENTOO / 'lib/mounts.sh', common.with_name('mounts.sh'))
        fake = self.directory / 'bin'
        fake.mkdir()
        commands = {
            'id': 'printf "0\\n"\n',
            'uname': 'case "$1" in -r) printf "6.12.111\\n";; -m) printf "x86_64\\n";; *) exit 2;; esac\n',
            'mountpoint': 'printf "mountpoint %s\\n" "$*" >> "$NETWORK_EVENTS"\n',
            'mkdir': 'printf "mkdir %s\\n" "$*" >> "$NETWORK_EVENTS"\n',
            # Descriptor-link setup also uses Python before networking.
            'python3': '[ "$1" != - ] || exit 0\nprintf "helper %s\\n" "$*" >> "$NETWORK_EVENTS"\nexit "${DHCP_RESULT:-0}"\n',
            'bash': 'printf "driver %s\\n" "$*" >> "$NETWORK_EVENTS"\n',
        }
        for name, body in commands.items():
            command = fake / name
            command.write_text('#!/bin/sh\n' + body)
            command.chmod(0o755)
        self.env = dict(os.environ, PATH=str(fake) + ':' + os.defpath,
                        NETWORK_EVENTS=str(self.events), GENTOO_CONSOLE_SESSION='1')

    def run_resume(self, result=0):
        env = dict(self.env, DHCP_RESULT=str(result))
        return subprocess.run(['/bin/bash', str(self.resume)], env=env, text=True, capture_output=True)

    def test_restores_network_then_runs_exact_snapshot(self):
        result = self.run_resume()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self.events.read_text().splitlines()
        network = next(i for i, line in enumerate(events) if line.startswith('helper '))
        driver = next(i for i, line in enumerate(events) if line.startswith('driver '))
        self.assertLess(network, driver)
        self.assertTrue(events[driver].endswith('/inputs/' + 'a' * 64 + '/gentoo.sh build'))
        self.assertTrue(events[network].endswith('/inputs/' + 'a' * 64 + '/bootstrap-network.py'))

    def test_dhcp_failure_stops_download_driver_and_can_be_retried(self):
        result = self.run_resume(result=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('network setup failed', result.stderr)
        self.assertNotIn('driver ', self.events.read_text())
        result = self.run_resume()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('driver ', self.events.read_text())

    def test_enters_console_session_before_network_or_build(self):
        self.env['GENTOO_CONSOLE_SESSION'] = '0'
        result = self.run_resume()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self.events.read_text()
        self.assertIn('/console-session.py bash ' + str(self.resume), events)
        self.assertNotIn('/bootstrap-network.py', events)
        self.assertNotIn('driver ', events)


if __name__ == '__main__':
    unittest.main()
