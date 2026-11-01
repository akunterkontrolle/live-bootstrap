"""Build and run an internal, RAM-resident offline recovery environment."""
import argparse
from contextlib import contextmanager
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import uuid

SOURCE = Path(__file__).resolve().parent


class RecoveryPendingError(RuntimeError):
    """LVM may have changed; preserve receipts and require inspection."""


@contextmanager
def mounted(m, source, target, options='ro,noload', *, keep=None, unsafe=None):
    """Own a verified recovery mount, including a matching stale prior mount."""
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.path.ismount(target):
        actual = m.run(['findmnt', '-rn', '-M', str(target), '-o', 'SOURCE']).decode().strip()
        kind = m.run(['findmnt', '-rn', '-M', str(target), '-o', 'FSTYPE']).decode().strip()
        flags = m.run(['findmnt', '-rn', '-M', str(target), '-o', 'OPTIONS']).decode().strip().split(',')
        m.need(os.path.realpath(actual) == os.path.realpath(source) and kind == 'ext4' and
               ('ro' if 'ro' in options.split(',') else 'rw') in flags,
               'unexpected existing recovery mount: ' + str(target))
    else:
        m.run(['mount', '-t', 'ext4', '-o', options, source, str(target)])
    try:
        yield target
    finally:
        if os.path.ismount(target) and not (keep and keep()):
            try:
                m.run(['umount', str(target)])
            except Exception as exc:
                if unsafe and unsafe():
                    raise RecoveryPendingError('Mount cleanup failed after merge intent; inspect before retrying: ' + str(target)) from exc
                raise


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def storage_identity(m, cfg, storage):
    m.need(storage.is_absolute() and os.path.ismount(storage), 'recovery storage must be a mounted persistent filesystem')
    source = m.run(['findmnt', '-rn', '-M', str(storage), '-o', 'SOURCE']).decode().strip()
    kind = m.run(['findmnt', '-rn', '-M', str(storage), '-o', 'FSTYPE']).decode().strip()
    m.need(kind == 'ext4', 'internal recovery currently requires an ext4 recovery filesystem')
    ancestors = m.run(['lsblk', '-s', '-r', '-n', '-o', 'PATH', source]).decode().split()
    target = {os.path.realpath(path) for path in m.run(['lvs', '--noheadings', '-o', 'lv_path', cfg['vg']]).decode().split()}
    m.need(not target.intersection(map(os.path.realpath, ancestors)), 'recovery storage must be outside the entire target VG')
    fs_uuid = m.run(['blkid', '-p', '-s', 'UUID', '-o', 'value', source]).decode().strip()
    m.need(str(uuid.UUID(fs_uuid)) == fs_uuid, 'invalid recovery filesystem UUID')
    luks = []
    for path in ancestors:
        try:
            if m.run(['blkid', '-p', '-s', 'TYPE', '-o', 'value', path]).decode().strip() == 'crypto_LUKS':
                luks.append(m.run(['blkid', '-p', '-s', 'UUID', '-o', 'value', path]).decode().strip())
        except RuntimeError:
            continue
    m.need(len(luks) == 1 and str(uuid.UUID(luks[0])) == luks[0],
           'recovery storage must have exactly one identifiable LUKS ancestor')
    return {'filesystem_uuid': fs_uuid, 'luks_uuid': luks[0]}


