#!/usr/bin/env python3
"""Prepare private VM images and cold-boot from a recorded bootstrap invocation."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.gentoo_config import load, root_image_gib
from system.gentoo.kernel_source import verify_kernel_source

RELEASE = '6.12.111'
GIB = 1024**3
RECORD = 'gentoo-vm.json'


def run(*args):
    executable = shutil.which(str(args[0]), path=os.environ.get('PATH', '') + ':/usr/sbin:/sbin')
    if not executable:
        raise ValueError(f'Missing host tool: {args[0]}')
    return subprocess.check_output([executable, *[str(arg) for arg in args[1:]]], text=True,
                                   env=dict(os.environ, LC_ALL='C')).strip()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path):
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f'Expected a regular image/file, without a symlink: {path}')
    if any(c in str(path) for c in ',\n\r\0'):
        raise ValueError(f'Unsafe QEMU path: {path}')
    return path


def atomic_json(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.record-', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def run_lock(directory):
    with (directory / '.gentoo-vm.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def parse_command(argv, cwd):
    """Support rootfs.py's actual argument forms; reject ambiguous drive syntax."""
    if not isinstance(argv, list) or not argv or any(not isinstance(v, str) or not v or '\0' in v for v in argv):
        raise ValueError('QEMU command must be a nonempty JSON array of argument strings')
    executable = shutil.which(argv[0])
    if not executable or Path(executable).name != 'qemu-system-x86_64':
        raise ValueError('Expected the qemu-system-x86_64 executable')
    base, boot, disks = [], [], []
    cpus = ram = None
    root = '/dev/sda1'
    index = 1
    while index < len(argv):
        option = argv[index]
        index += 1
        if option in ('-enable-kvm', '-nographic', '-no-reboot'):
            if option != '-no-reboot':
                base.append(option)
            continue
        if index == len(argv):
            raise ValueError(f'Missing value for {option}')
        value = argv[index]
        index += 1
        if option == '-drive':
            pairs = value.split(',')
            if any('=' not in pair for pair in pairs):
                raise ValueError('Use explicit file=,format= for every bootstrap drive')
            fields = dict(pair.split('=', 1) for pair in pairs)
            if len(fields) != len(pairs) or set(fields) - {'file', 'format', 'if', 'index'}:
                raise ValueError('Bootstrap drive supports only file, format, if=ide and index')
            if fields.get('format') not in ('raw', 'qcow2') or fields.get('if', 'ide') != 'ide':
                raise ValueError('Bootstrap disks must be raw/qcow2 IDE disks as emitted by rootfs.py')
            if fields.get('index', str(len(disks))) != str(len(disks)):
                raise ValueError('Bootstrap drive order/index is ambiguous')
            path = Path(fields['file'])
            if not path.is_absolute():
                path = cwd / path
            # Test the lexical path before resolve so a symlink is not hidden.
            regular(path)
            disks.append({'path': str(path.resolve()), 'format': fields['format']})
        elif option in ('-kernel', '-initrd'):
            path = Path(value) if Path(value).is_absolute() else cwd / value
            boot.extend([option, str(regular(path).resolve())])
        elif option == '-append':
            boot.extend([option, value])
            roots = [arg[5:] for arg in value.split() if arg.startswith('root=')]
            if len(roots) > 1:
                raise ValueError('Multiple bootstrap root arguments')
            if roots:
                root = roots[0]
        elif option == '-smp':
            if cpus is not None or not re.fullmatch(r'[1-9][0-9]*', value):
                raise ValueError('Use the single integer -smp value emitted by rootfs.py')
            cpus = int(value)
            base.extend([option, value])
        elif option == '-m':
            match = re.fullmatch(r'([1-9][0-9]*)([MG]?)', value)
            if ram is not None or not match:
                raise ValueError('Use an integer QEMU memory value with optional M/G suffix')
            ram = int(match[1]) * (1024 if match[2] == 'G' else 1)
            base.extend([option, value])
        elif option in ('-machine', '-cpu', '-accel', '-nic', '-display', '-vnc', '-name'):
            if option == '-machine':
                model = value.split(',')[0]
                if '=' not in model and model != 'pc' and not model.startswith('pc-i440fx-'):
                    raise ValueError('The original bootstrap requires the pc/i440fx IDE machine, not a different controller layout')
            base.extend([option, value])
        else:
            raise ValueError(f'Unsupported bootstrap option: {option}; supply the rootfs.py invocation without monitor/serial overrides')
    if cpus is None or ram is None or not 1 <= len(disks) <= 2:
        raise ValueError('Record needs -smp, -m and one or two bootstrap disks')
    if not re.fullmatch(r'(/dev/[A-Za-z0-9_/-]+|(?:UUID|PARTUUID)=[A-Za-z0-9-]+)', root):
        raise ValueError('Unsafe bootstrap root argument')
    if len({disk['path'] for disk in disks}) != len(disks):
        raise ValueError('Bootstrap disks must differ')
    return {'executable': executable, 'base_options': base, 'boot_options': boot,
            'bootstrap_disks': disks, 'cpus': cpus, 'ram_mib': ram, 'bootstrap_root': root}


