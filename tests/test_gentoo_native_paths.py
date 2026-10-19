"""Sequential native builds retain their caller cwd and bind absolute locks."""
import hashlib
import io
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'


class NativePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        self.work = self.root / 'work'
        for name in ('sources', 'lib'):
            (self.bundle / name).mkdir(parents=True)
        for name in ('distfiles', 'state', 'build/native', 'native/usr/bin'):
            (self.work / name).mkdir(parents=True)
        (self.bundle / 'lib/native.sh').write_text('')
        (self.bundle / 'lib/native-tools.sh').write_bytes((GENTOO / 'lib/native-tools.sh').read_bytes())

    def source(self, package, lock):
        archive = package + '.tar.gz'
        with tarfile.open(self.work / 'distfiles' / archive, 'w:gz') as stream:
            content = b'#!/bin/sh\ntouch config.status\n'
            info = tarfile.TarInfo(package + '/configure')
            info.size = len(content)
            info.mode = 0o755
            stream.addfile(info, io.BytesIO(content))
        digest = hashlib.sha512((self.work / 'distfiles' / archive).read_bytes()).hexdigest()
        (self.bundle / 'sources' / lock).write_text(archive + '|https://fixture.invalid/' + archive + '|sha512=' + digest + '\n')
        return archive

    def shell(self, extra, success=True):
        script = '''set -Eeuo pipefail
SCRIPT_DIR={bundle}
WORK_ROOT={work}
NATIVE_BUILDS="$WORK_ROOT/build/native"
NATIVE_ROOT="$WORK_ROOT/native"
NATIVE_CROSS_GCC=/usr/bin/gcc
NATIVE_CROSS_GXX=/usr/bin/g++
NATIVE_TRIPLE=x86_64-pc-linux-gnu
NATIVE_JOBS=1
GENTOO_MODERN_KERNEL_READY=1
gentoo_die() {{ printf '%s\\n' "$*" >&2; exit 1; }}
. {sources}
native_recipe_fingerprint() {{ printf '%064d\\n' 0; }}
gentoo_fingerprint() {{ printf '%064d\\n' 0; }}
gentoo_run_stage() {{ :; }}
. {stage}
. {tools}
. {floor}
native_verify_elf() {{ test -x "$1"; }}
native_run() {{ "$@"; }}
make() {{
    printf '%s\\n' "$PWD $*" >> "$WORK_ROOT/make-calls"
    case " $* " in *' install '*)
        printf '#!/bin/sh\\nexit 0\\n' > "$NATIVE_ROOT/usr/bin/$FIXTURE_BINARY"
        chmod 0755 "$NATIVE_ROOT/usr/bin/$FIXTURE_BINARY"
        ;;
    esac
}}
cd {start}
{extra}
'''.format(bundle=shlex.quote(str(self.bundle)), work=shlex.quote(str(self.work)),
           sources=shlex.quote(str(GENTOO / 'lib/sources.sh')),
           stage=shlex.quote(str(GENTOO / 'stages/61-native-shell-tools.sh')),
           tools=shlex.quote(str(GENTOO / 'lib/native-tools.sh')),
           floor=shlex.quote(str(GENTOO / 'stages/63-native-tool-floor.sh')),
           start=shlex.quote(str(self.root)), extra=extra)
        result = subprocess.run(['bash', '-c', script], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_two_shell_tools_from_outside_bundle_keep_cwd_and_correct_lock_receipts(self):
        self.source('bash-5.3', 'native-bash.lock')
        self.source('coreutils-9.11', 'native-coreutils.lock')
        self.shell('''
start=$PWD
FIXTURE_BINARY=bash
native_build_one bash-5.3.tar.gz sources/native-bash.lock bash-5.3 bash --version ''
[ "$PWD" = "$start" ]
FIXTURE_BINARY=env
native_build_one coreutils-9.11.tar.gz sources/native-coreutils.lock coreutils-9.11 env --version ''
[ "$PWD" = "$start" ]
# Repeat verification must not need another configure/build/install.
native_build_one coreutils-9.11.tar.gz sources/native-coreutils.lock coreutils-9.11 env --version ''
''')
        self.assertEqual(len((self.work / 'make-calls').read_text().splitlines()), 4)
        for package, lock in (('bash-5.3', 'native-bash'), ('coreutils-9.11', 'native-coreutils')):
            expected = hashlib.sha256((self.bundle / 'sources' / (lock + '.lock')).read_bytes()).hexdigest()
            marker = (self.work / 'state' / ('61-' + package + '.done')).read_text()
            self.assertIn('archive=' + expected + '\n', marker)
            self.assertIn('recipe=stage61-' + package + '-v2\n', marker)
            self.assertRegex(marker, r'program sha256=[0-9a-f]{64}')
        (self.work / 'native/usr/bin/bash').write_text('changed')
        self.shell("FIXTURE_BINARY=bash\nnative_build_one bash-5.3.tar.gz sources/native-bash.lock bash-5.3 bash --version ''", success=False)

    def test_two_utility_builds_keep_cwd_and_source_identity(self):
        self.source('tool-a', 'tool-a.lock')
        self.source('tool-b', 'tool-b.lock')
        self.shell('''
start=$PWD
FIXTURE_BINARY=tool-a
native_tool_build tool-a.tar.gz sources/tool-a.lock tool-a tool-a --version fixture-v1
[ "$PWD" = "$start" ]
FIXTURE_BINARY=tool-b
native_tool_build tool-b.tar.gz sources/tool-b.lock tool-b tool-b --version fixture-v1
[ "$PWD" = "$start" ]
native_tool_build tool-b.tar.gz sources/tool-b.lock tool-b tool-b --version fixture-v1
''')
        self.assertEqual(len((self.work / 'make-calls').read_text().splitlines()), 4)

    def test_missing_lock_cannot_publish_an_empty_archive_receipt(self):
        self.shell("FIXTURE_BINARY=bash\nnative_build_one bash-5.3.tar.gz sources/missing.lock bash-5.3 bash --version ''", success=False)
        self.assertFalse((self.work / 'state/61-bash-5.3.done').exists())