def menu_entry(m, cfg, descriptor):
    m.protected_path(descriptor)
    data = json.loads(descriptor.read_text())
    m.need(re.fullmatch(r'[a-f0-9]{32}', data['id']), 'invalid internal recovery identity')
    directory = descriptor.parent / data['id']
    for name in ('vmlinuz', 'initramfs.img'):
        path = directory / name
        m.protected_path(path)
        m.need(digest(path) == data['sha256'][name], 'internal recovery artifact changed: ' + name)
    args = data['cmdline']
    m.need(isinstance(args, list) and all(re.fullmatch(r'[A-Za-z0-9=._:/,+-]+', token) for token in args),
           'invalid recovery kernel arguments')
    m.need(args.count('rd.ms1504.recovery=1') == 1 and args.count('noresume') == 1,
           'recovery boot must disable hibernation and select offline recovery')
    escape = lambda value: value.replace('-', '--')
    root = f"(lvm/{escape(cfg['vg'])}-{escape(cfg['system']['root_lv'])})"
    prefix = '/boot/ms1504-recovery/' + data['id']
    commands = [f'linux {prefix}/vmlinuz ' + ' '.join(args), f'initrd {prefix}/initramfs.img']
    if m.signed(cfg):
        body = f'if verify_detached {prefix}/vmlinuz {prefix}/vmlinuz.sig; then\n' + \
               f'        if verify_detached {prefix}/initramfs.img {prefix}/initramfs.img.sig; then\n' + \
               '            ' + '\n            '.join(commands) + '\n        fi\n    fi'
    else:
        body = '\n    '.join(commands)
    return "menuentry 'Offline root rollback (RAM recovery)' {\n    insmod lvm\n    insmod ext2\n" + \
           f'    set root={root}\n    ' + body + '\n}'


def unlock_storage(m, target, storage):
    config = target / 'etc/conf.d/ms1504-extra'
    m.protected_path(config)
    fields = {}
    # Read assignments as data. Never source shell code from the installed root.
    for line in config.read_text().splitlines():
        parts = shlex.split(line)
        m.need(len(parts) == 1 and '=' in parts[0], 'invalid installed extra configuration')
        key, value = parts[0].split('=', 1)
        m.need(key not in fields, 'duplicate installed extra configuration field')
        fields[key] = value
    m.need(set(fields) == {'LUKS_UUID', 'MAPPER', 'KEY', 'MOUNT', 'LABEL'} and
           fields['LUKS_UUID'] == storage['luks_uuid'] and fields['MAPPER'] == 'ms1504-extra' and
           fields['KEY'] == '/root/.keys/ms1504-extra.key' and fields['MOUNT'] == '/extra',
           'installed extra configuration differs from recovery storage')
    filesystem = '/dev/disk/by-uuid/' + str(uuid.UUID(storage['filesystem_uuid']))
    if not Path(filesystem).exists():
        keyfile = target / fields['KEY'].lstrip('/')
        m.protected_path(keyfile)
        info = keyfile.stat()
        m.need(keyfile.is_file() and info.st_uid == 0 and info.st_gid == 0 and
               info.st_mode & 0o777 == 0o400 and info.st_size == 64,
               'installed extra key must be the original root-only 64-byte key')
        device = '/dev/disk/by-uuid/' + str(uuid.UUID(storage['luks_uuid']))
        # Feed the root's key directly through an inherited descriptor. It is
        # never put in argv, an initramfs, a receipt or a temporary file.
        with keyfile.open('rb') as stream:
            subprocess.run(['cryptsetup', 'open', '--key-file', '-', device, 'ms1504-recovery-extra'],
                           stdin=stream, check=True)
        m.run(['udevadm', 'settle'])
    return filesystem


def verify_checkpoint_storage(m, target, cfg, generation):
    """Do not restore a root that would lose access to the unchanged /extra."""
    check = Path('/run/ms1504-checkpoint')
    check.mkdir(mode=0o700, exist_ok=True)
    snapshot = f"/dev/{cfg['vg']}/{cfg['system']['root_lv']}_rollback_{generation}"
    m.run(['mount', '-t', 'ext4', '-o', 'ro,noload', snapshot, str(check)])
    try:
        m.need((check / 'etc/ms1504-generation').read_text().strip() == generation,
               'selected checkpoint has a different generation marker')
        config = 'etc/conf.d/ms1504-extra'
        m.protected_path(check / config)
        m.need((target / config).read_bytes() == (check / config).read_bytes(),
               'checkpoint predates or differs from current /extra configuration; preserve its key before external recovery')
        key = 'root/.keys/ms1504-extra.key'
        for root in (target, check):
            path = root / key
            m.protected_path(path)
            info = path.stat()
            m.need(path.is_file() and info.st_uid == 0 and info.st_gid == 0 and
                   info.st_mode & 0o777 == 0o400 and info.st_size == 64,
                   'checkpoint must retain the original root-only /extra key')
        m.need(digest(target / key) == digest(check / key),
               'checkpoint has a different /extra key; refusing to lose access to unchanged encrypted data')
    finally:
        m.run(['umount', str(check)])


