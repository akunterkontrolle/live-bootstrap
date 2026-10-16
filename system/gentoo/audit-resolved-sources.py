#!/usr/bin/env python3
"""Record actual source metadata and payload warning signs for a pretend plan.

This report supports semantic review; a quiet scan is not proof of source-only
bootstrap ancestry. Run inside the native Gentoo root before each major merge.
"""
import hashlib
import json
from pathlib import Path
import re
import sys
import portage
from portage.dep import Atom

plan, output = Path(sys.argv[1]), Path(sys.argv[2])
cpvs = set()
for line in plan.read_text().splitlines():
    if line.startswith('[binary'):
        raise SystemExit('Binary package found in resolved plan')
    match = re.match(r'^\[ebuild[^]]*\]\s+(\S+)', line)
    if match:
        cpvs.add(Atom('=' + match.group(1)).cpv)
if not cpvs:
    raise SystemExit('No ebuilds in plan; use --emptytree for a full closure audit')
portdb = portage.db['/']['porttree'].dbapi
records = []
pattern = re.compile(r'(?i)(prebuilt|stage3|stage0|bootstrap.*binary|rust-bin|clang-bin|gcc-bin|wasi-sdk|\.deb\b|\.rpm\b|binpkg)')
for cpv in sorted(cpvs):
    settings = portage.config(clone=portage.settings)
    settings.setcpv(cpv, mydb=portdb)
    inherited, uri = portdb.aux_get(cpv, ['INHERITED', 'SRC_URI'])
    ebuild = Path(portdb.findname(cpv))
    repo = ebuild.parents[2]
    # Thin manifests contain distfile records only. Metadata-only packages
    # such as acct-group/audio have no SRC_URI and legitimately no Manifest.
    # Still require the Manifest for every package declaring source archives.
    manifest = ebuild.parent / 'Manifest'
    files = [ebuild] + ([manifest] if manifest.exists() or uri.strip() else [])
    files += [repo / 'eclass' / (name + '.eclass') for name in inherited.split()]
    evidence = []
    for path in files:
        if not path.is_file():
            raise SystemExit(f'Missing source audit input: {path}')
        evidence.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'warning_lines': [f'{n}: {line}' for n, line in enumerate(path.read_text(errors='replace').splitlines(), 1) if pattern.search(line)]})
    fetch = {name: sorted(urls) for name, urls in portdb.getFetchMap(cpv, useflags=settings['PORTAGE_USE'].split()).items()}
    records.append({'cpv': cpv, 'use': settings['PORTAGE_USE'], 'src_uri': uri, 'distfiles': fetch, 'inputs': evidence,
                    'manifest_present': manifest.is_file(),
                    'firmware_exception': cpv == 'sys-kernel/linux-firmware-20260916'})
output.write_text(json.dumps({'plan_sha256': hashlib.sha256(plan.read_bytes()).hexdigest(), 'packages': records,
                             'semantic_review': 'required; inspect distfile payloads too'}, indent=2) + '\n')
print(f'Recorded {len(records)} packages and inherited eclasses: {output}')
