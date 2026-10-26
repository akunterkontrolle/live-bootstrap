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
        raise ValueError("no visible source Rust ebuild enables mrustc-bootstrap in effective settings")
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



def enabled_seeds(visible, iuse, effective_use):
    # IUSE alone is insufficient: the pinned profile masks bootstrap on some
    # newer Rust recipes. Never choose a seed that cannot enable source bootstrap.
    return [cpv for cpv in visible if
            "mrustc-bootstrap" in {flag.lstrip("+-") for flag in iuse(cpv).split()} and
            "mrustc-bootstrap" in effective_use(cpv).split()]


def main():
    import portage

    db = portage.db["/"]["porttree"].dbapi
    visible = db.xmatch("match-visible", "dev-lang/rust")
    def effective_use(cpv):
        settings = portage.config(clone=portage.settings)
        settings.setcpv(cpv, mydb=db)
        return settings["PORTAGE_USE"]

    seeds = enabled_seeds(visible, lambda cpv: db.aux_get(cpv, ["IUSE"])[0], effective_use)
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
