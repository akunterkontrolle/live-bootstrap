#!/usr/bin/env python3
"""Reuse configured bootstrap IPv4; bound a needed DHCP request externally."""
# SPDX-License-Identifier: GPL-3.0-or-later
import fcntl
import ipaddress
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys


def ipv4_ready():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for line in Path('/proc/net/route').read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) < 4 or fields[1] != '00000000' or not int(fields[3], 16) & 1:
                continue
            try:
                info = fcntl.ioctl(sock, 0x8915, struct.pack('256s', fields[0].encode()[:15]))
                address = ipaddress.IPv4Address(info[20:24])
            except OSError:
                continue
            if not (address.is_unspecified or address.is_loopback or address.is_link_local):
                return True
    return False


def request_dhcp(command, seconds=65):
    # A CLI talking to an old daemon may block outside dhcpcd's lease timeout.
    # A separate process group lets us terminate this request without signalling
    # PID 1, the serial shell, or any pre-existing DHCP daemon.
    process = subprocess.Popen(command, start_new_session=True)
    try:
        return process.wait(timeout=seconds)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        if isinstance(error, KeyboardInterrupt):
            return 130
        print('gentoo: DHCP control request exceeded its external timeout.', file=sys.stderr)
        return 124


def main():
    if ipv4_ready():
        print('Reusing configured IPv4 address and default route.', flush=True)
        return 0
    for name in ('/var/run/dhcpcd', '/var/db/dhcpcd'):
        Path(name).mkdir(parents=True, exist_ok=True)
    print('Requesting IPv4 networking (65-second external limit).', flush=True)
    result = request_dhcp(['dhcpcd', '--waitip=4', '--timeout', '60'])
    if result:
        return result
    if not ipv4_ready():
        print('gentoo: DHCP returned without a usable IPv4 address and default route.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except OSError as error:
        print('gentoo: bootstrap networking failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
