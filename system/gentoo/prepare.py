#!/usr/bin/env python3
"""Prepare an explicit, self-contained input bundle on the host; no VM changes."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.gentoo_config import load, render, root_image_gib

# Extend this list in the batch that adds each runtime artifact.
BUNDLE_FILES = (
    'lib/perl-locale.sh', 'lib/shutdown.sh',
    'lib/installer-filesystems.sh', 'retire-bootstrap-nscd.py',
    'gentoo.sh', 'console-session.py', 'bootstrap-network.py', 'install-base.sh', 'lib/common.sh', 'lib/devices.sh', 'lib/storage.sh', 'lib/state.sh',
    'signed-generation.py', 'signed-generation.example.toml', 'SIGNED-GENERATIONS.md',
    'install-signed-generation-tools.sh', 'export-boot-signing-key.sh',
    'harden-libreboot-payload.sh', 'firmware-build-receipt.sh', 'firmware-flash.sh',
    'dracut-ms1504-resume-guard/module-setup.sh',
    'dracut-ms1504-resume-guard/check-generation.sh',
    'setup-extra.sh', 'complete-desktop.sh', 'desktop-packages.list',
    'desktop-package.use', 'desktop-package.accept_keywords',
    'desktop-package.mask', 'desktop-rust-bootstrap.py', 'desktop-lightdm.conf',
    'lib/sources.sh', 'stages/00-inputs.sh',
    'lib/toolchain.sh', 'stages/10-host-preflight.sh',
    'stages/11-binutils-elf.sh', 'stages/12-gcc-elf.sh',
    'stages/13-host-tools.sh', 'stages/14-linux-headers.sh',
    'stages/15-binutils-linux.sh', 'stages/16-gcc-linux-stage1.sh',
    'stages/17-glibc-bootstrap.sh', 'stages/18-gcc-linux-runtime.sh',
    'stages/19-glibc.sh', 'stages/20-verify-sysroot.sh',
    'sources/binutils.lock', 'sources/gcc.lock', 'sources/glibc.lock',
    'sources/linux.lock', 'sources/argp.lock', 'sources/elfutils.lock',
    'sources/rsync.lock',
    'kernel-6.12.111.config.fragment', 'lib/kernel.sh',
    'stages/30-kernel.sh', 'stages/31-kernel-modules.sh',
    'stages/32-handoff.sh', 'post-reboot.sh', 'shutdown.sh',
    'sources/linux-signature.lock', 'keys/kernel-release.gpg',
    'lib/cxx.sh', 'lib/runtime.sh', 'probes/runtime.c', 'probes/runtime.cc',
    'stages/40-gcc-cxx.sh', 'stages/41-cxx-libgcc.sh',
    'stages/42-libstdcxx.sh', 'stages/43-runtime-acceptance.sh',
    'lib/portage.sh', 'lib/manifests.sh', 'lib/portage-host.sh', 'lib/python.sh',
    'probes/portage-integrity.py', 'probes/portage-version.py',
    'sources/gentoo-repository.lock', 'sources/python.lock',
    'sources/bzip2.lock', 'sources/portage.lock',
    'stages/50-gentoo-sources.sh', 'stages/51-host-python.sh',
    'stages/52-portage-host.sh',
    'lib/native.sh', 'lib/native-resume.sh', 'lib/mounts.sh', 'lib/bootstrap-tools.sh', 'stages/60-native-root.sh',
    'stages/61-native-shell-tools.sh', 'stages/62-native-compiler.sh',
    'sources/native-bash.lock', 'sources/native-coreutils.lock',
    'sources/native-gmp.lock', 'sources/native-mpfr.lock', 'sources/native-mpc.lock',
    'lib/native-tools.sh', 'stages/63-native-tool-floor.sh',
    'sources/native-m4-1.4.21.lock', 'sources/native-ncurses-6.6.lock',
    'sources/native-diffutils-3.12.lock', 'sources/native-findutils-4.11.0.lock',
    'sources/native-gawk-5.4.1.lock', 'sources/native-grep-3.12.lock',
    'sources/native-gzip-1.14.lock', 'sources/native-lzip-1.26.lock',
    'sources/native-make-4.4.1.lock', 'sources/native-patch-2.8.lock',
    'sources/native-sed-4.10.lock', 'sources/native-tar-1.35.lock',
    'sources/native-xz-5.8.3.lock',
    'sources/native-file-5.48.lock',
    'stages/native-runtime-build.sh', 'stages/70-native-runtimes.sh',
    'stages/71-native-portage.sh', 'stages/80-native-gentoo-system.sh',
    'stages/90-native-base-runtime.sh',
    'lib/native-gentoo-system.sh',
    'stages/system/10-prepare.sh', 'stages/system/20-runtime-and-downloader.sh',
    'stages/system/30-bootstrap-prerequisites.sh', 'stages/system/40-runtime-providers.sh',
    'stages/system/50-elf-and-compiler.sh', 'stages/system/60-gentoo-bootstrap.sh',
    'stages/system/70-system.sh', 'stages/system/80-verify.sh',
    'build-bootstrap-scanelf.sh', 'audit-resolved-sources.py', 'probes/reinstall-plan.py',
    'probes/portage-mount-shim.py', 'probes/native-permissions.py',
    'sources/native-xz-5.8.3.lock', 'sources/native-tar-1.35.lock',
    'sources/native-gawk-5.4.1.lock', 'sources/wget.lock',
    'sources/zlib.lock', 'sources/openssl.lock',
    'sources/libffi.lock', 'sources/perl.lock',
    'policy/defaults/base-packages.list', 'policy/defaults/base-package.use',
    'policy/defaults/base-package.license', 'policy/defaults/base-package.accept_keywords',
    'policy/defaults/firmware-linux-firmware', 'policy/90-encrypted-base-dracut.conf',
)


def prepare(config, mode, output):
    settings = load(config, mode)
    output = Path(output).absolute()
    if output.exists():
        raise ValueError(f'Output already exists (never overwritten): {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='.gentoo-inputs-', dir=output.parent) as temporary:
        bundle = Path(temporary) / 'bundle'
        bundle.mkdir()
        for name in BUNDLE_FILES:
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        for key, value in settings['packages'].items():
            if value:
                (bundle / 'policy').mkdir(exist_ok=True)
                shutil.copyfile(value, bundle / 'policy' / key)
        (bundle / 'config.sh').write_text(render(settings, mode), encoding='utf-8')
        (bundle / 'config.inputs.sh').write_text(render(settings, mode, semantic=True), encoding='utf-8')
        files = sorted(p for p in bundle.rglob('*') if p.is_file())
        manifest = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(bundle).as_posix()}\n'
                           for p in files)
        (bundle / 'SHA256SUMS').write_text(manifest, encoding='utf-8')
        bundle.rename(output)
    return root_image_gib(settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--mode', required=True, choices=('vm', 'hardware'))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        capacity = prepare(args.config, args.mode, args.output)
    except (ValueError, OSError) as error:
        parser.exit(2, f'{error}\n')
    print(f'Prepared {args.output}; planned installation-root capacity: {capacity} GiB')


if __name__ == '__main__':
    main()