def new_record(settings, directory, argv, cwd):
    record = parse_command(argv, cwd)
    run_id = uuid.uuid4().hex[:10]
    record.update(version=1, run_id=run_id, original_argv=argv, original_cwd=str(cwd))
    record['images'] = {}
    for role in ('workspace', 'scratch', 'root', 'extra'):
        fmt = 'raw' if role in ('workspace', 'scratch') else 'qcow2'
        capacity = root_image_gib(settings) if role == 'root' else settings['images'][role + '_gib']
        suffix = 'WS' if role == 'workspace' else role.upper()
        item = {'path': str(directory / 'images' / (role + ('.img' if fmt == 'raw' else '.qcow2'))),
                'format': fmt, 'bytes': capacity * GIB, 'serial': f'GT{run_id}{suffix}'}
        if fmt == 'raw':
            item['uuid'] = str(uuid.UUID(settings['workspace']['uuid'])) if role == 'workspace' and settings['workspace']['uuid'] else str(uuid.uuid4())
            item['label'] = settings['workspace']['label'] if role == 'workspace' else 'GENTOO_SCRATCH'
        record['images'][role] = item
    for index, disk in enumerate(record['bootstrap_disks']):
        disk['serial'] = f'GT{run_id}' + ('BOOT' if index == 0 else 'SOURCES')
    record['configuration'] = {'images': settings['images'], 'root_gib': root_image_gib(settings),
                               'workspace': {key: settings['workspace'][key] for key in ('label', 'uuid')}}
    return record


def read_record(directory):
    path = regular(directory / RECORD)
    record = json.loads(path.read_text())
    if record.get('version') != 1 or set(record.get('images', {})) != {'workspace', 'scratch', 'root', 'extra'}:
        raise ValueError('Unsupported or incomplete VM record')
    original = parse_command(record['original_argv'], Path(record['original_cwd']))
    for key in ('executable', 'base_options', 'boot_options', 'cpus', 'ram_mib', 'bootstrap_root'):
        if record.get(key) != original[key]:
            raise ValueError(f'VM record differs from its original invocation: {key}')
    if len(record['bootstrap_disks']) != len(original['bootstrap_disks']):
        raise ValueError('Bootstrap image count differs from original invocation')
    for disk, expected in zip(record['bootstrap_disks'], original['bootstrap_disks']):
        if any(disk.get(key) != expected[key] for key in ('path', 'format')):
            raise ValueError('Bootstrap disk differs from original invocation')
    # Managed images may never be redirected outside the recorded run directory.
    for role, item in record['images'].items():
        fmt = 'raw' if role in ('workspace', 'scratch') else 'qcow2'
        expected = directory / 'images' / (role + ('.img' if fmt == 'raw' else '.qcow2'))
        if item.get('path') != str(expected) or item.get('format') != fmt:
            raise ValueError('Managed image path/format differs from its recorded role')
        if type(item.get('bytes')) is not int or item['bytes'] <= 0:
            raise ValueError('Invalid recorded image capacity')
        if fmt == 'raw':
            uuid.UUID(item['uuid'])
            if not re.fullmatch('[A-Za-z0-9_-]{1,16}', item['label']):
                raise ValueError('Invalid recorded filesystem label')
    serials = [item['serial'] for item in all_images(record)]
    if len(set(serials)) != len(serials) or any(not re.fullmatch('[A-Za-z0-9]{1,20}', serial) for serial in serials):
        raise ValueError('Invalid/duplicate generated disk serial')
    if record['images']['workspace']['uuid'] == record['images']['scratch']['uuid']:
        raise ValueError('Workspace and scratch UUIDs must differ')
    return record


