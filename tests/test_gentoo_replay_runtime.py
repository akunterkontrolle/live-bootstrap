"""Behavioral regressions for the ordinary Gentoo replay path.

These fixtures execute maintained recipes and helpers, independently of the
historical snapshot repair generators and paste payloads.
"""
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

network = load('replay_network', GENTOO / 'bootstrap-network.py')
permissions = load('replay_permissions', GENTOO / 'probes/native-permissions.py')
retirement = load('replay_nscd', GENTOO / 'retire-bootstrap-nscd.py')
HELPER = (GENTOO / 'lib/perl-locale.sh').read_text()
PROGRAM = HELPER.split("<<'PERL'\n", 1)[1].split('\nPERL\n', 1)[0]
FULL = '# Temporary providers for the source-built Gentoo bootstrap only.\nsys-libs/glibc-2.44\ndev-lang/perl-5.42.2\n'
PERL = '# Temporary source-built Perl provider for @system dependency ordering.\ndev-lang/perl-5.42.2\n'

class NetworkTests(unittest.TestCase):
    def test_existing_address_and_route_does_not_invoke_dhcpcd(self):
        with patch.object(network, 'ipv4_ready', return_value=True), patch.object(network, 'request_dhcp') as request:
            self.assertEqual(network.main(), 0)
            request.assert_not_called()

    def test_real_blocked_request_has_external_deadline_and_parent_survives(self):
        start = time.monotonic()
        self.assertEqual(network.request_dhcp([sys.executable, '-c', 'import time; time.sleep(120)'], seconds=0.15), 124)
        self.assertLess(time.monotonic() - start, 3)

    def test_request_exit_status_preserved(self):
        self.assertEqual(network.request_dhcp([sys.executable, '-c', 'raise SystemExit(7)']), 7)


