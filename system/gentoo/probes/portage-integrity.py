#!/usr/bin/env python3
"""Audit Portage strict digest checks without running ebuild test phases."""
# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
from pathlib import Path
import tempfile
from portage.manifest import Manifest
from portage.package.ebuild.digestcheck import digestcheck

with tempfile.TemporaryDirectory(prefix='portage-integrity-') as temporary:
    root = Path(temporary)
    package = root / 'category' / 'fixture'
    package.mkdir(parents=True)
    distfiles = root / 'distfiles'
    distfiles.mkdir()
    archive = distfiles / 'fixture.tar.xz'
    original = b'verified source bytes'
    archive.write_bytes(original)
    manifest = package / 'Manifest'
    manifest.write_text(f'DIST {archive.name} {len(original)} SHA512 {hashlib.sha512(original).hexdigest()}\n')
    saved = manifest.read_bytes()
    settings = {'O': str(package), 'DISTDIR': str(distfiles), 'PORTAGE_QUIET': '1'}

    def verify(name):
        parsed = Manifest(str(package), str(distfiles), thin=True, allow_missing=False)
        result = digestcheck([name], settings, strict=True, mf=parsed)
        assert manifest.read_bytes() == saved, 'digestcheck changed a Manifest'
        return result

    assert verify(archive.name) == 1
    archive.write_bytes(b'X' * len(original))
    assert verify(archive.name) == 0, 'strict digestcheck accepted corrupt data'
    archive.write_bytes(original)
    assert verify('source-with-no-checksum.tar.xz') == 0, 'strict digestcheck accepted a missing source'
    manifest.write_text(f'DIST {archive.name} {len(original)}\n')
    saved = manifest.read_bytes()
    assert verify(archive.name) == 0, 'strict digestcheck accepted a size-only Manifest record'
print('Portage strict digestcheck rejects corrupt sources and missing required checksums without changing Manifests.')
