#!/usr/bin/python3.12
"""Temporary mount helper for the initial native Portage root."""
# SPDX-License-Identifier: GPL-3.0-or-later
import ctypes
import os
import sys

MS_REC = 16384
MS_UNBINDABLE = 1 << 17
MS_PRIVATE = 1 << 18
MS_SLAVE = 1 << 19
MS_SHARED = 1 << 20

OPERATIONS = {
    '--make-unbindable': MS_UNBINDABLE,
    '--make-runbindable': MS_UNBINDABLE | MS_REC,
    '--make-private': MS_PRIVATE,
    '--make-rprivate': MS_PRIVATE | MS_REC,
    '--make-slave': MS_SLAVE,
    '--make-rslave': MS_SLAVE | MS_REC,
    '--make-shared': MS_SHARED,
    '--make-rshared': MS_SHARED | MS_REC,
}
OPTION_OPERATIONS = {
    'unbindable': MS_UNBINDABLE,
    'runbindable': MS_UNBINDABLE | MS_REC,
    'private': MS_PRIVATE,
    'rprivate': MS_PRIVATE | MS_REC,
    'slave': MS_SLAVE,
    'rslave': MS_SLAVE | MS_REC,
    'shared': MS_SHARED,
    'rshared': MS_SHARED | MS_REC,
}


def main(argv):
    # Once Gentoo util-linux is installed, transparently delegate to its mount.
    system_mount = next((path for path in ('/usr/bin/mount', '/bin/mount')
                         if os.path.isfile(path) and os.access(path, os.X_OK)), None)
    if system_mount is not None:
        os.execv(system_mount, [system_mount, *argv[1:]])

    operation = None
    mount_type = None
    positionals = []
    args = iter(argv[1:])
    for arg in args:
        if arg == '-n':
            # util-linux's --no-mtab; there is no mtab to update here.
            continue
        if arg in ('-t', '--types'):
            try:
                mount_type = next(args)
            except StopIteration:
                mount_type = None
                break
            continue
        if arg in ('-o', '--options'):
            try:
                option_text = next(args)
            except StopIteration:
                option_text = ''
            option_items = option_text.split(',')
            if len(option_items) == 1 and option_items[0] in OPTION_OPERATIONS and operation is None:
                operation = OPTION_OPERATIONS[option_items[0]]
            else:
                operation = None
                positionals = []
                break
        elif arg.startswith('-o') and len(arg) > 2:
            option_items = arg[2:].split(',')
            if len(option_items) == 1 and option_items[0] in OPTION_OPERATIONS and operation is None:
                operation = OPTION_OPERATIONS[option_items[0]]
            else:
                mount_type = None
                positionals = []
                break
        elif arg in OPERATIONS:
            if operation is not None:
                operation = None
                positionals = []
                break
            operation = OPERATIONS[arg]
        elif any(arg.startswith(option + '=') for option in OPERATIONS):
            option, target = arg.split('=', 1)
            if operation is not None:
                operation = None
                positionals = []
                break
            operation = OPERATIONS[option]
            positionals.append(target)
        elif arg.startswith('/') or not arg.startswith('-'):
            positionals.append(arg)
        else:
            operation = None
            mount_type = None
            positionals = []
            break

    if (operation is not None and mount_type is None and len(positionals) == 1
            and positionals[0].startswith('/')):
        source = filesystem = None
        target = os.fsencode(positionals[0])
        flags = operation
    elif operation is None and mount_type == 'proc' and positionals == ['proc', '/proc']:
        # Portage's sandbox creates a private proc mount with exactly
        # `mount -n -t proc proc /proc` before util-linux is available.
        source, target, filesystem, flags = b'proc', b'/proc', b'proc', 0
    else:
        print('bootstrap mount supports only propagation changes and the exact proc mount before util-linux is installed; '
              f'arguments={argv[1:]!r}', file=sys.stderr)
        return 2

    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.c_ulong, ctypes.c_void_p)
    libc.mount.restype = ctypes.c_int
    if libc.mount(source, target, filesystem, flags, None) != 0:
        error = ctypes.get_errno()
        print(f'mount: {os.fsdecode(target)}: {os.strerror(error)}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