def prepare(m, cfg, args):
    m.root_is_live(cfg)
    m.check_policy(cfg)
    m.command_verify_running(cfg)
    m.need(re.fullmatch(r'[A-Za-z0-9._+-]+', args.kernel_release), 'invalid kernel release')
    m.protected_path(args.kernel)
    m.need(args.kernel.is_file(), 'kernel image is missing')
    m.need(not m.signed(cfg) or bool(args.secret_key_file) != bool(args.gnupg_home),
           'signed recovery preparation requires exactly one signing source')
    identity = storage_identity(m, cfg, args.storage)
    m.need(not (cfg['state_dir'] / 'pruning').exists(), 'finish pruning first')
    journal = cfg['state_dir'] / 'recovery-preparation.json'
    m.protected_path(journal)
    if journal.exists():
        return publish_prepared(m, cfg, args, json.loads(journal.read_text()), identity)
    if (cfg['state_dir'] / 'pending').exists():
        m.check_pending(cfg)
        rows = [m.parse_fields(cfg['state_dir'] / 'pending/transaction')] + m.retained_transactions(cfg)
    else:
        rows = m.snapshot_rows(cfg)
    # The installed module is self-contained: no workspace, root filesystem,
    # /home executable, source download or private key is needed to run it.
    module = Path('/usr/lib/dracut/modules.d/92ms1504-rollback')
    m.protected_path(module)
    module.mkdir(mode=0o755, parents=True, exist_ok=True)
    for name in ('module-setup.sh', 'recover.sh'):
        m.atomic_write(module / name, (SOURCE / 'dracut-ms1504-rollback' / name).read_bytes(), 0o755)
    for name in ('generation.py', 'signed-generation.py', 'generation_recovery.py', 'generation_transition.py'):
        m.atomic_write(module / name, (SOURCE / name).read_bytes(), 0o644)
    m.atomic_write(module / 'config.toml', m.CONFIG.read_bytes(), 0o600)
    m.atomic_write(module / 'storage.json', (json.dumps(identity) + '\n').encode(), 0o600)
    root = cfg['boot_dir'] / 'ms1504-recovery'
    m.protected_path(root)
    root.mkdir(mode=0o755, exist_ok=True)
    ident = uuid.uuid4().hex
    directory = root / ident
    directory.mkdir(mode=0o755)
    shutil.copyfile(args.kernel, directory / 'vmlinuz')
    os.chmod(directory / 'vmlinuz', 0o644)
    m.run(['dracut', '--force', '--kver', args.kernel_release, '--no-hostonly', '--no-hostonly-cmdline',
           '--add', 'ms1504-rollback', '--omit', 'resume', str(directory / 'initramfs.img')])
    listing = m.run(['lsinitrd', str(directory / 'initramfs.img')]).decode()
    m.need(not any(secret in listing for secret in ('ms1504-extra.key', 'secret-key.gpg', '/.gnupg/')),
           'recovery image unexpectedly contains a private key path; do not use it')
    for expected in ('ms1504-python', 'ms1504-offline/generation_recovery.py',
                     'ms1504-offline/signed-generation.py', '00-recover.sh'):
        m.need(expected in listing, 'recovery initramfs lacks ' + expected)
    # Sidecar verification before publishing the descriptor or any menu entry.
    for name in ('vmlinuz', 'initramfs.img'):
        with (directory / name).open('rb') as stream:
            os.fsync(stream.fileno())
        m.sign(directory / name, args, cfg)
        m.verify_signature(directory / name, args, cfg)
    m.fsync_dir(directory)
    cmdline = Path('/etc/kernel/cmdline').read_text().split()
    cmdline = [token for token in cmdline if token != 'noresume' and
               not token.startswith(('resume=', 'rd.ms1504.recovery='))]
    cmdline += ['noresume', 'rd.ms1504.recovery=1']
    descriptor = {'id': ident, 'cmdline': cmdline,
                  'sha256': {name: digest(directory / name) for name in ('vmlinuz', 'initramfs.img')}}
    current = Path('/etc/ms1504-generation').read_text().strip()
    pending = cfg['state_dir'] / 'pending'
    active = cfg['boot_dir'] / 'grub/active/grub.cfg'
    m.verify_signature(active, args, cfg)
    receipt = dict(descriptor=descriptor, current=current, config_sha256=digest(m.CONFIG),
                   kernel_sha256=digest(args.kernel), kernel_release=args.kernel_release, storage=identity,
                   pending_sha256=digest(pending / 'transaction') if pending.exists() else None,
                   previous_menu_sha256=digest(active),
                   snapshots={row['snapshot']: m.run(['lvs', '--noheadings', '-o', 'lv_uuid',
                              '/dev/' + cfg['vg'] + '/' + row['snapshot']]).decode().strip() for row in rows})
    m.atomic_write(journal, (json.dumps(receipt, sort_keys=True) + '\n').encode(), 0o600)
    publish_prepared(m, cfg, args, receipt, identity)


