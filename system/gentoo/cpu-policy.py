#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Record guest-native CPU inputs and refuse incompatible continuation CPUs."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

CFLAGS = '-O2 -pipe -march=native -mtune=native -fstack-protector-strong -D_FORTIFY_SOURCE=2'


def detect(detector, compiler, cpuinfo=Path('/proc/cpuinfo')):
    # GCC localizes the [enabled] markers. Installed locale selection must
    # not change the semantic CPU comparison after reboot.
    environment = os.environ | {'LC_ALL': 'C'}
    output = subprocess.check_output([str(detector)], text=True, env=environment).strip()
    if not re.fullmatch(r'\*/\* CPU_FLAGS_X86:(?: [a-z0-9_]+)+', output):
        raise ValueError('unexpected cpuid2cpuflags x86 output')
    flags = sorted(set(output.split(':', 1)[1].split()))
    target = subprocess.check_output([str(compiler), '-march=native', '-mtune=native',
                                      '-Q', '--help=target'], text=True, env=environment)
    resolved = {}
    for option in ('march', 'mtune'):
        match = re.findall(r'^\s*-' + option + r'=\s+(\S+)\s*$', target, re.M)
        if len(match) != 1 or match[0] == 'native':
            raise ValueError('compiler did not resolve native ' + option)
        resolved[option] = match[0]
    processors = [set(line.split(':', 1)[1].split()) for line in cpuinfo.read_text().splitlines()
                  if re.match(r'^flags\s*:', line)]
    if not processors:
        raise ValueError('kernel CPU feature list is unavailable')
    kernel_flags = set.intersection(*processors)
    return {'version': 1, 'cpu_flags_x86': flags, 'kernel_flags': sorted(kernel_flags),
            'compiler_target': resolved, 'compiler_target_output': target,
            'compiler_features': sorted(re.findall(r'^\s*(-m[\w-]+)\s+\[enabled\]', target, re.M)),
            'cflags': CFLAGS}


def verify(record, observed):
    if record.get('version') != 1 or record.get('cflags') != CFLAGS:
        raise ValueError('unrecognized native CPU policy receipt')
    for name in ('cpu_flags_x86', 'kernel_flags'):
        # Added features would also change future -march=native outputs while
        # leaving older receipts intact. Keep one CPU feature set per lineage.
        if not record[name] or set(record[name]) != set(observed[name]):
            raise ValueError('CPU capabilities changed: ' + name)
    if record['compiler_target'] != observed['compiler_target']:
        raise ValueError('native compiler CPU target changed; use the recorded CPU model')
    if record['compiler_features'] != observed['compiler_features']:
        raise ValueError('native compiler target features changed')


def publish(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--detector', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--package-use', type=Path)
    args = parser.parse_args()
    observed = detect(args.detector, args.compiler)
    if args.receipt.exists():
        record = json.loads(args.receipt.read_text())
        verify(record, observed)
    else:
        if not args.initialize:
            raise ValueError('native CPU receipt is missing; a fresh build is required')
        record = observed
        publish(args.receipt, json.dumps(record, sort_keys=True, indent=2) + '\n')
    if args.package_use is not None:
        data = '*/* CPU_FLAGS_X86: ' + ' '.join(record['cpu_flags_x86']) + '\n'
        if args.package_use.exists() and args.package_use.read_text() != data:
            raise ValueError('managed CPU package.use differs from recorded policy')
        publish(args.package_use, data)
    print('Native CPU policy verified:', record['compiler_target'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit('gentoo: ' + str(error))