class NativePermissionsTests(unittest.TestCase):
    def test_public_modes_repaired_private_files_and_external_symlinks_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'native-root-x86_64'
            root.mkdir(mode=0o700)
            for name in ('usr/bin', 'lib64', 'etc/portage', 'etc/ssl/private', 'root', 'host-build', 'var/log'):
                (root / name).mkdir(parents=True, mode=0o700)
            for directory in root.rglob('*'):
                if directory.is_dir():
                    directory.chmod(0o700)
            files = ('usr/bin/runtime', 'etc/portage/make.conf', 'etc/shadow',
                     'etc/ssl/private/key', 'root/key', 'host-build/config.sh')
            for name in files:
                (root / name).write_text(name)
                (root / name).chmod(0o600)
            external = Path(tmp) / 'private'
            external.mkdir(mode=0o700)
            (root / 'usr/external').symlink_to(external)
            (root / 'usr/bin/bash').write_text('executable')
            (root / 'usr/bin/bash').chmod(0o700)
            before = {name: (root / name).read_bytes() for name in files}
            journal = root / 'var/log/repair.jsonl'
            changed = permissions.repair_modes(root, journal)
            self.assertIn(str(root), changed)
            for name in ('', 'usr', 'usr/bin', 'lib64', 'etc', 'etc/portage'):
                self.assertEqual((root / name).stat().st_mode & 0o777, 0o755)
            for name in files:
                expected = 0o644 if name in ('usr/bin/runtime', 'etc/portage/make.conf') else 0o600
                self.assertEqual((root / name).stat().st_mode & 0o777, expected)
                self.assertEqual((root / name).read_bytes(), before[name])
            self.assertEqual(external.stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / 'usr/bin/bash').stat().st_mode & 0o777, 0o755)
            self.assertTrue(all(json.loads(line)['before'] in (0o600, 0o700) for line in journal.read_text().splitlines()))
            self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
            self.assertEqual(permissions.repair_modes(root, journal), [])

    def test_public_resolver_files_are_readable_and_shadow_stays_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'native-root-x86_64'
            (root / 'etc').mkdir(parents=True)
            public = ('resolv.conf', 'nsswitch.conf', 'hosts')
            for name in public + ('shadow', 'gshadow'):
                path = root / 'etc' / name
                path.write_bytes(b'original contents')
                path.chmod(0o600)
            permissions.repair_modes(root, root / 'var/log/repair.jsonl')
            for name in public + ('shadow', 'gshadow'):
                path = root / 'etc' / name
                self.assertEqual(path.stat().st_mode & 0o777, 0o644 if name in public else 0o600)
                self.assertEqual(path.read_bytes(), b'original contents')
            self.assertIn('socket.getaddrinfo("distfiles.gentoo.org"', permissions.PROBE)

    def test_batched_journal_is_durable_before_each_mode_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'native-root-x86_64'
            headers = root / 'usr/include'
            headers.mkdir(parents=True)
            for i in range(300):
                path = headers / ('header-%d.h' % i)
                path.write_bytes(b'unchanged source header')
                path.chmod(0o600)
            journal = root / 'var/log/repair.jsonl'
            durable = set()
            sync = os.fsync
            chmod = Path.chmod

            def synced(fd):
                sync(fd)
                durable.update(json.loads(line)['path'] for line in journal.read_text().splitlines())

            def checked_chmod(path, mode, *args, **kwargs):
                self.assertIn(str(path), durable)
                return chmod(path, mode, *args, **kwargs)

            with patch.object(permissions.os, 'fsync', side_effect=synced) as flush, \
                    patch.object(Path, 'chmod', checked_chmod):
                changed = permissions.repair_modes(root, journal)
            self.assertEqual(len(changed), 300)
            self.assertEqual(flush.call_count, 2)
            for path in headers.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                self.assertEqual(path.read_bytes(), b'unchanged source header')

    def test_scratch_probe_writes_and_removes_only_its_unique_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            distfiles = scratch / 'distfiles'
            distfiles.mkdir()
            (scratch / 'portage-tmp').mkdir()
            sentinel = distfiles / 'locked-source.tar'
            sentinel.write_bytes(b'locked source content')
            fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY)
            try:
                permissions.scratch_write_probe(fd)
            finally:
                os.close(fd)
            self.assertEqual(list(distfiles.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_bytes(), b'locked source content')

    def test_scratch_probe_reports_permission_failure(self):
        if os.geteuid() == 0:
            self.skipTest('root bypasses ordinary file mode permission denial')
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            distfiles = scratch / 'distfiles'
            distfiles.mkdir()
            distfiles.chmod(0)
            fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(PermissionError):
                    permissions.scratch_write_probe(fd)
            finally:
                os.close(fd)
                distfiles.chmod(0o755)

    def test_build_parent_denial_is_caught_even_when_distfiles_is_writable(self):
        if os.geteuid() == 0:
            self.skipTest('root bypasses file mode traversal checks')
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            (scratch / 'distfiles').mkdir()
            build = scratch / 'portage-tmp'
            build.mkdir()
            build.chmod(0)
            fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(PermissionError):
                    permissions.scratch_write_probe(fd)
                self.assertEqual(list((scratch / 'distfiles').iterdir()), [])
            finally:
                os.close(fd)
                build.chmod(0o755)


    def test_actual_probe_requires_privilege_drop_and_checks_python_and_repository(self):
        body = (GENTOO / 'probes/native-permissions.py').read_text()
        self.assertIn('os.setgroups([])', body)
        self.assertIn('os.setgid(account.pw_gid)', body)
        self.assertIn('os.setuid(account.pw_uid)', body)
        compile(permissions.PROBE, '<native-user-probe>', 'exec')
        self.assertIn('import portage', permissions.PROBE)
        self.assertIn('/usr/portage/profiles/repo_name', permissions.PROBE)


class NscdRetirementTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.root, self.seed = self.work / 'root', self.work / 'sysroot'
        for tree in self.root, self.seed:
            file = tree / 'usr/sbin/nscd'
            file.parent.mkdir(parents=True)
            file.write_bytes(b'original seed binary')
        self.vdb = self.root / 'var/db/pkg/sys-libs/glibc-2.44-r3/CONTENTS'
        self.vdb.parent.mkdir(parents=True)
        self.vdb.write_text('obj /lib64/libc.so.6 checksum 0\n')
        self.backup = self.work / 'backup'

    def apply(self):
        return retirement.retire(self.root, self.seed, self.backup, apply=True)

    def test_matching_unowned_seed_is_backed_up_and_retired_without_modifying_libc_or_seed(self):
        seed = self.seed / 'usr/sbin/nscd'
        original = seed.read_bytes()
        self.assertEqual(self.apply()['status'], 'retired')
        self.assertFalse((self.root / 'usr/sbin/nscd').exists())
        self.assertEqual((self.backup / 'nscd').read_bytes(), original)
        self.assertEqual(seed.read_bytes(), original)
        self.assertEqual(self.apply()['status'], 'absent')
        self.assertEqual(self.vdb.read_text(), 'obj /lib64/libc.so.6 checksum 0\n')

    def test_package_owned_binary_is_preserved_even_when_matching_seed(self):
        self.vdb.write_text('obj /usr/sbin/nscd checksum 0\n')
        self.assertEqual(self.apply()['status'], 'package-owned')
        self.assertTrue((self.root / 'usr/sbin/nscd').exists())
        self.assertFalse(self.backup.exists())

    def test_unknown_unowned_binary_and_missing_vdb_refuse(self):
        (self.root / 'usr/sbin/nscd').write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'differs from bootstrap'):
            self.apply()
        self.vdb.unlink()
        with self.assertRaisesRegex(ValueError, 'ownership records'):
            self.apply()
        self.assertFalse(self.backup.exists())

    def test_symlink_and_enabled_service_refuse(self):
        (self.root / 'usr/sbin/nscd').unlink()
        (self.root / 'usr/sbin/nscd').symlink_to(self.seed / 'usr/sbin/nscd')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.apply()
        (self.root / 'usr/sbin/nscd').unlink()
        (self.root / 'usr/sbin/nscd').write_bytes(b'original seed binary')
        service = self.root / 'etc/init.d/nscd'
        service.parent.mkdir(parents=True)
        service.write_text('configured daemon')
        with self.assertRaisesRegex(ValueError, 'service configuration'):
            self.apply()

    def test_prepared_backup_allows_retry_after_interruption_before_unlink(self):
        unlink = Path.unlink
        candidate = self.root / 'usr/sbin/nscd'
        def interrupt(path, *a, **kw):
            if path == candidate:
                raise OSError('interrupted')
            return unlink(path, *a, **kw)
        with patch.object(Path, 'unlink', interrupt):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                self.apply()
        self.assertTrue(candidate.exists())
        self.assertTrue((self.backup / 'nscd').exists())
        self.assertEqual(self.apply()['status'], 'retired')

    def test_default_mode_is_read_only_and_wrong_expected_hash_refuses(self):
        self.assertEqual(retirement.retire(self.root, self.seed, self.backup)['status'], 'bootstrap-unowned')
        self.assertTrue((self.root / 'usr/sbin/nscd').exists())
        self.assertFalse(self.backup.exists())
        with self.assertRaisesRegex(ValueError, 'inspected VM hash'):
            retirement.retire(self.root, self.seed, self.backup, expected='wrong', apply=True)


    def test_source_hooks_and_explicit_bundle_and_stage_fingerprint(self):
        self.assertIn('retire-bootstrap-nscd.py', (GENTOO / 'prepare.py').read_text())
        self.assertIn('retire-bootstrap-nscd.py', (GENTOO / 'stages/80-native-gentoo-system.sh').read_text())
        self.assertIn('retire-bootstrap-nscd.py', (GENTOO / 'stages/system/80-verify.sh').read_text())
        installer = (GENTOO / 'install-base.sh').read_text()
        self.assertLess(installer.index('"$SCRIPT_DIR/retire-bootstrap-nscd.py"'), installer.index('rsync -aHAX'))