def publish_prepared(m, cfg, args, receipt, identity):
    pending = cfg['state_dir'] / 'pending'
    m.need(receipt['storage'] == identity and receipt['config_sha256'] == digest(m.CONFIG) and
           receipt['kernel_sha256'] == digest(args.kernel) and receipt['kernel_release'] == args.kernel_release and
           receipt['current'] == Path('/etc/ms1504-generation').read_text().strip(),
           'recovery preparation inputs changed; inspect its journal')
    m.need(receipt['pending_sha256'] == (digest(pending / 'transaction') if pending.exists() else None),
           'root transaction changed during recovery preparation')
    rows = m.retained_transactions(cfg)
    if pending.exists():
        rows.insert(0, m.parse_fields(pending / 'transaction'))
    m.need(m.actual_root_snapshots(cfg) == set(receipt['snapshots']) == {row['snapshot'] for row in rows},
           'snapshot inventory changed during recovery preparation')
    for name, recorded_uuid in receipt['snapshots'].items():
        m.need(m.run(['lvs', '--noheadings', '-o', 'lv_uuid', '/dev/' + cfg['vg'] + '/' + name]).decode().strip() == recorded_uuid,
               'snapshot UUID changed during recovery preparation')
        percent, _ = m.snapshot_health(cfg, name)
        m.need(percent < cfg['snapshots']['maximum_data_percent'], 'snapshot reached COW threshold')
    descriptor = cfg['boot_dir'] / 'ms1504-recovery/descriptor.json'
    m.atomic_write(descriptor, (json.dumps(receipt['descriptor'], sort_keys=True) + '\n').encode())
    menu = m.make_menu(cfg, receipt['current'], rows, default=1 if pending.exists() else 0)
    active = cfg['boot_dir'] / 'grub/active/grub.cfg'
    m.need(digest(active) == receipt['previous_menu_sha256'] or active.read_bytes() == menu,
           'active menu changed during recovery publication')
    m.verify_signature(active, args, cfg)
    menu_id = m.publish(cfg, menu, args)
    if pending.exists():
        m.atomic_write(pending / 'menu', (menu_id + '\n').encode(), 0o600)
    (cfg['state_dir'] / 'recovery-preparation.json').unlink()
    m.fsync_dir(cfg['state_dir'])
    print('Internal recovery prepared. Reboot and select Offline root rollback (RAM recovery).')
    print('No merge, reboot, firmware change or private-key copy was performed.')


