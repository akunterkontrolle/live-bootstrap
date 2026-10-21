"""Resume before LUKS creation without repartitioning or repeating a started format."""
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import importlib.util
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
SOURCE = (GENTOO / 'install-base.sh').read_text()
HELPER = SOURCE[SOURCE.index('initialize_blank_luks() {'):SOURCE.index('\nresume_storage()')]

spec = importlib.util.spec_from_file_location('pre_luks_repair', GENTOO / 'repair-pre-luks-resume.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)



class LuksResumeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.state / 'target.identity').write_text('verified existing target\n')
        self.script = ('set -Eeuo pipefail\nSTATE=' + shlex.quote(str(self.state)) + '\nEVENTS=' +
                       shlex.quote(str(self.root / 'events')) + '\n' + HELPER + '''
ROOT_PART=/dev/fixture1; ROOT_SERIAL=TESTROOT; ROOT_MAJMIN=8:32
MODE=--initialize-storage
fail() { echo "$*" >&2; exit 19; }
sha256() { sha256sum < "$1" | awk '{print $1}'; }
wipefs() { echo wipefs >> "$EVENTS"; [ "${WIPE_FAIL:-0}" = 0 ] || return 7; printf '%s' "${SIGNATURE:-}"; }
blkid() {
    echo blkid >> "$EVENTS"
    if [ "${GPT_ONLY:-0}" = 1 ]; then
        case " $* " in *' -D '*) return 2;; *) return 0;; esac
    fi
    return "${BLKID_STATUS:-2}"
}
read_secret() {
    COUNT=$((COUNT+1))
    case "$COUNT" in 1) printf -v "$2" first;; 2) printf -v "$2" mismatch;; *) printf -v "$2" correct;; esac
}
phase() { local marker=$1; shift; echo "$marker" >> "$EVENTS"; printf '%s\\n' "$@" > "$STATE/$marker"; }
native_run() {
    case "$2" in
        luksFormat)
            [ -s "$STATE/luks.started" ] || exit 98
            echo FORMAT >> "$EVENTS"; cat >/dev/null
            [ "${FORMAT_FAIL:-0}" = 0 ] || return 7;;
        isLuks) echo VERIFY >> "$EVENTS";;
        luksUUID) echo UUID;;
        *) echo "unexpected native operation" >&2; exit 99;;
    esac
}
COUNT=0
initialize_blank_luks
''')

    def run_helper(self, **overrides):
        return subprocess.run(['bash', '-c', self.script], env=dict(os.environ, **overrides),
                              capture_output=True, text=True, timeout=5)

    def test_mismatched_passphrase_reprompts_before_any_format_intent(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mismatched passphrases', result.stderr)
        events = (self.root / 'events').read_text().splitlines()
        self.assertEqual(events, ['wipefs', 'blkid', 'luks.started', 'FORMAT', 'VERIFY', 'luks.done'])
        self.assertEqual((self.state / 'luks.done').read_text(), 'LUKS UUID=UUID\n')

    def test_gpt_partition_metadata_cannot_be_mistaken_for_a_superblock(self):
        fixed = self.run_helper(GPT_ONLY='1')
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        self.assertTrue((self.state / 'luks.done').exists())
        (self.state / 'luks.started').unlink()
        (self.state / 'luks.done').unlink()
        self.script = self.script.replace('blkid -p -D -s TYPE', 'blkid -p -s TYPE')
        broken = self.run_helper(GPT_ONLY='1')
        self.assertNotEqual(broken.returncode, 0)
        self.assertIn('not verified blank', broken.stderr)
        self.assertFalse((self.state / 'luks.started').exists())

    def test_no_partition_details_still_detects_a_real_ext4_signature(self):
        tool_path = os.environ.get('PATH', '') + ':/usr/sbin:/sbin'
        blkid = shutil.which('blkid', path=tool_path)
        mkfs = shutil.which('mkfs.ext4', path=tool_path)
        if not blkid or not mkfs:
            self.skipTest('requires blkid and mkfs.ext4')
        image = self.root / 'probe.img'
        with image.open('wb') as stream:
            stream.truncate(32 * 1024 * 1024)
        blank = subprocess.run([blkid, '-p', '-D', '-s', 'TYPE', '-o', 'value', str(image)],
                               text=True, capture_output=True)
        self.assertEqual((blank.returncode, blank.stdout), (2, ''))
        subprocess.run([mkfs, '-q', '-F', str(image)], check=True, capture_output=True)
        present = subprocess.run([blkid, '-p', '-D', '-s', 'TYPE', '-o', 'value', str(image)],
                                 text=True, capture_output=True)
        self.assertEqual((present.returncode, present.stdout.strip()), (0, 'ext4'))

    def test_signature_probe_failure_and_existing_signature_never_format(self):
        for env in ({'SIGNATURE': 'crypto_LUKS'}, {'WIPE_FAIL': '1'}, {'BLKID_STATUS': '4'}, {'BLKID_STATUS': '0'}):
            result = self.run_helper(**env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.state / 'luks.started').exists())
            self.assertNotIn('FORMAT', (self.root / 'events').read_text())

    def test_format_start_or_downstream_state_prevents_reformat(self):
        for name in ('luks.started', 'luks.done', 'pv.done', 'vg.done', 'lv-home.done', 'fs-root.started'):
            marker = self.state / name
            marker.write_text('existing receipt\n')
            result = self.run_helper()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / 'events').exists())
            marker.unlink()

    def test_failed_format_keeps_intent_and_cannot_be_repeated(self):
        first = self.run_helper(FORMAT_FAIL='1')
        self.assertEqual(first.returncode, 7, first.stderr)
        self.assertTrue((self.state / 'luks.started').exists())
        self.assertFalse((self.state / 'luks.done').exists())
        events = (self.root / 'events').read_text()
        retry = self.run_helper()
        self.assertNotEqual(retry.returncode, 0)
        self.assertEqual((self.root / 'events').read_text(), events)

    def test_resume_confirmation_is_required_before_formatting(self):
        self.script = self.script.replace(' > /dev/tty', ' >> "$EVENTS"').replace(
            'MODE=--initialize-storage', 'MODE=--resume-storage')
        self.script = self.script.replace('COUNT=0\n', 'read_tty_line() { printf -v "$2" wrong; }\nCOUNT=0\n')
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state / 'luks.started').exists())
        self.assertNotIn('FORMAT', (self.root / 'events').read_text())
        self.script = self.script.replace('printf -v "$2" wrong', 'printf -v "$2" "CREATE LUKS TESTROOT 8:32"')
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.state / 'luks.done').exists())

    def test_actual_resume_checks_target_and_partition_before_formatting(self):
        prefix = SOURCE.split('resume_storage() {', 1)[1].split(
            '    native_run cryptsetup isLuks "$ROOT_PART"', 1)[0]
        for name, data in (('storage.started', 'verified\n'),
                           ('partition.done', 'partition=/dev/fixture1\n')):
            (self.state / name).write_text(data)
        script = self.script.replace(' > /dev/tty', ' >> "$EVENTS"')
        script = script.replace('MODE=--initialize-storage', 'MODE=--resume-storage')
        setup = '. ' + shlex.quote(str(GENTOO / 'lib/storage.sh')) + """
verify_target() { echo identity >> "$EVENTS"; [ "${TARGET_FAIL:-0}" = 0 ] || fail 'identity differs'; }
lsblk() { printf '%s' "${PARTTYPE:-CA7D7CCB-63ED-4C53-861C-1742536059CC}"; }
[() { if builtin [ "$#" = 3 ] && builtin [ "$1" = -b ]; then return 0; fi; builtin [ "$@"; }
read_tty_line() { printf -v "$2" 'CREATE LUKS TESTROOT 8:32'; }
resume_checkpoint() {
""" + prefix + '\n}\nresume_checkpoint\n'
        self.script = script.rsplit('initialize_blank_luks\n', 1)[0] + setup
        for env in ({'TARGET_FAIL': '1'}, {'PARTTYPE': 'wrong'}):
            result = self.run_helper(**env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.state / 'luks.started').exists())
            self.assertNotIn('FORMAT', (self.root / 'events').read_text())
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.state / 'luks.done').exists())

    def test_partition_gate_accepts_pre_luks_but_requires_all_three_receipts(self):
        script = '. ' + shlex.quote(str(GENTOO / 'lib/storage.sh')) + '\ngentoo_storage_partition_gate "$1"'
        for name in ('storage.started', 'partition.done'):
            self.assertNotEqual(subprocess.run(['bash', '-c', script, 'gate', str(self.state)], capture_output=True).returncode, 0)
            (self.state / name).write_text('existing receipt\n')
        self.assertEqual(subprocess.run(['bash', '-c', script, 'gate', str(self.state)], capture_output=True).returncode, 0)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        entries = {'gentoo.sh': b'unchanged driver\n'}
        for name, spec in repair.PATCHES.items():
            new = (GENTOO / name).read_text()
            self.assertEqual(repair.sha(new.encode()), spec['new'])
            offset = 0
            reverse = []
            for start, end, replacement, removed in spec['edits']:
                adjusted = start + offset
                reverse.append((adjusted, adjusted + len(replacement), removed))
                offset += len(replacement) - (end - start)
            for start, end, removed in reversed(reverse):
                new = new[:start] + removed + new[end:]
            entries[name] = new.encode()
            self.assertEqual(repair.sha(entries[name]), spec['old'])
        manifest = ''.join(repair.sha(data) + '  ' + name + '\n' for name, data in sorted(entries.items())).encode()
        identity = repair.sha(manifest)
        source = self.work / 'inputs' / identity
        for name, data in entries.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (source / 'SHA256SUMS').write_bytes(manifest)
        (self.work / 'current-inputs').write_text(identity + '\n')
        self.state = self.work / 'install-base-state'
        self.state.mkdir()
        self.receipts = {}
        for name in ('storage.started', 'target.identity', 'partition.done'):
            path = self.state / name
            path.write_text('verified partition checkpoint\n')
            self.receipts[path] = path.read_bytes()
        self.original = identity

    def test_full_paste_publishes_repeats_and_leaves_storage_receipts_untouched(self):
        paste = (GENTOO / 'repair-vm-pre-luks-resume.paste.txt').read_text().replace(
            "default=Path('/build/gentoo-bootstrap')", 'default=Path(' + repr(str(self.work)) + ')')
        for _ in range(2):
            result = subprocess.run(['bash', '-eu', '-s'], input=paste + '\necho PARENT_ALIVE\n',
                                    text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('storage-resume', result.stdout)
            self.assertIn('PARENT_ALIVE', result.stdout)
        selected = (self.work / 'current-inputs').read_text().strip()
        fixed = repair.snapshot(self.work, selected)
        for name, spec in repair.PATCHES.items():
            self.assertEqual(repair.sha(fixed[name]), spec['new'])
        repair.snapshot(self.work, self.original)
        for path, data in self.receipts.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse((self.state / 'luks.started').exists())

    def test_followup_updates_the_previously_published_repair_without_overwriting_its_journal(self):
        entries = {'gentoo.sh': b'unchanged driver\n'}
        for name, spec in repair.FOLLOWUP_PATCHES.items():
            text = (GENTOO / name).read_text()
            offset = 0
            reverse = []
            for start, end, replacement, removed in spec['edits']:
                adjusted = start + offset
                reverse.append((adjusted, adjusted + len(replacement), removed))
                offset += len(replacement) - (end - start)
            for start, end, removed in reversed(reverse):
                text = text[:start] + removed + text[end:]
            self.assertEqual(repair.sha(text.encode()), spec['old'])
            entries[name] = text.encode()
        manifest = ''.join(repair.sha(data) + '  ' + name + '\n' for name, data in sorted(entries.items())).encode()
        identity = repair.sha(manifest)
        source = self.work / 'inputs' / identity
        for name, data in entries.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (source / 'SHA256SUMS').write_bytes(manifest)
        (self.work / 'current-inputs').write_text(identity + '\n')
        previous = self.work / 'repair-state/pre-luks-inputs.json'
        previous.parent.mkdir()
        previous.write_text('previous repair evidence\n')
        driver = repair.repair(self.work)
        updated = repair.snapshot(self.work, driver.parent.name)
        self.assertIn(b'blkid -p -D -s TYPE', updated['install-base.sh'])
        self.assertEqual(previous.read_text(), 'previous repair evidence\n')
        for path, data in self.receipts.items():
            self.assertEqual(path.read_bytes(), data)

    def test_later_storage_state_refuses_before_source_selection_changes(self):
        for name in ('luks.started', 'luks.done', 'pv.done'):
            path = self.state / name
            path.write_text('existing state\n')
            with self.assertRaises(ValueError):
                repair.repair(self.work)
            self.assertEqual((self.work / 'current-inputs').read_text().strip(), self.original)
            path.unlink()
