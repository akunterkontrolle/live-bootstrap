#!/bin/bash
# SPDX-FileCopyrightText: 2026 akunterkontrolle
# SPDX-License-Identifier: GPL-3.0-or-later
set -e
# The root RAM disk uses lowmem pages. Keep growing build trees on tmpfs,
# whose file data can use the PAE highmem pages above 4GiB.
tcc -static -o /tmp/linux24-mount /steps/improve/linux24-mount.c
mv /steps /steps.lowmem
mkdir /steps
/tmp/linux24-mount
tar -cf /steps/bootstrap-tree.tar -C /steps.lowmem .
tar -xf /steps/bootstrap-tree.tar -C /steps
rm /steps/bootstrap-tree.tar
cd /steps
rm -rf /steps.lowmem
rm /tmp/linux24-mount
echo "Build trees now use PAE-backed tmpfs"
