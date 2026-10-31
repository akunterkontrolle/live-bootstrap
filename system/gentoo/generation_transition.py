"""Explicit, resumable signing preparation and one-way policy activation.

This module never edits firmware, imports a key into GRUB, or generates a key.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def configuration(cfg):
    sections = dict(system=cfg['system'], boot=cfg['boot'], snapshots=cfg['snapshots'],
                    state={'directory': str(cfg['state_dir'])})
    return ''.join('[' + section + ']\n' + ''.join(
        key + ' = ' + json.dumps(value) + '\n' for key, value in fields.items()) + '\n'
        for section, fields in sections.items()).encode()


def target_config(cfg, fingerprint, key):
    return {**cfg, 'policy': 'signed', 'public_key': key,
            'boot': {'directory': '/boot', 'policy': 'signed', 'public_key': str(key),
                     'signing_fingerprint': fingerprint}}


def validate_interlocks(m):
    source = Path(__file__).resolve().parent
    guard = Path('/usr/local/libexec/gentoo-continuation/source-only-emerge.py')
    hibernate = Path('/usr/local/sbin/ms1504-hibernate').resolve()
    m.need(guard.is_file() and guard.read_bytes() == (source / 'source-only-emerge.py').read_bytes(),
           'install the matching source-only package guard before signing preparation')
    m.need(hibernate.is_file() and hibernate.parent == source and
           hibernate.read_bytes() == (source / 'hibernate.py').read_bytes(),
           'install the matching hibernation tool cohort before signing preparation')


def inventory(m, cfg):
    m.root_is_live(cfg)
    m.read_root_identity(cfg)
    m.need(not (cfg['state_dir'] / 'pending').exists(), 'finish or recover the root update before signing migration')
    m.need(not (cfg['state_dir'] / 'pruning').exists(), 'finish pruning before signing migration')
    current = Path('/etc/ms1504-generation').read_text().strip()
    m.need(m.NAME.fullmatch(current), 'invalid installed generation')
    rows = [{key: value for key, value in row.items() if key not in ('data_percent', 'size_bytes')}
            for row in m.snapshot_rows(cfg)]
    for row in rows:
        row['actual_snapshot_uuid'] = m.run(['lvs', '--noheadings', '-o', 'lv_uuid',
                                           '/dev/' + cfg['vg'] + '/' + row['snapshot']]).decode().strip()
    files = {}
    for generation in sorted({current} | {row['old'] for row in rows}):
        m.verify_artifact_receipt(cfg, generation)
        for name in ('vmlinuz', 'initramfs.img'):
            path = cfg['boot_dir'] / 'generations' / generation / name
            m.protected_path(path)
            files[str(path)] = digest(path)
    recovery = cfg['boot_dir'] / 'ms1504-recovery/descriptor.json'
    if recovery.exists():
        descriptor = json.loads(recovery.read_text())
        for name in ('vmlinuz', 'initramfs.img'):
            m.need(m.re.fullmatch(r'[a-f0-9]{32}', descriptor['id']), 'invalid recovery identity')
            path = recovery.parent / descriptor['id'] / name
            m.protected_path(path)
            m.need(digest(path) == descriptor['sha256'][name], 'recovery artifact changed')
            files[str(path)] = digest(path)
        files[str(recovery)] = digest(recovery)
    return {'current': current, 'rows': rows, 'files': files,
            'cmdline': Path('/etc/kernel/cmdline').read_text()}


def validate_inputs(m, cfg, manifest):
    now = inventory(m, cfg)
    m.need(now == manifest['inventory'], 'migration inputs changed; preserve the journal and inspect before continuing')
    m.need(digest(Path(manifest['key'])) == manifest['key_sha256'], 'migration public key changed')
    m.need(str(m.CONFIG) == manifest['config'], 'migration must use its original configuration path')


def prepare(m, cfg, args, directory):
    m.need(not m.signed(cfg), 'signing preparation requires the existing unsigned policy')
    m.check_policy(cfg)
    m.command_verify_running(cfg)
    m.verify_signature(cfg['boot_dir'] / 'grub/grub.cfg', args, cfg)
    m.verify_signature(cfg['boot_dir'] / 'grub/active/grub.cfg', args, cfg)
    validate_interlocks(m)
    m.protected_path(args.public_key)
    fingerprint = args.fingerprint.replace(' ', '').upper()
    m.need(m.re.fullmatch(r'[A-F0-9]{40}|[A-F0-9]{64}', fingerprint), 'supply the full primary fingerprint')
    key = Path('/etc/ms1504-boot/boot.key')
    target = target_config(cfg, fingerprint, key)
    source = {**target, 'public_key': args.public_key}
    m.verify_public_key(source)
    listing = m.run(['gpg', '--batch', '--show-keys', '--with-colons', str(args.public_key)]).decode()
    primary = [line.split(':') for line in listing.splitlines() if line.startswith('pub:')]
    m.need(len(primary) == 1 and primary[0][3] in ('1', '3') and 's' in primary[0][11].lower(),
           'use a dedicated RSA signing key compatible with the firmware GRUB')
    m.protected_path(key)
    if key.exists():
        m.need(key.read_bytes() == args.public_key.read_bytes(), 'refusing to replace a different installed public key')
    else:
        m.atomic_write(key, args.public_key.read_bytes())
    manifest_path = directory / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        m.need(manifest['fingerprint'] == fingerprint and manifest['phase'] in ('preparing', 'prepared'),
               'migration key differs or activation has started; resume activation instead')
        validate_inputs(m, cfg, manifest)
    else:
        m.need(not directory.exists() or not any(directory.iterdir()), 'incomplete migration journal; inspect before continuing')
        state = inventory(m, cfg)
        manifest = dict(version=1, phase='preparing', config=str(m.CONFIG),
                        original_config=m.CONFIG.read_text(), fingerprint=fingerprint,
                        key=str(key), key_sha256=digest(key), inventory=state)
        directory.mkdir(mode=0o700, exist_ok=True)
        m.atomic_write(manifest_path, encoded(manifest), 0o600)
    # The journal blocks package changes and other generation operations. Only
    # sidecar signatures are added; the active unsigned menu is left untouched.
    for filename in manifest['inventory']['files']:
        if filename.endswith('descriptor.json'):
            continue
        path = Path(filename)
        signature = Path(str(path) + '.sig')
        if signature.exists():
            m.verify_signature(path, args, target)
        else:
            m.sign(path, args, target)
            m.verify_signature(path, args, target)
    menu = m.make_menu(target, manifest['inventory']['current'], manifest['inventory']['rows'],
                       base_cmdline=manifest['inventory']['cmdline'].split())
    candidate = directory / 'grub.cfg'
    m.atomic_write(candidate, menu)
    m.run(['grub-script-check', str(candidate)])
    m.sign(candidate, args, target)
    m.verify_signature(candidate, args, target)
    # A signed candidate is available for an explicit GRUB trust/enforcement
    # test before changing the normal wrapper or flashing any firmware.
    boot_candidate = cfg['boot_dir'] / 'grub/signing-candidate'
    m.protected_path(boot_candidate)
    m.atomic_write(boot_candidate / 'boot.key', key.read_bytes())
    m.atomic_write(boot_candidate / 'grub.cfg', candidate.read_bytes())
    m.atomic_write(boot_candidate / 'grub.cfg.sig', Path(str(candidate) + '.sig').read_bytes())
    m.atomic_write(directory / 'signed-config.toml', configuration(target), 0o600)
    manifest['menu_sha256'] = digest(candidate)
    manifest['phase'] = 'prepared'
    m.atomic_write(manifest_path, encoded(manifest), 0o600)
    print('Signatures prepared and verified. Unsigned boot remains active. Public key: ' + str(key))
    print('Configure and test firmware trust before activate-signed. No firmware was changed.')


def activate(m, cfg, args, directory):
    manifest_path = directory / 'manifest.json'
    m.need(manifest_path.is_file(), 'no prepared signing migration')
    manifest = json.loads(manifest_path.read_text())
    m.need(manifest['phase'] in ('prepared', 'activating', 'activated'), 'finish signature preparation first')
    validate_inputs(m, cfg, manifest)
    target = target_config(cfg, manifest['fingerprint'], Path(manifest['key']))
    m.verify_public_key(target)
    candidate = directory / 'grub.cfg'
    m.need(digest(candidate) == manifest['menu_sha256'], 'prepared signed menu changed')
    m.need(m.CONFIG.read_bytes() in (manifest['original_config'].encode(), configuration(target)),
           'configuration changed since preparation')
    for filename in manifest['inventory']['files']:
        if not filename.endswith('descriptor.json'):
            m.verify_signature(Path(filename), args, target)
    m.verify_signature(candidate, args, target)
    boot_candidate = cfg['boot_dir'] / 'grub/signing-candidate/grub.cfg'
    m.need(boot_candidate.read_bytes() == candidate.read_bytes(), 'bootable signing candidate changed')
    m.verify_signature(boot_candidate, args, target)
    if manifest['phase'] == 'prepared':
        m.check_policy(cfg)
        m.confirm_tty('Firmware must already trust this key, or a trusted recovery route must be ready. '
                      'This switches the installed boot path to signed menus.',
                      'ACTIVATE SIGNED ' + manifest['fingerprint'])
        manifest['phase'] = 'activating'
        m.atomic_write(manifest_path, encoded(manifest), 0o600)
    # All inputs are immutable and verified above. After a power cut these
    # writes can be repeated through activate-signed, even across a policy/config
    # mismatch; ordinary mutators remain blocked by the durable journal.
    menu_id = m.publish(target, candidate.read_bytes(), args, initialize=True)
    m.atomic_write(cfg['boot_dir'] / 'grub/generation-policy.json', m.policy_binding(target))
    m.atomic_write(m.CONFIG, configuration(target), 0o600)
    m.record_generation(target, manifest['inventory']['current'], menu_id)
    archive = cfg['state_dir'] / 'signing-migrations'
    archive.mkdir(mode=0o700, exist_ok=True)
    manifest['phase'] = 'activated'
    m.atomic_write(manifest_path, encoded(manifest), 0o600)
    destination = archive / manifest['fingerprint']
    m.need(not destination.exists(), 'migration archive already exists; inspect journal')
    os.rename(directory, destination)
    m.fsync_dir(archive)
    m.fsync_dir(cfg['state_dir'])
    print('Signed policy activated. Reboot and verify-running; firmware enforcement needs its own acceptance test.')


def command(m, cfg, args):
    directory = cfg['state_dir'] / 'signing-migration'
    m.protected_path(directory)
    if args.action == 'prepare-signed':
        prepare(m, cfg, args, directory)
    elif args.action == 'activate-signed':
        activate(m, cfg, args, directory)
    else:
        m.need(not m.signed(cfg), 'signed policy cannot be downgraded by abort-signing')
        m.check_policy(cfg)
        manifest = json.loads((directory / 'manifest.json').read_text())
        m.need(manifest['phase'] in ('preparing', 'prepared'), 'activation has started; resume it instead')
        m.need(m.CONFIG.read_text() == manifest['original_config'], 'configuration changed')
        m.confirm_tty('Cancel signing preparation; unsigned boot stays active.', 'ABORT SIGNING')
        # Signatures and public key can remain as inert audit material. Remove
        # only the private staging directory, never generation artifacts.
        shutil.rmtree(directory)
        m.fsync_dir(cfg['state_dir'])
        print('Signing preparation cancelled; unsigned policy retained.')
