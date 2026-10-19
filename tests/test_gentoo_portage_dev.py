# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage 71 must mount proc/dev for Portage and unmount them on every return."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


def helper():
    source = (GENTOO / 'stages/71-native-portage.sh').read_text()
    return 'native_portage_with_devices() (' + source.split('native_portage_with_devices() (', 1)[1].split('\nnative_portage_tree_hash()', 1)[0]


class ChrootDeviceTests(unittest.TestCase):
    def test_mount_ownership_and_cleanup_after_propagation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'native'
            log = Path(directory) / 'calls'
            script = '''set -Eeuo pipefail
NATIVE_ROOT={root}
LOG={log}
mountpoint() {{ return 1; }}
mount() {{
    printf 'mount %s\\n' "$*" >> "$LOG"
    case "$*" in *--make-rslave*) return 9;; esac
}}
gentoo_unmount_tree() {{ printf 'unmount %s\\n' "$1" >> "$LOG"; }}
{helper}
callback() {{ printf 'CALLBACK SHOULD NOT RUN\\n' >> "$LOG"; }}
if native_portage_with_devices callback; then exit 99; fi
'''.format(root=shlex.quote(str(root)), log=shlex.quote(str(log)), helper=helper())
            root.mkdir()
            subprocess.run(['bash', '-c', script], check=True, capture_output=True)
            lines = log.read_text().splitlines()
            self.assertNotIn('CALLBACK SHOULD NOT RUN', lines)
            self.assertEqual(lines[-2:], ['unmount ' + str(root / 'sys'), 'unmount ' + str(root / 'proc')])
            self.assertFalse(any('/dev' in line for line in lines))

    def test_real_namespace_chroot_pinned_portage_before_and_after_mounts(self):
        source = os.environ.get('GENTOO_AUDIT_PORTAGE_SOURCE')
        prefix = os.environ.get('GENTOO_AUDIT_STATIC_PREFIX')
        if not source or not prefix or not shutil.which('unshare'):
            self.skipTest('set the pinned source/static Python audit paths; unshare is required')
        supported = subprocess.run(['unshare', '--user', '--map-root-user', '--mount', '--pid', '--fork', 'true'],
                                   capture_output=True)
        if supported.returncode:
            self.skipTest('user/mount/PID namespaces unavailable: ' + supported.stderr.decode().strip())
        with tempfile.TemporaryDirectory(prefix='gentoo-portage-dev-') as directory:
            root = Path(directory) / 'root'
            for name in ('bin', 'usr/bin', 'usr/lib/python3.12/lib-dynload', 'etc/portage', 'dev', 'proc', 'sys', 'tmp', 'root'):
                (root / name).mkdir(parents=True)
            def copy_executable(path, destination=None):
                path = Path(path)
                dest = root / (destination or str(path).lstrip('/'))
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
                shutil.copymode(path, dest)
                listing = subprocess.run(['ldd', str(path)], capture_output=True, text=True)
                for name in re.findall(r'(?:^|\s)(/[^\s]+)', listing.stdout):
                    library = Path(name)
                    target = root / name.lstrip('/')
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(library, target)
                        shutil.copymode(library, target)
            copy_executable('/bin/bash')
            copy_executable(shutil.which('bzip2'), 'usr/bin/bzip2')
            audit = Path(prefix).parent
            copy_executable(audit / 'python-build/python', 'usr/bin/python3.12')
            shutil.copytree(audit / 'python-source/Lib', root / 'usr/lib/python3.12', dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('test', '__pycache__'))
            for build_lib in (audit / 'python-build/build').glob('lib.*'):
                for path in build_lib.iterdir():
                    if path.suffix == '.so':
                        copy_executable(path, 'usr/lib/python3.12/lib-dynload/' + path.name)
                    elif path.name.startswith('_sysconfigdata') and path.suffix == '.py':
                        shutil.copyfile(path, root / 'usr/lib/python3.12' / path.name)
            shutil.copytree(source, root / 'usr/lib/portage', ignore=shutil.ignore_patterns('__pycache__'))
            (root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/bash\nportage:x:250:250:Portage:/tmp:/bin/bash\n')
            (root / 'etc/group').write_text('root:x:0:\nportage:x:250:\n')
            (root / 'etc/portage/make.conf').write_text('FEATURES="-test strict"\n')
            script = '''set -Eeuo pipefail
mount --make-rprivate /
export PATH=/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C
NATIVE_ROOT={root}
# A minimal device fixture avoids inherited locked sandbox submounts, which
# cannot be unmounted individually in a user namespace. Mounts remain real.
DEVICE_FIXTURE={device}
SYSTEM_FIXTURE={system}
mkdir -p "$DEVICE_FIXTURE" "$SYSTEM_FIXTURE"
command mount -t tmpfs tmpfs "$DEVICE_FIXTURE"
touch "$DEVICE_FIXTURE/null"
command mount --bind /dev/null "$DEVICE_FIXTURE/null"
ln -s /proc/self/fd "$DEVICE_FIXTURE/fd"
ln -s /proc/self/fd/0 "$DEVICE_FIXTURE/stdin"
ln -s /proc/self/fd/1 "$DEVICE_FIXTURE/stdout"
ln -s /proc/self/fd/2 "$DEVICE_FIXTURE/stderr"
mount() {{
    if [ "$1" = --rbind ] && [ "$2" = /dev ]; then
        command mount --rbind "$DEVICE_FIXTURE" "$3"
    elif [ "$1" = --rbind ] && [ "$2" = /sys ]; then
        command mount --rbind "$SYSTEM_FIXTURE" "$3"
    else
        command mount "$@"
    fi
}}
. {common}
. {mounts}
{helper}
run_portage() {{
    env PYTHONHOME=/usr PYTHONPATH=/usr/lib/portage/lib PYTHONDONTWRITEBYTECODE=1 LANG=C LC_ALL=C PORTAGE_USERNAME=root PORTAGE_GRPNAME=root \
        chroot "$NATIVE_ROOT" /usr/bin/python3.12 /usr/lib/portage/bin/emerge --version
}}
# Reproduce the real Portage error with /dev present but no proc mount.
mount --rbind /dev "$NATIVE_ROOT/dev"
mount --make-rslave "$NATIVE_ROOT/dev"
if run_portage > {before} 2>&1; then echo 'broken chroot unexpectedly passed'; exit 1; fi
grep -F "bash process substitution doesn't work" {before}
gentoo_unmount_tree "$NATIVE_ROOT/dev"
# Exercise both action and verification entries, and actual Portage execution.
native_portage_with_devices run_portage > {after} 2>&1
grep -F 'Portage HEAD (' {after}
native_portage_with_devices run_portage > {verified} 2>&1
for path in proc sys dev; do if mountpoint -q "$NATIVE_ROOT/$path"; then exit 1; fi; done
failing_callback() {{ return 7; }}
if native_portage_with_devices failing_callback; then exit 1; else result=$?; [ "$result" = 7 ]; fi
for path in proc sys dev; do if mountpoint -q "$NATIVE_ROOT/$path"; then exit 1; fi; done
# Exercise actual errexit unwinding as well as conditional failure returns.
errexit_callback() {{ false; printf 'UNREACHABLE'; }}
set +e
(set -e; native_portage_with_devices errexit_callback) > {error} 2>&1
result=$?
set -e
[ "$result" = 1 ]
if grep -Eq 'UNREACHABLE|unbound variable' {error}; then exit 1; fi
for path in proc sys dev; do if mountpoint -q "$NATIVE_ROOT/$path"; then exit 1; fi; done
'''.format(root=shlex.quote(str(root)), common=shlex.quote(str(GENTOO / 'lib/common.sh')),
           mounts=shlex.quote(str(GENTOO / 'lib/mounts.sh')), helper=helper(),
           device=shlex.quote(str(Path(directory) / 'devices')),
           system=shlex.quote(str(Path(directory) / 'system')),
           before=shlex.quote(str(Path(directory) / 'before.txt')),
           after=shlex.quote(str(Path(directory) / 'after.txt')),
           verified=shlex.quote(str(Path(directory) / 'verify.txt')),
           error=shlex.quote(str(Path(directory) / 'callback-error.txt')))
            result = subprocess.run(['unshare', '--user', '--map-root-user', '--mount', '--pid', '--fork',
                                     'bash', '-c', script], capture_output=True, text=True, timeout=40)
            diagnostics = result.stdout + result.stderr
            for name in ('before.txt', 'after.txt', 'verify.txt'):
                if (Path(directory) / name).exists():
                    diagnostics += '\n' + name + ':\n' + (Path(directory) / name).read_text()
            self.assertEqual(result.returncode, 0, diagnostics)
