"""Repository identity follows its lock and base/desktop selection is explicit."""
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'


class RepositoryPolicyTests(unittest.TestCase):
    def run_policy(self, lock):
        script = ('. ' + shlex.quote(str(GENTOO / 'lib/repository-policy.sh'))
                  + '\ngentoo_repository_commit ' + shlex.quote(str(lock)))
        return subprocess.run(['bash', '-c', script], text=True, capture_output=True)

    def test_commit_changes_with_the_unique_locked_archive_name(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'repository.lock'
            for commit in ('a' * 40, 'b' * 40):
                lock.write_text('# reviewed pin\ngentoo-' + commit + '.tar.gz|https://example.invalid/source|sha512=' + '0' * 128 + '\n')
                result = self.run_policy(lock)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, commit + '\n')

    def test_ambiguous_or_malformed_archive_identity_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'repository.lock'
            for content in ('a' * 40 + '|url|hash\n', 'gentoo-short.tar.gz|url|hash\n',
                            ('gentoo-' + 'a' * 40 + '.tar.gz|url|hash\n') * 2):
                lock.write_text(content)
                self.assertNotEqual(self.run_policy(lock).returncode, 0)

    def test_one_multilib_base_profile_and_explicit_desktop_transition(self):
        result = subprocess.run(['bash', '-c', '. ' + shlex.quote(str(GENTOO / 'lib/repository-policy.sh'))
                                 + '; printf "%s\\n" "$GENTOO_BASE_PROFILE" "$GENTOO_DESKTOP_PROFILE"'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['default/linux/amd64/23.0/split-usr',
                                                     'default/linux/amd64/23.0/split-usr/desktop'])
