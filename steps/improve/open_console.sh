#!/bin/sh

# SPDX-FileCopyrightText: 2024 Gábor Stefanik <netrolller.3d@gmail.com>
#
# SPDX-License-Identifier: GPL-3.0-or-later

if bash --version | grep -q 'GPLv3'; then
    env - PATH=${PREFIX}/bin PS1="\w # " openvt -- bash -i
else
    bash -c 'while true; do printf "[early Bash] $PWD # "; if ! read -r cmd; then sleep 1; continue; fi; eval "$cmd"; done' </dev/tty2 &> /dev/tty2 &
fi
