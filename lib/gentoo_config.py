"""Host-only Gentoo schema, validation and literal shell rendering."""
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import math
import re
import shlex
import tomllib

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / 'system/gentoo/config.example.toml'


def load(path, mode):
    """Merge partial TOML with reviewed defaults; mode belongs to the caller."""
    if mode not in ('vm', 'hardware'):
        raise ValueError('mode must be vm or hardware')
    path = Path(path).resolve()
    with DEFAULT_CONFIG.open('rb') as stream:
        settings = tomllib.load(stream)
    with path.open('rb') as stream:
        supplied = tomllib.load(stream)
    for section, values in supplied.items():
        if section not in settings or not isinstance(values, dict):
            raise ValueError(f'Unknown section: {section}')
        for key, value in values.items():
            if key not in settings[section]:
                raise ValueError(f'Unknown setting: {section}.{key}')
            default = settings[section][key]
            numeric_load = section == 'portage' and key == 'load_average'
            valid_type = (type(value) in (int, float) if numeric_load
                          else type(value) is type(default))
            if not valid_type:
                raise ValueError(f'Wrong type: {section}.{key}')
            settings[section][key] = value
    for section, values in settings.items():
        for key, value in values.items():
            name = f'{section}.{key}'
            if isinstance(value, str) and any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError(f'Control character: {name}')
            if type(value) in (int, float) and (value <= 0 or not math.isfinite(value)):
                raise ValueError(f'{name} must be finite and positive')
    system = settings['system']
    for key in ('username', 'hostname', 'volume_group'):
        pattern = r'[a-z_][a-z0-9_-]{0,30}' if key == 'username' else r'[A-Za-z_][A-Za-z0-9_-]{0,30}'
        if not re.fullmatch(pattern, system[key]):
            raise ValueError(f'Invalid system.{key}')
    if system['username'] == 'root':
        raise ValueError('system.username must be a separate login account')
    for key in ('fqdn', 'locale', 'keymap', 'timezone'):
        if (not re.fullmatch(r'[A-Za-z0-9_./@+-]+', system[key])
                or '..' in system[key] or system[key].startswith('/')):
            raise ValueError(f'Invalid system.{key}')
    workspace = settings['workspace']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,16}', workspace['label']):
        raise ValueError('workspace.label must be a short filesystem label')
    if workspace['uuid'] and not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', workspace['uuid']):
        raise ValueError('workspace.uuid must be an ext4 UUID or empty')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', workspace['network_interface']):
        raise ValueError('Invalid workspace.network_interface')
    disks = settings['hardware_disks']
    for role in ('root', 'boot', 'extra'):
        if disks[role + '_min_bytes'] > disks[role + '_max_bytes']:
            raise ValueError(f'Invalid {role} capacity range')
        by_id, serial = disks[role + '_by_id'], disks[role + '_serial']
        if by_id and not re.fullmatch(r'/dev/disk/by-id/[A-Za-z0-9_.:+-]+', by_id):
            raise ValueError(f'Invalid hardware_disks.{role}_by_id')
        if re.search(r'-part[0-9]+$', by_id):
            raise ValueError(f'{role}_by_id must name a whole disk')
        if mode == 'hardware' and (not by_id or not serial.strip()):
            raise ValueError(f'hardware requires {role}_by_id and {role}_serial')
        if mode == 'hardware' and role in ('root', 'extra'):
            if not disks[role + '_model'].strip() or not re.fullmatch(r'(?:0[xX])?[0-9a-fA-F]{16,32}', disks[role + '_wwn']):
                raise ValueError(f'hardware requires {role}_model and a hexadecimal {role}_wwn')
    if mode == 'hardware':
        for suffix in ('by_id', 'serial'):
            if len({disks[role + '_' + suffix] for role in ('root', 'boot', 'extra')}) != 3:
                raise ValueError(f'Hardware disk {suffix} identities must differ')
    for key, value in settings['packages'].items():
        if value:
            chosen = (path.parent / value).resolve()
            if not chosen.is_file():
                raise ValueError(f'Missing packages.{key}: {chosen}')
            settings['packages'][key] = str(chosen)
    return settings


def root_image_gib(settings):
    """Reserve two GiB for partition/encryption metadata, beyond the LV plan."""
    return max(settings['images']['root_gib'], sum(settings['layout'].values()) + 2)


def render(settings, mode, *, semantic=False):
    """Render data with quoting; no guest TOML parser is needed."""
    lines = ['# Generated literal assignments. Do not put secrets here.',
             f'export GENTOO_MODE={shlex.quote(mode)}']
    for section, values in settings.items():
        if semantic and section in ('build', 'portage'):
            # Concurrency is operational tuning, not a different source lineage.
            continue
        for key, value in values.items():
            if section == 'packages':
                value = f'policy/{key}' if value else ''
            if isinstance(value, bool):
                value = 'yes' if value else 'no'
            lines.append(f'export GENTOO_{section.upper()}_{key.upper()}={shlex.quote(str(value))}')
    return '\n'.join(lines) + '\n'
