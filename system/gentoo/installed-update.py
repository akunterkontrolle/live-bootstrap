#!/usr/bin/env python3
"""Export, verify and activate versioned installed continuation sources, preserving local inputs."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

SOURCE = Path(__file__).resolve().parent
PREFIX = Path('/usr/local/share/ms1504-bootstrap')
SBIN = Path('/usr/local/sbin')
STATE = Path('/var/lib/ms1504-gentoo/continuation-updates')
EXTRA_LOCK = Path('/var/lib/ms1504-extra/lock')
DESKTOP_LOCK = Path('/run/lock/ms1504-complete-desktop.lock')
GENERATION_LOCK = Path('/run/ms1504-generations/lock')
DESKTOP_STATE = Path('/var/lib/ms1504-gentoo/desktop')
REQUIRED = ('lib/installer-erase.sh', 'installed-grub.py', 'installed-update.py', 'setup-extra.sh', 'workspace-retention.py',
            'generation.py', 'signed-generation.py', 'generation.example.toml', 'GENERATIONS.md', 'probes/generation-lvm.py',
            'complete-desktop.sh', 'desktop-packages.list', 'desktop-package.use',
            'desktop-package.accept_keywords', 'desktop-package.mask', 'desktop-rust-bootstrap.py',
            'desktop-lightdm.conf', 'source-only-emerge.py', 'cpu-policy.py',
            'lib/repository-policy.sh', 'lib/devices.sh', 'lib/hardware-discovery.sh',
            'sources/gentoo-repository.lock')
DOCS = ('README.md', 'OPERATIONS.md', 'HARDWARE-WORKSPACE.md', 'CONTINUATION-PLAN.md',
        'WORKSPACE-RETENTION.md', 'UPGRADE-DESIGN.md', 'CLEANUP.md')
ENTRYPOINTS = {'ms1504-extra': 'setup-extra.sh', 'complete-desktop': 'complete-desktop.sh',
               'ms1504-generation': 'generation.py', 'ms1504-signed-generation': 'signed-generation.py',
               'ms1504-grub-config': 'installed-grub.py', 'ms1504-continuation-update': 'installed-update.py'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def atomic(path, data, mode=0o600):
    require(not path.is_symlink(), 'unsafe record path: ' + str(path))
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def export(path):
    require(not path.exists(), 'export already exists; choose a new filename')
    repo = SOURCE.parents[1]
    names = subprocess.check_output(['git', 'ls-files', '-z', '--', 'system/gentoo'], cwd=repo).split(b'\0')
    files = {name.decode().removeprefix('system/gentoo/') for name in names if name}
    files.update(REQUIRED)
    files.update(name for name in DOCS if (SOURCE / name).is_file())
    require('config.sh' not in files and 'config.t440p.toml' not in files, 'operator configuration in export list')
    data = {}
    for name in sorted(files):
        source = SOURCE / name
        require(source.is_file() and not source.is_symlink(), 'unsafe/missing source: ' + name)
        data[name] = source.read_bytes()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    manifest = {'format': 1, 'source_commit': revision,
                'files': {name: digest(value) for name, value in data.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        with tarfile.open(fileobj=stream, mode='w:gz') as packed:
            for name, value in {'release.json': encode(manifest), **{'system/gentoo/' + n: b for n, b in data.items()}}.items():
                info = tarfile.TarInfo(name)
                info.size = len(value)
                info.mode = 0o644
                packed.addfile(info, io.BytesIO(value))
        stream.flush()
        os.fsync(stream.fileno())
    checksum = path.with_name(path.name + '.sha256')
    checksum.write_text(digest(path.read_bytes()) + '  ' + path.name + '\n')
    print('Exported source commit:', revision)
    print('Source manifest SHA256:', digest(encode(manifest)))
    print('Archive:', path)
    print('Checksum:', checksum)


def read_archive(path):
    with tarfile.open(path, 'r:gz') as packed:
        members = packed.getmembers()
        require(len(members) <= 1000 and sum(m.size for m in members) <= 32 * 1024 * 1024, 'archive exceeds source bundle limits')
        require(all(m.isfile() and not m.name.startswith('/') and '..' not in Path(m.name).parts for m in members), 'archive contains links/special/unsafe paths')
        require(len({m.name for m in members}) == len(members), 'duplicate archive member')
        data = {m.name: packed.extractfile(m).read() for m in members}
    manifest = json.loads(data.pop('release.json'))
    require(manifest.get('format') == 1 and re.fullmatch('[0-9a-f]{40}', manifest.get('source_commit', '')), 'invalid release format/commit')
    records = manifest['files']
    require(set(data) == {'system/gentoo/' + name for name in records}, 'archive inventory differs from manifest')
    for name, checksum in records.items():
        require(re.fullmatch(r'[A-Za-z0-9_.+/-]+', name) and '..' not in Path(name).parts and not name.startswith('/'), 'unsafe source filename')
        require(digest(data['system/gentoo/' + name]) == checksum, 'source digest differs: ' + name)
    require(all(name in records for name in REQUIRED), 'required installed sources missing')
    require('config.sh' not in records and 'config.t440p.toml' not in records, 'archive must not supply operator configuration')
    return manifest, {name: data['system/gentoo/' + name] for name in records}


def current():
    entry = SBIN / 'ms1504-extra'
    require(entry.is_symlink(), 'installed ms1504-extra entry is not a symlink; inspect custom installation')
    directory = entry.resolve(strict=True).parent
    require(directory.is_relative_to(PREFIX.resolve()), 'installed continuation is outside the managed prefix')
    require(directory.stat().st_uid == 0 and directory.stat().st_mode & 0o022 == 0, 'active continuation directory is not protected')
    return directory


def local_inputs(directory):
    config = directory / 'config.sh'
    require(config.is_file() and not config.is_symlink(), 'installed config.sh missing or unsafe')
    data = {'config.sh': config.read_bytes()}
    # Parse only literal generated assignments; never execute operator config.
    for line in data['config.sh'].decode().splitlines():
        if not line.startswith('export GENTOO_PACKAGES_'):
            continue
        words = shlex.split(line)
        require(len(words) == 2 and '=' in words[1], 'unexpected package-policy assignment')
        value = words[1].split('=', 1)[1]
        if not value:
            continue
        require(re.fullmatch(r'policy/[A-Za-z0-9_.+-]+', value), 'unsafe custom package policy path')
        path = directory / value
        require(path.is_file() and not path.is_symlink(), 'custom package policy missing: ' + value)
        data[value] = path.read_bytes()
    return data


def verify_release(directory):
    record_path = directory / 'deployment.json'
    require(record_path.is_file() and not record_path.is_symlink(), 'deployment record missing')
    record = json.loads(record_path.read_text())
    require(directory.name == record['release'], 'release directory name differs')
    for name, checksum in record['files'].items():
        require(not name.startswith('/') and '..' not in Path(name).parts, 'unsafe deployment path')
        path = directory / name
        require(path.is_file() and not path.is_symlink() and digest(path.read_bytes()) == checksum, 'deployed file changed: ' + name)
        require(path.stat().st_mode & 0o777 == record['modes'][name], 'deployed permissions changed: ' + name)
    return record


def activate(archive, apply=False):
    require(os.geteuid() == 0, 'run as installed-system root (sudo)')
    manifest, data = read_archive(archive)
    active = current()
    require((active / 'sources/gentoo-repository.lock').read_bytes() == data['sources/gentoo-repository.lock'],
            'repository snapshot changed; this helper is not a repository/package upgrade tool')
    preserved = local_inputs(active)
    local_modes = {name: (active / name).stat().st_mode & 0o777 for name in preserved}
    require(all(mode & 0o022 == 0 for mode in local_modes.values()), 'operator inputs must not be group/other writable')
    require(not set(preserved).intersection(set(data)), 'archive overlaps installed operator policy')
    identity = {'source_manifest': digest(encode(manifest)),
                'local_inputs': {name: digest(value) for name, value in preserved.items()},
                'local_modes': local_modes}
    release = digest(encode(identity))
    destination = PREFIX / 'releases' / release
    print('Source commit:', manifest['source_commit'])
    print('Source manifest SHA256:', identity['source_manifest'])
    print('Release:', destination)
    print('Preserve: installed config.sh, custom policy, /etc/portage, CPU/source identities and runtime journals.')
    if not apply:
        print('Plan only; use --apply to verify and activate this release.')
        return
    require(not STATE.is_symlink(), 'unsafe update state directory')
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    with contextlib.ExitStack() as stack:
        lock_paths = [STATE / 'lock', EXTRA_LOCK, DESKTOP_LOCK, GENERATION_LOCK]
        for path in lock_paths:
            require(not path.is_symlink(), 'unsafe operation lock: ' + str(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = stack.enter_context(path.open('a'))
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('continuation operation is running; stop it before deployment')
        require(current() == active and local_inputs(active) == preserved, 'installed inputs changed during update planning')
        desktop = DESKTOP_STATE
        require(not desktop.exists() or not any(desktop.iterdir()), 'desktop has started; changing its driver/policy requires a separate receipt-aware review')
        directory = destination.parent
        require(not directory.is_symlink(), 'unsafe releases directory')
        directory.mkdir(parents=True, exist_ok=True)
        combined = {**data, **preserved}
        for name, value in combined.items():
            if name.endswith('.py'):
                compile(value, name, 'exec')
            elif name.endswith('.sh'):
                subprocess.run(['bash', '-n'], input=value, check=True)
        files = {name: digest(value) for name, value in combined.items()}
        modes = {name: local_modes.get(name, 0o755 if Path(name).suffix in ('.sh', '.py') else 0o644) for name in combined}
        record = {'release': release, 'source_commit': manifest['source_commit'], **identity, 'files': files, 'modes': modes}
        if destination.exists():
            require(verify_release(destination) == record, 'existing release differs')
        else:
            temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=directory))
            try:
                for name, value in combined.items():
                    path = temporary / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(value)
                    path.chmod(modes[name])
                    with path.open('rb') as stream:
                        os.fsync(stream.fileno())
                for parent, directories, names in os.walk(temporary):
                    Path(parent).chmod(0o755)
                atomic(temporary / 'deployment.json', encode(record))
                os.sync()
                temporary.rename(destination)
                sync_dir(directory)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        verify_release(destination)
        pending_path = STATE / 'pending.json'
        if pending_path.exists():
            pending = json.loads(pending_path.read_text())
            require(pending['release'] == release, 'unfinished activation belongs to another source bundle; resume that bundle first')
        else:
            previous = {}
            for entry in ENTRYPOINTS:
                link = SBIN / entry
                require(not link.exists() or link.is_symlink(), 'custom regular entrypoint exists: ' + str(link))
                previous[entry] = os.readlink(link) if link.is_symlink() else None
            pending = {'release': release, 'previous': previous}
            atomic(pending_path, encode(pending))
        SBIN.mkdir(parents=True, exist_ok=True)
        for entry, source in ENTRYPOINTS.items():
            link = SBIN / entry
            temporary = SBIN / ('.' + entry + '.update')
            if temporary.is_symlink():
                require(os.readlink(temporary) == str(destination / source), 'different temporary activation link')
            else:
                require(not temporary.exists(), 'unexpected temporary activation file')
                os.symlink(destination / source, temporary)
            os.replace(temporary, link)
            sync_dir(SBIN)
        history = STATE / (release + '.json')
        if not history.exists():
            atomic(history, encode({**pending, **record}))
        atomic(STATE / 'active.json', encode(record))
        pending_path.unlink()
        sync_dir(STATE)
    print('Activated verified extra/desktop/GRUB/update entrypoints. Prior releases and legacy installed directory retained.')
    print('No packages, boot menus, disk formats, signing policy or completed build receipts changed.')


def verify_active():
    require(os.geteuid() == 0, 'run with sudo to verify protected deployment records')
    directory = current()
    record = verify_release(directory)
    for entry, name in ENTRYPOINTS.items():
        require((SBIN / entry).is_symlink() and (SBIN / entry).resolve() == directory / name, 'entrypoint differs: ' + entry)
    print('Verified active source commit:', record['source_commit'])
    print('Verified source manifest SHA256:', record['source_manifest'])
    print('Active release:', directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--export', type=Path)
    mode.add_argument('--archive', type=Path)
    mode.add_argument('--verify', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(not args.apply or args.archive, '--apply requires --archive')
    if args.export:
        export(args.export)
    elif args.verify:
        verify_active()
    else:
        activate(args.archive, args.apply)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, tarfile.TarError) as error:
        raise SystemExit('installed-update: ' + str(error))