def recovery_config(m, base):
    m.need(Path('/etc/initrd-release').exists(), 'run internal recovery only inside the initramfs')
    m.need(Path('/proc/cmdline').read_text().split().count('rd.ms1504.recovery=1') == 1,
           'internal recovery boot argument is missing or duplicated')
    m.CONFIG = base / 'config.toml'
    cfg = m.read_config()
    m.need(m.current_root_source() != os.path.realpath(cfg['root']), 'installed root is running')
    return cfg


def active_config(m, target, base, cfg):
    actual = target / 'etc/ms1504-generation.toml'
    m.protected_path(actual)
    m.atomic_write(base / 'active-config.toml', actual.read_bytes(), 0o600)
    m.CONFIG = base / 'active-config.toml'
    active = m.read_config()
    m.need(active['system'] == cfg['system'], 'installed layout differs from recovery image')
    return active


def rollback_choices(m, *, base=Path('/etc/ms1504-offline')):
    cfg = recovery_config(m, base)
    with mounted(m, cfg['root'], Path('/run/ms1504-target')) as target:
        active = active_config(m, target, base, cfg)
        m.validate_root_identity(m.parse_fields(target / 'etc/ms1504-root-identity'), active)
        m.check_policy({**active, 'boot_dir': target / 'boot'}, target=target)
        report = m.merge_report(active)
        if any(row['lv_merging'].strip() or row['lv_attr'].startswith(('O', 'S')) for row in report):
            raise RecoveryPendingError('LVM still reports an unfinished merge; inspect it before another rollback.')
        state = target / active['state_dir'].relative_to('/')
        rows = m.retained_transactions({**active, 'state_dir': state})
        pending = state / 'pending/transaction'
        if pending.exists():
            rows.insert(0, m.parse_fields(pending))
        m.need(m.actual_root_snapshots(active) == {row['snapshot'] for row in rows},
               'snapshot inventory differs from recovery receipts; inspect before continuing')
        choices = []
        for index, row in enumerate(rows):
            reason = '' if index == 0 else 'boot-only; permanent merge currently supports the newest checkpoint'
            try:
                percent, _ = m.snapshot_health(active, row['snapshot'])
                m.need(percent < active['snapshots']['maximum_data_percent'], 'snapshot reached COW safety threshold')
                actual_uuid = m.run(['lvs', '--noheadings', '-o', 'lv_uuid',
                                     '/dev/' + active['vg'] + '/' + row['snapshot']]).decode().strip()
                m.need(not row.get('snapshot_uuid') or row['snapshot_uuid'] == actual_uuid, 'snapshot UUID changed')
            except (RuntimeError, OSError) as exc:
                reason = str(exc)
            if (state / 'signing-migration').exists() or (state / 'recovery-preparation.json').exists():
                reason = 'finish the interrupted signing/recovery preparation first'
            choices.append(dict(generation=row['old'], eligible=not reason, reason=reason,
                                signed=m.signed(active)))
        return choices


def recovery_menu(m, *, base=Path('/etc/ms1504-offline'), signed_key=None, read=input):
    while True:
        choices = rollback_choices(m, base=base)
        print('\nPermanent root rollback — /home and /extra are kept.')
        for index, choice in enumerate(choices, 1):
            note = 'available' if choice['eligible'] else choice['reason']
            print(f"  {index}) {choice['generation']} ({note})")
        if not choices:
            print('  No retained checkpoints are available.')
        print('  0) Reboot without rolling back')
        try:
            selection = read('Choose a checkpoint [0]: ').strip().lower()
            if selection in ('', '0', 'none', 'q'):
                return
            if not selection.isdecimal() or not 1 <= int(selection) <= len(choices):
                print('Choose one of the listed numbers.')
                continue
            choice = choices[int(selection) - 1]
            if not choice['eligible']:
                print(choice['reason'])
                continue
            key = signed_key
            if choice['signed'] and not key:
                key = read('Encrypted signing-key file on unlocked signing storage (blank cancels): ').strip()
                if not key:
                    continue
            offline(m, choice['generation'], base=base, signed_key=key, interactive_menu=True)
        except RecoveryPendingError:
            raise
        except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
            print('Rollback was not started or was cancelled: ' + str(exc))
            continue
        except (EOFError, KeyboardInterrupt):
            print('\nSelection cancelled.')
            return
        print('Permanent rollback completed. Rebooting into the normal root.')
        return


