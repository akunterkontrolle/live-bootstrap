#!/usr/bin/env python3
"""Order the packaged XDG runtime provider before OpenRC's PAM user session."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import os
from pathlib import Path
import re
import tempfile

RULE = 'session optional pam_xdg.so runtime\n'


def configure(root, provider="xdg", check=False):
    if provider == "elogind":
        return configure_elogind(root, check=check)
    if check:
        raise ValueError("check mode is currently for the desktop elogind provider")
    path = root / 'etc/pam.d/system-login'
    if path.is_symlink() or not path.is_file():
        raise ValueError('expected a regular system-login PAM policy')
    modules = ('lib64/security/pam_xdg.so', 'usr/lib64/security/pam_xdg.so',
               'lib/security/pam_xdg.so', 'usr/lib/security/pam_xdg.so')
    if not any((root / name).is_file() for name in modules):
        raise ValueError('install the pinned sys-auth/pam_xdg package before configuring PAM')
    data = path.read_text()
    lines = data.splitlines(keepends=True)
    hooks = [i for i, line in enumerate(lines)
             if re.match(r'^\s*-?session\s+\S+\s+pam_openrc\.so(?:\s|$)', line)]
    if len(hooks) != 1:
        raise ValueError('expected exactly one OpenRC session hook; inspect PAM policy')
    if any(re.match(r'^\s*-?session\s+\S+\s+pam_(elogind|systemd)\.so(?:\s|$)', line)
           for line in lines):
        raise ValueError('another runtime/session provider is configured; inspect before combining')
    providers = [i for i, line in enumerate(lines)
                 if not line.lstrip().startswith('#') and 'pam_xdg.so' in line]
    if providers:
        if providers != [hooks[0] - 1] or lines[providers[0]] != RULE:
            raise ValueError('existing pam_xdg configuration differs; preserve it for review')
        return False
    lines.insert(hooks[0], RULE)
    info = path.stat()
    backup = path.with_name('system-login.before-gentoo-xdg')
    if backup.is_symlink() or (backup.exists() and backup.read_text() != data):
        raise ValueError('PAM backup differs; refusing to overwrite evidence')
    if not backup.exists():
        with backup.open('x') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        backup.chmod(info.st_mode & 0o777)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.system-login-', delete=False) as out:
        pending = Path(out.name)
        out.write(''.join(lines))
        out.flush()
        os.fsync(out.fileno())
    pending.chmod(info.st_mode & 0o777)
    if os.geteuid() == 0:
        os.chown(pending, info.st_uid, info.st_gid)
    os.replace(pending, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True



def configure_elogind(root, check=False):
    # This is the reviewed transition for the pinned OpenRC/pambase stack.
    # Authentication/account/password rules are retained byte for byte.
    path = root / 'etc/pam.d/system-login'
    if path.is_symlink() or not path.is_file():
        raise ValueError('expected a regular system-login PAM policy')
    module_paths = ('lib64/security', 'usr/lib64/security', 'lib/security', 'usr/lib/security')
    if not any((root / name / 'pam_elogind.so').is_file() for name in module_paths):
        raise ValueError('install sys-auth/elogind[pam] before changing session providers')
    packages = list((root / 'var/db/pkg/sys-auth').glob('pambase-*/USE'))
    if len(packages) != 1 or 'elogind' not in packages[0].read_text().split():
        raise ValueError('installed pambase must have USE=elogind before the transition')
    data = path.read_text()
    lines = data.splitlines(keepends=True)
    hooks = [i for i, line in enumerate(lines) if re.match(r'^\s*-?session\s+\S+\s+pam_openrc\.so(?:\s|$)', line)]
    if len(hooks) != 1:
        raise ValueError('expected exactly one OpenRC session hook')
    providers = [(i, line) for i, line in enumerate(lines) if not line.lstrip().startswith('#') and
                 re.search(r'pam_(xdg|elogind|systemd)\.so', line)]
    target_rule = '-session\toptional\tpam_elogind.so\n'
    if len(providers) != 1 or providers[0][0] != hooks[0] - 1:
        raise ValueError('expected exactly one managed session provider before OpenRC')
    position, rule = providers[0]
    if rule != RULE and rule.split() != target_rule.split():
        raise ValueError('custom session provider requires review; policy preserved')
    target = ''.join(lines[:position] + [target_rule] + lines[position + 1:])
    pending = sorted(path.parent.glob('._cfg????_*'))
    candidates = []
    normalize = lambda text: [' '.join(line.split()) for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    for candidate in pending:
        if candidate.is_symlink() or not candidate.is_file() or not re.fullmatch(r'\._cfg[0-9]{4}_system-login', candidate.name):
            raise ValueError('pending PAM configuration requires manual review: ' + str(candidate))
        if normalize(candidate.read_text()) != normalize(target):
            raise ValueError('packaged PAM update changes more than the reviewed session hook: ' + str(candidate))
        candidates.append(candidate)
    if len(candidates) > 1:
        raise ValueError('multiple packaged PAM updates require review')
    if check:
        if data != target or candidates:
            raise ValueError('elogind PAM transition is incomplete; inspect policy and pending updates')
        return False
    backup = path.with_name('system-login.before-ms1504-elogind')
    if backup.is_symlink():
        raise ValueError('unsafe elogind PAM backup')
    if data != target:
        if backup.exists() and backup.read_text() != data:
            raise ValueError('elogind PAM backup differs; inspect before changing login policy')
        if not backup.exists():
            with backup.open('x') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            backup.chmod(path.stat().st_mode & 0o777)
    if candidates:
        evidence = path.with_name('system-login.packaged-ms1504-elogind')
        if evidence.is_symlink() or (evidence.exists() and evidence.read_bytes() != candidates[0].read_bytes()):
            raise ValueError('packaged PAM evidence differs; preserve it for review')
        if not evidence.exists():
            with evidence.open('xb') as stream:
                stream.write(candidates[0].read_bytes()); stream.flush(); os.fsync(stream.fileno())
            evidence.chmod(path.stat().st_mode & 0o777)
    if data != target:
        info = path.stat()
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.system-login-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(target); stream.flush(); os.fsync(stream.fileno())
        temporary.chmod(info.st_mode & 0o777)
        if os.geteuid() == 0:
            os.chown(temporary, info.st_uid, info.st_gid)
        os.replace(temporary, path)
    for candidate in candidates:
        candidate.unlink()
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return data != target or bool(candidates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--provider', choices=('xdg', 'elogind'), default='xdg')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    changed = configure(args.root.resolve(strict=True), args.provider, check=args.check)
    print('OpenRC PAM runtime provider ' + args.provider + ' ' + ('configured; log out and log in again.' if changed else 'already configured.'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('gentoo-user-runtime: ' + str(error))
