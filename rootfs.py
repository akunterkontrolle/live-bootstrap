#!/usr/bin/env python3
"""
A helper application used to start bootstrapping process.
It has a few modes of operation, you can create initramfs with
binary seeds and sources that you can boot into or alternatively
you can run bootstap inside chroot.
"""

# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2022 Dor Askayo <dor.askayo@gmail.com>
# SPDX-FileCopyrightText: 2021 Andrius Štikonas <andrius@stikonas.eu>
# SPDX-FileCopyrightText: 2021 Bastian Bittorf <bb@npl.de>
# SPDX-FileCopyrightText: 2021 Melg Eight <public.melg8@gmail.com>
# SPDX-FileCopyrightText: 2021-23 Samuel Tyler <samuel@samuelt.me>
# SPDX-FileCopyrightText: 2023-24 Gábor Stefanik <netrolller.3d@gmail.com>

import argparse
import json
import os
import signal
import sys
import tempfile
import threading

from lib.generator import Generator, stage0_arch_map
from lib.simple_mirror import SimpleMirror
from lib.target import Target
from lib.utils import run, run_as_root

def create_configuration_file(args):
    """
    Creates bootstrap.cfg file which would contain options used to
    customize bootstrap.
    """
    config_path = os.path.join('steps', 'bootstrap.cfg')
    with open(config_path, "w", encoding="utf_8") as config:
        config.write(f"ARCH={args.arch}\n")
        config.write(f"ARCH_DIR={stage0_arch_map.get(args.arch, args.arch)}\n")
        config.write(f"FORCE_TIMESTAMPS={args.force_timestamps}\n")
        config.write(f"CHROOT={args.chroot or args.bwrap}\n")
        config.write(f"UPDATE_CHECKSUMS={args.update_checksums}\n")
        config.write(f"JOBS={args.cores}\n")
        config.write(f"SWAP_SIZE={args.swap}\n")
        config.write(f"FINAL_JOBS={args.cores}\n")
        config.write(f"INTERNAL_CI={args.internal_ci or False}\n")
        config.write(f"INTERACTIVE={args.interactive}\n")
        config.write(f"QEMU={args.qemu}\n")
        config.write(f"BARE_METAL={args.bare_metal or (args.qemu and args.interactive)}\n")
        if "gentoo" in args.extra_builds:
            config.write("GENTOO_BOOTSTRAP=True\n")
            config.write(f"GENTOO_MODE={'hardware' if args.bare_metal else 'vm'}\n")
        if (args.bare_metal or args.qemu) and not args.kernel:
            if args.repo or args.external_sources:
                config.write("DISK=sdb1\n")
            else:
                config.write("DISK=sda\n")
            config.write("KERNEL_BOOTSTRAP=True\n")
        else:
            config.write("DISK=sda1\n")
            config.write("KERNEL_BOOTSTRAP=False\n")
        config.write(f"BUILD_KERNELS={args.update_checksums or args.build_kernels}\n")
        config.write(f"CONFIGURATOR={args.configurator}\n")
        if not args.external_sources:
            if args.mirrors:
                config.write(f'MIRRORS="{" ".join(args.mirrors)}"\n')
                config.write(f"MIRRORS_LEN={len(args.mirrors)}\n")
            else:
                config.write("MIRRORS_LEN=0\n")


def validate_extra_builds(args, parser):
    """Validate optional continuation arguments without changing base modes."""
    selected = "gentoo" in args.extra_builds
    if args.gentoo_config and not selected:
        parser.error("--gentoo-config requires --extra-builds=gentoo")
    if selected and not (args.qemu or args.bare_metal):
        parser.error("--extra-builds=gentoo requires --qemu or --bare-metal")
    if selected and args.tmpfs:
        parser.error("--tmpfs cannot hold the persistent Gentoo workspace")
    if selected and args.no_create_config:
        parser.error("--extra-builds=gentoo requires automatic bootstrap.cfg generation")
    return selected


def prepare_gentoo_inputs(args, output):
    """Create a self-contained host-validated guest input bundle."""
    mode = "hardware" if args.bare_metal else "vm"
    config = args.gentoo_config or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "system", "gentoo", "config.example.toml")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "system", "gentoo", "prepare.py")
    run(sys.executable, script, "--config", config, "--mode", mode, "--output", output)


