#!/bin/sh
# SPDX-FileCopyrightText: 2026 akunterkontrolle
# SPDX-License-Identifier: GPL-3.0-or-later
set -ex
make_linux24_initrd -s 1048576 /boot/linux24.ext2
if match x${BARE_METAL} xTrue; then
    kexec-linux24 /boot/vmlinux-linux24 /boot/linux24.ext2.gz /e820 "root=/dev/ram0 rw init=/usr/bin/kaem ramdisk_size=1048576 console=tty0 consoleblank=0" t440p
else
    kexec-linux24 /boot/vmlinux-linux24 /boot/linux24.ext2.gz /e820 "root=/dev/ram0 rw init=/usr/bin/kaem ramdisk_size=1048576 console=tty0 console=ttyS0,115200 consoleblank=0"
fi