class ShutdownTests(unittest.TestCase):
    def spawn(self, directory, name='dhcpcd'):
        child = subprocess.Popen([sys.executable, '-c', '''
import ctypes, os, sys, time
ctypes.CDLL(None).prctl(15, sys.argv[2].encode(), 0, 0, 0)
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT, 0o600)
print(fd, flush=True)
time.sleep(60)
''', str(directory / 'pid'), name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.wait()
            child.stdout.close()
            child.stderr.close()
        self.addCleanup(cleanup)
        fd = child.stdout.readline().strip()
        self.assertTrue(fd.isdigit())
        return child, fd

    def run_helper(self, child, fd, extra=''):
        helper = shlex.quote(str(GENTOO / 'lib/shutdown.sh'))
        descriptor = '/proc/' + str(child.pid) + '/fd/' + fd
        # Only the displayed pathname/device is mocked; /proc comm/stat/fds,
        # SIGTERM handling, process exit and descriptor closure are real.
        script = ('set -Eeuo pipefail\n. ' + helper + '\n'
                  'readlink() { if [ "$1" = ' + shlex.quote(descriptor) + ' ]; then '
                  'echo /var/run/dhcpcd/pid; else command readlink "$@"; fi; }\n'
                  'stat() { if [ "${!#}" = ' + shlex.quote(descriptor) + ' ]; then '
                  'command stat -Lc %d /; else command stat "$@"; fi; }\n' + extra + '\n'
                  'gentoo_stop_bootstrap_dhcpcd\n')
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)

    def test_verified_daemon_closes_pidfile_after_sigterm(self):
        with tempfile.TemporaryDirectory() as tmp:
            child, fd = self.spawn(Path(tmp))
            result = self.run_helper(child, fd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Stopping bootstrap dhcpcd PID ' + str(child.pid), result.stdout)
            self.assertEqual(child.wait(timeout=2), -signal.SIGTERM)

    def test_unrelated_process_holding_same_path_is_not_signalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            child, fd = self.spawn(Path(tmp), 'not-dhcpcd')
            result = self.run_helper(child, fd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsNone(child.poll())
            child.terminate()

    def test_timeout_stops_shutdown_without_escalating_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            child, fd = self.spawn(Path(tmp))
            result = self.run_helper(child, fd, 'kill() { [ "$1" = -TERM ]; }; sleep() { :; }')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('after 10 seconds; shutdown stopped', result.stderr)
            self.assertIsNone(child.poll())
            child.terminate()

    def test_shutdown_stops_daemon_before_unmount_and_refuses_poweroff_on_failure(self):
        script = (GENTOO / 'shutdown.sh').read_text()
        tail = 'gentoo_stop_bootstrap_dhcpcd\n' + script.split('gentoo_stop_bootstrap_dhcpcd\n', 1)[1]
        # Execute the real shutdown sequence, substituting a private mounts
        # table and an event for the final kernel write. All mount operations
        # and daemon management are fixtures; this never shuts down the host.
        tail = tail.replace('/proc/mounts', '"$FIXTURE/mounts"')
        tail = tail.replace('printf o > /proc/sysrq-trigger', 'poweroff_fixture')
        for daemon_status, remount_status, root_mode, powered_off in (
                (0, 0, 'ro', True), (6, 0, 'ro', False),
                (0, 7, 'ro', False), (0, 0, 'rw', False)):
            with self.subTest(daemon=daemon_status, remount=remount_status, root=root_mode), \
                    tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory)
                (fixture / 'mounts').write_text(
                    'root / ext4 ' + root_mode + ' 0 0\n'
                    'build /build ext4 rw 0 0\nscratch /gentoo-scratch ext4 rw 0 0\n')
                prefix = '''set -Eeuo pipefail
FIXTURE=$1
gentoo_stop_bootstrap_dhcpcd() { echo daemon >> "$FIXTURE/events"; return "$2"; }
'''.replace('return "$2"', 'return ' + str(daemon_status))
                prefix += '''
sync() { :; }
umount() { echo "unmount $*" >> "$FIXTURE/events"; }
mount() {
    echo "remount $*" >> "$FIXTURE/events"
    # Model successful unmounts in the final inspection table.
    sed -i '/^build /d; /^scratch /d' "$FIXTURE/mounts"
    return REMOUNT_STATUS
}
poweroff_fixture() { echo poweroff >> "$FIXTURE/events"; }
'''.replace('REMOUNT_STATUS', str(remount_status))
                result = subprocess.run(['bash', '-c', prefix + tail, 'shutdown-fixture', directory],
                                        text=True, capture_output=True, timeout=3)
                events = (fixture / 'events').read_text().splitlines()
                self.assertEqual(events[0], 'daemon')
                self.assertEqual('poweroff' in events, powered_off)
                self.assertNotEqual(result.returncode, 0)
                if powered_off:
                    self.assertEqual(events[1:3], ['unmount /gentoo-scratch', 'unmount /build'])
                    self.assertEqual(events[-2:], ['remount -o remount,ro /', 'poweroff'])
                elif daemon_status:
                    self.assertEqual(events, ['daemon'])


class PerlLocaleTests(unittest.TestCase):
    def test_actual_mixed_locale_round_trips_under_both_starting_locales(self):
        for value in ('C', 'C.UTF-8'):
            result = subprocess.run(['perl', '-e', PROGRAM], text=True, capture_output=True,
                                    env={**os.environ, 'LC_ALL': value, 'LANG': value}, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('mixed-category locale round-trips passed', result.stdout)

    def test_old_empty_metadata_and_wrong_category_reports_are_rejected(self):
        for program in (PROGRAM.replace("eq 'define'", "eq 'not-defined'", 1),
                        PROGRAM.replace('my $all = setlocale(LC_ALL);', 'my $all = "C;C;C;C";', 1)):
            result = subprocess.run(['perl', '-e', program], text=True, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('round-trips passed', result.stdout)

    def transition(self, ready=False, fail=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            if ready:
                (root / 'ready').touch()
            script = ('set -Eeuo pipefail\ncd ' + shlex.quote(temporary) + '\n' + HELPER + '\n' + '''
gentoo_die() { printf '%s\\n' "$*" >&2; exit 1; }
perl_locale_install_policy() { echo policy >> events; }
perl_locale_probe() { echo probe >> events; [ -f ready ]; }
run_emerge() { echo "$*" >> events; [ FAIL = no ] || return 7; touch ready; }
ensure_perl_locale
echo ACCEPTED >> events
'''.replace('FAIL', 'yes' if fail else 'no'))
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=3)
            return result, (root / 'events').read_text().splitlines()

    def test_repair_reinstalls_pinned_perl_before_acceptance(self):
        result, events = self.transition()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['policy', 'probe', '--oneshot =dev-lang/perl-5.44.0', 'probe', 'ACCEPTED'])

    def test_working_perl_skips_rebuild_but_keeps_persistent_policy(self):
        result, events = self.transition(ready=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['policy', 'probe', 'ACCEPTED'])

    def test_failed_merge_does_not_reprobe_or_accept(self):
        result, events = self.transition(fail=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(events, ['policy', 'probe', '--oneshot =dev-lang/perl-5.44.0'])

    def test_policy_is_idempotent_preserves_existing_flags_and_refuses_unknown_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = ('set -Eeuo pipefail\nNATIVE_ROOT=' + shlex.quote(temporary) + '\n' + HELPER + '''
gentoo_die() { printf '%s\\n' "$*" >&2; exit 1; }
chroot_env() { shift; python3 "$@"; }
perl_locale_install_policy
perl_locale_install_policy
EXTRA_ECONF=-Dexisting=define
. "$NATIVE_ROOT/etc/portage/env/90-gentoo-perl-locale.conf"
eval "args=($EXTRA_ECONF)"
printf '%s\\n' "${args[@]}"
''')
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('-Dexisting=define', result.stdout)
            self.assertIn('-Dd_perl_lc_all_uses_name_value_pairs=define', result.stdout)
            self.assertIn('-Ud_perl_lc_all_separator', result.stdout)
            self.assertIn('-Ud_perl_lc_all_category_positions_init', result.stdout)
            target = Path(temporary) / 'etc/portage/env/90-gentoo-perl-locale.conf'
            target.write_text('# operator override\n')
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('environment differs', result.stderr)
            self.assertEqual(target.read_text(), '# operator override\n')

    def test_build_probe_drops_identity_and_bounds_child_processes(self):
        self.assertIn('os.setgroups([])', HELPER)
        self.assertIn('os.setgid(250)', HELPER)
        self.assertIn('os.setuid(250)', HELPER)
        self.assertIn('timeout=20, check=True', HELPER)


class FunctionsSeedTests(unittest.TestCase):
    def test_seed_into_missing_destination_then_resume_and_reject_changed_data(self):
        recipe = (GENTOO / 'stages/system/30-bootstrap-prerequisites.sh').read_text()
        fragment = recipe[recipe.index('    # The native seed contains libc,'):recipe.index('    installed_elt=')]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'native'
            (root / 'lib').mkdir(parents=True)
            source = Path(tmp) / 'verified-source'
            (source / 'functions').mkdir(parents=True)
            (source / 'functions.sh').write_text('verified functions\n')
            (source / 'functions/helper.sh').write_text('verified module\n')
            command = ('set -Eeuo pipefail\numask 077\nNATIVE_ROOT=' + shlex.quote(str(root)) + '\n'
                       'helper_source=' + shlex.quote(str(source)) + '\n' + fragment)
            for _ in range(2):
                result = subprocess.run(['bash', '-c', command], text=True, capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / 'lib/gentoo').stat().st_mode & 0o777, 0o755)
                self.assertEqual((root / 'lib/gentoo/functions.sh').read_bytes(), (source / 'functions.sh').read_bytes())
                self.assertEqual((root / 'lib/gentoo/functions/helper.sh').read_bytes(), (source / 'functions/helper.sh').read_bytes())
            (root / 'lib/gentoo/functions.sh').write_text('different data\n')
            result = subprocess.run(['bash', '-c', command], text=True, capture_output=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Refusing to replace', result.stderr)
            self.assertEqual((root / 'lib/gentoo/functions.sh').read_text(), 'different data\n')


class GlibcRetirementTests(unittest.TestCase):
    def test_retirement_removes_only_claim_and_recovers_interrupted_replacement(self):
        phase = (GENTOO / 'stages/system/40-runtime-providers.sh').read_text()
        block = phase[phase.index('retire_bootstrap_glibc_provider()'):phase.index('retire_bootstrap_perl_provider()')]
        for initial in (FULL, PERL):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                provider = root / 'provided'
                provider.write_text(initial)
                (root / 'provider.done').write_text('provider file sha256=' + hashlib.sha256(FULL.encode()).hexdigest() + '\n')
                (root / 'bootstrap.done').write_text('completed verified bootstrap\n')
                (root / 'libc.so.6').write_bytes(b'existing seed runtime')
                variables = dict(STATE=str(root), provider_file=str(provider), provider_marker=str(root / 'provider.done'),
                    bootstrap_marker=str(root / 'bootstrap.done'), glibc_provider_removed_marker=str(root / 'glibc.removed'),
                    perl_provider_removed_marker=str(root / 'perl.removed'), system_marker=str(root / 'system.done'),
                    glibc_provider_entry='sys-libs/glibc-2.44', perl_provider_entry='dev-lang/perl-5.42.2',
                    perl_only_provider_hash=hashlib.sha256(PERL.encode()).hexdigest())
                script = 'set -Eeuo pipefail\n' + '\n'.join(k + '=' + shlex.quote(v) for k, v in variables.items())
                script += '\nsha256() { sha256sum "$1" | cut -d" " -f1; }\n'
                # Neither Gentoo query nor a package merge is needed to stop
                # claiming a seed as a package; genuine installation is later.
                script += 'chroot_env() { echo UNEXPECTED_CHROOT >&2; return 9; }\n'
                script += block + '\nretire_bootstrap_glibc_provider\nretire_bootstrap_glibc_provider\n'
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(provider.read_text(), PERL)
                self.assertEqual((root / 'libc.so.6').read_bytes(), b'existing seed runtime')
                self.assertTrue((root / 'glibc.removed').is_file())
                (root / 'bootstrap.done').unlink()
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('before bootstrap completion', result.stderr)
                (root / 'bootstrap.done').write_text('completed verified bootstrap\n')
                provider.write_text(PERL + 'sys-devel/gcc-99\n')
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Remaining Perl provider does not match', result.stderr)
                self.assertIn('sys-devel/gcc-99', provider.read_text())

    def test_uninterrupted_bootstrap_retires_claim_before_system_resolution(self):
        # The final system still needs the real pinned package and runtime.
        system = (GENTOO / 'stages/system/70-system.sh').read_text()
        prefix = system[:system.index('# Bootstrap has installed Gentoo GCC.')]
        result = subprocess.run(['bash', '-eu', '-c',
            'retire_bootstrap_glibc_provider() { echo RETIRED_BEFORE_RESOLUTION; }\n' + prefix],
            text=True, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'RETIRED_BEFORE_RESOLUTION')
        self.assertIn('verify_native_gentoo_glibc || {', system)
        self.assertIn('Unexpected Gentoo glibc after @system merge:', system)


class PerlTransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_transition(self, installed=False, ready=False, fail=False):
        phase = (GENTOO / 'stages/system/70-system.sh').read_text()
        block = phase[phase.index('installed_gentoo_perl='):phase.index('texinfo_marker=')]
        if installed:
            (self.root / 'current').write_text('dev-lang/perl-5.44.0\n')
        if ready:
            (self.root / 'ready').touch()
        script = '''set -Eeuo pipefail
cd {root}
FAIL_MODULES={fail}
chroot_env() {{
    if [ "$1" = /usr/bin/portageq ]; then cat current; return; fi
    [ "$1" = /usr/bin/python3.12 ] || return 9
    printf 'imports\\n' >> events
    [ -f ready ]
}}
run_emerge() {{
    printf '%s\\n' "$*" >> events
    if [ "$*" = '--oneshot dev-lang/perl' ]; then
        printf 'dev-lang/perl-5.44.0\\n' > current
    else
        [ -s current ] || exit 8
        [ "$FAIL_MODULES" = 0 ] || return 7
        touch ready
    fi
}}
'''.format(root=shlex.quote(str(self.root)), fail=int(fail)) + block
        return subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=3)

    def test_seed_perl_replaced_before_module_dependency_chain(self):
        result = self.run_transition()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'events').read_text().splitlines(), [
            '--oneshot dev-lang/perl', 'imports',
            '--oneshot dev-perl/Capture-Tiny dev-perl/PerlIO-utf8_strict dev-perl/File-Slurper dev-perl/Config-AutoConf',
            'imports'])

    def test_current_imports_skip_rebuild(self):
        result = self.run_transition(installed=True, ready=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'events').read_text(), 'imports\n')

    def test_failed_module_merge_does_not_continue(self):
        result = self.run_transition(installed=True, fail=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual((self.root / 'events').read_text().splitlines().count('imports'), 1)

    def provider_probe(self, installed=False, corrupt=False):
        phase = (GENTOO / 'stages/system/40-runtime-providers.sh').read_text()
        block = phase[phase.index('# Gentoo may have replaced'):phase.index('retire_bootstrap_glibc_provider()')]
        if installed:
            vdb = self.root / 'var/db/pkg/dev-lang/perl-5.44.0'
            vdb.mkdir(parents=True)
            (vdb / 'repository').write_text('gentoo\n')
            (self.root / 'usr/bin').mkdir(parents=True)
            binary = self.root / 'usr/bin/perl'
            binary.write_bytes(b'package executable')
            md5 = hashlib.md5(binary.read_bytes()).hexdigest()
            (vdb / 'CONTENTS').write_text('obj /usr/bin/perl ' + md5 + ' 0\n')
            if corrupt:
                binary.write_bytes(b'changed executable')
        else:
            core = self.root / 'usr/lib/perl5/5.42.2/x86_64-linux/CORE'
            core.mkdir(parents=True)
            (core / 'libperl.so').write_text('seed runtime')
        script = '''set -Eeuo pipefail
NATIVE_ROOT={root}
chroot_env() {{
    if [ "$1" = /usr/bin/portageq ]; then printf '%s' {package}; return; fi
    printf '%s\\n' {version}
}}
'''.format(root=shlex.quote(str(self.root)), package=shlex.quote('dev-lang/perl-5.44.0' if installed else ''),
           version=shlex.quote('5.44.0' if installed else '5.42.2')) + block
        return subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=3)

    def test_verified_gentoo_perl_is_accepted_on_resume(self):
        result = self.provider_probe(installed=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_gentoo_perl_binary_is_rejected(self):
        result = self.provider_probe(installed=True, corrupt=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not match', result.stderr)

    def test_original_seed_perl_is_still_checked_before_replacement(self):
        result = self.provider_probe()
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_probe_drops_identity_before_actual_perl_imports(self):
        phase = (GENTOO / 'stages/system/70-system.sh').read_text()
        block = phase.split('# BEGIN PERL BUILD MODULE TRANSITION\n')[1].split('# END PERL BUILD MODULE TRANSITION')[0]
        code = block.split("-c '\n", 1)[1].split("\n'", 1)[0]
        compile(code, '<perl-module-probe>', 'exec')
        self.assertLess(code.index('os.setuid(250)'), code.index('os.execv('))
        self.assertIn('-MCapture::Tiny', code)
        self.assertIn('-MPerlIO::utf8_strict', code)
        self.assertIn('-MFile::Slurper', code)
        self.assertIn('-MConfig::AutoConf', code)


class GawkReceiptTests(unittest.TestCase):
    def test_actual_producer_handles_commented_lock_and_checks_resume_source_hash(self):
        recipe = (GENTOO / 'stages/63-native-tool-floor.sh').read_text()
        block = recipe[recipe.index('    # The reviewed LFS fix'):recipe.index('    native_tool_build gawk-5.4.1.tar.xz')]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            source = work / 'sources/gawk-5.4.1'
            source.mkdir(parents=True)
            (work / 'state').mkdir()
            (source / 'Makefile.in').write_text('SUBDIRS = awklib extras\n')
            command = ('set -Eeuo pipefail\nWORK_ROOT=' + shlex.quote(tmp) + '\n'
                       'SCRIPT_DIR=' + shlex.quote(str(GENTOO)) + '\n'
                       '. "$SCRIPT_DIR/lib/native-tools.sh"\n'
                       'gentoo_die() { echo "$*" >&2; exit 1; }\n'
                       'gentoo_prepare_source() { :; }\n'
                       'produce() {\n' + block + '\n}\nproduce\n')
            result = subprocess.run(['bash', '-c', command], text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            marker = work / 'state/63-gawk-source-patch.receipt'
            digest = next(line.split('|')[2].removeprefix('sha512=') for line in
                          (GENTOO / 'sources/native-gawk-5.4.1.lock').read_text().splitlines()
                          if line.startswith('gawk-5.4.1.tar.xz|'))
            expected = ('source sha512=' + digest + '\npatched Makefile.in sha256=' +
                        hashlib.sha256((source / 'Makefile.in').read_bytes()).hexdigest() +
                        '\nrecipe=lfs13-gawk-extras-removal-v1\n')
            self.assertEqual(marker.read_text(), expected)
            result = subprocess.run(['bash', '-c', command], text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(), expected)
            marker.write_text(expected.replace(digest, '0' * 128))
            result = subprocess.run(['bash', '-c', command], text=True, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)


class PreparePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.native = self.directory / 'native'
        self.tree = self.directory / 'repository'
        self.state = self.directory / 'state'
        self.state.mkdir()
        (self.state / 'native-cpu-policy.json').write_text('{"cpu_flags_x86": ["sse2"], "cflags": "-O2 -pipe -march=native -mtune=native -fstack-protector-strong -D_FORTIFY_SOURCE=2"}\n')
        self.ca = self.directory / 'ca.crt'
        self.ca.write_text('certificate-data fixture\n')
        for path in (self.native / 'usr/bin', self.native / 'etc/portage',
                     self.native / 'usr/portage/profiles/default/linux/amd64/23.0/split-usr',
                     self.native / 'usr/lib/portage/cnf/sets', self.tree / 'profiles',
                     self.tree / 'sys-libs/glibc'):
            path.mkdir(parents=True)
        (self.native / 'usr/bin/python3.12').write_text('#!/bin/sh\nexit 0\n')
        (self.native / 'usr/bin/python3.12').chmod(0o755)
        (self.native / 'usr/lib/portage/cnf/sets/portage.conf').write_text('set config fixture\n')
        (self.tree / 'profiles/repo_name').write_text('gentoo\n')
        (self.tree / 'sys-libs/glibc/glibc-2.44-r3.ebuild').write_text('EAPI=8\n')
        (self.tree / 'sys-libs/glibc/Manifest').write_text(
            'DIST glibc-2.44.tar.xz 1 SHA512 ' + 'a' * 128 + '\n'
            'DIST glibc-2.44-patches-4.tar.xz 1 SHA512 ' + 'b' * 128 + '\n')

    def run_prepare(self, success=True):
        # Execute the complete configuration section through the last policy
        # write. Stop before the separately tested mount/command section.
        code = (GENTOO / 'stages/system/10-prepare.sh').read_text().split('created_dev_shm_mount=0', 1)[0]
        prefix = '''set -Eeuo pipefail
SCRIPT_DIR={script}
NATIVE_ROOT={native}
GENTOO_TREE={tree}
STATE={state}
DISTFILES={distfiles}
SCRATCH={scratch}
GENTOO_CA_BUNDLE={ca}
REPO_COMMIT={commit}
driver_hash={driver}
BOOTSTRAP_GLIBC_VERSION=2.44-r3
BOOTSTRAP_GLIBC_KEYWORD_ENTRY='=sys-libs/glibc-2.44-r3 ~amd64'
JOBS=3
PACKAGE_JOBS=2
LOAD_AVERAGE=4
GENTOO_PORTAGE_MAKE_JOBS=3
. "$SCRIPT_DIR/lib/repository-policy.sh"
sha256() {{ sha256sum < "$1" | cut -d' ' -f1; }}
marker_driver_recorded() {{ grep -Fxq "driver sha256=$driver_hash" "$1"; }}
chroot() {{ printf 'bindist\\n'; }}
'''.format(script=shlex.quote(str(GENTOO)), native=shlex.quote(str(self.native)), tree=shlex.quote(str(self.tree)),
           state=shlex.quote(str(self.state)), distfiles=shlex.quote(str(self.directory / 'distfiles')),
           scratch=shlex.quote(str(self.directory / 'scratch')), ca=shlex.quote(str(self.ca)),
           commit='a' * 40, driver='b' * 64)
        result = subprocess.run(['bash', '-c', prefix + code], text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_fresh_root_creates_keyword_parent_before_first_write_and_resumes(self):
        keyword_dir = self.native / 'etc/portage/package.accept_keywords'
        self.assertFalse(keyword_dir.exists())
        self.run_prepare()
        mount_helper = self.native / 'usr/sbin/mount'
        self.assertEqual(mount_helper.read_bytes(), (GENTOO / 'probes/portage-mount-shim.py').read_bytes())
        self.assertTrue(mount_helper.stat().st_mode & 0o111)
        keyword = keyword_dir / '90-native-system-glibc'
        self.assertIn('=sys-libs/glibc-2.44-r3 ~amd64', keyword.read_text())
        saved = {p.name: p.read_bytes() for p in self.state.iterdir() if p.is_file()}
        self.run_prepare()
        self.assertEqual(saved, {p.name: p.read_bytes() for p in self.state.iterdir() if p.is_file()})
        for name in ('env/gentoo-memory.conf', 'package.env/gentoo-memory',
                     'package.use/10-native-build-alternatives', 'repos.conf/gentoo.conf'):
            self.assertTrue((self.native / 'etc/portage' / name).is_file(), name)

    def test_conflicting_keyword_content_is_not_replaced(self):
        keyword = self.native / 'etc/portage/package.accept_keywords/90-native-system-glibc'
        keyword.parent.mkdir()
        keyword.write_text('user policy\n')
        result = self.run_prepare(success=False)
        self.assertIn('Refusing an unexpected glibc keyword override', result.stderr)
        self.assertEqual(keyword.read_text(), 'user policy\n')

    def test_unexpected_mount_helper_is_preserved(self):
        helper = self.native / 'usr/sbin/mount'
        helper.parent.mkdir(parents=True)
        helper.write_text('operator-installed helper\n')
        result = self.run_prepare(success=False)
        self.assertIn('Refusing to replace an unexpected', result.stderr)
        self.assertEqual(helper.read_text(), 'operator-installed helper\n')


class CacheModeTests(unittest.TestCase):
    def test_actual_setup_repairs_parent_without_changing_package_work(self):
        recipe = (GENTOO / 'lib/native-gentoo-system.sh').read_text()
        fragment = recipe[recipe.index('    # Portage drops to UID/GID 250'):recipe.index('    export SCRATCH')]
        # The host fixture uses our own numeric IDs; production uses root:250.
        fragment = fragment.replace('-o 0 -g 250', '-o %d -g %d' % (os.getuid(), os.getgid()))
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            package = scratch / 'portage-tmp/portage/app-portage/elt-patches-20250718'
            package.mkdir(parents=True)
            package.chmod(0o700)
            (scratch / 'portage-tmp').chmod(0o700)
            sentinel = package / 'existing-work'
            sentinel.write_text('preserve build output')
            script = ('set -Eeuo pipefail\nSCRATCH=' + shlex.quote(tmp) + '\n'
                      'gentoo_die() { echo "$*" >&2; exit 1; }\n' + fragment)
            for _ in range(2):
                result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)
                for name in ('portage-tmp', 'distfiles', 'binpkgs'):
                    self.assertEqual((scratch / name).stat().st_mode & 0o7777, 0o2775)
                self.assertEqual(package.stat().st_mode & 0o777, 0o700)
                self.assertEqual(sentinel.read_text(), 'preserve build output')
            (scratch / 'binpkgs').rmdir()
            (scratch / 'binpkgs').symlink_to(scratch / 'portage-tmp')
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unexpected scratch cache path', result.stderr)


class MountHelperTests(unittest.TestCase):

    def test_exact_syscall_for_proc_and_propagation_with_no_host_mount(self):
        helper = load('bootstrap_mount', GENTOO / 'probes/portage-mount-shim.py')
        libc = Mock()
        libc.mount.return_value = 0
        with patch.object(helper.os.path, 'isfile', return_value=False), \
                patch.object(helper.ctypes, 'CDLL', return_value=libc):
            self.assertEqual(helper.main(['mount', '-n', '-t', 'proc', 'proc', '/proc']), 0)
            libc.mount.assert_called_with(b'proc', b'/proc', b'proc', 0, None)
            self.assertEqual(helper.main(['mount', '--make-slave', '/proc']), 0)
            libc.mount.assert_called_with(None, b'/proc', None, helper.MS_SLAVE, None)
            self.assertEqual(helper.main(['mount', '-o', 'rslave', '/dev']), 0)
            libc.mount.assert_called_with(None, b'/dev', None, helper.MS_SLAVE | helper.MS_REC, None)
            count = libc.mount.call_count
            self.assertEqual(helper.main(['mount', '-t', 'proc', 'proc', '/tmp']), 2)
            self.assertEqual(libc.mount.call_count, count)
