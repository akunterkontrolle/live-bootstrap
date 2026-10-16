"""Batch 2 checks using disposable sources and real ELF32/ELF64 objects."""
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import importlib.util
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENTOO = ROOT / 'system/gentoo'
spec = importlib.util.spec_from_file_location('gentoo_prepare', GENTOO / 'prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def shell(self, script, *, success=True):
        result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + script],
                                cwd=self.directory, text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def helpers(self, *names):
        prefix = 'WORK_ROOT="$PWD/work"\nGENTOO_BUILD_SOURCE_JOBS=1\n'
        prefix += 'mkdir -p "$WORK_ROOT/state" "$WORK_ROOT/build/probes"\n'
        return prefix + ''.join(f'. {shlex.quote(str(GENTOO / "lib" / (name + ".sh")))}\n' for name in names)

    def test_interrupted_build_refuses_new_recipe_before_action(self):
        prefix = self.helpers('common', 'state') + '''
echo original > recipe
verify() { test -s result; }
fingerprint=$(gentoo_fingerprint recipe)
'''
        self.shell(prefix + '''action() { echo partial > partial; false; }
gentoo_run_stage cross "$fingerprint" action verify''', success=False)
        self.assertTrue((self.directory / 'work/state/cross.inputs').is_file())
        self.assertFalse((self.directory / 'work/state/cross.done').exists())
        self.shell(prefix + '''echo changed > recipe
fingerprint=$(gentoo_fingerprint recipe)
action() { echo wrongly-reused > result; }
gentoo_run_stage cross "$fingerprint" action verify''', success=False)
        self.assertFalse((self.directory / 'result').exists())
        self.shell(prefix + '''action() { echo resumed > result; }
gentoo_run_stage cross "$fingerprint" action verify''')
        self.assertTrue((self.directory / 'work/state/cross.done').is_file())

    def make_archive(self):
        source = self.directory / 'example-1'
        source.mkdir()
        (source / 'configure').write_text('# source fixture\n')
        archive = self.directory / 'example-1.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(source, arcname='example-1')
        digest = hashlib.sha512(archive.read_bytes()).hexdigest()
        return archive, digest

    def test_sha512_archive_unpack_and_cache_integrity(self):
        archive, digest = self.make_archive()
        (self.directory / 'example.lock').write_text(f'example-1.tar.gz|https://invalid.example/source|sha512={digest}\n')
        prefix = self.helpers('common', 'sources') + f'''
mkdir -p "$WORK_ROOT/distfiles"
cp {shlex.quote(str(archive))} "$WORK_ROOT/distfiles/"
'''
        self.shell(prefix + 'gentoo_prepare_source example.lock')
        copied = self.directory / 'work/sources/example-1/configure'
        self.assertEqual(copied.read_text(), '# source fixture\n')
        self.shell(self.helpers('common', 'sources') + 'gentoo_prepare_source example.lock')
        cached = self.directory / 'work/distfiles/example-1.tar.gz'
        cached.write_bytes(b'corrupted after extraction')
        self.shell(self.helpers('common', 'sources') + 'gentoo_prepare_source example.lock', success=False)

    def test_interrupted_extraction_never_publishes_a_tree(self):
        archive, digest = self.make_archive()
        (self.directory / 'example.lock').write_text(f'example-1.tar.gz|https://invalid.example/source|sha512={digest}\n')
        prefix = self.helpers('common', 'sources') + f'''
mkdir -p "$WORK_ROOT/distfiles"
cp {shlex.quote(str(archive))} "$WORK_ROOT/distfiles/"
'''
        self.shell(prefix + '''tar() { return 1; }
gentoo_prepare_source example.lock''', success=False)
        self.assertFalse((self.directory / 'work/sources/example-1').exists())
        self.shell(prefix + 'gentoo_prepare_source example.lock')
        self.assertTrue((self.directory / 'work/sources/example-1/.source-pin').is_file())
        (self.directory / 'work/sources/example-1/.source-pin').write_text('wrong pin')
        self.shell(prefix + 'gentoo_prepare_source example.lock', success=False)

    def test_lock_parser_rejects_duplicate_and_malformed_records(self):
        prefix = self.helpers('common', 'sources')
        for data in ('', 'a|url|hash\nb|url|hash\n', 'a|url\n'):
            (self.directory / 'bad.lock').write_text(data)
            self.shell(prefix + 'gentoo_source_record bad.lock', success=False)

    def test_real_elf_objects_and_mixed_archive_rejection(self):
        if not shutil.which('gcc') or not shutil.which('ar'):
            self.skipTest('host GCC/binutils unavailable')
        (self.directory / 'probe.c').write_text('int continuation_object;\n')
        for abi in (32, 64):
            subprocess.run(['gcc', f'-m{abi}', '-ffreestanding', '-c', 'probe.c', '-o', f'probe{abi}.o'],
                           cwd=self.directory, check=True, capture_output=True)
            subprocess.run(['ar', 'rc', f'good{abi}.a', f'probe{abi}.o'], cwd=self.directory, check=True)
        subprocess.run(['ar', 'rc', 'mixed.a', 'probe32.o', 'probe64.o'], cwd=self.directory, check=True)
        prefix = self.helpers('common', 'toolchain')
        for abi in (32, 64):
            self.shell(prefix + f'gentoo_verify_elf probe{abi}.o ELF{abi}\ngentoo_verify_elf good{abi}.a ELF{abi}')
            self.shell(prefix + f'gentoo_verify_elf probe{abi}.o ELF{96 - abi}', success=False)
            self.shell(prefix + f'gentoo_verify_elf mixed.a ELF{abi}', success=False)

    def test_linker_verifier_rejects_host_paths(self):
        # Source the real stage definitions without building/downloading, then
        # exercise the actual installed-output verifier against a fake linker.
        script = self.helpers('common', 'toolchain') + f'''
gentoo_prepare_source() {{ :; }}
gentoo_toolchain_fingerprint() {{ printf '%064d' 0; }}
gentoo_run_stage() {{ :; }}
. {shlex.quote(str(GENTOO / 'stages/15-binutils-linux.sh'))}
mkdir -p "$LINUX_BINUTILS/bin" "$TOOLBIN" "$PREFIX/bin" "$PREFIX/$TARGET/bin"
for tool in ar as nm objcopy objdump ranlib readelf strip; do
    printf '#!/bin/sh\\nexit 0\\n' > "$LINUX_BINUTILS/bin/$TARGET-$tool"
    chmod +x "$LINUX_BINUTILS/bin/$TARGET-$tool"
done
cat > "$LINUX_BINUTILS/bin/$TARGET-ld" <<'LINKER'
#!/bin/sh
if [ "$1" = -V ]; then echo elf_i386; else cat "$WORK_ROOT/search-paths"; fi
LINKER
chmod +x "$LINUX_BINUTILS/bin/$TARGET-ld"
export WORK_ROOT
for tuple in "$TARGET" i686-pc-linux-gnu; do
    for tool in ar as ld nm objcopy objdump ranlib readelf strip; do
        ln -s "$LINUX_BINUTILS/bin/$TARGET-$tool" "$TOOLBIN/$tuple-$tool"
        ln -s "$LINUX_BINUTILS/bin/$TARGET-$tool" "$PREFIX/bin/$tuple-$tool"
    done
done
for tool in ar as ld nm objcopy objdump ranlib readelf strip; do
    ln -s "$LINUX_BINUTILS/bin/$TARGET-$tool" "$PREFIX/$TARGET/bin/$tool"
done
'''
        valid = '; '.join(f'SEARCH_DIR("={path}")' for path in ('/usr/lib64', '/lib64', '/usr/lib32', '/lib32', '/usr/lib', '/lib'))
        self.shell(script + f'printf %s {shlex.quote(valid)} > "$WORK_ROOT/search-paths"\nbinutils_linux_verify')
        # A second run needs fresh symlinks; use the already defined fixture.
        invalid = valid + '; SEARCH_DIR("/lib")'
        self.shell(script.replace('ln -s ', 'ln -sf ') + f'printf %s {shlex.quote(invalid)} > "$WORK_ROOT/search-paths"\nbinutils_linux_verify', success=False)

    def test_dispatch_dependency_order_and_bundle_completeness(self):
        driver = (GENTOO / 'gentoo.sh').read_text()
        stages = re.findall(r'\. "\$SCRIPT_DIR/(stages/[^"]+)"', driver)
        self.assertEqual(stages, sorted(stages))
        self.assertEqual(stages[-8:], ['stages/60-native-root.sh', 'stages/61-native-shell-tools.sh', 'stages/62-native-compiler.sh', 'stages/63-native-tool-floor.sh', 'stages/70-native-runtimes.sh', 'stages/71-native-portage.sh', 'stages/80-native-gentoo-system.sh', 'stages/90-native-base-runtime.sh'])
        self.assertTrue(set(stages) <= set(prepare.BUNDLE_FILES))
        seen = set()
        for stage in stages:
            text = (GENTOO / stage).read_text()
            for line in text.splitlines():
                requirements = re.findall(r'\$WORK_ROOT/state/([A-Za-z0-9_-]+)\.done', line)
                self.assertTrue(set(requirements) <= seen, f'{stage} requires an unfinished stage: {set(requirements) - seen}')
                seen.update(re.findall(r'^gentoo_run_stage ([A-Za-z0-9_-]+)', line))
        self.assertIn('20-sysroot-links', seen)
        # Every referenced archive lock must be shipped, uniquely pinned, and
        # name an actual version that the source recipes unpack/consume.
        for stage in stages:
            text = (GENTOO / stage).read_text()
            for lock in re.findall(r'sources/[A-Za-z0-9.-]+\.lock', text):
                self.assertIn(lock, prepare.BUNDLE_FILES)
                records = [line for line in (GENTOO / lock).read_text().splitlines() if line and not line.startswith('#')]
                self.assertEqual(len(records), 1)
                self.assertRegex(records[0], r'^[A-Za-z0-9.+_-]+\.tar\.(xz|gz|bz2|lz|sign)\|https://[^|]+\|sha512=[a-f0-9]{128}$')

    def test_compiler_fingerprint_stays_stable_when_adding_a_later_stage(self):
        snapshot = self.directory / 'snapshot'
        shutil.copytree(GENTOO, snapshot)
        (self.directory / 'work/state').mkdir(parents=True)
        (self.directory / 'work/state/host-toolchain.inputs').write_text('host-compiler')
        script = self.helpers('common', 'state', 'toolchain') + f'''
cd {shlex.quote(str(snapshot))}
gentoo_toolchain_fingerprint stages/11-binutils-elf.sh sources/binutils.lock > before
printf new-docs > README.md
printf kernel > stages/30-kernel.sh
gentoo_toolchain_fingerprint stages/11-binutils-elf.sh sources/binutils.lock > after
cmp before after
printf '\\n# changed recipe\\n' >> stages/11-binutils-elf.sh
gentoo_toolchain_fingerprint stages/11-binutils-elf.sh sources/binutils.lock > changed
if cmp -s before changed; then exit 1; fi
'''
        self.shell(script)


if __name__ == '__main__':
    unittest.main()
