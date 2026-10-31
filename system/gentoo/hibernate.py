#!/usr/bin/env python3
"""Install and manage generation-bound elogind hibernation markers; never sleep."""
import argparse
import contextlib
import fcntl
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

SOURCE = Path(__file__).resolve().parent
spec = spec_from_file_location('hibernate_generations', SOURCE / 'signed-generation.py')
m = module_from_spec(spec)
spec.loader.exec_module(m)
FILES = ('hibernate.py', 'signed-generation.py', 'generation.py', 'source-only-emerge.py', 'hibernate-hook.sh',
         'generation_transition.py', 'generation_recovery.py',
         'dracut-ms1504-rollback/module-setup.sh', 'dracut-ms1504-rollback/recover.sh',
         'hibernate-sleep.conf', 'dracut-ms1504-resume-guard/module-setup.sh',
         'dracut-ms1504-resume-guard/check-generation.sh', 'dracut-ms1504-resume-guard/resume-lib.sh')
DRACUT = b'add_dracutmodules+=" crypt lvm ms1504-resume-guard "\nomit_dracutmodules+=" resume "\n'
READY = b'generation-aware-v1\n'
FIELDS = {'format', 'generation', 'kernel_release', 'root_uuid', 'home_uuid', 'swap_uuid', 'boot_id'}


