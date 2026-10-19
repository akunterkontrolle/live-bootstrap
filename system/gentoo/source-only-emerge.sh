#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
exec /usr/bin/python3.12 /usr/local/libexec/gentoo-continuation/source-only-emerge.py /usr/lib/portage/bin/emerge "$@"
