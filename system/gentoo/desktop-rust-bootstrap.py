#!/usr/bin/env python3
"""Print the source Rust build order from the installed pinned Portage tree."""

import re
import sys


def release_minor(cpv):
    match = re.fullmatch(r"dev-lang/rust-(\d+)\.(\d+)\.(\d+)(?:_p\d+)?(?:-r\d+)?", cpv)
    return (int(match[1]), int(match[2])) if match else None


def select_chain(visible, seeds, best):
    releases = [cpv for cpv in visible if release_minor(cpv) is not None]
    seeds = [cpv for cpv in seeds if cpv in releases]
    if not seeds:
        raise ValueError("no visible source Rust ebuild declares mrustc-bootstrap")
    seed = best(seeds)
    target = best(releases)
    start, end = release_minor(seed), release_minor(target)
    if start[0] != end[0]:
        raise ValueError("Rust bootstrap chain crosses an unsupported major release")
    chain = [seed]
    for minor in range(start[1] + 1, end[1] + 1):
        candidates = [cpv for cpv in releases if release_minor(cpv) == (start[0], minor)]
        if not candidates:
            raise ValueError(f"missing visible intermediate Rust {start[0]}.{minor} slot")
        chain.append(best(candidates))
    return chain


def main():
    import portage

    db = portage.db["/"]["porttree"].dbapi
    visible = db.xmatch("match-visible", "dev-lang/rust")
    seeds = [
        cpv for cpv in visible
        if "mrustc-bootstrap" in {
            flag.lstrip("+-") for flag in db.aux_get(cpv, ["IUSE"])[0].split()
        }
    ]
    try:
        chain = select_chain(visible, seeds, portage.best)
    except ValueError as error:
        print(f"Rust source bootstrap: {error}", file=sys.stderr)
        return 1
    for cpv in chain:
        print("=" + cpv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
