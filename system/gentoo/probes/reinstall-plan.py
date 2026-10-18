#!/usr/bin/env python3
"""Accept only already-installed exact-version reinstalls with identical USE."""
# SPDX-License-Identifier: GPL-3.0-or-later
import re
import sys


def verify_plan(text, atom, vardb, portdb, settings_factory):
    if not re.search(r'^Calculating dependencies[ .]*done!', text, re.M):
        return False
    if re.search(r'^!!!|^[ \t]*\* Error:', text, re.M):
        return False
    count = 0
    for line in text.splitlines():
        if not line.startswith('['):
            continue
        match = re.match(r'^\[ebuild\s+R\s*\]\s+(\S+)', line)
        if not match:
            return False
        try:
            selected = atom('=' + match.group(1))
            cpv = selected.cpv
            if not vardb.cpv_exists(cpv) or not portdb.cpv_exists(cpv):
                return False
            old_use, repository = vardb.aux_get(cpv, ['USE', 'repository'])
            if selected.repo and selected.repo != repository:
                return False
            settings = settings_factory()
            settings.setcpv(cpv, mydb=portdb)
            if set(settings['PORTAGE_USE'].split()) != set(old_use.split()):
                return False
        except (KeyError, TypeError, ValueError):
            return False
        count += 1
    return count > 0


def main(argv):
    import portage
    from pathlib import Path
    from portage.dep import Atom
    if len(argv) != 1:
        raise SystemExit('usage: reinstall-plan.py PLAN')
    accepted = verify_plan(Path(argv[0]).read_text(), Atom,
                           portage.db['/']['vartree'].dbapi,
                           portage.db['/']['porttree'].dbapi,
                           lambda: portage.config(clone=portage.settings))
    return 0 if accepted else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