def all_images(record):
    return record['bootstrap_disks'] + list(record['images'].values())


def check_idle(items):
    inodes = set()
    for item in items:
        path = Path(item['path'])
        if not path.exists() and not path.is_symlink():
            continue
        regular(path)
        identity = (path.stat().st_dev, path.stat().st_ino)
        if identity in inodes:
            raise ValueError('Two disk roles refer to the same file/inode')
        inodes.add(identity)
        if run('losetup', '-j', path):
            raise ValueError(f'Image is attached to a loop device: {path}')
        result = subprocess.run(['fuser', '-s', '--', str(path)], capture_output=True)
        if result.returncode == 0:
            raise ValueError(f'Image is in use; shut down/unmount first: {path}')
        if result.returncode != 1 or result.stderr:
            raise ValueError(f'Could not establish image idleness: {path}')


def inspect_image(item, filesystem=False):
    path = regular(item['path'])
    info = json.loads(run('qemu-img', 'info', '--output=json', path))
    format_data = info.get('format-specific', {}).get('data', {})
    if (info['format'] != item['format'] or info.get('backing-filename') or info.get('full-backing-filename')
            or info.get('encrypted') or info.get('data-file') or format_data.get('data-file') or format_data.get('encrypt')):
        raise ValueError(f'Unexpected format/backing/encryption for {path}')
    if 'bytes' in item and info['virtual-size'] != item['bytes']:
        raise ValueError(f'Image size mismatch (never resized automatically): {path}')
    if filesystem:
        for field, expected in (('TYPE', 'ext4'), ('LABEL', item['label']), ('UUID', item['uuid'])):
            if run('blkid', '-p', '-s', field, '-o', 'value', path) != expected:
                raise ValueError(f'Filesystem {field} mismatch: {path}')
        header = run('dumpe2fs', '-h', path)
        state = re.search(r'^Filesystem state:\s*(.*)$', header, re.M)
        features = re.search(r'^Filesystem features:\s*(.*)$', header, re.M)
        if not state or state[1] != 'clean' or not features or 'needs_recovery' in features[1].split():
            raise ValueError(f'Unclean filesystem; inspect/repair offline before resuming: {path}')


