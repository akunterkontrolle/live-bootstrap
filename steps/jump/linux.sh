#!/bin/bash
#
# SPDX-FileCopyrightText: 2023 Samuel Tyler <samuel@samuelt.me>
#
# SPDX-License-Identifier: GPL-3.0-or-later

. /steps/bootstrap.cfg

set -e

# Perform the actual kexec
if [ "${KERNEL_BOOTSTRAP}" = True ]; then
    mv "/High Level Prototypes" "/High_Level_Prototypes"
    rm -f /initramfs.list /boot/linux-next.initramfs.bz2
    sync
    # /steps is a separate tmpfs on the PAE bridge. Include it explicitly;
    # find / -xdev stops at its mount point.
    find / -xdev -type d -printf "dir %p %m %U %G\n" >> /initramfs.list
    find / -xdev -type f -printf "file %p %p %m %U %G\n" >> /initramfs.list
    find / -xdev -type l -printf "slink %p %l %m %U %G\n" >> /initramfs.list
    find /steps -mindepth 1 -xdev -type d -printf "dir %p %m %U %G\n" >> /initramfs.list
    find /steps -mindepth 1 -xdev -type f -printf "file %p %p %m %U %G\n" >> /initramfs.list
    find /steps -mindepth 1 -xdev -type l -printf "slink %p %l %m %U %G\n" >> /initramfs.list
    rm -f /tmp/linux-initramfs-complete
    (gen_init_cpio /initramfs.list && echo complete > /tmp/linux-initramfs-complete) | bzip2 -c > /boot/linux-next.initramfs.bz2
    test -f /tmp/linux-initramfs-complete
    rm /tmp/linux-initramfs-complete
    if [ "${BARE_METAL}" = True ]; then
        kexec-linux /boot/vmlinuz /boot/linux-next.initramfs.bz2 /e820 \
            "init=/init console=tty0" t440p
    else
        kexec-linux /boot/vmlinuz /boot/linux-next.initramfs.bz2 /e820 \
            "init=/init console=tty0 console=ttyS0,115200"
    fi
else
    mkdir /etc
    # kexec time
    if [ "${BARE_METAL}" = True ]; then
        kexec -l "/boot/vmlinuz" \
            --append="root=/dev/sda1 init=/init rw rootwait"
    else
        kexec -l "/boot/vmlinuz" --console-serial \
            --append="console=ttyS0 root=/dev/sda1 init=/init rw rootwait"
    fi
    sync
    echo u > /proc/sysrq-trigger || true
    mount -o remount,ro / || true
    kexec -e
fi