def start_managed_gentoo_vm(args, target, qemu_args, config_path):
    """Prepare managed continuation disks, then launch the original bootstrap VM."""
    run_dir = os.path.join(target.path, "gentoo-run")
    command_path = os.path.join(target.path, "gentoo-initial-qemu.json")
    with open(command_path, "w", encoding="utf-8") as command_file:
        json.dump([args.qemu_cmd, *qemu_args], command_file, indent=2)
        command_file.write("\n")
    vm_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "system", "gentoo", "vm.py")
    common = [sys.executable, vm_script]
    run_as_root(*common, "prepare", "--run-dir", run_dir, "--config", config_path,
                "--qemu-command", command_path, "--qemu-cwd", os.getcwd(), "--apply")
    run_as_root(*common, "start", "--run-dir", run_dir, "--apply")

# pylint: disable=too-many-statements,too-many-branches
def main():
    """
    A few command line arguments to customize bootstrap.
    This function also creates object which prepares directory
    structure with bootstrap seeds and all sources.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--arch", help="Bootstrap architecture",
                        default="x86")
    parser.add_argument("-c", "--chroot", help="Run inside chroot",
                        action="store_true")
    parser.add_argument("-bw", "--bwrap", help="Run inside a bwrap sandbox",
                        action="store_true")
    parser.add_argument("-t", "--target", help="Target directory",
                        default="target")
    parser.add_argument("--tmpfs", help="Use a tmpfs on target",
                        action="store_true")
    parser.add_argument("--tmpfs-size", help="Size of the tmpfs",
                        default="8G")
    parser.add_argument("--cores", help="Cores to use for building",
                         default=2)
    parser.add_argument("--force-timestamps",
                        help="Force all files timestamps to be 0 unix time",
                        action="store_true")
    parser.add_argument("--update-checksums",
                        help="Update checksum files",
                        action="store_true")
    parser.add_argument("--external-sources",
                        help="Download sources externally from live-bootstrap",
                        action="store_true")
    parser.add_argument("--build-kernels",
                        help="Also build kernels in chroot and bwrap builds",
                        action="store_true")
    parser.add_argument("--extra-builds", choices=("gentoo",), nargs="+", default=[],
                        help="Run the selected source-built continuation after live-bootstrap")
    parser.add_argument("--gentoo-config", help="TOML choices for the Gentoo build and installation")
    parser.add_argument("--no-create-config",
                        help="Do not automatically create config file",
                        action="store_true")
    parser.add_argument("-i", "--interactive",
                        help="Use interactive prompts to resolve issues during bootstrap",
                        action="store_true")
    parser.add_argument("--configurator",
                        help="Run the interactive configurator",
                        action="store_true")
    parser.add_argument("-m", "--mirrors",
                        help="Mirrors to download distfiles from",
                        nargs='+')
    parser.add_argument("-r", "--repo",
                        help="Path to prebuilt binary packages", nargs=None)
    parser.add_argument("--early-preseed",
                        help="Skip early stages of live-bootstrap", nargs=None)
    parser.add_argument("--internal-ci", help="INTERNAL for github CI")
    parser.add_argument("-s", "--swap", help="Swap space to allocate in Linux",
                        default=0)

    # QEMU arguments
    parser.add_argument("-q", "--qemu", help="Use QEMU",
                        action="store_true")
    parser.add_argument("-qc", "--qemu-cmd", help="QEMU command to run",
                        default="qemu-system-x86_64")
    parser.add_argument("-qr", "--qemu-ram", help="Memory (in megabytes) allocated to QEMU VM",
                        default=4096)
    parser.add_argument("-qs", "--target-size", help="Size of the target image (for QEMU only)",
                        default="16G")
    parser.add_argument("-qk", "--kernel", help="Custom early kernel to use")

    parser.add_argument("-b", "--bare-metal", help="Build images for bare metal",
                        action="store_true")

    args = parser.parse_args()

    gentoo_selected = validate_extra_builds(args, parser)

    # Mode validation
    def check_types():
        count = 0
        if args.qemu:
            count += 1
        if args.chroot:
            count += 1
        if args.bwrap:
            count += 1
        if args.bare_metal:
            count += 1
        return count

    if check_types() > 1:
        raise ValueError("No more than one of qemu, chroot, bwrap, bare metal"
                         "may be used.")
    if check_types() == 0:
        raise ValueError("One of qemu, chroot, bwrap, or bare metal must be selected.")

    # Arch validation
    if args.arch != "x86":
        print("Only x86 is supported at the moment, other arches are for development only.")

    # Tmpfs validation
    if args.bwrap and args.tmpfs:
        raise ValueError("tmpfs cannot be used with bwrap.")

    # Cores validation
    if int(args.cores) < 1:
        raise ValueError("Must use one or more cores.")

    # Target image size validation
    if args.qemu:
        if int(str(args.target_size).rstrip('gGmM')) < 1:
            raise ValueError("Please specify a positive target size for qemu.")
        args.target_size = (int(str(args.target_size).rstrip('gGmM')) *
            (1024 if str(args.target_size).lower().endswith('g') else 1))
    else:
        args.target_size = 0

    # Swap file size validation
    if args.qemu or args.bare_metal:
        args.swap = (int(str(args.swap).rstrip('gGmM')) *
            (1024 if str(args.swap).lower().endswith('g') else 1))
    else:
        args.swap = 0

    # Validate mirrors
    if not args.mirrors:
        raise ValueError("At least one mirror must be provided.")

    # Set constant umask
    os.umask(0o022)

    # file:// mirrors
    mirror_servers = []
    if args.mirrors:
        for i, mirror in enumerate(args.mirrors):
            if mirror.startswith("file://"):
                path = mirror.removeprefix("file://")
                if not path.startswith("/"):
                    raise ValueError("A file:// mirror must be an absolute path.")

                server = SimpleMirror(path)
                args.mirrors[i] = f"http://127.0.0.1:{server.port}"
                mirror_servers.append(server)

    # bootstrap.cfg
    try:
        os.remove(os.path.join('steps', 'bootstrap.cfg'))
    except FileNotFoundError:
        pass
    if not args.no_create_config:
        create_configuration_file(args)
    else:
        with open(os.path.join('steps', 'bootstrap.cfg'), 'a', encoding='UTF-8'):
            pass

    # target
    target = Target(path=args.target)
    if args.tmpfs:
        target.tmpfs(size=args.tmpfs_size)

    gentoo_temp = None
    gentoo_inputs = None
    if gentoo_selected:
        gentoo_temp = tempfile.TemporaryDirectory(prefix="live-bootstrap-gentoo-inputs-")
        gentoo_inputs = os.path.join(gentoo_temp.name, "bundle")
        try:
            prepare_gentoo_inputs(args, gentoo_inputs)
        except BaseException:
            gentoo_temp.cleanup()
            raise

    for server in mirror_servers:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()

    def cleanup(*_):
        for server in mirror_servers:
            server.shutdown()
    signal.signal(signal.SIGINT, cleanup)

    generator = Generator(arch=args.arch,
                          external_sources=args.external_sources,
                          repo_path=args.repo,
                          early_preseed=args.early_preseed,
                          mirrors=args.mirrors,
                          gentoo_inputs=gentoo_inputs)

    try:
        bootstrap(args, generator, target, args.target_size, cleanup)
    except BaseException:
        if gentoo_selected:
            cleanup()
        if gentoo_temp:
            gentoo_temp.cleanup()
        raise
    cleanup()
    if gentoo_temp:
        gentoo_temp.cleanup()

def bootstrap(args, generator, target, size, cleanup):
    """Kick off bootstrap process."""
    print(f"Bootstrapping {args.arch}", flush=True)
    if args.chroot:
        find_chroot = """
