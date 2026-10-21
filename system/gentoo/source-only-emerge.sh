#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu
# Gentoo installs emerge through python-exec. Ask that package-managed entry
# which runtime it selected, then run the guard and emerge under the same one.
case "$(readlink -f /usr/bin/emerge)" in
    */python-exec2|*/python-exec2c)
        banner=$(LC_ALL=C /usr/bin/emerge --version)
        python=$(printf '%s\n' "$banner" | sed -n 's/^Portage .* (python \(3\.[0-9][0-9]*\)\..*/\1/p')
        case "$python" in 3.[0-9]|3.[0-9][0-9]) ;; *) echo 'gentoo: cannot identify installed Portage Python runtime' >&2; exit 1 ;; esac
        entry=/usr/lib/python-exec/python$python/emerge
        [ -f "$entry" ] && [ -x "/usr/bin/python$python" ] || {
            echo 'gentoo: installed Portage runtime entry is unavailable' >&2; exit 1;
        }
        exec "/usr/bin/python$python" /usr/local/libexec/gentoo-continuation/source-only-emerge.py "$entry" "$@"
        ;;
    *)
        # Before the first Gentoo Portage merge, only the verified source tree
        # and the source-built Python 3.12 prerequisite exist in this root.
        [ -r /usr/lib/portage/lib/portage/__init__.py ] || {
            echo 'gentoo: verified bootstrap Portage runtime is unavailable' >&2; exit 1;
        }
        export PYTHONPATH=/usr/lib/portage/lib
        exec /usr/bin/python3.12 /usr/local/libexec/gentoo-continuation/source-only-emerge.py /usr/lib/portage/bin/emerge "$@"
        ;;
esac
