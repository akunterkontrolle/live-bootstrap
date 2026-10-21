"""Exercise the split-lib startup binding before native CPU detection."""
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'
STAGE = 'stages/72-native-cpu-policy.sh'
OLD_LINK = '    "$PREFIX/bin/$TARGET-gcc" --sysroot="$SYSROOT" -O2 -pipe -static \\\n'
NEW_LINK = '    # The bootstrap cross driver does not infer the split usr/lib64 layout.\n    # Bind startup objects and static libc just as the earlier link probes do.\n    "$PREFIX/bin/$TARGET-gcc" -m64 --sysroot="$SYSROOT" \\\n        -B"$SYSROOT/usr/lib64/" -L"$SYSROOT/usr/lib64" -O2 -pipe -static \\\n'


class CpuLinkTests(unittest.TestCase):
    def test_actual_action_binds_split_lib64_before_detection(self):
        recipe = (GENTOO / STAGE).read_text()
        action = recipe[recipe.index('cpu_policy_action() {'):recipe.index('\ncpu_policy_verify()')]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tool/bin').mkdir(parents=True)
            (root / 'sysroot/usr/lib64').mkdir(parents=True)
            (root / 'state').mkdir()
            compiler = root / 'tool/bin/target-gcc'
            compiler.write_text('''#!/bin/bash
set -eu
seen_b=0; seen_l=0; seen_64=0; seen_static=0; output=
for arg; do
    case "$arg" in
        -B"$FIXTURE/sysroot/usr/lib64/") seen_b=1;;
        -L"$FIXTURE/sysroot/usr/lib64") seen_l=1;;
        -m64) seen_64=1;; -static) seen_static=1;;
    esac
done
[ "$seen_b$seen_l$seen_64$seen_static" = 1111 ] || { echo 'missing startup binding' >&2; exit 17; }
while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then output=$2; break; fi
    shift
done
printf '#!/bin/sh\\nprintf "cpuid2cpuflags 18\\\\n"\\n' > "$output"
chmod 755 "$output"
''')
            compiler.chmod(0o755)
            (root / 'cpu-policy.py').write_text('import pathlib, sys\npathlib.Path(sys.argv[sys.argv.index("--receipt")+1]).write_text("detected")\n')
            variables = ('set -Eeuo pipefail\nWORK_ROOT=' + shlex.quote(temporary) + '\n'
                         'SCRIPT_DIR=$WORK_ROOT\nPREFIX=$WORK_ROOT/tool\nTARGET=target\n'
                         'SYSROOT=$WORK_ROOT/sysroot\nCPU_RECEIPT=$WORK_ROOT/state/cpu.json\n'
                         'CPU_PREREQUISITE=$WORK_ROOT/native-build-tools/bin/cpuid2cpuflags\n'
                         'gentoo_prepare_source() { :; }\n')
            env = dict(os.environ, FIXTURE=temporary)
            old = action.replace(NEW_LINK, OLD_LINK)
            broken = subprocess.run(['bash', '-c', variables + old + '\ncpu_policy_action'],
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(broken.returncode, 17)
            self.assertFalse((root / 'state/cpu.json').exists())
            fixed = subprocess.run(['bash', '-c', variables + action + '\ncpu_policy_action'],
                                   env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            self.assertEqual((root / 'state/cpu.json').read_text(), 'detected')
            self.assertTrue((root / 'state/cpu-detector.sha256').is_file())