import shutil
print(shutil.which('chroot'))
"""
        chroot_binary = run_as_root('python3', '-c', find_chroot,
                                    capture_output=True).stdout.decode().strip()

        generator.prepare(target, using_kernel=False)

        arch = stage0_arch_map.get(args.arch, args.arch)
        init = os.path.join(os.sep, 'bootstrap-seeds', 'POSIX', arch, 'kaem-optional-seed')
        run_as_root('env', '-i', 'PATH=/bin', chroot_binary, generator.target_dir, init,
                    cleanup=cleanup)

    elif args.bwrap:
        init = '/init'
        if not args.internal_ci or args.internal_ci == "pass1":
            generator.prepare(target, using_kernel=False)

            arch = stage0_arch_map.get(args.arch, args.arch)
            init = os.path.join(os.sep, 'bootstrap-seeds', 'POSIX', arch, 'kaem-optional-seed')
        else:
            generator.reuse(target)

        run('env', '-i', 'bwrap', '--unshare-user',
                                  '--uid', '0',
                                  '--gid', '0',
                                  '--unshare-net' if args.external_sources else None,
                                  '--setenv', 'PATH', '/usr/bin',
                                  '--bind', generator.target_dir, '/',
                                  '--dir', '/dev',
                                  '--dev-bind', '/dev/null', '/dev/null',
                                  '--dev-bind', '/dev/zero', '/dev/zero',
                                  '--dev-bind', '/dev/random', '/dev/random',
                                  '--dev-bind', '/dev/urandom', '/dev/urandom',
                                  '--dev-bind', '/dev/ptmx', '/dev/ptmx',
                                  '--dev-bind', '/dev/tty', '/dev/tty',
                                  '--tmpfs', '/dev/shm',
                                  '--proc', '/proc',
                                  '--bind', '/sys', '/sys',
                                  '--tmpfs', '/tmp',
                                  init,
            cleanup=cleanup)

    elif args.bare_metal:
        if args.kernel:
            generator.prepare(target, using_kernel=True, target_size=size)
            path = os.path.join(args.target, os.path.relpath(generator.target_dir, args.target))
            print("Please:")
            print(f"  1. Take {path}/initramfs and your kernel, boot using this.")
            print(f"  2. Take {path}/disk.img and put this on a writable storage medium.")
        else:
            generator.prepare(target, kernel_bootstrap=True, target_size=size)
            path = os.path.join(args.target, os.path.relpath(generator.target_dir, args.target))
            print("Please:")
            print(f"  1. Take {path}.img and write it to a boot drive and then boot it.")

    else:
        if args.kernel:
            generator.prepare(target, using_kernel=True, target_size=size)

            arg_list = [
                '-enable-kvm',
                '-m', str(args.qemu_ram) + 'M',
                '-smp', str(args.cores),
                '-drive', 'file=' + target.get_disk("disk") + ',format=raw'
            ]
            if target.get_disk("external") is not None:
                arg_list += [
                    '-drive', 'file=' + target.get_disk("external") + ',format=raw',
                ]
            arg_list += [
                '-nic', 'user,ipv6=off,model=e1000',
                '-kernel', args.kernel,
                '-append',
            ]
            if args.interactive:
                arg_list += ['consoleblank=0 earlyprintk=vga root=/dev/sda1 '
                             'rootfstype=ext3 init=/init rw']
            else:
                arg_list += ['console=ttyS0 earlycon=uart8250,io,0x3f8,115200n8 '
                             'root=/dev/sda1 rootfstype=ext3 init=/init rw']
        else:
            generator.prepare(target, kernel_bootstrap=True, target_size=size)
            arg_list = [
                '-enable-kvm',
                '-m', str(args.qemu_ram) + 'M',
                '-smp', str(args.cores),
                '-drive', 'file=' + generator.target_dir + '.img' + ',format=raw'
            ]
            if target.get_disk("external") is not None:
                arg_list += [
                    '-drive', 'file=' + target.get_disk("external") + ',format=raw',
                ]
            arg_list += [
                '-machine', 'kernel-irqchip=split',
                '-nic', 'user,ipv6=off,model=e1000'
            ]
        if not args.interactive:
            arg_list += ['-no-reboot', '-nographic']
        if "gentoo" in args.extra_builds:
            config_path = args.gentoo_config or os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "system", "gentoo", "config.example.toml")
            start_managed_gentoo_vm(args, target, arg_list, config_path)
        else:
            run(args.qemu_cmd, *arg_list, cleanup=cleanup)

if __name__ == "__main__":
    main()
