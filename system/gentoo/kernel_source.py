#!/usr/bin/env python3
"""Host verification of the pinned kernel source, detached signature and signer."""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

FINGERPRINT = '647F28654894E3BD457199BE38DBBDC86092693E'


def verify_kernel_source(build, snapshot):
    paths, hashes = [], []
    for lock, name in (('linux.lock', 'linux-6.12.111.tar.xz'),
                       ('linux-signature.lock', 'linux-6.12.111.tar.sign')):
        records = [line for line in (snapshot / 'sources' / lock).read_text().splitlines() if line and not line.startswith('#')]
        if len(records) != 1:
            raise ValueError('Kernel source lock must have one record')
        archive, url, checksum = records[0].split('|')
        if archive != name or not url.startswith('https://') or not checksum.startswith('sha512='):
            raise ValueError('Unexpected kernel source/signature lock')
        path = build / 'distfiles' / name
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha512').hexdigest()
        if checksum != 'sha512=' + actual:
            raise ValueError(f'Kernel source/signature checksum mismatch: {name}')
        paths.append(path)
        hashes.append(actual)
    with tempfile.TemporaryDirectory(prefix='gentoo-signature-') as temporary:
        # No keyserver, default keyring, private key, ownertrust or operator
        # GNUPGHOME is consulted. Only the shipped public release key is used.
        unpack = subprocess.Popen(['xz', '-dc', str(paths[0])], stdout=subprocess.PIPE)
        try:
            verify = subprocess.run(['gpgv', '--homedir', temporary, '--status-fd', '1', '--keyring',
                                     str((snapshot / 'keys/kernel-release.gpg').resolve()), str(paths[1]), '-'],
                                    stdin=unpack.stdout, text=True, capture_output=True)
        finally:
            unpack.stdout.close()
            unpack_status = unpack.wait()
        if verify.returncode or unpack_status:
            raise ValueError(f'Kernel detached signature verification failed: {verify.stderr.strip()}')
        signers = [line.split() for line in verify.stdout.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
        if len(signers) != 1 or FINGERPRINT not in (signers[0][2], signers[0][-1]):
            raise ValueError('Kernel signature belongs to an unexpected primary key')
    return {'primary_fingerprint': FINGERPRINT, 'source_sha512': hashes[0], 'signature_sha512': hashes[1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, required=True, help='mounted workspace/gentoo-bootstrap')
    args = parser.parse_args()
    build = args.build_root.resolve(strict=True)
    selected = (build / 'current-handoff').read_text().strip()
    import re
    if not re.fullmatch('[0-9a-f]{64}', selected):
        parser.error('Invalid handoff selector')
    result = verify_kernel_source(build, build / 'inputs' / selected)
    print(f'Kernel source and detached signature verified: {result["primary_fingerprint"]}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f'gentoo-kernel-source: {error}')
