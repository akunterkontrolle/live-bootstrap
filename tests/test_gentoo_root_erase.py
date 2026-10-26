"""Explicit first-install erasure clears old signatures; resume cannot repeat own formats."""
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
HELPER = GENTOO / 'lib/installer-erase.sh'


class RootSignatureEraseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.part = self.root / 'partition'
        with self.part.open('wb') as stream:
            stream.truncate(32 * 1024 * 1024)
        self.uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        (self.state / 'target.identity').write_text('verified ROOT serial=TESTROOT\n')
        identity = hashlib.sha256((self.state / 'target.identity').read_bytes()).hexdigest()
        (self.state / 'erase-approved.done').write_text('policy=erase previous signatures before first LUKS format\ntarget identity sha256=' + identity + '\n')
        (self.state / 'partition.done').write_text('partition UUID=' + self.uuid + '\n')
        self.events = self.root / 'events'
        self.script = '''set -Eeuo pipefail
. {helper}
STATE={state}; ROOT_PART={part}; EVENTS={events}
fail() {{ echo "$*" >&2; exit 19; }}
sha256() {{ sha256sum < "$1" | cut -d' ' -f1; }}
phase() {{ local name=$1; shift; printf '%s\\n' "$@" > "$STATE/$name"; }}
installer_require_unused_root() {{ [[ ${{BUSY:-0}} = 0 ]] || fail 'root target is mounted'; }}
blkid() {{ echo "${{PART_UUID:-{uuid}}}"; }}
wipefs() {{ echo "$*" >> "$EVENTS"; /usr/sbin/wipefs "$@"; }}
installer_clear_authorized_partition
'''.format(helper=shlex.quote(str(HELPER)), state=shlex.quote(str(self.state)),
           part=shlex.quote(str(self.part)), events=shlex.quote(str(self.events)), uuid=self.uuid)

    def run_helper(self, **env):
        return subprocess.run(['bash', '-c', self.script], env=dict(os.environ, **env), capture_output=True, text=True)

    def test_actual_luks2_headers_are_cleared_and_backup_saved_once(self):
        cryptsetup = shutil.which('cryptsetup') or '/usr/sbin/cryptsetup'
        if not Path(cryptsetup).exists() or not Path('/usr/sbin/wipefs').exists():
            self.skipTest('requires cryptsetup and wipefs')
        key = self.root / 'key'
        key.write_bytes(b'fixture only, not a production key')
        subprocess.run([cryptsetup, 'luksFormat', '--batch-mode', '--type', 'luks2', '--pbkdf', 'pbkdf2',
                        '--pbkdf-force-iterations', '1000', '--key-file', str(key), str(self.part)],
                       check=True, capture_output=True)
        observed = subprocess.check_output(['/usr/sbin/wipefs', '--no-act', '--noheadings', '--output', 'TYPE', str(self.part)], text=True)
        self.assertIn('crypto_LUKS', observed)
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(subprocess.check_output(['/usr/sbin/wipefs', '--no-act', '--noheadings', '--output', 'TYPE', str(self.part)], text=True), '')
        self.assertTrue(list((self.state / 'signature-backups').glob('*')))
        before = self.events.read_text()
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.events.read_text(), before)
        self.assertTrue((self.state / 'partition-clear.done').exists())

    def test_missing_approval_wrong_partition_or_busy_root_never_erases(self):
        for env in ({'BUSY': '1'}, {'PART_UUID': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'}):
            result = self.run_helper(**env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.events.exists())
        (self.state / 'erase-approved.done').write_text('different target\n')
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertFalse(self.events.exists())

    def test_own_format_intent_or_completed_luks_never_erases(self):
        for marker in ('luks.started', 'luks.done', 'storage.done'):
            (self.state / marker).write_text('installer-owned state\n')
            self.assertNotEqual(self.run_helper().returncode, 0)
            self.assertFalse(self.events.exists())
            (self.state / marker).unlink()

    def test_unused_root_gate_rejects_mount_inventory_errors_holders_and_swap(self):
        disk = self.root / 'disk'
        disk.write_bytes(b'private device fixture')
        sysfs = self.root / 'sys'
        for device in (disk, self.part):
            (sysfs / device.name / 'holders').mkdir(parents=True)
        swaps = self.root / 'swaps'
        swaps.write_text('Filename Type Size Used Priority\n')
        production = HELPER.read_text().replace('/sys/class/block', str(sysfs)).replace('/proc/swaps', str(swaps))
        production = production.replace('[[ -b $child ]]', '[[ -f $child ]]')
        script = 'set -Eeuo pipefail\n' + production + r'''
ROOT_REAL=$1; PART=$2
fail() { echo "$*" >&2; exit 19; }
lsblk() {
 [[ ${LSBLK_FAIL:-0} = 0 ]] || return 7
 if [[ $* == *MAJ:MIN* ]]; then
   if [[ ${!#} == "$ROOT_REAL" ]]; then echo 8:0; else echo 8:1; fi
 else printf '%s\n' "$ROOT_REAL" "$PART"; fi
}
findmnt() {
 [[ ${MOUNT_FAIL:-0} = 0 ]] || return 7
 [[ ${EMPTY_TABLE:-0} = 0 ]] || return 0
 if [[ ${MOUNTED:-0} = 1 ]]; then echo '8:1 /occupied'; else echo '9:1 /'; fi
}
installer_require_unused_root "${ALLOW_MAPPINGS:-}"
'''
        def run(**changes):
            return subprocess.run(['bash', '-c', script, 'unused-root', str(disk), str(self.part)],
                                  env=dict(os.environ, **changes), capture_output=True, text=True)
        self.assertEqual(run().returncode, 0)
        for changes in ({'LSBLK_FAIL': '1'}, {'MOUNT_FAIL': '1'}, {'EMPTY_TABLE': '1'}, {'MOUNTED': '1'}):
            with self.subTest(changes=changes):
                self.assertNotEqual(run(**changes).returncode, 0)
        holder = sysfs / self.part.name / 'holders/active-mapper'
        holder.write_text('active device')
        self.assertNotEqual(run().returncode, 0)
        self.assertEqual(run(ALLOW_MAPPINGS='allow-mappings').returncode, 0)
        holder.unlink()
        swaps.write_text('Filename Type Size Used Priority\n' + str(self.part) + ' partition 100 0 -2\n')
        self.assertNotEqual(run().returncode, 0)

    def test_actual_initialization_dispatch_warns_and_requires_confirmation_before_writes(self):
        source = (GENTOO / 'install-base.sh').read_text()
        branch = source.rsplit('case "$MODE" in', 1)[1].split('    --initialize-storage)\n', 1)[1].split('        initialize_blank_luks', 1)[0]
        branch = branch.replace(' > /dev/tty', ' >> "$EVENTS"')
        fixture = '''set -Eeuo pipefail
STATE=$1; EVENTS=$2; ROOT_REAL=/dev/fixture; ROOT_PART=/dev/fixture1
ROOT_SERIAL=SERIAL; ROOT_MAJMIN=8:0; ROOT_BYTES=1000; ROOT_WWN_OBSERVED=WWN; ROOT_MODEL_OBSERVED=MODEL
GENTOO_MODE=hardware; VG=test; ROOT_GIB=1; SWAP_GIB=1; RESERVE_GIB=1; HOME_MIN_GIB=1
[() { if builtin [ "$#" = 3 ] && { builtin [ "$1" = -t ] || builtin [ "$1" = -b ]; }; then return 0; fi; builtin [ "$@"; }
fail() { echo "$*" >&2; exit 19; }
native_mounts() { :; }; native_unmounts() { :; }; verify_target() { :; }
installer_signature_inventory() { echo '/dev/fixture: dos; /dev/fixture1: crypto_LUKS'; }
installer_require_unused_root() { :; }
read_tty_line() { printf -v "$2" '%s' "${ANSWER:-wrong}"; }
sha256() { sha256sum < "$1" | cut -d' ' -f1; }
phase() { local marker=$1; shift; printf '%s\\n' "$@" > "$STATE/$marker"; }
wipefs() { test -s "$STATE/erase-approved.done"; echo WIPE >> "$EVENTS"; }
native_run() { echo "$*" >> "$EVENTS"; }
lsblk() { echo CA7D7CCB-63ED-4C53-861C-1742536059CC; }
blkid() { echo aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa; }
''' + branch
        (self.state / 'erase-approved.done').unlink()
        for answer, success in (('wrong', False), ('ERASE SERIAL 8:0', True)):
            result = subprocess.run(['bash', '-c', fixture, 'fixture', str(self.state), str(self.events)],
                                    env=dict(os.environ, ANSWER=answer), capture_output=True, text=True)
            self.assertIn('WARNING: existing root-disk signatures', result.stdout)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            events = self.events.read_text()
            if success:
                self.assertIn('WIPE', events)
                self.assertIn('sgdisk --zap-all', events)
            else:
                self.assertNotIn('WIPE', events)
                self.assertFalse((self.state / 'storage.started').exists())
