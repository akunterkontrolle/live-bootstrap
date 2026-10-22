"""Exercise extra-disk format/resume gates without touching block devices."""
import hashlib
import os
from pathlib import Path
import shlex
import re
import shutil
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
SOURCE = (GENTOO / 'setup-extra.sh').read_text()


def function(name):
    start = name + '() {'
    return start + SOURCE.split(start, 1)[1].split('\n}\n', 1)[0] + '\n}\n'


class ExtraStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.key = self.root / 'key'
        self.key.write_bytes(b'x' * 64)
        self.key.chmod(0o400)
        (self.state / 'key.done').write_text('key sha256=' + hashlib.sha256(self.key.read_bytes()).hexdigest() + '\n')
        (self.state / 'initialize.started').write_text('serial=EXTRA\nsize=1000000\nmajor:minor=8:16\n')
        (self.state / 'partition.done').write_text('partition UUID=11111111-1111-1111-1111-111111111111\n')
        self.helpers = ''.join(function(name) for name in ('extra_probe_type', 'extra_require_blank',
                                                          'extra_partition_identity', 'extra_verify_key', 'extra_check_mounts'))
        self.helpers = self.helpers.replace('[[ -b $EXTRA_PART ]]', '[[ -n $EXTRA_PART ]]')
        self.resume = SOURCE.split('    resume)\n', 1)[1].split('        ;;\n    restore)', 1)[0]
        self.shell = '''set -Eeuo pipefail
STATE={state}; KEY={key}; EVENTS={events}
EXTRA_REAL=/dev/sdb; EXTRA_PART=/dev/sdb1; EXTRA_SERIAL=EXTRA; EXTRA_BYTES=1000000; EXTRA_MAJMIN=8:16
MAPPER=ms1504-extra; MOUNT={mount}; WORKSPACE_LABEL=GENTOO_BUILD
RETENTION_POLICY=installed-continuation-v1; SOURCE_DEVICE=/dev/source1
fail() {{ echo "$*" >&2; exit 19; }}
validate_home_migration() {{ echo VALIDATE >> "$EVENTS"; [[ ${{BAD_STAGING:-0}} == 0 ]] || fail 'invalid staging'; }}
readlink() {{ echo "${{!#}}"; }}
sha256sum() {{ [[ ${{HASH_FAIL:-0}} == 0 ]] || return 7; command sha256sum "$@"; }}
stat() {{
    if [[ $1 == -c && $2 == '%u:%g:%a:%s' ]]; then
        printf '0:0:%s:%s\\n' "$(command stat -c %a "$3")" "$(command stat -c %s "$3")"
    else command stat "$@"; fi
}}
lsblk() {{
    [[ ${{LSBLK_FAIL:-0}} == 0 ]] || return 7
    case "$*" in *-lnrpo*) echo 'list and raw cannot be combined' >&2; return 1;; esac
    case "$*" in
      *NAME,TYPE*) echo '/dev/sdb disk';;
      *PKNAME*) echo "${{PARENT:-sdb}}";;
      *MAJ:MIN*) echo 8:16;;
      *) echo /dev/sdb;;
    esac
}}
findmnt() {{ [[ ${{MOUNT_SCAN_FAIL:-0}} == 0 ]] || return 7; printf '%b\\n' "${{MOUNTS:-8:1 /}}"; }}
wipefs() {{ [[ ${{WIPE_FAIL:-0}} == 0 ]] || return 7; printf '%s' "${{WIPE_SIGNATURE:-}}"; }}
blkid() {{
    case "$*" in
      *PART_ENTRY_UUID*) echo "${{PART_UUID:-11111111-1111-1111-1111-111111111111}}"; return;;
      *PART_ENTRY_TYPE*) echo "${{PART_TYPE:-ca7d7ccb-63ed-4c53-861c-1742536059cc}}"; return;;
      *PART_ENTRY_NUMBER*) echo 1; return;;
      *PART_ENTRY_NAME*) echo MS1504-EXTRA; return;;
      *UUID*) echo "${{FS_UUID:-22222222-2222-2222-2222-222222222222}}"; return;;
      *LABEL*) echo GENTOO_BUILD; return;;
    esac
    case " $* " in *' -p -D -s TYPE '*) ;; *) echo 'probe must disable partition metadata' >&2; return 99;; esac
    if [[ ${{!#}} == /dev/sdb1 ]]; then
        if [[ -e $STATE/header ]]; then echo crypto_LUKS; return 0; fi
        printf '%s' "${{PART_SIGNATURE:-}}"; return "${{PART_STATUS:-2}}"
    else
        if [[ -e $STATE/filesystem ]]; then echo ext4; return 0; fi
        printf '%s' "${{FS_SIGNATURE:-}}"; return "${{FS_STATUS:-2}}"
    fi
}}
cryptsetup() {{
    case "$1" in
      isLuks) [[ ${{HEADER_FAIL:-0}} == 0 ]];;
      luksFormat)
        [[ -s $STATE/luks.started ]] || exit 98
        echo CRYPT_FORMAT >> "$EVENTS"
        [[ ${{LUKS_FORMAT_FAIL:-0}} == 0 ]] || return 7
        touch "$STATE/header";;
      luksUUID) echo 33333333-3333-3333-3333-333333333333;;
      status)
        [[ -e $STATE/open ]] || return 4
        echo 'device: /dev/sdb1';;
      open)
        if [[ $* == *--test-passphrase* ]]; then [[ ${{WRONG_KEY:-0}} == 0 ]]; else touch "$STATE/open"; fi;;
      *) exit 99;;
    esac
}}
mkfs.ext4() {{
    [[ -s $STATE/filesystem.started ]] || exit 98
    echo MKFS >> "$EVENTS"; [[ ${{MKFS_FAIL:-0}} == 0 ]] || return 7
    touch "$STATE/filesystem"
}}
'''.format(state=shlex.quote(str(self.state)), key=shlex.quote(str(self.key)),
           events=shlex.quote(str(self.root / 'events')), mount=shlex.quote(str(self.root / 'mount')))

    def run_resume(self, **env):
        return subprocess.run(['bash', '-c', self.shell + self.helpers + self.resume],
                              env=dict(os.environ, **env), capture_output=True, text=True)

    def events(self):
        path = self.root / 'events'
        return path.read_text().splitlines() if path.exists() else []

    def test_actual_descendant_and_topology_options_pass_the_real_lsblk_parser(self):
        binary = shutil.which('lsblk', path=os.environ.get('PATH', '') + ':/usr/sbin:/sbin')
        if not binary:
            self.skipTest('lsblk is required')
        commands = re.findall(r'lsblk (\S+) (NAME(?:,TYPE)?) "\$EXTRA_REAL"', SOURCE)
        self.assertEqual(len(commands), 2)
        for options, columns in commands:
            # No target argument: read sysfs only, without needing fixture block nodes.
            result = subprocess.run([binary, options, columns], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_first_format_and_resume_preserve_both_completed_uuids(self):
        first = self.run_resume()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = {name: (self.state / name).read_bytes() for name in ('luks.done', 'filesystem.done')}
        again = self.run_resume()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.events().count('CRYPT_FORMAT'), 1)
        self.assertEqual(self.events().count('MKFS'), 1)
        self.assertTrue((self.state / 'initialize.done').exists())
        for name, data in before.items():
            self.assertEqual((self.state / name).read_bytes(), data)

    def test_luks_probe_errors_foreign_signatures_and_blankness_failure_never_format(self):
        for env in ({'PART_STATUS': '8'}, {'PART_STATUS': '0'},
                    {'PART_SIGNATURE': 'ext4', 'PART_STATUS': '0'},
                    {'PART_SIGNATURE': 'x', 'PART_STATUS': '2'},
                    {'WIPE_SIGNATURE': 'gpt'}, {'WIPE_FAIL': '1'}):
            with self.subTest(env=env):
                result = self.run_resume(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('CRYPT_FORMAT', self.events())
                self.assertNotIn('MKFS', self.events())
                self.assertFalse((self.state / 'luks.started').exists())

    def test_mapping_probe_failure_never_creates_filesystem(self):
        (self.state / 'header').touch()
        for env in ({'FS_STATUS': '8'}, {'FS_STATUS': '0'},
                    {'FS_STATUS': '0', 'FS_SIGNATURE': 'LVM2_member'},
                    {'WIPE_FAIL': '1'}, {'WIPE_SIGNATURE': 'xfs'}):
            with self.subTest(env=env):
                result = self.run_resume(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('MKFS', self.events())
                self.assertFalse((self.state / 'filesystem.started').exists())

    def test_changed_partition_staging_or_key_stop_before_format(self):
        for env in ({'PARENT': 'sdc'}, {'PART_TYPE': 'wrong'},
                    {'PART_UUID': '44444444-4444-4444-4444-444444444444'}, {'BAD_STAGING': '1'}, {'HASH_FAIL': '1'}):
            with self.subTest(env=env):
                self.assertNotEqual(self.run_resume(**env).returncode, 0)
                self.assertNotIn('CRYPT_FORMAT', self.events())
        self.key.chmod(0o600)
        self.key.write_bytes(b'y' * 64)
        self.key.chmod(0o400)
        result = self.run_resume()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('key differs', result.stderr)
        self.assertNotIn('CRYPT_FORMAT', self.events())

    def test_interrupted_unreadable_formats_cannot_be_repeated(self):
        result = self.run_resume(LUKS_FORMAT_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state / 'luks.done').exists())
        retry = self.run_resume()
        self.assertNotEqual(retry.returncode, 0)
        self.assertEqual(self.events().count('CRYPT_FORMAT'), 1)
        (self.state / 'header').touch()
        result = self.run_resume(MKFS_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state / 'filesystem.done').exists())
        retry = self.run_resume()
        self.assertNotEqual(retry.returncode, 0)
        self.assertEqual(self.events().count('MKFS'), 1)

    def test_completed_readable_header_can_be_recovered_but_unknown_ext4_cannot(self):
        (self.state / 'header').touch()
        (self.state / 'filesystem').touch()
        result = self.run_resume()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('without a recorded format intent', result.stderr)
        (self.state / 'filesystem.started').write_text('format intent\n')
        result = self.run_resume()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('CRYPT_FORMAT', self.events())
        self.assertNotIn('MKFS', self.events())

    def test_wrong_completed_uuid_or_unreadable_header_never_reformat(self):
        self.assertEqual(self.run_resume().returncode, 0)
        for env in ({'FS_UUID': '55555555-5555-5555-5555-555555555555'}, {'HEADER_FAIL': '1'}, {'WRONG_KEY': '1'}):
            with self.subTest(env=env):
                self.assertNotEqual(self.run_resume(**env).returncode, 0)
        self.assertEqual(self.events().count('CRYPT_FORMAT'), 1)
        self.assertEqual(self.events().count('MKFS'), 1)

    def test_confirmation_and_vm_blank_disk_gate_precede_any_format_intent(self):
        self.key.unlink()
        (self.state / 'initialize.started').unlink()
        (self.state / 'partition.done').unlink()
        prefix = SOURCE.split('    initialize)\n', 1)[1].split(
            "        printf 'started %s", 1)[0]
        prefix = prefix.replace(' > /dev/tty', ' >> "$EVENTS"').replace(
            ' < /dev/tty', ' <<< "${CONFIRMATION:-wrong}"')
        setup = r'''
PROFILE=vm; SOURCE_MOUNT=/mnt/source
check_workspace_source() { :; }
check_retained_source() { :; }
workspace_copy_differences() { :; }
mountpoint() { return 1; }
'''
        command = self.shell + self.helpers + setup + prefix + 'echo APPROVED >> "$EVENTS"\n'
        for env in ({}, {'FS_STATUS': '0', 'FS_SIGNATURE': 'xfs'}, {'MOUNT_SCAN_FAIL': '1'}):
            result = subprocess.run(['bash', '-c', command, 'initialize', '--source', '/mnt/source'],
                                    env=dict(os.environ, **env), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('APPROVED', self.events())
            self.assertFalse((self.state / 'initialize.started').exists())
        result = subprocess.run(['bash', '-c', command, 'initialize', '--source', '/mnt/source'],
                                env=dict(os.environ, CONFIRMATION='ERASE-EXTRA EXTRA 8:16'),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('APPROVED', self.events())

    def test_real_blank_file_and_ext4_signature_use_the_same_probe(self):
        path = os.environ.get('PATH', '') + ':/usr/sbin:/sbin'
        mkfs = shutil.which('mkfs.ext4', path=path)
        if not mkfs or not shutil.which('blkid', path=path) or not shutil.which('wipefs', path=path):
            self.skipTest('requires filesystem inspection tools')
        image = self.root / 'probe.img'
        with image.open('wb') as stream:
            stream.truncate(32 * 1024 * 1024)
        script = ('set -Eeuo pipefail\nfail() { echo "$*" >&2; exit 19; }\n'
                  + function('extra_probe_type') + function('extra_require_blank')
                  + 'extra_require_blank "$1"\n')
        run = lambda: subprocess.run(['bash', '-c', script, 'probe', str(image)],
                                     env=dict(os.environ, PATH=path), text=True, capture_output=True)
        self.assertEqual(run().returncode, 0)
        subprocess.run([mkfs, '-q', '-F', str(image)], check=True, capture_output=True)
        present = run()
        self.assertNotEqual(present.returncode, 0)
        self.assertIn('existing signature', present.stderr)

    def test_failed_inventory_or_mounted_descendant_blocks_destruction(self):
        for env in ({'MOUNT_SCAN_FAIL': '1'}, {'LSBLK_FAIL': '1'}, {'MOUNTS': '8:16 /protected'}):
            result = subprocess.run(['bash', '-c', self.shell + self.helpers + 'extra_check_mounts\n'],
                                    env=dict(os.environ, **env), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
        good = subprocess.run(['bash', '-c', self.shell + self.helpers + 'extra_check_mounts\n'],
                              capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)