def offline(m, generation, *, base=Path('/etc/ms1504-offline'), signed_key=None, interactive_menu=False):
    cfg = recovery_config(m, base)
    m.need(m.NAME.fullmatch(generation), 'invalid generation name')
    storage = json.loads((base / 'storage.json').read_text())
    target = Path('/run/ms1504-target')
    receipts = Path('/run/ms1504-receipts')
    home = Path('/run/ms1504-home-check')
    # Preserve images and markers. Do not swapoff or erase anything implicitly.
    m.need(m.run(['blkid', '-p', '-s', 'TYPE', '-o', 'value',
                  f"/dev/{cfg['vg']}/{cfg['system']['swap_lv']}"]).decode().strip() == 'swap',
           'hibernation image or unknown swap state; preserve it and stop')
    with mounted(m, f"/dev/{cfg['vg']}/{cfg['system']['home_lv']}", home):
        marker = home / '.ms1504-hibernate/hibernation-pending'
        m.need(not marker.exists() and not marker.is_symlink(), 'resolve the persistent hibernation marker first')
    uncertain = False
    operation = None
    unsafe = lambda: uncertain or bool(operation and getattr(operation, 'merge_started', False))
    with mounted(m, cfg['root'], target, 'ro', unsafe=unsafe):
        active = active_config(m, target, base, cfg)
        filesystem = unlock_storage(m, target, storage)
        verify_checkpoint_storage(m, target, active, generation)
        with mounted(m, filesystem, receipts, 'rw', keep=lambda: uncertain, unsafe=unsafe):
            storage_identity(m, active, receipts)
            m.need(m.run(['blkid', '-p', '-s', 'UUID', '-o', 'value', filesystem]).decode().strip() == storage['filesystem_uuid'],
                   'recovery filesystem differs')
            if m.signed(active):
                m.need(signed_key, 'signed recovery requires --secret-key-file on separately unlocked signing storage')
            operation = argparse.Namespace(generation=generation, recovery_root=str(target), recovery_receipts=str(receipts),
                                           secret_key_file=signed_key, gnupg_home=None,
                                           installed_config='etc/ms1504-generation.toml', interactive_menu=interactive_menu)
            try:
                m.command_merge_rollback(active, operation)
            except (Exception, KeyboardInterrupt) as exc:
                if getattr(operation, 'merge_started', False) or (receipts / ('merge-pending-' + generation)).exists():
                    uncertain = True
                    raise RecoveryPendingError('A merge intent exists. Receipts remain at ' + str(receipts) +
                                               '; inspect LVM before retrying or booting root. ' + str(exc)) from exc
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('generation', nargs='?')
    parser.add_argument('--menu', action='store_true', help='choose a checkpoint interactively; returning requests reboot')
    parser.add_argument('--secret-key-file')
    args = parser.parse_args()
    if bool(args.generation) == args.menu:
        parser.error('choose --menu or one generation name')
    spec = spec_from_file_location('offline_generations', SOURCE / 'signed-generation.py')
    manager = module_from_spec(spec)
    spec.loader.exec_module(manager)
    manager.need(os.geteuid() == 0, 'run as root')
    manager.LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(manager.LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        manager.fcntl.flock(fd, manager.fcntl.LOCK_EX | manager.fcntl.LOCK_NB)
        if args.menu:
            recovery_menu(manager, signed_key=args.secret_key_file)
        else:
            offline(manager, args.generation, signed_key=args.secret_key_file)
            print('Permanent rollback completed. Reboot into the normal root entry; do not continue this boot.')
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        raise SystemExit('internal recovery: ' + str(exc))
