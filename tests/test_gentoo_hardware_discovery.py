"""Discover disks without udev and reject ambiguity or conflicting links."""
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'


class HardwareDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.observed = self.root / 'inventory'
        self.observed.write_text('/dev/fixture-a|SERIAL|1000|naa.500abc|Disk Model\n'
                                 '/dev/fixture-b|OTHER|1000|0x500def|Other Model\n')
        self.prefix = '''set -Eeuo pipefail
. HELPER
FIXTURE=FIXTURE_PATH
gentoo_die() { echo "$*" >&2; exit 19; }
# Only kernel/udev observations are substituted. Matching and disambiguation
# run through the production functions; no real storage command is called.
gentoo_whole_disks() { cut -d'|' -f1 "$FIXTURE/inventory"; }
gentoo_disk_serial() { awk -F'|' -v disk="$1" '$1==disk {print $2}' "$FIXTURE/inventory"; }
blockdev() { awk -F'|' -v disk="$2" '$1==disk {print $3}' "$FIXTURE/inventory"; }
gentoo_disk_attribute() {
    awk -F'|' -v disk="$2" -v field="$1" '$1==disk {if(field=="WWN") print $4; else print $5}' "$FIXTURE/inventory"
}
# Use actual private filesystem entries for the by-id metadata query.
eval "$(declare -f gentoo_by_id_target | sed '1s/gentoo_by_id_target/read_fixture_link/')"
gentoo_by_id_target() { read_fixture_link "$FIXTURE/${1##*/}"; }
'''.replace('HELPER', shlex.quote(str(GENTOO / 'lib/hardware-discovery.sh'))).replace('FIXTURE_PATH', shlex.quote(str(self.root)))

    def resolve(self, serial='SERIAL', maximum=1100, wwn='0x500abc', model='Disk Model', by_id='ata-target'):
        command = self.prefix + '\ngentoo_resolve_hardware_disk ' + ' '.join(map(shlex.quote, (
            '/dev/disk/by-id/' + by_id, serial, '900', str(maximum), wwn, model)))
        return subprocess.run(['bash', '-c', command], text=True, capture_output=True, timeout=3)

    def test_missing_udev_link_resolves_only_the_fully_verified_disk(self):
        result = self.resolve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '/dev/fixture-a\n')
        self.assertFalse((self.root / 'ata-target').exists())
        (self.root / 'ata-target').symlink_to('/dev/fixture-a')
        self.assertEqual(self.resolve().returncode, 0)

    def test_serial_capacity_model_and_wwn_mismatches_refuse(self):
        for arguments in ({'serial': 'missing'}, {'maximum': 999}, {'wwn': '0x999'}, {'model': 'Wrong Model'}):
            result = self.resolve(**arguments)
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertIn('missing or ambiguous', result.stderr)

    def test_two_matching_kernel_disks_are_ambiguous_even_with_a_by_id_link(self):
        self.observed.write_text(self.observed.read_text() + '/dev/fixture-c|SERIAL|1000|0x500abc|Disk Model\n')
        (self.root / 'ata-target').symlink_to('/dev/fixture-a')
        result = self.resolve()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ambiguous', result.stderr)

    def test_conflicting_or_nonlink_by_id_path_refuses_and_is_preserved(self):
        link = self.root / 'ata-target'
        link.symlink_to('/dev/fixture-b')
        result = self.resolve()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('conflicts', result.stderr)
        self.assertEqual(os.readlink(link), '/dev/fixture-b')
        link.unlink()
        link.write_text('unexpected operator data')
        result = self.resolve()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(link.read_text(), 'unexpected operator data')

    def test_partition_by_id_is_rejected(self):
        result = self.resolve(by_id='ata-target-part1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('whole-disk', result.stderr)

    def test_empty_lsblk_values_fall_back_to_sysfs_model_and_wwn(self):
        script = ('. ' + shlex.quote(str(GENTOO / 'lib/devices.sh')) + '\n' + '''
lsblk() { return 0; }
cat() { case "$1" in */device/model) echo '  Disk Model  ';; */wwid) echo naa.500ABC;; *) return 1;; esac; }
gentoo_disk_attribute MODEL /dev/fixture-a
gentoo_disk_attribute WWN /dev/fixture-a
''')
        result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['Disk Model', '0x500ABC'])

    def test_missing_optional_vm_attributes_are_allowed_but_hardware_inspection_failure_refuses(self):
        recipe = (GENTOO / 'install-base.sh').read_text()
        function = 'installer_disk_attribute() {' + recipe.split('installer_disk_attribute() {', 1)[1].split('check_disk() {', 1)[0]
        for mode, expected in (('vm', 0), ('hardware', 19)):
            script = ('set -Eeuo pipefail\nGENTOO_MODE=' + mode + '\n'
                      'fail() { echo "$*" >&2; exit 19; }\n'
                      'gentoo_disk_attribute() { return 1; }\n' + function + '\n'
                      'installer_disk_attribute WWN /dev/fixture-a\n')
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
            self.assertEqual(result.returncode, expected, result.stderr)

    def test_kernel_model_remains_canonical_when_udev_reports_a_longer_name(self):
        script = ('. ' + shlex.quote(str(GENTOO / 'lib/devices.sh')) + '\n' + '''
lsblk() { echo 'Disk Model with more marketing details'; }
cat() { case "$1" in */device/model) echo 'Disk Model';; *) return 1;; esac; }
gentoo_disk_attribute MODEL /dev/fixture-a
''')
        result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Disk Model\n')

    def test_unit_serial_vpd_fallback_validates_page_type_length_and_payload(self):
        page = self.root / 'vpd_pg80'
        payload = b'  SATA-SERIAL  '
        correct = b'\0\x80' + len(payload).to_bytes(2, 'big') + payload
        for data, accepted in ((correct, True), (correct[:-1], False),
                               (correct.replace(b'\x80', b'\x83', 1), False),
                               (b'\0\x80\0\1\0', False), (b'\0\x80\0\1 ', False)):
            page.write_bytes(data)
            script = ('. ' + shlex.quote(str(GENTOO / 'lib/hardware-discovery.sh'))
                      + '\ngentoo_sysfs_vpd_serial ' + shlex.quote(str(page)))
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
            self.assertEqual(result.returncode == 0, accepted, result.stderr)
            if accepted:
                self.assertEqual(result.stdout, 'SATA-SERIAL\n')

    def test_serial_uses_readonly_vpd_when_udev_and_text_serial_are_absent(self):
        payload = b'GUEST-SERIAL '
        (self.root / 'vpd_pg80').write_bytes(b'\0\x80' + len(payload).to_bytes(2, 'big') + payload)
        script = ('. ' + shlex.quote(str(GENTOO / 'lib/devices.sh')) + '\n'
                  'lsblk() { return 0; }\n'
                  'readlink() { echo ' + shlex.quote(str(self.root)) + '; }\n'
                  'gentoo_native_ata_serial() { echo UNEXPECTED_ATA >&2; return 9; }\n'
                  'gentoo_disk_serial /dev/fixture-a\n')
        result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'GUEST-SERIAL\n')
