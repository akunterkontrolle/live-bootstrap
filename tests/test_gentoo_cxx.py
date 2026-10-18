"""Batch 4: generated build rules and real disposable runtime probes."""
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

GENTOO = Path(__file__).resolve().parents[1] / 'system/gentoo'


class CxxTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def shell(self, script, success=True):
        result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + self.helpers() + script],
                                cwd=self.directory, text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def helpers(self):
        return f'''
WORK_ROOT="$PWD/work"
GENTOO_BUILD_SOURCE_JOBS=1
SCRIPT_DIR={shlex.quote(str(GENTOO))}
. "$SCRIPT_DIR/lib/common.sh"
. "$SCRIPT_DIR/lib/toolchain.sh"
. "$SCRIPT_DIR/lib/cxx.sh"
. "$SCRIPT_DIR/lib/runtime.sh"
mkdir -p "$GCC_CXX_BUILD/gcc" "$PREFIX/bin" "$SYSROOT/usr/lib32" "$SYSROOT/usr/lib64"
'''

    def test_old_kernel_gate_precedes_source_or_build(self):
        result = self.shell('''
gentoo_prepare_source() { touch forbidden; }
. "$SCRIPT_DIR/stages/40-gcc-cxx.sh"
''', success=False)
        self.assertIn('verified modern-kernel handoff', result.stderr)
        self.assertFalse((self.directory / 'forbidden').exists())
        driver = (GENTOO / 'gentoo.sh').read_text()
        self.assertIn('if [ "$GENTOO_MODERN_KERNEL_READY" = 1 ]; then', driver)
        self.assertLess(driver.index('if [ "$GENTOO_MODERN_KERNEL_READY"'), driver.index('stages/40-gcc-cxx.sh'))

    def test_wrappers_select_both_startup_and_runtime_paths_last_abi_wins(self):
        self.shell('''
cat > "$PREFIX/bin/$TARGET-gcc" <<'COMPILER'
#!/bin/sh
case "$2" in
    -print-libgcc-file-name) echo "$WORK_ROOT/runtime/$1/private/libgcc.a" ;;
    *) echo "$WORK_ROOT/runtime/$1/shared/libgcc_s.so.1" ;;
esac
COMPILER
chmod +x "$PREFIX/bin/$TARGET-gcc"
export WORK_ROOT
# ELF validation has its own real-object tests; here record the emitted argv.
gentoo_verify_elf() { :; }
cat > "$GCC_CXX_BUILD/gcc/xgcc" <<'RECORD'
#!/bin/sh
printf '%s\\n' "$@"
RECORD
chmod +x "$GCC_CXX_BUILD/gcc/xgcc"
cp "$GCC_CXX_BUILD/gcc/xgcc" "$GCC_CXX_BUILD/gcc/xg++"
cxx_write_wrappers
for wrapper in cc cxx; do
    for abi in 32 64; do
        output=$("$GCC_CXX_BUILD/$wrapper-for-target" -m64 -m32 "-m$abi" 'argument with spaces')
        printf '%s\\n' "$output" | grep -Fx -- "-B$SYSROOT/usr/lib$abi/"
        printf '%s\\n' "$output" | grep -Fx -- "-L$WORK_ROOT/runtime/-m$abi/private"
        printf '%s\\n' "$output" | grep -Fx -- "-L$WORK_ROOT/runtime/-m$abi/shared"
        printf '%s\\n' "$output" | grep -Fx 'argument with spaces'
    done
done
''')

    def test_shadow_startup_removal_refuses_unexpected_objects(self):
        self.shell('''
for name in crti.o crtn.o; do ln -s "$SYSROOT/usr/lib64/$name" "$GCC_CXX_BUILD/gcc/$name"; done
cxx_remove_shadow_crt
[ ! -L "$GCC_CXX_BUILD/gcc/crti.o" ]
cxx_remove_shadow_crt
printf keep > "$GCC_CXX_BUILD/gcc/crti.o"
cxx_remove_shadow_crt
''', success=False)
        self.assertEqual((self.directory / 'work/build/gcc-linux-cxx/gcc/crti.o').read_text(), 'keep')

    def test_nm_and_recursive_libgcc_generated_rules_are_bound_idempotently(self):
        self.shell('''
printf 'ORIGINAL_NM_FOR_TARGET=""\\n' > "$GCC_CXX_BUILD/gcc/nm"
cxx_fix_nm
cxx_fix_nm
mkdir -p "$GCC_CXX_BUILD/$TARGET/libgcc" "$GCC_CXX_BUILD/$TARGET/32/libgcc"
for dir in "$GCC_CXX_BUILD/$TARGET/libgcc" "$GCC_CXX_BUILD/$TARGET/32/libgcc"; do
    printf 'CC = /build/../gcc/xgcc -B../gcc/\\nOTHER = unchanged\\n' > "$dir/Makefile"
done
cxx_bind_libgcc
cxx_bind_libgcc
for dir in "$GCC_CXX_BUILD/$TARGET/libgcc" "$GCC_CXX_BUILD/$TARGET/32/libgcc"; do
    grep -Fx "CC = $GCC_CXX_BUILD/cc-for-target -B../gcc/" "$dir/Makefile"
    grep -Fx 'OTHER = unchanged' "$dir/Makefile"
done
''')
        self.shell('''printf 'no expected assignment\\n' > "$GCC_CXX_BUILD/gcc/nm"
cxx_fix_nm''', success=False)

    def test_libtool_cxx_tag_uses_whole_abi_matched_tokens(self):
        self.shell('''
for abi in 32 64; do
    cat > "libtool-$abi" <<'LIBTOOL'
CC='keep C tag'
predep_objects='/original/crti.o keep-crtbeginS.o'
# ### BEGIN LIBTOOL TAG CONFIG: CXX
CC='old xg++'
predep_objects='/wrong/absolute/crti.o /build/crtbeginS.o'
postdep_objects='/build/crtendS.o crtn.o'
# ### END LIBTOOL TAG CONFIG: CXX
LIBTOOL
    cxx_fix_libtool "libtool-$abi" "$abi"
    cp "libtool-$abi" expected
    cxx_fix_libtool "libtool-$abi" "$abi"
    cmp "libtool-$abi" expected
    grep -Fx "CC='keep C tag'" "libtool-$abi"
    grep -Fx "predep_objects='$SYSROOT/usr/lib$abi/crti.o /build/crtbeginS.o'" "libtool-$abi"
    grep -Fx "postdep_objects='/build/crtendS.o $SYSROOT/usr/lib$abi/crtn.o'" "libtool-$abi"
done
''')
        broken = self.directory / 'libtool-32'
        broken.write_text(broken.read_text().replace('postdep_objects=', 'missing_field='))
        before = broken.read_bytes()
        self.shell('cxx_fix_libtool libtool-32 32', success=False)
        self.assertEqual(broken.read_bytes(), before)

    def test_cxx_fingerprint_ignores_concurrency_docs_and_later_recipes(self):
        snapshot = self.directory / 'snapshot'
        shutil.copytree(GENTOO, snapshot)
        self.shell(f'''
. "$SCRIPT_DIR/lib/state.sh"
mkdir -p "$WORK_ROOT/state"
printf host > "$WORK_ROOT/state/host-toolchain.inputs"
printf sysroot > "$WORK_ROOT/state/20-sysroot-links.done"
cd {shlex.quote(str(snapshot))}
a=$(cxx_fingerprint stages/40-gcc-cxx.sh sources/gcc.lock "$WORK_ROOT/state/20-sysroot-links.done")
JOBS=17
printf docs > README.md
printf later > stages/43-runtime-acceptance.sh
printf later-helper > lib/runtime.sh
b=$(cxx_fingerprint stages/40-gcc-cxx.sh sources/gcc.lock "$WORK_ROOT/state/20-sysroot-links.done")
[ "$a" = "$b" ]
printf changed >> stages/40-gcc-cxx.sh
c=$(cxx_fingerprint stages/40-gcc-cxx.sh sources/gcc.lock "$WORK_ROOT/state/20-sysroot-links.done")
[ "$a" != "$c" ]
''')

    def test_intermediate_headers_select_installed_abi_configuration(self):
        base = self.directory / 'work/linux-toolchain/include/c++/16.2.0'
        (base / 'backward').mkdir(parents=True)
        (base / 'iostream').write_text('// common C++ header\n')
        for abi, suffix in [(64, 'target'), (32, 'target/32')]:
            directory = base / suffix / 'bits'
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'c++config.h').write_text(f'#define FIXTURE_ABI {abi}\n')
        driver = self.directory / 'work/linux-toolchain/bin/x86_64-pc-linux-gnu-g++'
        driver.parent.mkdir()
        driver.write_text('''#!/bin/sh
case "$1" in -m64) suffix=target ;; -m32) suffix=target/32 ;; *) exit 2 ;; esac
printf '#include <...> search starts here:\n %s\n %s/%s\n %s/backward\n /usr/include\nEnd of search list.\n' \
    ''' + shlex.quote(str(base)) + ' ' + shlex.quote(str(base)) + ' "$suffix" ' + shlex.quote(str(base)) + ' >&2\n')
        driver.chmod(0o755)
        self.shell('''
for abi in 64 32; do
    cxx_header_flags=()
    cxx_runtime_headers "$abi"
    printf '%s\n' "${cxx_header_flags[@]}" > "headers-$abi"
done
''')
        for abi, suffix in [(64, 'target'), (32, 'target/32')]:
            self.assertEqual((self.directory / f'headers-{abi}').read_text().splitlines(),
                             ['-nostdinc++', '-isystem', str(base), '-isystem', str(base / suffix),
                              '-isystem', str(base / 'backward')])
        (base / 'target/32/bits/c++config.h').unlink()
        self.shell('cxx_header_flags=(); cxx_runtime_headers 32', success=False)

    def test_real_intermediate_compiler_with_disabled_default_cxx_includes(self):
        compiler = shutil.which('g++')
        if not compiler:
            self.skipTest('native C++ compiler unavailable')
        query = subprocess.run([compiler, '-E', '-x', 'c++', '-v', '-'], input='',
                               text=True, capture_output=True, check=True)
        directories = []
        inside = False
        for line in query.stderr.splitlines():
            if line == '#include <...> search starts here:':
                inside = True
            elif line == 'End of search list.':
                inside = False
            elif inside:
                directories.append(Path(line.strip()))
        common = next(p for p in directories if (p / 'iostream').is_file())
        config = next(p for p in directories if (p / 'bits/c++config.h').is_file())
        prefix = self.directory / 'work/linux-toolchain'
        copied_common = prefix / 'include/c++/fixture'
        copied_config = prefix / 'include/target-c++-config'
        shutil.copytree(common, copied_common)
        shutil.copytree(config, copied_config)
        flags = ['-nostdinc++', '-isystem', str(copied_common), '-isystem', str(copied_config)]
        if (copied_common / 'backward').is_dir():
            flags += ['-isystem', str(copied_common / 'backward')]
        driver = prefix / 'bin/x86_64-pc-linux-gnu-g++'
        driver.parent.mkdir(parents=True)
        driver.write_text('#!/bin/sh\nexec ' + ' '.join(shlex.quote(a) for a in [compiler] + flags) + ' "$@"\n')
        driver.chmod(0o755)
        intermediate = self.directory / 'intermediate-cxx'
        intermediate.write_text('#!/bin/sh\nexec ' + shlex.quote(compiler) + ' -nostdinc++ "$@"\n')
        intermediate.chmod(0o755)
        result = subprocess.run([str(intermediate), str(GENTOO / 'probes/runtime.cc'),
                                 '-o', str(self.directory / 'without-headers')], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('iostream', result.stderr)
        self.shell('''
cxx_header_flags=()
cxx_runtime_headers 64
"$PWD/intermediate-cxx" -m64 "${cxx_header_flags[@]}" -pthread "$SCRIPT_DIR/probes/runtime.cc" -o "$PWD/with-headers"
[ "$("$PWD/with-headers")" = 'gentoo C++ runtime ok' ]
''')

    def native_runtime(self):
        if not shutil.which('g++'):
            self.skipTest('native C++ compiler unavailable')
        # This is an isolated host-runtime fixture, never evidence for building
        # GCC 16.2/glibc 2.44 or completing the guest modern-kernel transition.
        base = self.directory / 'work'
        syslib = base / 'sysroot-x86_64/lib64'
        prefixlib = base / 'linux-toolchain/lib64'
        syslib.mkdir(parents=True)
        prefixlib.mkdir(parents=True)
        for name in ('ld-linux-x86-64.so.2', 'libc.so.6', 'libm.so.6', 'libstdc++.so.6', 'libgcc_s.so.1'):
            path = Path(subprocess.check_output(['g++', '-m64', '-print-file-name=' + name], text=True).strip())
            self.assertTrue(path.is_file(), str(path))
            shutil.copy2(path.resolve(), (prefixlib if name.startswith(('libstdc++', 'libgcc')) else syslib) / name)
        binpath = base / 'linux-toolchain/bin'
        binpath.mkdir()
        driver = binpath / 'x86_64-pc-linux-gnu-g++'
        driver.write_text('#!/bin/sh\ncase "$2" in -print-file-name=*) echo "' + str(prefixlib) + '/${2#-print-file-name=}";; *) exit 1;; esac\n')
        driver.chmod(0o755)
        for lang, compiler, source in [('c', 'gcc', 'runtime.c'), ('cxx', 'g++', 'runtime.cc')]:
            subprocess.run([compiler, '-m64', '-pthread', str(GENTOO / 'probes' / source), '-o', str(self.directory / lang)], check=True)
        return syslib, prefixlib

    def test_actual_native_c_and_cxx_execution_and_loader_isolation(self):
        syslib, prefixlib = self.native_runtime()
        self.shell('''
cxx_execute_probe 64 "$PWD/c" 'gentoo C runtime ok'
cxx_execute_probe 64 "$PWD/cxx" 'gentoo C++ runtime ok'
''')
        self.shell('cxx_execute_probe 64 "$PWD/cxx" "wrong expected output"', success=False)
        self.shell('cxx_execute_probe 32 "$PWD/cxx" "gentoo C++ runtime ok"', success=False)
        (prefixlib / 'libstdc++.so.6').unlink()
        self.shell('cxx_execute_probe 64 "$PWD/cxx" "gentoo C++ runtime ok"', success=False)
        # A host fallback has the right ABI but must still be rejected.
        hostlib = Path(subprocess.check_output(['g++', '-print-file-name=libstdc++.so.6'], text=True).strip())
        (prefixlib / 'libstdc++.so.6').symlink_to(hostlib.resolve())
        self.shell('cxx_execute_probe 64 "$PWD/cxx" "gentoo C++ runtime ok"', success=False)

    def test_runtime_nonzero_exit_is_rejected_even_with_expected_stdout(self):
        self.native_runtime()
        source = self.directory / 'failure.c'
        source.write_text('#include <stdio.h>\nint main(void) { puts("gentoo C runtime ok"); return 7; }\n')
        subprocess.run(['gcc', str(source), '-o', str(self.directory / 'failure')], check=True)
        self.shell('cxx_execute_probe 64 "$PWD/failure" "gentoo C runtime ok"', success=False)


if __name__ == '__main__':
    unittest.main()