def create_image(item):
    path = Path(item['path'])
    if path.parent.is_symlink():
        raise ValueError('Image directory must not be a symlink')
    if path.exists() or path.is_symlink():
        inspect_image(item, filesystem=item['format'] == 'raw')
        return
    path.parent.mkdir(exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.pending-')
    temporary = Path(name)
    try:
        if item['format'] == 'raw':
            os.ftruncate(descriptor, item['bytes'])
            os.close(descriptor)
            descriptor = None
            # Explicit old-kernel-compatible ext4 features; do not depend on
            # a host's changing mke2fs.conf defaults (metadata_csum/orphan_file).
            run('mkfs.ext4', '-F', '-q', '-b', '4096', '-m', '0', '-U', item['uuid'], '-L', item['label'],
                '-O', 'none,has_journal,ext_attr,dir_index,filetype,extent,sparse_super,large_file,uninit_bg,dir_nlink,extra_isize,flex_bg', temporary)
        else:
            os.close(descriptor)
            descriptor = None
            run('qemu-img', 'create', '-f', 'qcow2', temporary, item['bytes'])
        pending = dict(item, path=str(temporary))
        inspect_image(pending, filesystem=item['format'] == 'raw')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        # Exclusive publication: even an unexpected concurrent target is never
        # overwritten. A crash leaves only a recognizable .pending-* file.
        os.link(temporary, path)
        sync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def prepare(settings, directory, argv, cwd):
    desired = new_record(settings, directory, argv, cwd)
    if (directory / RECORD).exists():
        record = read_record(directory)
        if any(record[key] != desired[key] for key in ('original_argv', 'original_cwd', 'configuration')):
            raise ValueError('Recorded invocation/image configuration changed; preserve this run and use a new run directory')
    else:
        record = desired
    check_idle(all_images(record))
    if not (directory / RECORD).exists():
        # Existing filesystem images have real UUIDs, not user-supplied scratch
        # identities. Adopt them only after idleness, type, label and capacity
        # checks; no existing filesystem is ever formatted.
        for role in ('workspace', 'scratch'):
            item = record['images'][role]
            path = Path(item['path'])
            if path.exists():
                inspect_image(item)
                if run('blkid', '-p', '-s', 'TYPE', '-o', 'value', path) != 'ext4' or run('blkid', '-p', '-s', 'LABEL', '-o', 'value', path) != item['label']:
                    raise ValueError('Existing image has an unexpected filesystem/label')
                discovered = str(uuid.UUID(run('blkid', '-p', '-s', 'UUID', '-o', 'value', path)))
                if role == 'workspace' and settings['workspace']['uuid'] and discovered != item['uuid']:
                    raise ValueError('Existing workspace UUID differs from configuration')
                item['uuid'] = discovered
                inspect_image(item, filesystem=True)
        if record['images']['workspace']['uuid'] == record['images']['scratch']['uuid']:
            raise ValueError('Workspace and scratch UUIDs must differ')
    for disk in record['bootstrap_disks']:
        inspect_image(disk)
        if 'bytes' not in disk:
            disk['bytes'] = json.loads(run('qemu-img', 'info', '--output=json', disk['path']))['virtual-size']
    if not (directory / RECORD).exists():
        atomic_json(directory / RECORD, record)
    for item in record['images'].values():
        create_image(item)
    return record


def verify_manifest(directory, name='SHA256SUMS'):
    manifest = regular(directory / name)
    seen = set()
    for line in manifest.read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (?:\./)?([A-Za-z0-9_./+-]+)', line)
        if not match:
            raise ValueError(f'Invalid manifest record in {manifest}')
        checksum, relative = match.groups()
        if relative.startswith('/') or '..' in Path(relative).parts or relative in seen:
            raise ValueError('Unsafe/duplicate manifest path')
        seen.add(relative)
        path = directory / relative
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Manifest path escapes its bundle')
        if digest(regular(path)) != checksum:
            raise ValueError(f'Checksum mismatch: {path}')
    if not seen:
        raise ValueError('Empty manifest')
    return seen


def check_kernel(path):
    with regular(path).open('rb') as stream:
        header = stream.read(568)
    if len(header) < 568 or header[510:512] != b'\x55\xaa' or header[514:518] != b'HdrS' or not struct.unpack_from('<H', header, 566)[0] & 1:
        raise ValueError('Expected an x86_64 bzImage boot-protocol header')


def verify_handoff(build, workspace_uuid):
    inputs = (build / 'current-handoff').read_text().strip()
    if not re.fullmatch(r'[0-9a-f]{64}', inputs):
        raise ValueError('Invalid current handoff selector')
    handoff = build / 'handoff' / inputs
    metadata = json.loads(regular(handoff / 'handoff.json').read_text())
    if metadata.get('version') != 1 or metadata.get('release') != RELEASE or metadata.get('inputs') != inputs or metadata.get('workspace_uuid') != workspace_uuid:
        raise ValueError('Handoff identity mismatch')
    snapshot = build / 'inputs' / inputs
    if digest(snapshot / 'SHA256SUMS') != inputs:
        raise ValueError('Input snapshot identity mismatch')
    inputs_files = verify_manifest(snapshot)
    for required in ('gentoo.sh', 'post-reboot.sh', 'shutdown.sh', 'config.sh', 'config.inputs.sh'):
        if required not in inputs_files:
            raise ValueError(f'Missing durable input: {required}')
    for saved, original in (('resume.sh', 'post-reboot.sh'), ('shutdown.sh', 'shutdown.sh')):
        if digest(handoff / saved) != digest(snapshot / original):
            raise ValueError('Post-reboot script differs from verified snapshot')
    artifacts = build / 'artifacts' / ('kernel-' + RELEASE)
    if digest(artifacts / 'SHA256SUMS') != metadata['kernel_manifest_sha256']:
        raise ValueError('Kernel artifact manifest mismatch')
    files = verify_manifest(artifacts)
    required = {'bzImage', 'config', 'System.map', 'kernelrelease', 'build-boot-id', 'build-id', 'kernel.SHA256SUMS'}
    module_prefix = f'modules/lib/modules/{RELEASE}/kernel/'
    modules = [name for name in files if name.startswith(module_prefix) and name.endswith('.ko')]
    if not required <= files or not modules:
        raise ValueError('Incomplete kernel/module artifacts')
    for name in modules:
        with (artifacts / name).open('rb') as stream:
            header = stream.read(20)
        if len(header) < 20 or header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 62:
            raise ValueError('Kernel module has the wrong ELF class/machine')
    if (artifacts / 'kernelrelease').read_text().strip() != RELEASE or (artifacts / 'build-boot-id').read_text().strip() != metadata['boot_id']:
        raise ValueError('Kernel release/build boot identity mismatch')
    for stage, field in (('30-kernel', 'kernel_receipt_sha256'), ('31-kernel-modules', 'modules_receipt_sha256')):
        receipt = regular(build / 'state' / (stage + '.done'))
        if not re.fullmatch(r'[0-9a-f]{64}\n', receipt.read_text()) or digest(receipt) != metadata[field]:
            raise ValueError('Kernel/module stage receipt mismatch')
    check_kernel(artifacts / 'bzImage')
    return artifacts, metadata


def verify_installed_cmdline(commandline):
    args = commandline.split()
    if not args or any(not re.fullmatch(r'[A-Za-z0-9=._:/,+-]+', value) for value in args):
        raise ValueError('Installed kernel command line contains an unsafe token')
    roots = [value for value in args if value.startswith('root=')]
    luks = [value for value in args if value.startswith('rd.luks.uuid=')]
    lv_roots = [value for value in args if value.startswith('rd.lvm.lv=')]
    if (len(roots) != 1 or not re.fullmatch(r'root=/dev/mapper/[A-Za-z0-9+_.-]+-root', roots[0]) or
            len(luks) != 1 or not re.fullmatch(r'rd.luks.uuid=luks-[0-9a-fA-F-]{36}', luks[0]) or
            sorted(lv_roots) != sorted(set(lv_roots)) or
            set(lv_roots) != {f'rd.lvm.lv={roots[0].split("/")[-1][:-5]}/root',
                              f'rd.lvm.lv={roots[0].split("/")[-1][:-5]}/home',
                              f'rd.lvm.lv={roots[0].split("/")[-1][:-5]}/swap'} or args.count('ro') != 1 or 'rw' in args):
        raise ValueError('Installed kernel command line does not select the encrypted root layout')
    if any(value.startswith(('init=', 'rootfstype=', 'gentoo_')) for value in args):
        raise ValueError('Installed kernel command line contains a bootstrap-only override')
    expected_resume = f'resume=/dev/mapper/{roots[0].split("/")[-1][:-5]}-swap'
    if args.count(expected_resume) != 1:
        raise ValueError('Installed kernel command line does not select the configured swap LV')
    return args


@contextmanager
def workspace_mount(item):
    if os.geteuid() != 0:
        raise ValueError('Kernel extraction requires root for a read-only loop mount')
    inspect_image(item, filesystem=True)
    with tempfile.TemporaryDirectory(prefix='gentoo-kernel-') as temporary:
        run('mount', '-t', 'ext4', '-o', 'loop,ro,noload,nosuid,nodev,noexec', item['path'], temporary)
        try:
            yield Path(temporary)
        finally:
            run('umount', temporary)


def stage_kernel(directory, record):
    item = record['images']['workspace']
    with workspace_mount(item) as mounted:
        artifacts, metadata = verify_handoff(mounted / 'gentoo-bootstrap', item['uuid'])
        source_verification = verify_kernel_source(mounted / 'gentoo-bootstrap',
                                                  mounted / 'gentoo-bootstrap/inputs' / metadata['inputs'])
        identity = metadata['kernel_manifest_sha256']
        destination = directory / 'kernels' / identity / metadata['inputs']
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            for name in ('bzImage', 'config', 'SHA256SUMS', 'kernelrelease'):
                if digest(regular(destination / name)) != digest(artifacts / name):
                    raise ValueError('Existing staged kernel artifacts differ from workspace')
            if json.loads((destination / 'handoff.json').read_text()) != metadata or json.loads((destination / 'source-signature.json').read_text()) != source_verification:
                raise ValueError('Existing staged handoff/source verification differs')
        else:
            with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.kernel-') as temporary:
                pending = Path(temporary) / 'bundle'
                pending.mkdir()
                for name in ('bzImage', 'config', 'SHA256SUMS', 'kernelrelease'):
                    shutil.copyfile(artifacts / name, pending / name)
                atomic_json(pending / 'handoff.json', metadata)
                atomic_json(pending / 'source-signature.json', source_verification)
                for path in pending.iterdir():
                    with path.open('rb') as stream:
                        os.fsync(stream.fileno())
                pending.rename(destination)
                sync_directory(destination.parent)
        return destination / 'bzImage', metadata


def command(record, directory, kernel=None, initrd=None, kernel_append=None):
    argv = [record['executable'], *record['base_options']]
    for index, item in enumerate(record['bootstrap_disks']):
        # Serial belongs to the IDE device, not the raw/qcow2 block driver.
        # Keep the rootfs.py drive order on the machine's primary IDE bus.
        argv.extend(['-drive', f'file={item["path"]},format={item["format"]},if=none,id=gt_boot_{index}',
                     '-device', f'ide-hd,drive=gt_boot_{index},bus=ide.0,unit={index},serial={item["serial"]}'])
    argv.extend(['-device', 'usb-ehci,id=gentoo_ehci'])
    for role, item in record['images'].items():
        argv.extend(['-drive', f'file={item["path"]},format={item["format"]},if=none,id=gt_{role}',
                     '-device', f'usb-storage,drive=gt_{role},bus=gentoo_ehci.0,serial={item["serial"]}'])
    if kernel:
        free_kib = shutil.disk_usage(directory).free // 1024
        boot_disk = record['bootstrap_disks'][0]
        root_image = record['images']['root']
        extra_image = record['images']['extra']
        argv.extend(['-kernel', str(kernel), '-append',
                     f'root={record["bootstrap_root"]} rootfstype=ext4 init=/bin/bash rw rootwait '
                     'console=tty0 console=ttyS0,115200n8 '
                     f'gentoo_scratch_uuid={record["images"]["scratch"]["uuid"]} '
                     f'gentoo_root_serial={root_image["serial"]} gentoo_root_bytes={root_image["bytes"]} '
                     f'gentoo_boot_serial={boot_disk["serial"]} gentoo_boot_bytes={boot_disk.get("bytes", 1)} '
                     f'gentoo_extra_serial={extra_image["serial"]} gentoo_extra_bytes={extra_image["bytes"]} '
                     f'gentoo_workspace_host_free_kib={free_kib} gentoo_scratch_host_free_kib={free_kib}'])
        if initrd:
            argv.extend(['-initrd', str(initrd)])
        if kernel_append:
            argv[argv.index('-append') + 1] = kernel_append
    else:
        argv.extend(record['boot_options'])
    serial, qmp = directory / 'serial.sock', directory / 'qmp.sock'
    if max(len(str(serial)), len(str(qmp))) >= 100:
        raise ValueError('Run directory is too long for Unix console/QMP sockets')
    argv.extend(['-no-reboot', '-monitor', 'none', '-qmp', f'unix:{qmp},server=on,wait=off',
                 '-chardev', f'socket,id=gt_serial,path={serial},server=on,wait=off,logfile={directory}/console.log,logappend=on',
                 '-serial', 'chardev:gt_serial'])
    # A supplied -nographic already disables display. Otherwise use headless
    # operation unless an explicit display/VNC choice was in the original argv.
    if not any(option in record['base_options'] for option in ('-nographic', '-display', '-vnc')):
        argv.extend(['-display', 'none'])
    return argv


def stage_installed_boot(directory, record):
    """Copy the receipt-bound installed kernel/initramfs from offline workspace."""
    item = record['images']['workspace']
    with workspace_mount(item) as mounted:
        build = mounted / 'gentoo-bootstrap'
        state = build / 'install-base-state'
        receipt_path = regular(state / 'installed-boot.receipt')
        receipts = {}
        for line in receipt_path.read_text().splitlines():
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key in receipts:
                raise ValueError(f'Duplicate installed-boot receipt field: {key}')
            receipts[key] = value
        required = {'release', 'kernel sha256', 'initramfs sha256', 'workspace UUID', 'cmdline'}
        if set(receipts) != required or receipts['release'] != RELEASE:
            raise ValueError('Installed-boot receipt is incomplete or has an unsupported release')
        if receipts['workspace UUID'].lower() != item['uuid'].lower():
            raise ValueError('Installed-boot receipt belongs to another workspace')
        if not re.fullmatch(r'[0-9a-f]{64}', receipts['kernel sha256']) or not re.fullmatch(r'[0-9a-f]{64}', receipts['initramfs sha256']):
            raise ValueError('Installed-boot artifact digest is malformed')
        verify_installed_cmdline(receipts['cmdline'])
        if not (state / 'storage.done').is_file() or not (state / 'root-install.done').is_file():
            raise ValueError('Encrypted storage or root-install receipt is missing')
        source = build / 'artifacts' / 'installed-base'
        kernel_source = regular(source / 'kernel')
        initrd_source = regular(source / 'initramfs')
        if digest(kernel_source) != receipts['kernel sha256'] or digest(initrd_source) != receipts['initramfs sha256']:
            raise ValueError('Installed kernel or initramfs differs from its durable receipt')
        check_kernel(kernel_source)
        identity = receipts['initramfs sha256']
        destination = directory / 'kernels' / 'installed-base' / identity
        if destination.exists():
            if digest(regular(destination / 'kernel')) != receipts['kernel sha256'] or digest(regular(destination / 'initramfs')) != identity:
                raise ValueError('Staged installed boot artifacts differ from the workspace receipt')
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.installed-boot-') as temporary:
                pending = Path(temporary) / 'bundle'
                pending.mkdir()
                shutil.copyfile(kernel_source, pending / 'kernel')
                shutil.copyfile(initrd_source, pending / 'initramfs')
                atomic_json(pending / 'receipt.json', receipts)
                pending.rename(destination)
                sync_directory(destination.parent)
        return destination / 'kernel', destination / 'initramfs', receipts['cmdline'], receipts


def launch(directory, record, continuation, installed=False):
    check_idle(all_images(record))
    for item in all_images(record):
        inspect_image(item, filesystem='uuid' in item)
    for name in ('serial.sock', 'qmp.sock'):
        if (directory / name).exists():
            raise ValueError(f'Socket already exists; inspect its owner before removing: {directory / name}')
    handoff = None
    installed_boot = None
    if continuation:
        kernel, handoff = stage_kernel(directory, record)
    elif installed:
        kernel, initrd, cmdline, installed_boot = stage_installed_boot(directory, record)
        argv = command(record, directory, kernel, initrd, cmdline)
    else:
        kernel = None
    if not installed:
        argv = command(record, directory, kernel)
    atomic_json(directory / 'last-launch.json', {'argv': argv, 'cpus': record['cpus'], 'ram_mib': record['ram_mib'],
                                                 'handoff': handoff, 'installed_boot': installed_boot})
    print(f'Console: socat -,raw,echo=0 UNIX-CONNECT:{directory}/serial.sock', flush=True)
    if handoff:
        print(f'Guest after mounting the workspace at /build: bash /build/gentoo-bootstrap/handoff/{handoff["inputs"]}/resume.sh', flush=True)
    # Keep the process-bound run lock until QEMU exits; no background PID locks.
    subprocess.run(argv, cwd=directory, check=True)
    for name in ('serial.sock', 'qmp.sock'):
        path = directory / name
        if path.is_socket():
            path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('prepare', 'start', 'continue', 'boot-installed', 'status'))
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--qemu-command', type=Path, help='JSON array containing the original rootfs.py QEMU command')
    parser.add_argument('--qemu-cwd', type=Path, default=Path.cwd())
    parser.add_argument('--apply', action='store_true', help='create/inspect images or launch QEMU; default: print a plan')
    args = parser.parse_args()
    directory = args.run_dir.absolute()
    if any(c in str(directory) for c in ',\n\r\0') or directory.is_symlink():
        parser.error('Run directory must not be a symlink or contain commas/control characters')
    if args.stage == 'prepare':
        if not args.config or not args.qemu_command:
            parser.error('prepare needs --config and --qemu-command; continuation reads the saved record')
        settings = load(args.config, 'vm')
        argv = json.loads(args.qemu_command.read_text())
        if not args.apply:
            proposed = new_record(settings, directory, argv, args.qemu_cwd.resolve())
            if (directory / RECORD).exists():
                existing = read_record(directory)
                if any(existing[key] != proposed[key] for key in ('original_argv', 'original_cwd', 'configuration')):
                    raise ValueError('Recorded invocation/image configuration differs from proposed preparation')
                proposed = existing
            print(json.dumps(proposed, indent=2))
            print('Plan only; no files created or images modified.')
            return
        directory.mkdir(parents=True, exist_ok=True)
        directory = directory.resolve()
        with run_lock(directory):
            record = prepare(settings, directory, argv, args.qemu_cwd.resolve())
        print(f'Prepared {directory / RECORD}: {record["cpus"]} CPUs, {record["ram_mib"]} MiB')
    else:
        if args.config or args.qemu_command:
            parser.error('Use the saved run record; configuration/QEMU overrides are only accepted by prepare')
        directory = directory.resolve(strict=True)
        record = read_record(directory)
        if args.stage == 'status':
            print(json.dumps(record, indent=2))
            print('Run lock: ', end='')
            try:
                with run_lock(directory):
                    print('free (this is not a filesystem-cleanliness check)')
            except BlockingIOError:
                print('held by a lifecycle operation/VM')
        elif args.apply:
            with run_lock(directory):
                launch(directory, record, args.stage == 'continue', args.stage == 'boot-installed')
        else:
            print(f'{args.stage}: {record["cpus"]} CPUs, {record["ram_mib"]} MiB; original image paths from {directory / RECORD}')
            print('Plan only; no images mounted and no VM started.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'gentoo-vm: {error}')