class Hibernation:
    def __init__(self, cfg, root=Path('/')):
        self.cfg, self.root = cfg, root
        self.directory = self.path('/home/.ms1504-hibernate')
        self.marker = self.directory / 'hibernation-pending'

    def path(self, name):
        return self.root / name.lstrip('/')

    def device(self, lv):
        return f"/dev/{self.cfg['vg']}/{self.cfg['system'][lv + '_lv']}"

    def probe(self, lv, field):
        return m.run(['blkid', '-p', '-s', field, '-o', 'value', self.device(lv)]).decode().strip()

    def ordinary_swap(self):
        m.need(self.probe('swap', 'TYPE') == 'swap', 'swap contains an image or unknown signature; preserve it for recovery')

    def validate_policy(self):
        expected = {'AllowSuspendInterrupts': 'yes', 'AllowHibernation': 'yes',
                    'AllowHybridSleep': 'no', 'AllowSuspendThenHibernate': 'no'}
        files = [self.path('/etc/elogind/sleep.conf')]
        for directory in ('/usr/lib/elogind', '/usr/local/lib/elogind', '/run/elogind', '/etc/elogind'):
            files += list(self.path(directory + '/sleep.conf.d').glob('*.conf'))
        for file in files:
            if not file.exists():
                continue
            m.protected_path(file)
            for line in file.read_text().splitlines():
                if not line.strip() or line.lstrip().startswith(('#', ';')) or '=' not in line:
                    continue
                key, value = (part.strip() for part in line.split('=', 1))
                m.need(key not in expected or value == expected[key], 'conflicting elogind sleep policy: ' + str(file))
                m.need(key != 'HibernateByUsing' or not value, 'custom hibernate backend requires review')

    def context(self, installing=False):
        m.need(not (self.cfg['state_dir'] / 'signing-migration').exists(),
               'finish or abort signing preparation before hibernation')
        m.need(not (self.cfg['state_dir'] / 'recovery-preparation.json').exists(),
               'finish internal recovery publication before hibernation')
        m.root_is_live(self.cfg)
        m.read_root_identity(self.cfg)
        with contextlib.redirect_stdout(sys.stderr):
            m.command_verify_running(self.cfg)
        m.need(installing or not (self.cfg['state_dir'] / 'pending').exists(),
               'finish or recover the pending root update before hibernation')
        self.ordinary_swap()
        devices = {}
        for lv in ('root', 'home', 'swap'):
            devices[lv] = os.stat(self.device(lv)).st_rdev
            m.need(stat.S_ISBLK(os.stat(self.device(lv)).st_mode), 'layout device is not a block device')
        home = m.run(['findmnt', '-rn', '-M', '/home', '-o', 'SOURCE']).decode().strip()
        m.need(os.stat(home).st_rdev == devices['home'], 'persistent /home differs from the recorded layout')
        m.need(m.run(['findmnt', '-rn', '-M', '/home', '-o', 'FSTYPE']).decode().strip() == 'ext4',
               'persistent /home must use the reviewed ext4 marker layout')
        active = [line.split()[0] for line in self.path('/proc/swaps').read_text().splitlines()[1:] if line.split()]
        m.need(any(os.stat(name).st_rdev == devices['swap'] for name in active), 'recorded encrypted swap is not active')
        generation = self.path('/etc/ms1504-generation').read_text().strip()
        boot_id = self.path('/proc/sys/kernel/random/boot_id').read_text().strip()
        m.need(str(uuid.UUID(boot_id)) == boot_id, 'invalid boot identity')
        if installing and not self.path('/etc/ms1504-hibernate-ready').exists():
            m.need(self.path('/sys/power/resume').read_text().strip() == '0:0',
                   'first installation requires hibernation to remain unavailable until the reviewed reboot')
        if not installing:
            self.validate_policy()
            expected = f"{os.major(devices['swap'])}:{os.minor(devices['swap'])}"
            m.need(self.path('/sys/power/resume').read_text().strip() == expected,
                   'runtime resume device is not configured; boot the reviewed initramfs first')
            m.need(self.path('/sys/power/resume_offset').read_text().strip() == '0', 'swap LV must have zero resume offset')
            m.need(self.path('/etc/ms1504-hibernate-ready').read_bytes() == READY, 'hibernation installation is incomplete')
            m.need(self.path('/etc/elogind/sleep.conf.d/90-ms1504-hibernate.conf').read_bytes() == (SOURCE / 'hibernate-sleep.conf').read_bytes(),
                   'elogind hook-veto configuration changed; review before hibernating')
        fields = dict(format='1', generation=generation, kernel_release=os.uname().release,
                      root_uuid=self.probe('root', 'UUID'), home_uuid=self.probe('home', 'UUID'),
                      swap_uuid=self.probe('swap', 'UUID'), boot_id=boot_id)
        for key in ('root_uuid', 'home_uuid', 'swap_uuid'):
            m.need(str(uuid.UUID(fields[key])) == fields[key], 'invalid filesystem identity: ' + key)
        return fields

    def read_marker(self):
        m.protected_path(self.directory)
        m.protected_path(self.marker)
        m.need(self.marker.is_file(), 'hibernation marker is missing or unsafe')
        fields = m.parse_fields(self.marker)
        m.need(set(fields) == FIELDS and fields['format'] == '1', 'unsupported hibernation marker')
        return fields

    def pre(self):
        fields = self.context()
        m.need(not self.marker.exists() and not self.marker.is_symlink(), 'hibernation marker already exists; inspect it before retrying')
        m.protected_path(self.directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        m.need(self.directory.stat().st_mode & 0o777 == 0o700, 'hibernation state directory must be mode 0700')
        m.atomic_write(self.marker, ''.join(f'{key}={value}\n' for key, value in fields.items()).encode(), 0o600)
        # fsync commits ext4's journal; the initramfs deliberately uses noload.
        # Checkpoint /home's journal so the marker is visible without replay.
        # Never freeze root: LVM and generation metadata live there.
        m.run(['fsfreeze', '--freeze', '/home'])
        try:
            m.need(self.read_marker() == fields, 'hibernation identity changed during journal checkpoint')
        finally:
            m.run(['fsfreeze', '--unfreeze', '/home'])

    def retire(self, reason):
        # Archive before unlinking, so even an interrupted cleanup keeps evidence.
        history = self.directory / 'history'
        m.protected_path(history)
        history.mkdir(mode=0o700, exist_ok=True)
        target = history / (str(uuid.uuid4()) + '.' + reason)
        m.atomic_write(target, self.marker.read_bytes(), 0o600)
        self.marker.unlink()
        m.fsync_dir(self.directory)

    def post(self):
        if not self.marker.exists() and not self.marker.is_symlink():
            return
        fields = self.read_marker()
        current = self.context()
        m.need(fields == current, 'post-sleep identity differs; hibernation marker preserved')
        self.retire('post-returned-with-ordinary-swap')

    def clear_stale(self):
        fields = self.read_marker()
        current = self.context()
        # No same-boot manual override while a sleep request could still be live.
        m.need(fields['boot_id'] != current['boot_id'], 'same-boot marker requires the post hook or a cold boot before recovery')
        self.retire('cold-boot-no-image')

    def install(self):
        m.check_pending(self.cfg)
        self.context(installing=True)
        self.validate_policy()
        m.need(self.cfg['system']['home_lv'] == 'home' and self.cfg['system']['swap_lv'] == 'swap',
               'hibernation requires the reviewed home/swap LV names')
        m.need(not self.marker.exists() and not self.marker.is_symlink(), 'resolve the existing hibernation marker first')
        data = {name: (SOURCE / name).read_bytes() for name in FILES}
        checksum = hashlib.sha256()
        for name, content in sorted(data.items()):
            checksum.update(name.encode() + b'\0' + content + b'\0')
        destination = self.path('/usr/local/share/ms1504-hibernation') / checksum.hexdigest()
        m.protected_path(destination)
        destination.mkdir(parents=True, exist_ok=True, mode=0o755)
        for name, content in data.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            if target.exists():
                m.need(target.read_bytes() == content, 'existing hibernation release changed')
            else:
                m.atomic_write(target, content, 0o755 if name.endswith(('.py', '.sh')) else 0o644)
        for name, source in [('ms1504-hibernate', 'hibernate.py'), ('ms1504-generation', 'generation.py'),
                             ('ms1504-signed-generation', 'signed-generation.py')]:
            target = self.path('/usr/local/sbin') / name
            m.protected_path(target.parent)
            m.need(not target.exists() or target.is_symlink(), 'refusing to replace a custom regular entrypoint')
            temporary = target.with_name('.' + name + '.new')
            m.need(not temporary.exists() and not temporary.is_symlink(), 'unfinished entrypoint installation')
            temporary.symlink_to(destination / source)
            os.replace(temporary, target)
            m.fsync_dir(target.parent)
        installs = {
            '/usr/local/libexec/gentoo-continuation/source-only-emerge.py': (data['source-only-emerge.py'], 0o755),
            '/etc/elogind/system-sleep/00-ms1504-hibernate': (data['hibernate-hook.sh'], 0o755),
            '/etc/elogind/sleep.conf.d/90-ms1504-hibernate.conf': (data['hibernate-sleep.conf'], 0o644),
            '/etc/dracut.conf.d/95-ms1504-hibernate.conf': (DRACUT, 0o644),
        }
        for name in ('module-setup.sh', 'check-generation.sh', 'resume-lib.sh'):
            installs['/usr/lib/dracut/modules.d/05ms1504-resume-guard/' + name] = (data['dracut-ms1504-resume-guard/' + name], 0o644)
        # Ready is written last; old initramfs and running daemon stay untouched.
        installs['/etc/ms1504-hibernate-ready'] = (READY, 0o644)
        for name, (content, mode) in installs.items():
            target = self.path(name)
            m.protected_path(target)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            if target.exists() and target.read_bytes() != content:
                backup = target.with_name(target.name + '.before-ms1504-hibernate')
                m.need(not backup.exists() or backup.read_bytes() == target.read_bytes(), 'configuration backup differs; inspect before installing')
                m.atomic_write(backup, target.read_bytes(), target.stat().st_mode & 0o777)
            m.atomic_write(target, content, mode)
        print('Hibernation files installed. Build, inspect and seal a new initramfs, then reboot; no daemon reload or sleep was requested.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/ms1504-generation.toml'))
    parser.add_argument('action', choices=('install', 'check', 'pre', 'post', 'clear-stale'))
    args = parser.parse_args()
    m.need(os.geteuid() == 0, 'run as root')
    m.CONFIG = args.config
    m.protected_path(args.config)
    cfg = m.read_config()
    m.LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    m.protected_path(m.LOCK)
    fd = os.open(m.LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        tool = Hibernation(cfg)
        if args.action == 'check':
            fields = tool.context()
            m.need(not tool.marker.exists() and not tool.marker.is_symlink(), 'pending hibernation marker needs recovery')
            print(json.dumps(fields, indent=2))
        else:
            getattr(tool, args.action.replace('-', '_'))()
    finally:
        os.close(fd)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print('ms1504-hibernate: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
